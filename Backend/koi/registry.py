"""
registry.py

Holds one live PondTwin per user in process memory, and guarantees only
one thread mutates a given user's twin at a time.

A PondTwin aggregates all three domain engines (chemistry, evaporation,
algae) - see pond_twin.py for why they share one lock and one snapshot
rather than being tracked independently. The short version: a single
logged intervention mutates several engines at once, and a partial write
would leave the pond in a state that never physically existed.

Two layers keep a pond's state consistent:

  * Within one process, a per-pond lock stops a request thread and a poll
    thread from interleaving their reads and writes of the same twin.
  * Across processes (gunicorn API workers and the koi.worker poller each
    hold their own registry), the stored snapshot_version does. Each
    registry remembers the version it loaded; before every access it
    reloads the pond if the stored version is newer, and it saves against
    the version it loaded. If another process saved in between, storage
    rejects the save with StaleSnapshotError; the registry then reloads
    and runs the call once more on the fresh state. A second rejection
    propagates to the caller.

The retry re-runs the whole callback, so a callback's side effects (the
event endpoints push evaluation rows) can happen twice when a save races.
The evaluation logs are append-only and the later row reflects the saved
state, so that is harmless.

A call that fails for any other reason (the callback raised, or the save
failed) drops the in-memory twin as well: it may hold changes that were
never saved, and the next access must start again from the stored
snapshot. The poller relies on this for sensor ingestion: readings
applied to a twin whose save failed are not in the ledger, so the next
cycle finds them again and applies them once to the stored state.
"""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from koi.errors import PondNotConfigured
from koi.logs import log_event
from koi.models import evaporation_engine as ev
from koi.models.engine import PondConfig
from koi.models.hypoxia import HypoxiaThresholds
from koi.models.pond_twin import PondTwin, SensorHistory
from koi.models.profile import ProfileHistory, profile_from_row, profile_from_userdata_config
from koi.models.sensor_inputs import SENSOR_TYPES, IngestConfig, group_rows
from koi.storage import StaleSnapshotError, Storage

log = logging.getLogger(__name__)


@dataclass
class _Loaded:
    twin: PondTwin
    version: int  # snapshot_version the twin was loaded at or last saved as; 0 = never stored


class EngineRegistry:
    """One per process, shared by the API routes and the poller when both
    run in it (see koi.api.create_app and koi.worker)."""

    def __init__(self, storage: Storage, hypoxia_thresholds: Optional[HypoxiaThresholds] = None,
                 sensor_ingest: Optional[IngestConfig] = None):
        self.storage = storage
        # Temperature levels for the night-time hypoxia flag (Settings),
        # read by the poller and /assessment/all.
        self.hypoxia_thresholds = hypoxia_thresholds or HypoxiaThresholds()
        # The poller's sensor ingestion settings (Settings.sensor_ingest).
        self.sensor_ingest = sensor_ingest or IngestConfig()
        self._twins: dict[int, _Loaded] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._registry_lock = threading.Lock()  # protects the two dicts above

    def _lock_for(self, user_id: int) -> threading.Lock:
        with self._registry_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    def profile_history(self, user_id: int) -> Optional[ProfileHistory]:
        """The pond's profile history: its pond_profile rows, or, for a
        pond with none, one undated profile from its UserData volume and
        biomass. None when the pond has neither."""
        rows = self.storage.fetch_pond_profiles(user_id)
        if rows:
            return ProfileHistory([profile_from_row(r) for r in rows])
        config = self.storage.fetch_pond_config(user_id)
        return ProfileHistory([profile_from_userdata_config(config)]) if config is not None else None

    def sensor_history(self, user_id: int) -> SensorHistory:
        """What the twin reads back to replay (PondTwin._replay): the
        pond's ledgered sensor rows with an effective sample time in
        (after, until], grouped into inputs as the poller groups them."""
        def history(after: Optional[datetime], until: datetime) -> list:
            rows = self.storage.fetch_ingested_sensor_rows(user_id, SENSOR_TYPES, after, until)
            return group_rows(rows).inputs
        return history

    def _load_or_create(
        self,
        user_id: int,
        default_config: Optional[PondConfig],
        pond_depth_m: float,
        profiles: Optional[ProfileHistory] = None,
    ) -> _Loaded:
        """The pond's twin, reloaded from storage if another process has
        saved a newer snapshot since this registry loaded it."""
        loaded = self._twins.get(user_id)
        if loaded is not None and self.storage.fetch_snapshot_version(user_id) <= loaded.version:
            return loaded

        state = self.storage.load_engine_state(user_id)
        version = 0
        if state is not None:
            # from_snapshot transparently upgrades legacy bare-chemistry
            # snapshots, so existing ponds keep their accumulated nitrogen
            # state across this deployment.
            snapshot, version = state
            twin = PondTwin.from_snapshot(snapshot, pond_depth_m=pond_depth_m)
        elif default_config is not None:
            twin = PondTwin.create(default_config, pond_depth_m=pond_depth_m)
        elif profiles is not None:
            twin = PondTwin.create(profiles.at(datetime.now(timezone.utc)).pond_config(), pond_depth_m=pond_depth_m)
        else:
            raise PondNotConfigured(user_id)

        # Rehydrate the human rating history. Ratings live in their own
        # table rather than only inside the snapshot, so a snapshot written
        # before this feature existed (or one truncated by the retention
        # cap) still recovers the full calibration set.
        try:
            rows = self.storage.fetch_algae_ratings(user_id, limit=200)
            if rows and not twin.algae._ratings:
                twin.algae.load_ratings(rows)
        except Exception as exc:  # noqa: BLE001 - ratings are optional
            log_event(log, "algae_ratings_unavailable", level=logging.WARNING, pond_id=user_id, error=str(exc))

        loaded = _Loaded(twin, version)
        self._twins[user_id] = loaded
        return loaded

    def with_twin(
        self,
        user_id: int,
        fn: Callable[[PondTwin], Any],
        default_config: Optional[PondConfig] = None,
        pond_depth_m: float = ev.DEFAULT_POND_DEPTH_M,
        persist: bool = True,
        profiles: Optional[ProfileHistory] = None,
        ingest: Optional[Callable[[], Optional[dict]]] = None,
    ):
        """Runs fn(twin) under this user's lock, then persists the snapshot.

        profiles (from profile_history) is handed to the twin, which is put
        on the profile in force now before fn runs; the twin's own steps
        then read the profile in force at each step's time. It also
        creates the twin of a pond with no snapshot when default_config
        is not given.

        Use this for every mutation (event application, sensor ingest,
        camera assimilation) so locking + persistence can never be
        forgotten at a call site.

        persist=False skips the Supabase write for genuinely read-only
        work - the forecast endpoints project onto local copies and leave
        state untouched, so writing an identical snapshot back on every
        chart refresh would be pure write amplification. The lock is still
        taken, because reading a twin mid-mutation from the poller thread
        would produce a torn view.

        ingest, called after fn, returns the sensor rows fn applied
        (Discovery.commit()) or None; they are saved with the snapshot in
        one transaction (Storage.save_engine_snapshot).

        If another process saved this pond between the load and the save,
        the in-memory twin is dropped, reloaded and fn runs once more (see
        the module docstring); a second StaleSnapshotError is raised. Any
        other failure drops the in-memory twin and is raised.
        """
        lock = self._lock_for(user_id)
        with lock:
            for attempt in (1, 2):
                try:
                    return self._run(user_id, fn, default_config, pond_depth_m, persist, profiles, ingest)
                except StaleSnapshotError as exc:
                    self._twins.pop(user_id, None)
                    if attempt == 2:
                        raise
                    log_event(log, "stale_snapshot_retry", level=logging.WARNING, pond_id=user_id, error=str(exc))
                except BaseException:
                    self._twins.pop(user_id, None)
                    raise
            raise AssertionError("unreachable")

    def _run(self, user_id, fn, default_config, pond_depth_m, persist, profiles=None, ingest=None):
        loaded = self._load_or_create(user_id, default_config, pond_depth_m, profiles)
        loaded.twin.profiles = profiles
        if profiles is not None:
            loaded.twin.use_profile(profiles.at(datetime.now(timezone.utc)))
        result = fn(loaded.twin)
        if persist:
            commit = ingest() if ingest is not None else None
            if commit is None:
                loaded.version = self.storage.save_engine_snapshot(
                    user_id, loaded.twin.to_snapshot(), base_version=loaded.version)
            else:
                loaded.version = self.storage.save_engine_snapshot(
                    user_id, loaded.twin.to_snapshot(), base_version=loaded.version, ingest=commit)
        return result

    # Backwards-compatible alias. Older call sites passed a callback
    # expecting the chemistry engine directly; keeping this shim means
    # any straggler keeps working instead of failing at import time.
    def with_engine(
        self,
        user_id: int,
        fn: Callable,
        default_config: Optional[PondConfig] = None,
        persist: bool = True,
    ):
        return self.with_twin(
            user_id,
            lambda twin: fn(twin.chemistry),
            default_config=default_config,
            persist=persist,
        )

    def evict(self, user_id: int) -> None:
        """Drops a user's in-memory twin so the next access reloads from
        storage. Useful after an out-of-band snapshot change."""
        with self._registry_lock:
            self._twins.pop(user_id, None)
