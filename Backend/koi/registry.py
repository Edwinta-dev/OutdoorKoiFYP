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
"""
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from koi.models import evaporation_engine as ev
from koi.models.engine import PondConfig
from koi.models.pond_twin import PondTwin
from koi.storage import StaleSnapshotError, Storage


@dataclass
class _Loaded:
    twin: PondTwin
    version: int  # snapshot_version the twin was loaded at or last saved as; 0 = never stored


class EngineRegistry:
    """One per process, shared by the API routes and the poller when both
    run in it (see koi.api.create_app and koi.worker)."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self._twins: dict[int, _Loaded] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._registry_lock = threading.Lock()  # protects the two dicts above

    def _lock_for(self, user_id: int) -> threading.Lock:
        with self._registry_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    def _load_or_create(
        self,
        user_id: int,
        default_config: Optional[PondConfig],
        pond_depth_m: float,
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
        else:
            raise ValueError(
                f"No persisted state and no PondConfig supplied for user {user_id}"
            )

        # Rehydrate the human rating history. Ratings live in their own
        # table rather than only inside the snapshot, so a snapshot written
        # before this feature existed (or one truncated by the retention
        # cap) still recovers the full calibration set.
        try:
            rows = self.storage.fetch_algae_ratings(user_id, limit=200)
            if rows and not twin.algae._ratings:
                twin.algae.load_ratings(rows)
        except Exception as exc:  # noqa: BLE001 - ratings are optional
            print(f"[registry] could not load algae ratings for {user_id}: {exc}")

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
    ):
        """Runs fn(twin) under this user's lock, then persists the snapshot.

        Use this for every mutation (event application, sensor ingest,
        camera assimilation) so locking + persistence can never be
        forgotten at a call site.

        persist=False skips the Supabase write for genuinely read-only
        work - the forecast endpoints project onto local copies and leave
        state untouched, so writing an identical snapshot back on every
        chart refresh would be pure write amplification. The lock is still
        taken, because reading a twin mid-mutation from the poller thread
        would produce a torn view.

        If another process saved this pond between the load and the save,
        the in-memory twin is dropped, reloaded and fn runs once more (see
        the module docstring); a second StaleSnapshotError is raised.
        """
        lock = self._lock_for(user_id)
        with lock:
            try:
                return self._run(user_id, fn, default_config, pond_depth_m, persist)
            except StaleSnapshotError as exc:
                print(f"[registry] {exc}; reloading and retrying once")
                self._twins.pop(user_id, None)
            try:
                return self._run(user_id, fn, default_config, pond_depth_m, persist)
            except StaleSnapshotError:
                self._twins.pop(user_id, None)
                raise

    def _run(self, user_id, fn, default_config, pond_depth_m, persist):
        loaded = self._load_or_create(user_id, default_config, pond_depth_m)
        result = fn(loaded.twin)
        if persist:
            loaded.version = self.storage.save_engine_snapshot(
                user_id, loaded.twin.to_snapshot(), base_version=loaded.version)
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
