"""koi.storage.MemoryStorage: the in-memory Storage the tests run on.

It has to behave like the Supabase tables it stands in for, or tests
against it prove nothing about production: same columns, same ordering,
same "latest" rules, and StorageError on failure.
"""
from datetime import datetime, timezone

import pytest

from conftest import ALGAE_ASSESSMENT, POND_FIXTURE, PROVENANCE, USER, make_storage, ticking_clock
from koi.storage import MemoryStorage, Storage, StorageError, fail_soft


def test_memory_storage_satisfies_the_storage_protocol():
    storage: Storage = MemoryStorage()
    assert storage.fetch_active_pond_configs() == []


def test_seeded_pond_config_converts_biomass_to_grams(storage):
    assert storage.fetch_pond_config(USER) == {"volume_litres": 5000.0, "estimated_biomass_grams": 8000.0}
    assert storage.fetch_pond_config(999) is None


def test_active_configs_skip_ponds_without_volume_or_biomass(storage):
    storage.add_rows("UserData", [{"userID": 7, "volume": None, "biomass": 2.0}])
    assert storage.fetch_active_pond_configs() == [
        {"volume_litres": 5000.0, "estimated_biomass_grams": 8000.0, "user_id": USER}]


def test_daily_stats_average_the_trailing_days(storage):
    temp = storage.fetch_daily_sensor_stats(USER, "temp")
    assert temp == {"avg": 29.0, "min": None, "max": None, "sample_days": 10}
    tds = storage.fetch_daily_sensor_stats(USER, "TDS", days=2)  # the two newest days
    assert tds["avg"] == pytest.approx((225.6 + 226.3) / 2)
    assert storage.fetch_daily_sensor_stats(USER, "pH") is None


def test_daily_series_is_oldest_first_and_limited_to_the_newest_days(storage):
    series = storage.fetch_daily_sensor_series(USER, "TDS", days=3)
    assert [r["record_date"] for r in series] == ["2026-08-08", "2026-08-09", "2026-08-10"]
    assert set(series[0]) == {"avg_value", "min_value", "max_value", "record_date"}


def test_evaluation_rows_keep_only_table_columns(storage):
    storage.push_algae_evaluation(USER, {**ALGAE_ASSESSMENT, **PROVENANCE})
    row = storage.fetch_latest_algae_evaluation(USER)
    assert row["id"] == 1 and row["userid"] == USER and row["evaluated_at"]
    assert row["green_ratio"] == 0.03
    assert "label_count" not in row


def test_evaluation_missing_a_column_raises_storage_error(storage):
    with pytest.raises(StorageError, match="push_algae_evaluation") as info:
        storage.push_algae_evaluation(USER, {"status": "Green"})
    assert info.value.operation == "push_algae_evaluation"


def test_latest_evaluation_orders_by_evaluated_at_not_insertion(storage):
    storage.add_rows("pond_algae_evaluations", [
        {**{k: v for k, v in ALGAE_ASSESSMENT.items() if k != "label_count"},
         "userid": USER, "evaluated_at": "2026-08-02T00:00:00+00:00", "advisory": "newer"},
        {**{k: v for k, v in ALGAE_ASSESSMENT.items() if k != "label_count"},
         "userid": USER, "evaluated_at": "2026-08-01T00:00:00+00:00", "advisory": "older"},
    ])
    assert storage.fetch_latest_algae_evaluation(USER)["advisory"] == "newer"
    assert storage.fetch_latest_algae_evaluation(999) is None


def test_snapshot_round_trips_as_json_and_upserts(storage):
    assert storage.load_engine_snapshot(USER) is None
    storage.save_engine_snapshot(USER, {"version": 2, "t": (1, 2)})
    storage.save_engine_snapshot(USER, {"version": 3})
    assert storage.load_engine_snapshot(USER) == {"version": 3}
    assert len(storage.rows("pond_chemistry_state")) == 1


def test_unserialisable_snapshot_raises_storage_error(storage):
    with pytest.raises(StorageError, match="save_engine_snapshot"):
        storage.save_engine_snapshot(USER, {"when": datetime.now(timezone.utc)})
    assert storage.load_engine_snapshot(USER) is None


def test_ratings_are_oldest_first_and_limit_keeps_the_newest(storage):
    ids = [storage.insert_algae_rating(USER, sev, False, None, None, g)["id"]
           for sev, g in [("none", 0.01), ("minor", 0.03), ("severe", 0.2)]]
    assert ids == [1, 2, 3]
    assert [r["severity"] for r in storage.fetch_algae_ratings(USER)] == ["none", "minor", "severe"]
    assert [r["severity"] for r in storage.fetch_algae_ratings(USER, limit=2)] == ["minor", "severe"]
    storage.delete_algae_rating(999, 2)  # another user's delete leaves the row
    storage.delete_algae_rating(USER, 2)
    assert [r["id"] for r in storage.fetch_algae_ratings(USER)] == [1, 3]


def test_image_history_is_newest_first_and_matches_text_user_ids(storage):
    storage.add_rows("imageTable", [
        {"user_ID": USER, "created_at": "2026-08-02T00:00:00+00:00", "green_ratio": 0.2},
        {"user_ID": USER, "created_at": "2026-08-01T00:00:00+00:00", "green_ratio": 0.1},
    ])
    rows = storage.fetch_image_history(str(USER))
    assert [r["green_ratio"] for r in rows] == [0.2, 0.1]
    assert set(rows[0]) == {"id", "created_at", "green_ratio", "current_state", "imageURL", "mask_version",
                            "baseline_reset", "quality", "thumbnail_path", "gcc", "colour", "regions"}
    # Rows written before migration 0010 have no mask provenance, and
    # before 0011 no quality result or thumbnail.
    assert rows[0]["mask_version"] is None and rows[0]["baseline_reset"] is None
    assert rows[0]["quality"] is None and rows[0]["thumbnail_path"] is None
    assert storage.fetch_image_by_id(USER, rows[1]["id"])["green_ratio"] == 0.1
    assert storage.fetch_image_by_id(999, rows[1]["id"]) is None


def test_feeding_events_are_filtered_and_newest_first(storage):
    storage.add_rows("pondInterventions", [
        {"userID": USER, "event_type": "FEEDING", "food_grams": 10.0, "event_timestamp": "2026-08-01T08:00:00Z"},
        {"userID": USER, "event_type": "WATER_TOPUP", "volume_litres": 50.0,
         "event_timestamp": "2026-08-03T08:00:00Z"},
        {"userID": USER, "event_type": "FEEDING", "food_grams": 20.0, "event_timestamp": "2026-08-02T08:00:00Z"},
    ])
    rows = storage.fetch_recent_feeding_events(USER)
    assert [r["food_grams"] for r in rows] == [20.0, 10.0]
    assert set(rows[0]) == {"food_grams", "protein_percentage", "event_timestamp"}


def test_returned_values_are_copies(storage):
    payload = storage.fetch_dashboard_payload(USER)
    payload["raw_sensor"]["pH"] = 0.0
    assert storage.fetch_dashboard_payload(USER)["raw_sensor"]["pH"] == 7.64
    assert storage.fetch_dashboard_payload(999) is None


def test_failing_operations_raise_storage_error(storage):
    storage.failing.add("fetch_pond_config")
    with pytest.raises(StorageError) as info:
        storage.fetch_pond_config(USER)
    assert info.value.operation == "fetch_pond_config"
    assert str(info.value) == "fetch_pond_config failed: simulated failure"


def test_seed_rejects_unknown_tables_and_columns():
    with pytest.raises(KeyError):
        MemoryStorage({"tables": {"NoSuchTable": [{}]}})
    with pytest.raises(KeyError):
        MemoryStorage({"tables": {"UserData": [{"userID": 1, "tank": 3}]}})


def test_same_calls_give_the_same_state():
    def run():
        s = MemoryStorage.from_json(POND_FIXTURE, clock=ticking_clock())
        s.push_algae_evaluation(USER, {**ALGAE_ASSESSMENT, **PROVENANCE})
        s.insert_algae_rating(USER, "none", False, None, None, 0.01)
        s.insert_image(USER, 0.1, ["base", 0.1], "memory://x")
        return {t: s.rows(t) for t in ("pond_algae_evaluations", "algae_severity_ratings", "imageTable")}

    assert run() == run()


def test_make_storage_loads_the_fixture_pond():
    s = make_storage()
    assert s.fetch_dashboard_payload(USER)["raw_sensor"]["LUX"] == 22755
    assert len(s.rows("daily_sensor_averages")) == 30


def test_fail_soft_returns_the_default_and_reports_the_failure(storage, caplog):
    storage.failing.add("fetch_algae_ratings")
    with caplog.at_level("WARNING"):
        assert fail_soft(lambda: storage.fetch_algae_ratings(USER), []) == []
    [record] = [r for r in caplog.records if getattr(r, "koi_event", None) == "storage_call_failed"]
    assert record.koi_fields["operation"] == "fetch_algae_ratings"
    assert "fetch_algae_ratings failed: simulated failure" in record.koi_fields["error"]
    storage.failing.clear()
    storage.insert_algae_rating(USER, "none", False, None, None, 0.01)
    assert len(fail_soft(lambda: storage.fetch_algae_ratings(USER), [])) == 1


def test_worker_status_and_lease_reads_round_trip():
    storage = MemoryStorage(clock=ticking_clock())
    assert storage.fetch_worker_status("poller") is None and storage.fetch_lease("poller") is None
    status = {"holder": "w1", "cycle_started_at": "2026-08-20T00:00:00+00:00",
              "cycle_finished_at": "2026-08-20T00:00:02+00:00", "cycle_duration_sec": 2.0,
              "last_success_at": "2026-08-20T00:00:02+00:00",
              "ponds": {"455": {"result": "ok", "failures_total": 0, "sensor_recorded_at": None, "reason": None}}}
    storage.record_worker_status("poller", status)
    storage.record_worker_status("poller", {**status, "holder": "w2"})
    assert storage.rows("worker_status") == [{"name": "poller", **status, "holder": "w2"}]
    assert storage.fetch_worker_status("poller")["holder"] == "w2"
    storage.take_lease("poller", "w2", 60)
    assert storage.fetch_lease("poller")["holder"] == "w2"
    storage.save_engine_snapshot(USER, {"n": 1})
    assert list(storage.fetch_snapshot_times()) == [str(USER)]
    storage.failing.add("fetch_worker_status")
    with pytest.raises(StorageError):
        storage.fetch_worker_status("poller")
