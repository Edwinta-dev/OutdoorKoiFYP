"""
registry.py

Holds one live PondTwin per user in process memory, and guarantees only
one thread mutates a given user's twin at a time.

A PondTwin aggregates all three domain engines (chemistry, evaporation,
algae) - see pond_twin.py for why they share one lock and one snapshot
rather than being tracked independently. The short version: a single
logged intervention mutates several engines at once, and a partial write
would leave the pond in a state that never physically existed.

This is the piece that makes the architecture safe for a SINGLE Flask
process handling both the event endpoints (Flask's own request threads)
and the poller (a background thread) concurrently - without this lock, a
feeding-event request and a poll cycle for the same user could interleave
their reads/writes and produce corrupted state.

IMPORTANT LIMITATION: this lock only protects against races WITHIN one
process. It does NOT protect against running multiple Flask worker
processes (e.g. `gunicorn -w 4`) or multiple server instances - each
worker/instance would have its own independent copy of this registry and
could stomp on the Supabase-persisted snapshot. See the architecture
write-up for why this app should run as a single process/worker for now,
and what to change if that ever needs to scale.
"""
import threading
from typing import Any, Callable, Optional

from koi.models import evaporation_engine as ev
from koi.models.engine import PondConfig
from koi.models.pond_twin import PondTwin
from koi.storage import Storage


class EngineRegistry:
    """One per process, shared by the API routes and the poller when both
    run in it (see koi.api.create_app and koi.worker)."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self._twins: dict[int, PondTwin] = {}
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
    ) -> PondTwin:
        if user_id in self._twins:
            return self._twins[user_id]

        snapshot = self.storage.load_engine_snapshot(user_id)
        if snapshot is not None:
            # from_snapshot transparently upgrades legacy bare-chemistry
            # snapshots, so existing ponds keep their accumulated nitrogen
            # state across this deployment.
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

        self._twins[user_id] = twin
        return twin

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
        """
        lock = self._lock_for(user_id)
        with lock:
            twin = self._load_or_create(user_id, default_config, pond_depth_m)
            result = fn(twin)
            if persist:
                self.storage.save_engine_snapshot(user_id, twin.to_snapshot())
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
