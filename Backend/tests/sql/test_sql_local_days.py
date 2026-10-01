"""SQL tests for issue #15: local (Singapore) calendar days and UTC hourly
instants, against the local Supabase stack only.

Same connection rules as test_sql_rpcs.py: KOI_TEST_DB_URL must be local,
every test is rolled back, and the module skips when no local database
answers (as in CI).

Each test runs with the session time zone set to UTC (the Supabase
default) and to Asia/Singapore, so an implicit timestamp/timestamptz cast
cannot hide behind the default.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from test_sql_rpcs import POND_A, _pond, conn, db  # noqa: F401 - conn and db are pytest fixtures

from koi.models.engine import PondConfig, RawSample, WaterChemistryEngine
from koi.models.local_time import zone

SGT = zone("Asia/Singapore")


def sgt(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=SGT)


# Instants either side of local midnight (23:59/00:01 SGT), of UTC midnight
# (07:59/08:01 SGT) and of a month and a year boundary.
BOUNDARY_TIMES = [
    sgt(2026, 12, 31, 7, 59), sgt(2026, 12, 31, 8, 1),
    sgt(2026, 12, 31, 23, 59), sgt(2027, 1, 1, 0, 1),
    sgt(2027, 1, 1, 7, 59), sgt(2027, 1, 1, 8, 1),
    sgt(2027, 1, 31, 23, 59), sgt(2027, 2, 1, 0, 1),
]
EXPECTED_DATES = ["2026-12-31", "2026-12-31", "2026-12-31", "2027-01-01",
                  "2027-01-01", "2027-01-01", "2027-01-31", "2027-02-01"]


def boundary_samples() -> list[RawSample]:
    # Small steps so the sensor gate trusts every reading (no stale runs, no
    # rate limit hit at 2-minute spacing); lux above the SQL 50 lx cut.
    return [RawSample(time=t, ph=7.10 + 0.01 * (i % 3), tds=180.0 + i, temp_c=28.0 + 0.1 * i,
                      lux=900.0 + 10 * i)
            for i, t in enumerate(BOUNDARY_TIMES)]


@pytest.fixture(params=["UTC", "Asia/Singapore"])
def session_zone(request: Any, db: Any) -> str:  # noqa: F811 - pytest fixture
    db.execute("select set_config('TimeZone', %s, true)", (request.param,))
    return request.param


def _insert(cur: Any, user_id: int, samples: list[RawSample]) -> None:
    for s in samples:
        for sensor, value in (("pH", s.ph), ("TDS", s.tds), ("temp", s.temp_c), ("LUX", s.lux)):
            cur.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") '
                        "values (%s, %s, %s, %s)", (s.time, sensor, value, user_id))


def test_sql_and_python_agree_on_local_dates_and_daily_values(db, session_zone):  # noqa: F811
    _pond(db, POND_A)
    samples = boundary_samples()
    _insert(db, POND_A, samples)
    db.execute("select public.aggregate_daily_sensor_data()")
    db.execute("select sensor_type, record_date::text as d, avg_value, min_value, max_value "
               "from public.daily_sensor_averages where userid = %s", (POND_A,))
    sql = {(r["sensor_type"], r["d"]): r for r in db.fetchall()}

    engine = WaterChemistryEngine(PondConfig(volume_litres=2500, estimated_biomass_grams=5500))
    # The month boundary is over 30 days after the year boundary, so the
    # engine's retention drops the earlier buckets; collect each as it fills.
    python = {}
    for s in samples:
        engine.ingest_sensor_sample(s)
        key = engine._day_for(s.time).calendar_date().isoformat()
        python[key] = engine._daily[key]

    assert sorted(python) == sorted({d for (_, d) in sql}) == sorted(set(EXPECTED_DATES))
    for key, day in python.items():
        assert len(day.trusted_ph) == EXPECTED_DATES.count(key), "every sample trusted and on its local date"
        for sensor, values in (("pH", day.trusted_ph), ("TDS", day.trusted_tds), ("LUX", day.trusted_lux)):
            row = sql[(sensor, key)]
            assert float(row["avg_value"]) == pytest.approx(sum(values) / len(values), abs=0.0051), (sensor, key)
            assert float(row["min_value"]) == pytest.approx(min(values), abs=0.0051), (sensor, key)
            assert float(row["max_value"]) == pytest.approx(max(values), abs=0.0051), (sensor, key)


def test_sql_daily_counts_match_python_grouping(db, session_zone):  # noqa: F811
    """The SQL date for each sample, one at a time, is the Python key."""
    _pond(db, POND_A)
    for t, expected in zip(BOUNDARY_TIMES, EXPECTED_DATES, strict=True):
        db.execute("select ((%s::timestamptz) at time zone 'Asia/Singapore')::date::text as d", (t,))
        assert db.fetchone()["d"] == expected == WaterChemistryEngine(
            PondConfig(volume_litres=1, estimated_biomass_grams=1))._day_for(t).calendar_date().isoformat()


def test_sql_hourly_history_returns_utc_instants_in_any_session_zone(db, session_zone):  # noqa: F811
    _pond(db, POND_A)
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(hours=5)).replace(minute=10, second=0, microsecond=0)
    t2 = t1 + timedelta(hours=2, minutes=40)
    expected = defaultdict(list)
    for t, ph in ((t1, 7.2), (t1 + timedelta(minutes=20), 7.4), (t2, 7.6)):
        for sensor, value in (("pH", ph), ("TDS", 170.0)):
            db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") '
                       "values (%s, %s, %s, %s)", (t, sensor, value, POND_A))
        expected[t.replace(minute=0)].append(ph)

    db.execute("select bucket_time, ph from public.get_pond_telemetry_history(%s, 7) order by bucket_time",
               (POND_A,))
    rows = db.fetchall()
    assert all(r["bucket_time"].tzinfo is not None for r in rows)
    got = [(r["bucket_time"].astimezone(timezone.utc), float(r["ph"])) for r in rows]
    want = [(hour, round(sum(v) / len(v), 2)) for hour, v in sorted(expected.items())]
    assert got == want

    # The same instants reach the app through the dashboard payload as ISO
    # strings with an explicit offset.
    db.execute("select public.get_bundled_dashboard_payload(%s) as p", (POND_A,))
    times = [datetime.fromisoformat(h["time"]) for h in db.fetchone()["p"]["telemetry_history"]]
    assert [t.astimezone(timezone.utc) for t in times] == [hour for hour, _ in want]
