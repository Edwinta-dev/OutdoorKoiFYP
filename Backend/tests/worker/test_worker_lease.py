"""Running the API and the poller as separate processes (issue #8).

Each "process" here is its own EngineRegistry (and, for workers, its own
Worker) over one shared MemoryStorage, which is what separate gunicorn
API workers and a koi.worker process sharing one Supabase project look
like: nothing is shared between them except the stored rows.

Covers:
- two workers never poll the same pond in the same cycle (the lease);
- a snapshot save whose base version is stale is rejected and retried;
- an API registry picks up a snapshot a worker registry saved, and the
  other way round.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, make_settings, make_storage
from koi.api import create_app
from koi.models.engine import EventKind, PondConfig, PondEvent
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage, StaleSnapshotError
from koi.worker import poller

PONDS = [455, 456, 457, 458, 459, 460]


class Clock:
    """A clock tests move by hand, for lease expiry."""

    def __init__(self):
        self.now = datetime(2026, 8, 20, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def pond_storage(clock=None):
    storage = MemoryStorage(clock=clock)
    storage.add_rows("UserData", [{"userID": uid, "volume": 800, "biomass": 1.5} for uid in PONDS])
    return storage


HOLDERS: dict[int, str] = {}  # id(registry) -> the worker that owns it


def record_polls(monkeypatch):
    """Replaces _poll_user with one that records (thread-safe) which worker
    polled which pond. The worker is known by its registry."""
    polls = []
    lock = threading.Lock()

    def fake_poll_user(registry, user_id, row):
        with lock:
            polls.append((HOLDERS[id(registry)], user_id))

    monkeypatch.setattr(poller, "_poll_user", fake_poll_user)
    return polls


def make_worker(storage, holder, **settings):
    registry = EngineRegistry(storage)
    HOLDERS[id(registry)] = holder
    return poller.Worker(make_settings(**settings), registry, holder=holder)


# ---------------------------------------------------------------------
# The lease
# ---------------------------------------------------------------------
def test_memory_lease_is_taken_renewed_refused_and_expires():
    clock = Clock()
    storage = MemoryStorage(clock=clock)
    assert storage.take_lease("poller", "a", 60) is True
    assert storage.take_lease("poller", "b", 60) is False, "held and unexpired"
    clock.now += timedelta(seconds=50)
    assert storage.take_lease("poller", "a", 60) is True, "holder renews"
    clock.now += timedelta(seconds=50)
    assert storage.take_lease("poller", "b", 60) is False, "renewal moved the expiry"
    clock.now += timedelta(seconds=11)
    assert storage.take_lease("poller", "b", 60) is True, "expired lease is taken over"
    assert storage.rows("worker_lease") == [
        {"name": "poller", "holder": "b", "expires_at": (clock.now + timedelta(seconds=60)).isoformat()}]
    storage.release_lease("poller", "a")  # not the holder: no effect
    assert storage.take_lease("poller", "a", 60) is False
    storage.release_lease("poller", "b")
    assert storage.take_lease("poller", "a", 60) is True, "released lease is free"


def test_two_workers_never_poll_the_same_pond_in_the_same_cycle(monkeypatch, capsys):
    polls = record_polls(monkeypatch)
    clock = Clock()
    storage = pond_storage(clock)
    workers = [make_worker(storage, "worker-a", worker_threads=3),
               make_worker(storage, "worker-b", worker_threads=3)]
    per_cycle = []

    for _ in range(5):
        polls.clear()
        start = threading.Barrier(len(workers))
        ran = {}

        def cycle(worker, start=start, ran=ran):
            start.wait()  # both workers start the cycle at the same moment
            ran[worker.holder] = worker.run_cycle()

        threads = [threading.Thread(target=cycle, args=(w,)) for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ponds = [uid for _, uid in polls]
        assert sorted(ponds) == PONDS, f"every pond polled exactly once this cycle: {sorted(ponds)}"
        assert len({holder for holder, _ in polls}) == 1, f"one worker polled this cycle: {polls}"
        assert sorted(ran.values()) == [False, True], f"one active, one standby: {ran}"
        per_cycle.append(polls[0][0])
        clock.now += timedelta(minutes=15)

    assert len(set(per_cycle)) == 1, f"the lease holder stays active across cycles: {per_cycle}"
    assert "standby, another worker holds the poller lease" in capsys.readouterr().out


def test_standby_worker_takes_over_after_the_lease_holder_stops(monkeypatch):
    polls = record_polls(monkeypatch)
    clock = Clock()
    storage = pond_storage(clock)
    a = make_worker(storage, "worker-a", poll_interval_minutes=15)
    b = make_worker(storage, "worker-b", poll_interval_minutes=15)
    assert poller.lease_seconds(a.settings) == 30 * 60

    assert a.run_cycle() is True and b.run_cycle() is False
    # worker-a dies without releasing. worker-b stays on standby until the
    # lease (two intervals) runs out.
    clock.now += timedelta(minutes=15)
    assert b.run_cycle() is False
    clock.now += timedelta(minutes=15, seconds=1)
    assert b.run_cycle() is True
    assert a.run_cycle() is False, "the old holder comes back as the standby"

    # A clean stop hands over at the next cycle.
    b.stop()
    assert a.run_cycle() is True
    assert {h for h, _ in polls} == {"worker-a", "worker-b"}


def test_worker_skips_the_cycle_when_the_lease_cannot_be_read(monkeypatch):
    polls = record_polls(monkeypatch)
    storage = pond_storage()
    storage.failing.add("take_lease")
    assert make_worker(storage, "worker-a").run_cycle() is False
    assert polls == []


def test_worker_polls_ponds_on_a_thread_pool(monkeypatch):
    """With worker_threads=3 up to three ponds are in flight at once, and
    each pond is still polled exactly once."""
    in_flight, peak, seen = [0], [0], []
    lock = threading.Lock()
    all_started = threading.Barrier(3, timeout=5)

    def fake_poll_user(registry, user_id, row):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
            seen.append(user_id)
        if user_id in PONDS[:3]:
            all_started.wait()  # the first three must overlap
        with lock:
            in_flight[0] -= 1

    monkeypatch.setattr(poller, "_poll_user", fake_poll_user)
    assert make_worker(pond_storage(), "worker-a", worker_threads=3).run_cycle() is True
    assert peak[0] == 3, f"three ponds polled concurrently: peak {peak[0]}"
    assert sorted(seen) == PONDS


def test_one_failing_pond_does_not_stop_the_pool(monkeypatch):
    seen = []

    def fake_poll_user(registry, user_id, row):
        if user_id == PONDS[0]:
            raise RuntimeError("broken pond")
        seen.append(user_id)

    monkeypatch.setattr(poller, "_poll_user", fake_poll_user)
    poller._poll_once(EngineRegistry(pond_storage()), threads=4)
    assert sorted(seen) == PONDS[1:]


# ---------------------------------------------------------------------
# Snapshot versions
# ---------------------------------------------------------------------
def test_memory_snapshot_versions_count_saves_and_reject_stale_bases():
    storage = MemoryStorage()
    assert storage.fetch_snapshot_version(USER) == 0 and storage.load_engine_state(USER) is None
    assert storage.save_engine_snapshot(USER, {"n": 1}, base_version=0) == 1
    with pytest.raises(StaleSnapshotError):
        storage.save_engine_snapshot(USER, {"n": "new pond twice"}, base_version=0)
    assert storage.save_engine_snapshot(USER, {"n": 2}, base_version=1) == 2
    with pytest.raises(StaleSnapshotError, match="save_engine_snapshot"):
        storage.save_engine_snapshot(USER, {"n": "stale"}, base_version=1)
    assert storage.load_engine_state(USER) == ({"n": 2}, 2), "rejected save left the row alone"
    assert storage.save_engine_snapshot(USER, {"n": 3}) == 3, "no base version: unconditional"


def test_memory_rows_from_before_the_version_column_read_as_version_1():
    storage = MemoryStorage()
    storage.add_rows("pond_chemistry_state", [{"user_id": USER, "snapshot": {"old": True},
                                               "updated_at": "2026-01-01T00:00:00+00:00"}])
    assert storage.load_engine_state(USER) == ({"old": True}, 1)
    assert storage.save_engine_snapshot(USER, {"old": False}, base_version=1) == 2


def feed(grams):
    event = PondEvent(kind=EventKind.FEEDING, time=datetime(2026, 8, 20, tzinfo=timezone.utc),
                      food_grams=grams, protein_percent=40.0)
    return lambda twin: twin.apply_event(event)


def fed_grams(storage):
    return [e["food_grams"] for e in storage.load_engine_snapshot(USER)["chemistry"]["events"]]


def test_registry_stale_snapshot_save_is_rejected_and_retried():
    storage = make_storage()
    config = PondConfig(volume_litres=800.0, estimated_biomass_grams=1500.0)
    api, worker = EngineRegistry(storage), EngineRegistry(storage)
    worker.with_twin(USER, feed(10.0), default_config=config)           # version 1
    api.with_twin(USER, lambda twin: None, persist=False)                # api holds version 1

    calls = []

    def racing_feed(twin):
        calls.append(len(calls))
        if len(calls) == 1:
            # Another process saves between this registry's load and save.
            worker.with_twin(USER, feed(20.0))                           # version 2
        feed(30.0)(twin)

    api.with_twin(USER, racing_feed)
    assert len(calls) == 2, f"the stale save was rejected and the call retried once: {calls}"
    assert storage.fetch_snapshot_version(USER) == 3
    assert fed_grams(storage) == [10.0, 20.0, 30.0], \
        f"the retry ran on the reloaded state, so neither process lost its change: {fed_grams(storage)}"


def test_registry_gives_up_after_a_second_stale_save():
    storage = make_storage()
    config = PondConfig(volume_litres=800.0, estimated_biomass_grams=1500.0)
    api, worker = EngineRegistry(storage), EngineRegistry(storage)
    worker.with_twin(USER, feed(10.0), default_config=config)
    calls = []

    def always_racing(twin):
        calls.append(1)
        worker.with_twin(USER, feed(20.0))
        feed(30.0)(twin)

    with pytest.raises(StaleSnapshotError):
        api.with_twin(USER, always_racing)
    assert len(calls) == 2, "retried exactly once"
    assert 30.0 not in fed_grams(storage), "the rejected change was not written"
    # The unsaved in-memory change was dropped: the next call starts from storage.
    api.with_twin(USER, lambda twin: None, persist=False)
    assert api._twins[USER].version == storage.fetch_snapshot_version(USER)


def test_registry_creates_a_new_pond_only_once_across_processes():
    storage = MemoryStorage()
    config = PondConfig(volume_litres=800.0, estimated_biomass_grams=1500.0)
    a, b = EngineRegistry(storage), EngineRegistry(storage)
    calls = []

    def racing_create(twin):
        calls.append(1)
        if len(calls) == 1:
            b.with_twin(USER, feed(5.0), default_config=config)  # b creates it first
        feed(6.0)(twin)

    a.with_twin(USER, racing_create, default_config=config)
    assert len(calls) == 2 and fed_grams(storage) == [5.0, 6.0]


# ---------------------------------------------------------------------
# API and worker processes stay consistent
# ---------------------------------------------------------------------
def rewind(registry, days):
    def fn(twin):
        twin.evaporation._last_advance_time -= timedelta(days=days)
        twin.algae._last_advance_time -= timedelta(days=days)
        twin.chemistry._last_ingest_time -= timedelta(days=days)
    registry.with_twin(USER, fn)


def stored_loss(storage):
    return storage.load_engine_snapshot(USER)["evaporation"]["cumulative_loss_litres"]


def test_api_registry_picks_up_a_snapshot_the_worker_saved():
    storage = make_storage()
    app = create_app(make_settings(), storage=storage)
    client = app.test_client()
    api_registry = app.extensions["koi_registry"]
    worker_registry = EngineRegistry(storage)   # the koi.worker process
    config_row = storage.fetch_active_pond_configs()[0]

    poller._poll_user(worker_registry, USER, config_row)
    first = client.get(f"/forecast/evaporation/{USER}").get_json()["starting_loss_litres"]
    assert api_registry._twins[USER].version == storage.fetch_snapshot_version(USER)

    for _ in range(3):
        rewind(worker_registry, 1)
        poller._poll_user(worker_registry, USER, config_row)
    assert stored_loss(storage) > first, "the worker advanced evaporation"

    second = client.get(f"/forecast/evaporation/{USER}").get_json()["starting_loss_litres"]
    # starting_loss_litres is rounded to 0.01 L for transport.
    assert second == pytest.approx(stored_loss(storage), abs=0.005), \
        f"API served the worker's newer snapshot, not its cached twin: {second} vs {stored_loss(storage)}"
    assert api_registry._twins[USER].version == storage.fetch_snapshot_version(USER)

    # And the other way: a top-up logged through the API reaches the worker.
    r = client.post("/events/top-up", json={"user_id": USER, "volume_percent": 25.0})
    assert r.status_code == 200
    assert stored_loss(storage) == 0.0
    poller._poll_user(worker_registry, USER, config_row)
    assert stored_loss(storage) < second, \
        f"the worker polled from the API's top-up, not its own stale twin: {stored_loss(storage)}"
    assert worker_registry._twins[USER].version == storage.fetch_snapshot_version(USER)


def test_registry_does_not_reload_when_nothing_newer_was_stored():
    storage = make_storage()
    config = PondConfig(volume_litres=800.0, estimated_biomass_grams=1500.0)
    registry = EngineRegistry(storage)
    registry.with_twin(USER, feed(10.0), default_config=config)
    twin = registry._twins[USER].twin
    registry.with_twin(USER, feed(11.0))
    registry.with_twin(USER, lambda t: None, persist=False)
    assert registry._twins[USER].twin is twin, "same in-memory twin while it is current"
