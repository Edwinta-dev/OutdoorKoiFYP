"""Issue #18: the poller ingests every SensorData row since its cursor,
once, in sample-time order (koi/worker/poller.py, migration 0014), here
over MemoryStorage. tests/sql/test_sql_sensor_ingest.py runs the same
cases against the database functions.

Run from Backend/: python -m pytest tests/worker/test_sensor_ingest.py
"""
from datetime import datetime, timedelta, timezone

import pytest

from koi.models.sensor_inputs import IngestConfig
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage, StorageError
from koi.worker import poller

T0 = datetime(2026, 10, 4, tzinfo=timezone.utc)
POND, OTHER = 7001, 7002
FULL = {"temp": 29.0, "TDS": 220.0, "pH": 7.6, "LUX": 20000.0}


def minutes(n):
    return T0 + timedelta(minutes=n)


def make_storage(*ponds):
    storage = MemoryStorage()
    storage.add_rows("UserData", [{"userID": p, "volume": 5000, "biomass": 8.0} for p in ponds or (POND,)])
    return storage


def upload(storage, at, reading=None, pond=POND, first_id=None):
    """One node upload: one row per sensor_type at one insert time."""
    reading = FULL if reading is None else reading
    rows = [{"userID": pond, "sensor_type": t, "data1": v, "created_at": at.isoformat()} for t, v in reading.items()]
    if first_id is not None:
        for i, r in enumerate(rows):
            r["id"] = first_id + i
    return storage.add_rows("SensorData", rows)


def poll(registry, at, pond=POND):
    config = next(c for c in registry.storage.fetch_active_pond_configs() if c["user_id"] == pond)
    return poller._poll_user(registry, pond, config, now=at)


def ledger(storage, pond=POND):
    return sorted((r["sensor_row_id"], r["disposition"]) for r in storage.rows("sensor_ingest_ledger")
                  if r["pond_id"] == pond)


def sensor_state(storage, pond=POND):
    """What the ingested readings decide: the chemistry engine and the
    channel readings. Evaporation and algae also depend on poll times."""
    snap = storage.load_engine_snapshot(pond)
    return snap["chemistry"], snap["sensor_inputs"]


def test_equal_time_channels_are_one_input():
    storage = make_storage()
    rows = upload(storage, minutes(0))
    result = poll(EngineRegistry(storage), minutes(1))
    assert result == minutes(0).isoformat()
    assert (result.sensor["inputs"], result.sensor["rows"]) == (1, 4)
    assert ledger(storage) == [(r["id"], "applied") for r in rows]
    channels = storage.load_engine_snapshot(POND)["sensor_inputs"]["channels"]
    assert {c: s["value"] for c, s in channels.items()} == {"temp": 29.0, "tds": 220.0, "ph": 7.6, "lux": 20000.0}


def test_every_reading_between_polls_is_applied_in_order():
    storage = make_storage()
    for n in range(4):
        upload(storage, minutes(5 * n), {**FULL, "pH": 7.0 + n / 10})
    result = poll(EngineRegistry(storage), minutes(16))
    assert result.sensor["inputs"] == 4
    snap = storage.load_engine_snapshot(POND)
    assert snap["chemistry"]["last_ingest_time"] == minutes(15).isoformat()
    assert snap["chemistry"]["gate"]["last_raw_seen"]["ph"] == pytest.approx(7.3)


def test_no_new_rows_replays_nothing():
    storage = make_storage()
    upload(storage, minutes(0))
    registry = EngineRegistry(storage)
    poll(registry, minutes(1))
    before = sensor_state(storage)
    for n in (2, 3, 4, 5):
        result = poll(registry, minutes(n))
        assert result.sensor["inputs"] == 0 and result.sensor["rows"] == 0
    assert sensor_state(storage) == before
    # The gate's stale-run count is unchanged: polls are not readings.
    assert before[0]["gate"]["run_length"] == {"ph": 1, "temp": 1, "tds": 1, "lux": 1}
    assert len(storage.rows("sensor_ingest_ledger")) == 4


def test_freshness_comes_from_reading_time_not_poll_count(caplog):
    storage = make_storage()
    upload(storage, minutes(0), {**FULL, "LUX": 3.0, "temp": 31.0})
    registry = EngineRegistry(storage)
    with caplog.at_level("INFO"):
        assert poll(registry, minutes(29)).sensor["channels"]["temp"]["fresh"] is True
        stale = poll(registry, minutes(31)).sensor["channels"]["temp"]
    assert stale == {"reading_at": minutes(0).isoformat(), "time_basis": "ingestion", "age_minutes": 31.0,
                     "fresh": False}
    # The night-time oxygen check uses the readings only while they are fresh.
    levels = [r.koi_fields["hypoxia"] for r in caplog.records if getattr(r, "koi_event", None) == "pond_polled"]
    assert levels == ["watch", "unknown"]


def test_partial_cycle_passes_the_missing_channel_and_keeps_the_others():
    storage = make_storage()
    upload(storage, minutes(0))
    partial = upload(storage, minutes(15), {"TDS": 230.0, "pH": 7.7, "LUX": 21000.0})
    result = poll(EngineRegistry(storage), minutes(16))
    assert result.sensor["inputs"] == 2
    channels = storage.load_engine_snapshot(POND)["sensor_inputs"]["channels"]
    assert channels["temp"]["reading_at"] == minutes(0).isoformat()
    assert channels["tds"] == {"value": 230.0, "reading_at": minutes(15).isoformat(), "time_basis": "ingestion",
                               "row_id": partial[0]["id"]}
    gate = storage.load_engine_snapshot(POND)["chemistry"]["gate"]
    assert gate["last_raw_seen"]["temp"] == 29.0 and gate["last_raw_seen"]["tds"] == 230.0
    assert any("temp missing from this reading" in w for w in storage.fetch_latest_evaluation(POND)["sensor_warnings"])


def test_a_lower_id_committed_after_a_higher_one_is_found():
    """Node uploads A (ids 10-13, created 00:14) and B (ids 20-23, created
    00:15) overlap; B commits first and is polled; A commits after. An id
    cursor at 23 would never return 10-13; the overlap scan does."""
    storage = make_storage()
    registry = EngineRegistry(storage)
    upload(storage, minutes(15), first_id=20)
    first = poll(registry, minutes(16))
    assert first.sensor["inputs"] == 1
    upload(storage, minutes(14), {**FULL, "pH": 7.5}, first_id=10)
    second = poll(registry, minutes(17))
    assert (second.sensor["inputs"], second.sensor["late_inputs"]) == (1, 1)
    assert ledger(storage) == [(i, "applied") for i in (10, 11, 12, 13, 20, 21, 22, 23)]
    # The late reading did not move the clock back or replace the newer reading.
    snap = storage.load_engine_snapshot(POND)
    assert snap["chemistry"]["last_ingest_time"] == minutes(15).isoformat()
    assert snap["sensor_inputs"]["channels"]["ph"]["value"] == 7.6
    assert poll(registry, minutes(18)).sensor["inputs"] == 0, "applied once"
    [cursor] = storage.rows("sensor_ingest_cursor")
    assert cursor["watermark"] == minutes(15).isoformat(), "the watermark never moves back"


def test_the_overlap_bounds_how_late_a_commit_can_be():
    storage = make_storage()
    registry = EngineRegistry(storage, sensor_ingest=IngestConfig(overlap_minutes=60))
    upload(storage, minutes(120))
    poll(registry, minutes(121))
    upload(storage, minutes(59), first_id=500)   # 61 minutes before the watermark
    upload(storage, minutes(61), first_id=600)   # 59 minutes before it
    assert poll(registry, minutes(122)).sensor["inputs"] == 1
    assert {i for i, _ in ledger(storage)} >= {600, 601, 602, 603}
    assert not {i for i, _ in ledger(storage)} & {500, 501, 502, 503}


def _run(crash):
    """Uploads at 0, 15 and 30 minutes. The first is polled at 1; the
    poll at 31 crashes (crash: "save" or "cycle") or is skipped (None);
    a new registry (a restarted worker) polls at 32."""
    storage = make_storage()
    registry = EngineRegistry(storage)
    upload(storage, minutes(0))
    poll(registry, minutes(1))
    upload(storage, minutes(15), {**FULL, "pH": 7.7, "temp": 29.5})
    upload(storage, minutes(30), {"TDS": 225.0, "pH": 7.8, "LUX": 100.0})
    if crash == "save":
        storage.failing.add("save_engine_snapshot")
        with pytest.raises(StorageError):
            poll(registry, minutes(31))
        storage.failing.discard("save_engine_snapshot")
        assert registry._twins == {}, "the unsaved twin was dropped"
        assert len(storage.rows("sensor_ingest_ledger")) == 4, "nothing recorded by the failed save"
        # The same worker, before any restart, applies them once.
        poll(registry, minutes(31))
    elif crash == "cycle":
        original = poller.assess_hypoxia

        def boom(**_):
            raise RuntimeError("worker died after discovery")

        poller.assess_hypoxia = boom
        try:
            with pytest.raises(RuntimeError):
                poll(registry, minutes(31))
        finally:
            poller.assess_hypoxia = original
    restarted = EngineRegistry(storage)
    result = poll(restarted, minutes(32))
    return storage, result


def _in_order():
    """The same uploads, polled after each one."""
    storage = make_storage()
    registry = EngineRegistry(storage)
    upload(storage, minutes(0))
    poll(registry, minutes(1))
    upload(storage, minutes(15), {**FULL, "pH": 7.7, "temp": 29.5})
    poll(registry, minutes(16))
    upload(storage, minutes(30), {"TDS": 225.0, "pH": 7.8, "LUX": 100.0})
    poll(registry, minutes(31))
    return storage


@pytest.mark.parametrize("crash", [None, "save", "cycle"])
def test_crash_and_restart_neither_lose_nor_repeat_a_reading(crash):
    storage, _ = _run(crash)
    reference = _in_order()
    assert ledger(storage) == ledger(reference) and len(ledger(storage)) == 11
    assert sensor_state(storage) == sensor_state(reference)
    assert storage.rows("sensor_ingest_cursor")[0]["watermark"] == minutes(30).isoformat()


def test_two_ponds_keep_their_own_readings_and_cursors():
    storage = make_storage(POND, OTHER)
    registry = EngineRegistry(storage)
    upload(storage, minutes(0), pond=POND)
    upload(storage, minutes(0), {**FULL, "pH": 8.1}, pond=OTHER)
    upload(storage, minutes(15), {**FULL, "pH": 8.2}, pond=OTHER)
    assert poll(registry, minutes(16), POND).sensor["inputs"] == 1
    assert poll(registry, minutes(16), OTHER).sensor["inputs"] == 2
    assert [i for i, _ in ledger(storage, POND)] == [1, 2, 3, 4]
    assert [i for i, _ in ledger(storage, OTHER)] == [5, 6, 7, 8, 9, 10, 11, 12]
    assert storage.load_engine_snapshot(POND)["sensor_inputs"]["channels"]["ph"]["value"] == 7.6
    assert storage.load_engine_snapshot(OTHER)["sensor_inputs"]["channels"]["ph"]["value"] == 8.2
    assert sorted((c["pond_id"], c["watermark"]) for c in storage.rows("sensor_ingest_cursor")) == [
        (POND, minutes(0).isoformat()), (OTHER, minutes(15).isoformat())]


def test_a_batch_limit_takes_whole_uploads_and_the_rest_next_cycle():
    storage = make_storage()
    registry = EngineRegistry(storage, sensor_ingest=IngestConfig(batch_rows=6))
    for n in range(3):
        upload(storage, minutes(n))
    assert [poll(registry, minutes(10 + n)).sensor["rows"] for n in range(4)] == [4, 4, 4, 0]


def test_a_snapshot_from_before_the_ledger_starts_at_its_last_ingest():
    storage = make_storage()
    registry = EngineRegistry(storage)
    upload(storage, minutes(0))

    def old_poller_state(twin):
        twin.chemistry._last_ingest_time = minutes(10)
    registry.with_twin(POND, old_poller_state, profiles=registry.profile_history(POND))
    # Nothing newer yet: the pond is advanced on the weather, not skipped.
    quiet = poll(registry, minutes(11))
    assert quiet.sensor["inputs"] == 0 and quiet == ""
    upload(storage, minutes(20), {**FULL, "pH": 7.9})
    assert poll(registry, minutes(21)).sensor["inputs"] == 1
    assert [i for i, _ in ledger(storage)] == [5, 6, 7, 8]


def test_a_pond_with_no_rows_is_skipped_and_saves_nothing():
    storage = make_storage()
    with pytest.raises(poller.PondSkipped, match="no sensor reading for this pond yet"):
        poll(EngineRegistry(storage), minutes(1))
    assert storage.load_engine_snapshot(POND) is None and storage.rows("sensor_ingest_cursor") == []


def test_a_ledger_conflict_fails_the_whole_save():
    storage = make_storage()
    upload(storage, minutes(0))
    poll(EngineRegistry(storage), minutes(1))
    version = storage.fetch_snapshot_version(POND)
    entry = {"sensor_row_id": 1, "sensor_type": "temp", "effective_sample_time": minutes(0).isoformat(),
             "time_basis": "ingestion", "disposition": "applied"}
    with pytest.raises(StorageError, match="already in the ingest ledger"):
        storage.save_engine_snapshot(POND, {"version": 3}, base_version=version,
                                     ingest={"rows": [entry], "watermark": minutes(5).isoformat()})
    assert storage.fetch_snapshot_version(POND) == version
    assert storage.rows("sensor_ingest_cursor")[0]["watermark"] == minutes(0).isoformat()
