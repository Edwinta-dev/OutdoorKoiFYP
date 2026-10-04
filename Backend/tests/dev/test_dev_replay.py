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
    assert len(storage.rows("pond_chemistry_evaluations")) == 1
    assert len(storage.rows("pond_evaporation_evaluations")) == 1
    assert len(storage.rows("pond_algae_evaluations")) == 1
    assert storage.fetch_snapshot_version(1) == 1


def test_snapshot_holds_the_logged_interventions(replayed):
    storage, _ = replayed
    twin = PondTwin.from_snapshot(storage.load_engine_snapshot(1))
    kinds = [e.kind.value for e in twin.chemistry._events]
    assert "water_change" in kinds and "top_up" in kinds and "feeding" in kinds


def test_pond_without_tds_is_skipped_and_its_old_snapshot_kept(replayed, tables):
    storage, results = replayed
    legacy = results[2]
    assert legacy.polls_ok == 0 and legacy.polls_skipped == 1
    assert legacy.skip_reasons == ["unusable sensor reading (KeyError: 'TDS')"]
    assert storage.fetch_latest_evaluation(2) is None
    # The snapshot from before snapshot_version is untouched and still loads.
    assert storage.fetch_snapshot_version(2) == 1
    stored = storage.load_engine_snapshot(2)
    assert stored == next(r for r in tables["pond_chemistry_state"] if r["user_id"] == 2)["snapshot"]
    PondTwin.from_snapshot(stored)


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
