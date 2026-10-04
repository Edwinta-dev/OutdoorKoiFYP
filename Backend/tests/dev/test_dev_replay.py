"""Issue #14: the fast-forward that runs the worker over the demo seed's
history before koi.dev serves (koi/dev/replay.py).

Run from Backend/: python -m pytest tests/dev
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import make_settings
from koi.dev import seed as seed_data
from koi.dev.__main__ import memory_storage
from koi.dev.replay import ReplayStorage, fast_forward
from koi.models.engine import SensorChannel
from koi.models.pond_twin import PondTwin
from koi.storage.base import parse_timestamp

NOW = datetime(2026, 10, 4, 7, 42, 13, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def tables():
    return seed_data.shift(seed_data.load(), NOW)["tables"]


@pytest.fixture(scope="module")
def replayed(tables):
    storage = memory_storage(tables, [1, 2], NOW)
    results = {r.pond_id: r for r in fast_forward(storage, tables, make_settings(), [1, 2])}
    return storage, results


def test_every_reported_time_of_the_demo_pond_is_polled(replayed):
    _, results = replayed
    demo = results[1]
    assert (demo.polls_ok, demo.polls_skipped) == (14 * 24, 0)
    assert demo.events_applied == 16  # 14 feeds, a water change and a top-up
    assert parse_timestamp(demo.last) == NOW
    assert NOW - parse_timestamp(demo.first) == timedelta(days=14) - timedelta(hours=1)


def test_assessments_exist_after_the_fast_forward(replayed):
    storage, _ = replayed
    chemistry = storage.fetch_latest_evaluation(1)
    evaporation = storage.fetch_latest_evaporation_evaluation(1)
    algae = storage.fetch_latest_algae_evaluation(1)
    assert chemistry["status"] in ("Green", "Amber", "Red")
    # The top-up four days before now reset the evaporation loss.
    assert 0 < evaporation["loss_litres"] < 100
    # Every one of the 84 demo frames was assimilated, none from the future.
    assert algae["sample_count"] == 84 and algae["rate_source"] == "fitted_from_camera"


def test_only_the_last_state_is_written(replayed):
    storage, _ = replayed
    for table in ("pond_chemistry_evaluations", "pond_evaporation_evaluations", "pond_algae_evaluations"):
        assert sorted(r["userid"] for r in storage.rows(table)) == [1, 2], table
    assert storage.fetch_snapshot_version(1) == 1


def test_every_sensor_row_is_ingested_once_with_the_snapshot(replayed, tables):
    storage, _ = replayed
    seeded = sorted(r["id"] for r in tables["SensorData"] if r.get("userID") == 1)
    ledger = [r for r in storage.rows("sensor_ingest_ledger") if r["pond_id"] == 1]
    assert sorted(r["sensor_row_id"] for r in ledger) == seeded
    assert {r["time_basis"] for r in ledger} == {"ingestion"}
    assert {r["snapshot_version"] for r in ledger} == {1}
    [cursor] = [r for r in storage.rows("sensor_ingest_cursor") if r["pond_id"] == 1]
    assert parse_timestamp(cursor["watermark"]) == NOW
    twin = PondTwin.from_snapshot(storage.load_engine_snapshot(1))
    assert twin.last_input_at == NOW
    assert {c["reading_at"] for c in twin.sensor_channels.values()} == {NOW.isoformat()}


def test_snapshot_holds_the_logged_interventions(replayed):
    storage, _ = replayed
    twin = PondTwin.from_snapshot(storage.load_engine_snapshot(1))
    kinds = [e.kind.value for e in twin.chemistry._events]
    assert "water_change" in kinds and "top_up" in kinds and "feeding" in kinds


def test_pond_without_tds_is_advanced_with_tds_missing(replayed, tables):
    storage, results = replayed
    legacy = results[2]
    assert legacy.polls_ok == 1 and legacy.polls_skipped == 0
    assert storage.fetch_latest_evaluation(2) is not None
    # The snapshot from before snapshot_version loaded and was saved once.
    assert storage.fetch_snapshot_version(2) == 2
    old = next(r for r in tables["pond_chemistry_state"] if r["user_id"] == 2)["snapshot"]
    twin = PondTwin.from_snapshot(storage.load_engine_snapshot(2))
    assert [e.to_dict() for e in twin.chemistry._events] == old["chemistry"]["events"]
    # TDS was not sent: no channel reading, and the gate keeps the last trusted value.
    assert set(twin.sensor_channels) == {"ph", "temp", "lux"}
    assert twin.chemistry._gate._last_trusted[SensorChannel.TDS] == old["chemistry"]["gate"]["last_trusted"]["tds"]
    # Two pH rows at one time: the higher id is used, the other recorded as superseded.
    assert twin.sensor_channels["ph"] == {**twin.sensor_channels["ph"], "value": 6.5, "row_id": 40002}
    ledger = {r["sensor_row_id"]: r["disposition"] for r in storage.rows("sensor_ingest_ledger")
              if r["pond_id"] == 2}
    assert ledger == {40001: "superseded", 40002: "applied", 40003: "applied", 40004: "applied"}


def test_replay_storage_reads_history_as_of_its_time(tables):
    storage = memory_storage(tables, [1, 2], NOW)
    replay = ReplayStorage(storage, seed_data.SeedHistory(tables))
    replay.at = NOW - timedelta(days=10)
    assert all(parse_timestamp(r["created_at"]) <= replay.at for r in replay.fetch_image_history(1))
    assert len(replay.fetch_image_history(1)) < len(storage.fetch_image_history(1))
    assert replay.fetch_dashboard_payload(1)["raw_sensor"] != storage.fetch_dashboard_payload(1)["raw_sensor"]
    # Anything else goes to the real storage.
    assert replay.fetch_pond_profiles(1) == storage.fetch_pond_profiles(1)


def test_replay_storage_writes_nothing_until_flush(tables):
    storage = memory_storage(tables, [1, 2], NOW)
    replay = ReplayStorage(storage, seed_data.SeedHistory(tables))
    assert replay.save_engine_snapshot(1, {"version": 2}, base_version=0) == 1
    assert replay.save_engine_snapshot(1, {"version": 2, "n": 2}, base_version=1) == 2
    replay.push_evaluation(1, {"status": "Green"})
    assert storage.fetch_snapshot_version(1) == 0 and storage.rows("pond_chemistry_evaluations") == []
    assert replay.load_engine_snapshot(1) == {"version": 2, "n": 2}


def test_replay_needs_a_time():
    replay = ReplayStorage(memory_storage({}, [], NOW), seed_data.SeedHistory({}))
    with pytest.raises(RuntimeError):
        replay.fetch_dashboard_payload(1)
