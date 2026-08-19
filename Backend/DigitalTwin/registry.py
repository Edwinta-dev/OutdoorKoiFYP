"""
registry.py

Holds one live WaterChemistryEngine per user in process memory, and
guarantees only one thread mutates a given user's engine at a time.

This is the piece that makes the architecture safe for a SINGLE Flask
process handling both the event endpoints (Flask's own request threads)
and the poller (a background thread) concurrently - without this lock,
a feeding-event request and a poll cycle for the same user could interleave
their reads/writes to the same pools and produce a corrupted state.

IMPORTANT LIMITATION: this lock only protects against races WITHIN one
process. It does NOT protect against running multiple Flask worker
processes (e.g. `gunicorn -w 4`) or multiple server instances - each
worker/instance would have its own independent copy of this registry and
could stomp on the Supabase-persisted snapshot. See the architecture
write-up for why this app should run as a single process/worker for now,
and what to change if that ever needs to scale.
"""
import threading
from typing import Callable

from engine import WaterChemistryEngine, PondConfig
import state_store


class EngineRegistry:
    def __init__(self):
        self._engines: dict[int, WaterChemistryEngine] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._registry_lock = threading.Lock()  # protects the two dicts above

    def _lock_for(self, user_id: int) -> threading.Lock:
        with self._registry_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    def _load_or_create(self, user_id: int, default_config: PondConfig | None) -> WaterChemistryEngine:
        if user_id in self._engines:
            return self._engines[user_id]
        snapshot = state_store.load_engine_snapshot(user_id)
        if snapshot is not None:
            engine = WaterChemistryEngine.from_snapshot(snapshot)
        elif default_config is not None:
            engine = WaterChemistryEngine(default_config)
        else:
            raise ValueError(
                f"No persisted state and no PondConfig supplied for user {user_id}"
            )
        self._engines[user_id] = engine
        return engine

    def with_engine(self, user_id: int, fn: Callable[[WaterChemistryEngine], any],
                     default_config: PondConfig | None = None):
        """Runs fn(engine) under this user's lock, then persists the snapshot.
        Use this for every mutation (event application, sensor ingest) so
        locking + persistence can never be forgotten at a call site."""
        lock = self._lock_for(user_id)
        with lock:
            engine = self._load_or_create(user_id, default_config)
            result = fn(engine)
            state_store.save_engine_snapshot(user_id, engine.to_snapshot())
            return result


registry = EngineRegistry()
