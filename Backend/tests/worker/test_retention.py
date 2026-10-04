"""Evaluation retention (issue #22, migration 0017) over MemoryStorage:
the worker's daily job folds the evaluation rows of local days older than
the retention period into evaluation_daily and deletes them.

The SQL function is tested against the same expectations in
tests/sql/test_sql_evaluation_retention.py.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from conftest import make_settings
from koi.storage import StorageError
from koi.storage.evaluation_daily import HEADLINE_METRICS, merge_summary, summarize_group
from koi.storage.memory import MemoryStorage
from koi.worker import retention

NOW = datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc)  # 12:00 on 4 October in Singapore
POND, OTHER = 455, 456


def _chem(at: str, pond: int = POND, tan: float | None = 0.1, ph: float | None = 0.2, status: str = "Green",
          version: str | None = "0.1.0+gaaa") -> dict:
    return {"userid": pond, "evaluated_at": at, "status": status, "category": "Normal", "tan_ppm": tan,
            "no2_ppm": 0.0, "no3_ppm": 5.0, "ph_reactivity": ph, "advisory": "ok", "add_hardener_now": False,
            "model_version": version}


def _evap(at: str, pond: int = POND, loss: float | None = 1.0, days: int | None = 3) -> dict:
    return {"userid": pond, "evaluated_at": at, "status": "Green", "category": "Normal", "loss_litres": loss,
            "loss_pct": None, "water_temp_c": None, "days_to_topup": days, "topup_now": False}


def _algae(at: str, pond: int = POND, green: float | None = 0.03) -> dict:
    return {"userid": pond, "evaluated_at": at, "status": "Green", "category": "clear", "green_ratio": green,
            "scrub_now": False}


def _store(**tables: list[dict]) -> MemoryStorage:
    store = MemoryStorage(clock=lambda: NOW)
    for table, rows in tables.items():
        store.add_rows(table, rows)
    return store


def _recent(store: MemoryStorage, pond: int = POND) -> None:
    """Today's rows, so the old days are not the pond's newest."""
    store.add_rows("pond_chemistry_evaluations", [_chem("2026-10-04T01:00:00+00:00", pond=pond)])
    store.add_rows("pond_evaporation_evaluations", [_evap("2026-10-04T01:00:00+00:00", pond=pond)])
    store.add_rows("pond_algae_evaluations", [_algae("2026-10-04T01:00:00+00:00", pond=pond)])


def test_retention_cutoff_is_local_date_minus_the_retention_days():
    assert retention.retention_cutoff(NOW, 30) == date(2026, 9, 4)
    # 23:30 UTC on 3 October is already 4 October in Singapore.
    assert retention.retention_cutoff(datetime(2026, 10, 3, 23, 30, tzinfo=timezone.utc), 30) == date(2026, 9, 4)
    with pytest.raises(ValueError):
        retention.retention_cutoff(NOW, 0)


def test_retention_summarises_old_days_and_deletes_their_rows():
    store = _store(pond_chemistry_evaluations=[
        _chem("2026-08-01T01:00:00+00:00", tan=0.30, status="Amber"),
        _chem("2026-08-01T05:00:00+00:00", tan=0.10),
        _chem("2026-08-01T09:00:00+00:00", tan=0.20),
        _chem("2026-09-20T01:00:00+00:00", tan=0.50),  # inside the 30 days: kept
    ])
    _recent(store)
    report = retention.run_retention(store, 30, now=NOW)

    assert report == {"before": "2026-09-04", "days": ["2026-08-01"], "rows": 3, "summaries": 1, "calls": 2,
                      "complete": True}
    left = store.rows("pond_chemistry_evaluations")
    assert [r["evaluated_at"][:10] for r in left] == ["2026-09-20", "2026-10-04"]

    [day] = store.fetch_evaluation_daily(POND)
    assert (day["pond_id"], day["domain"], day["local_date"], day["time_zone"]) == (
        POND, "chemistry", "2026-08-01", "Asia/Singapore")
    assert day["row_count"] == 3
    assert day["first_evaluated_at"] == "2026-08-01T01:00:00+00:00"
    assert day["last_evaluated_at"] == "2026-08-01T09:00:00+00:00"
    assert day["status_counts"] == {"Amber": 1, "Green": 2}
    assert day["metrics"]["tan_ppm"] == {"n": 3, "first": 0.30, "first_at": "2026-08-01T01:00:00+00:00",
                                         "last": 0.20, "last_at": "2026-08-01T09:00:00+00:00",
                                         "min": 0.10, "max": 0.30}
    assert set(day["metrics"]) == set(HEADLINE_METRICS["chemistry"])
    assert day["model_versions"] == [{"model_version": "0.1.0+gaaa", "rows": 3,
                                      "first_evaluated_at": "2026-08-01T01:00:00+00:00",
                                      "last_evaluated_at": "2026-08-01T09:00:00+00:00"}]


def test_retention_is_idempotent():
    store = _store(pond_chemistry_evaluations=[_chem("2026-08-01T01:00:00+00:00"),
                                               _chem("2026-08-02T01:00:00+00:00")])
    _recent(store)
    retention.run_retention(store, 30, now=NOW)
    summaries, rows = store.rows("evaluation_daily"), store.rows("pond_chemistry_evaluations")

    again = retention.run_retention(store, 30, now=NOW)
    assert (again["rows"], again["summaries"], again["days"], again["complete"]) == (0, 0, [], True)
    assert store.rows("evaluation_daily") == summaries
    assert store.rows("pond_chemistry_evaluations") == rows


def test_retention_uses_the_pond_local_day():
    # 16:30 UTC on 1 August is 00:30 on 2 August in Singapore.
    store = _store(pond_chemistry_evaluations=[_chem("2026-08-01T15:59:00+00:00"),
                                               _chem("2026-08-01T16:30:00+00:00")])
    _recent(store)
    retention.run_retention(store, 30, now=NOW)
    assert [(d["local_date"], d["row_count"]) for d in store.fetch_evaluation_daily(POND)] == [
        ("2026-08-01", 1), ("2026-08-02", 1)]


def test_retention_keeps_missing_values_and_absent_domains_missing():
    store = _store(pond_chemistry_evaluations=[_chem("2026-08-01T01:00:00+00:00", ph=None),
                                               _chem("2026-08-01T02:00:00+00:00", ph=None, tan=0.0)],
                   pond_evaporation_evaluations=[_evap("2026-08-01T01:00:00+00:00", loss=None, days=None),
                                                 _evap("2026-08-01T02:00:00+00:00", loss=2.5, days=None)])
    _recent(store)
    retention.run_retention(store, 30, now=NOW)
    days = {d["domain"]: d for d in store.fetch_evaluation_daily(POND)}

    # No algae rows that day: no algae summary, not a zero one.
    assert set(days) == {"chemistry", "evaporation"}
    missing = {"n": 0, "first": None, "first_at": None, "last": None, "last_at": None, "min": None, "max": None}
    assert days["chemistry"]["metrics"]["ph_reactivity"] == missing
    assert days["evaporation"]["metrics"]["days_to_topup"] == missing
    assert days["evaporation"]["metrics"]["water_temp_c"] == missing
    # A real zero is a value; a null beside it is left out, not counted as zero.
    assert days["chemistry"]["metrics"]["tan_ppm"]["min"] == 0.0
    assert days["evaporation"]["metrics"]["loss_litres"] == {
        "n": 1, "first": 2.5, "first_at": "2026-08-01T02:00:00+00:00", "last": 2.5,
        "last_at": "2026-08-01T02:00:00+00:00", "min": 2.5, "max": 2.5}


def test_retention_records_model_version_changes_within_a_day():
    store = _store(pond_chemistry_evaluations=[
        _chem("2026-08-01T01:00:00+00:00", version=None),  # written before 0016
        _chem("2026-08-01T02:00:00+00:00", version="0.1.0+gaaa"),
        _chem("2026-08-01T03:00:00+00:00", version="0.1.0+gbbb"),
        _chem("2026-08-01T04:00:00+00:00", version="0.1.0+gbbb"),
    ])
    _recent(store)
    retention.run_retention(store, 30, now=NOW)
    [day] = store.fetch_evaluation_daily(POND, domain="chemistry")
    assert [(v["model_version"], v["rows"], v["first_evaluated_at"][11:16], v["last_evaluated_at"][11:16])
            for v in day["model_versions"]] == [
        (None, 1, "01:00", "01:00"), ("0.1.0+gaaa", 1, "02:00", "02:00"), ("0.1.0+gbbb", 2, "03:00", "04:00")]


def test_retention_keeps_each_ponds_newest_day_as_detailed_rows():
    """A pond that stopped polling long ago keeps its last day, so the API
    still has a latest evaluation; its earlier days are summarised."""
    store = _store(pond_chemistry_evaluations=[_chem("2026-06-01T01:00:00+00:00", pond=OTHER, tan=0.1),
                                               _chem("2026-06-02T01:00:00+00:00", pond=OTHER, tan=0.2)],
                   pond_algae_evaluations=[_algae("2026-06-02T01:00:00+00:00", pond=OTHER)])
    retention.run_retention(store, 30, now=NOW)

    assert [d["local_date"] for d in store.fetch_evaluation_daily(OTHER)] == ["2026-06-01"]
    assert store.fetch_latest_evaluation(OTHER)["tan_ppm"] == 0.2
    assert store.fetch_latest_algae_evaluation(OTHER) is not None


def test_retention_merges_a_late_row_into_its_summary():
    store = _store(pond_chemistry_evaluations=[_chem("2026-08-01T02:00:00+00:00", tan=0.2, version="v1")])
    _recent(store)
    retention.run_retention(store, 30, now=NOW)
    # A row for 1 August written after the day was summarised.
    store.add_rows("pond_chemistry_evaluations", [
        _chem("2026-08-01T01:00:00+00:00", tan=0.4, status="Red", version="v0"),
        _chem("2026-08-01T05:00:00+00:00", tan=None, ph=0.9, version="v1"),
    ])
    retention.run_retention(store, 30, now=NOW)

    [day] = store.fetch_evaluation_daily(POND)
    assert day["row_count"] == 3
    assert day["status_counts"] == {"Green": 2, "Red": 1}
    assert (day["first_evaluated_at"], day["last_evaluated_at"]) == (
        "2026-08-01T01:00:00+00:00", "2026-08-01T05:00:00+00:00")
    tan = day["metrics"]["tan_ppm"]
    assert (tan["n"], tan["first"], tan["last"], tan["min"], tan["max"]) == (2, 0.4, 0.2, 0.2, 0.4)
    assert [(v["model_version"], v["rows"]) for v in day["model_versions"]] == [("v0", 1), ("v1", 2)]
    assert [r["evaluated_at"][:10] for r in store.rows("pond_chemistry_evaluations")] == ["2026-10-04"]


def test_merge_matches_summarising_all_rows_at_once():
    rows = [_chem(f"2026-08-01T0{h}:00:00+00:00", tan=t, ph=p, status=s, version=v) | {"id": h}
            for h, t, p, s, v in [(1, 0.3, None, "Green", None), (2, None, 0.5, "Amber", "a"),
                                  (3, 0.1, None, "Green", "a"), (4, 0.2, 0.4, "Red", "b")]]
    whole = summarize_group("chemistry", rows)
    for split in range(1, 4):
        for first, second in ((rows[:split], rows[split:]), (rows[split:], rows[:split])):
            assert merge_summary(summarize_group("chemistry", first), summarize_group("chemistry", second)) == whole


def test_retention_takes_days_in_batches_oldest_first():
    store = _store(pond_chemistry_evaluations=[_chem(f"2026-08-0{d}T01:00:00+00:00") for d in range(1, 6)])
    _recent(store)
    calls = []
    summarise = store.summarize_evaluation_days

    def spy(before, time_zone, max_days):
        result = summarise(before, time_zone, max_days)
        calls.append(result["days"])
        return result

    store.summarize_evaluation_days = spy  # type: ignore[method-assign]
    report = retention.run_retention(store, 30, now=NOW, max_days_per_call=2)
    assert calls == [["2026-08-01", "2026-08-02"], ["2026-08-03", "2026-08-04"], ["2026-08-05"], []]
    assert (report["rows"], report["summaries"], report["calls"], report["complete"]) == (5, 5, 4, True)

    capped = _store(pond_chemistry_evaluations=[_chem(f"2026-08-0{d}T01:00:00+00:00") for d in range(1, 6)])
    _recent(capped)
    partial = retention.run_retention(capped, 30, now=NOW, max_days_per_call=2, max_calls=1)
    assert (partial["days"], partial["complete"]) == (["2026-08-01", "2026-08-02"], False)


def test_retention_configurable_period():
    store = _store(pond_chemistry_evaluations=[_chem("2026-09-20T01:00:00+00:00")])
    _recent(store)
    assert retention.run_retention(store, 30, now=NOW)["rows"] == 0
    assert retention.run_retention(store, 7, now=NOW)["days"] == ["2026-09-20"]
    assert make_settings().evaluation_retention_days == 30
    assert make_settings(KOI_EVALUATION_RETENTION_DAYS="60").evaluation_retention_days == 60
    with pytest.raises(ValueError):
        make_settings(evaluation_retention_days=0)


def test_retention_never_summarises_today_or_later():
    store = _store(pond_chemistry_evaluations=[_chem("2026-10-03T01:00:00+00:00")])
    _recent(store)
    with pytest.raises(StorageError):
        store.summarize_evaluation_days(date(2026, 10, 5), "Asia/Singapore", 7)
    assert store.summarize_evaluation_days(date(2026, 10, 4), "Asia/Singapore", 7)["days"] == ["2026-10-03"]


def test_retention_failure_leaves_rows_and_summaries_unchanged():
    store = _store(pond_chemistry_evaluations=[_chem("2026-08-01T01:00:00+00:00")])
    _recent(store)
    before = store.rows("pond_chemistry_evaluations")
    store.failing.add("summarize_evaluation_days")
    job = retention.RetentionJob(store, 30, holder="worker-a")
    assert job.run(now=NOW) is None
    assert store.rows("pond_chemistry_evaluations") == before
    assert store.rows("evaluation_daily") == []
    assert store.rows("worker_lease") == []  # released, so the next run is not blocked

    store.failing.clear()
    report = job.run(now=NOW)
    assert report is not None and report["rows"] == 1
    assert len(store.rows("evaluation_daily")) == 1


def test_retention_unserialisable_summary_writes_nothing():
    store = _store(pond_chemistry_evaluations=[_chem("2026-08-01T01:00:00+00:00")])
    _recent(store)
    store._tables["pond_chemistry_evaluations"][0]["ph_reactivity"] = {1, 2}  # not a jsonb value
    before = len(store._tables["pond_chemistry_evaluations"])
    with pytest.raises(StorageError):
        store.summarize_evaluation_days(date(2026, 9, 4), "Asia/Singapore", 7)
    assert len(store._tables["pond_chemistry_evaluations"]) == before
    assert store.rows("evaluation_daily") == []


def test_retention_job_runs_under_its_own_lease():
    store = _store(pond_chemistry_evaluations=[_chem("2026-08-01T01:00:00+00:00")])
    _recent(store)
    assert store.take_lease(retention.LEASE_NAME, "worker-b", 600)
    assert retention.RetentionJob(store, 30, holder="worker-a").run(now=NOW) is None
    assert store.rows("evaluation_daily") == []
    # The poller's own lease is a different one and does not block it.
    store.release_lease(retention.LEASE_NAME, "worker-b")
    assert store.take_lease("poller", "worker-b", 600)
    assert retention.RetentionJob(store, 30, holder="worker-a").run(now=NOW)["rows"] == 1


def test_retention_leaves_model_inputs_alone():
    store = _store(
        pond_chemistry_evaluations=[_chem("2026-08-01T01:00:00+00:00")],
        SensorData=[{"userID": POND, "sensor_type": "PH", "data1": 7.2, "created_at": "2026-08-01T01:00:00+00:00"}],
        pondInterventions=[{"userID": POND, "event_type": "FEEDING", "food_grams": 10,
                            "event_timestamp": "2026-08-01T01:00:00+00:00"}],
        imageTable=[{"user_ID": POND, "green_ratio": 0.03, "created_at": "2026-08-01T01:00:00+00:00"}],
        daily_sensor_averages=[{"userid": POND, "sensor_type": "PH", "avg_value": 7.2, "min_value": 7.1,
                                "max_value": 7.3, "record_date": "2026-08-01"}],
    )
    _recent(store)
    kept = {t: store.rows(t) for t in ("SensorData", "pondInterventions", "imageTable", "daily_sensor_averages")}
    retention.run_retention(store, 30, now=NOW)
    assert {t: store.rows(t) for t in kept} == kept
    assert store.rows("pond_chemistry_evaluations")[0]["evaluated_at"].startswith("2026-10-04")
