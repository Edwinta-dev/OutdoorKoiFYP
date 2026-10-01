"""SQL tests: the migrations' functions run in a real PostgreSQL.

Runs against the local Supabase stack built from supabase/migrations
(`supabase start`, or `supabase db reset` after a migration changes).
KOI_TEST_DB_URL overrides the address; it must point at localhost, so
these tests can never reach the live project. Every test runs in one
transaction that is rolled back, so the database is left as it was.

Skipped (not failed) when no local database answers, as in CI.

Several tests pin the observed live behaviour recorded in
docs/database-reconciliation.md, defects included (substituted history
readings, hours without TDS dropped). A migration that fixes one of those
defects updates the matching test.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402

from koi.models import forecast_utils  # noqa: E402
from koi.models.algae_engine import parse_image_rows  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND_A, POND_B, POND_EMPTY = 9001, 9002, 9003

# Shapes copied from live rows (read-only query, 2026-10-01).
STATIONS = {"rainfall": "S43", "wind-speed": "S43", "air-temperature": "S43",
            "two-hr-forecast": "Serangoon", "twenty-four-hr-forecast": "REG_CENTRAL"}
TELEMETRY = {"rainfall": 0, "wind_speed": 4.1, "air_temperature": 30.7}
GENERAL_24HR = {"wind": {"speed": {"low": 5, "high": 15}, "direction": "VARIABLE"},
                "forecast": {"code": "TL", "text": "Thundery Showers"},
                "temperature": {"low": 25, "high": 35, "unit": "Degrees Celsius"},
                "relativeHumidity": {"low": 60, "high": 95, "unit": "Percentage"}}


def _connect() -> Any:
    host = urlparse(DB_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail(f"KOI_TEST_DB_URL must point at a local database, not {host}")
    try:
        return psycopg.connect(DB_URL, connect_timeout=2, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no local database at {DB_URL} ({exc.__class__.__name__}); run `supabase start`")


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    c = _connect()
    with c.cursor() as cur:
        cur.execute("select to_regclass('public.worker_status') is not null as ok, "
                    "to_regclass('public.algae_severity_ratings') is not null as ratings")
        row = cur.fetchone()
    c.rollback()
    if not (row["ok"] and row["ratings"]):
        c.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        yield cur
    conn.rollback()


def _pond(db: Any, user_id: int, stations: dict | None = None) -> None:
    # No coordinates and no postal code, so neither UserData trigger runs a
    # station lookup or the OneMap HTTP request.
    db.execute('insert into public."UserData" ("userID", volume, biomass, "ClosestStations") '
               "values (%s, 2500, 55, %s)",
               (user_id, json.dumps(stations) if stations is not None else None))


def _reading(db: Any, user_id: int, sensor: str, value: float, hours_ago: float) -> None:
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") '
               "values (now() - make_interval(secs => %s), %s, %s, %s)",
               (hours_ago * 3600, sensor, value, user_id))


def _cycle(db: Any, user_id: int, hours_ago: float, **values: float) -> None:
    for sensor, value in values.items():
        _reading(db, user_id, sensor, value, hours_ago)


def _dashboard(db: Any, user_id: int) -> dict:
    db.execute("select public.get_bundled_dashboard_payload(%s) as p", (user_id,))
    return db.fetchone()["p"]


def _history(db: Any, user_id: int, days: int = 30) -> dict:
    db.execute("select public.get_historical_graph_payload(%s, %s) as p", (user_id, days))
    return db.fetchone()["p"]


# ---------------------------------------------------------------------
# Pond separation
# ---------------------------------------------------------------------
def test_sql_dashboard_never_mixes_ponds(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    _cycle(db, POND_A, 1.0, pH=7.1, TDS=150, temp=27, LUX=800)
    _cycle(db, POND_B, 0.5, pH=8.3, TDS=300, temp=30, LUX=100)  # newer, other pond

    a, b = _dashboard(db, POND_A), _dashboard(db, POND_B)
    assert a["raw_sensor"] == pytest.approx({"pH": 7.1, "TDS": 150, "temp": 27, "LUX": 800})
    assert b["raw_sensor"] == pytest.approx({"pH": 8.3, "TDS": 300, "temp": 30, "LUX": 100})
    assert [h["ph"] for h in a["telemetry_history"]] == pytest.approx([7.1])
    assert [h["ph"] for h in b["telemetry_history"]] == pytest.approx([8.3])


def test_sql_historical_graph_never_mixes_ponds(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    _cycle(db, POND_A, 2.0, pH=7.0, TDS=150)
    _cycle(db, POND_B, 2.0, pH=8.0, TDS=300)
    db.execute('insert into public."pondInterventions" ("userID", event_type, volume_percentage, event_timestamp) '
               "values (%s, 'WATER_CHANGE', 20, now() - interval '3 days'), "
               "(%s, 'WATER_CHANGE', 50, now() - interval '2 days'), "
               "(%s, 'WATER_CHANGE', 90, now() - interval '1 day')",
               (POND_A, POND_A, POND_B))

    a = _history(db, POND_A)
    ph_a = [t["avg_value"] for t in a["daily_trends"] if t["sensor_type"] == "pH"]
    assert ph_a and all(float(v) == pytest.approx(7.0) for v in ph_a)
    assert [i["pct"] for i in a["interventions"]] == [20, 50]
    # The single largest water change in the window is the major reset.
    assert [i["is_major_reset"] for i in a["interventions"]] == [False, True]


# ---------------------------------------------------------------------
# Empty and partial data
# ---------------------------------------------------------------------
@pytest.mark.parametrize("user_id", [POND_EMPTY, 987654321])  # onboarded with no readings; not onboarded
def test_sql_dashboard_for_pond_without_readings(db, user_id):
    _pond(db, POND_EMPTY)
    p = _dashboard(db, user_id)
    assert p["raw_sensor"] == {}
    assert p["telemetry_history"] == []
    assert p["nea_telemetry"] == {"air_temp": {}, "rainfall": {}, "wind_speed": {}}
    assert set(p["nea_forecasts"]) == {"forecast_2hr", "forecast_24hr", "outlook_4day", "uv_index"}


def test_sql_history_drops_hours_without_tds(db):
    """Observed behaviour: get_pond_telemetry_history keeps only hours with
    both pH and TDS, so hours with the TDS probe off vanish from the chart."""
    _pond(db, POND_A)
    _cycle(db, POND_A, 5.0, pH=7.2, temp=26.5)           # no TDS
    _cycle(db, POND_A, 1.0, pH=7.4, TDS=160, temp=27.0)
    hours = _dashboard(db, POND_A)["telemetry_history"]
    assert [h["ph"] for h in hours] == pytest.approx([7.4])


def test_sql_history_substitutes_missing_temp_and_lux(db):
    """Observed defect D4: missing temperature and light are replaced by
    26.0 degC and 500 lx in telemetry_history, not left empty."""
    _pond(db, POND_A)
    _cycle(db, POND_A, 1.0, pH=7.4, TDS=160)
    (hour,) = _dashboard(db, POND_A)["telemetry_history"]
    assert hour["tempC"] == pytest.approx(26.0)
    assert hour["lux"] == pytest.approx(500.0)


def test_sql_daily_aggregate_uses_singapore_dates_and_skips_dark_lux(db):
    _pond(db, POND_A)
    # 17:30 UTC on 1 March is 01:30 on 2 March in Singapore.
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") values '
               "('2026-03-01 17:30+00', 'pH', 7.0, %s), "
               "('2026-03-01 17:30+00', 'LUX', 20, %s), "
               "('2026-03-01 18:30+00', 'LUX', 900, %s)",
               (POND_A, POND_A, POND_A))
    db.execute("select public.aggregate_daily_sensor_data()")
    db.execute("select sensor_type, record_date::text as d, avg_value from public.daily_sensor_averages "
               "where userid = %s order by sensor_type", (POND_A,))
    rows = db.fetchall()
    assert [(r["sensor_type"], r["d"]) for r in rows] == [("LUX", "2026-03-02"), ("pH", "2026-03-02")]
    assert float(rows[0]["avg_value"]) == pytest.approx(900)  # the 20 lx night reading is excluded


# ---------------------------------------------------------------------
# Weather cache shapes
# ---------------------------------------------------------------------
def test_sql_weather_cache_reaches_the_forecast_helpers(db):
    _pond(db, POND_A, STATIONS)
    _cycle(db, POND_A, 1.0, pH=7.4, TDS=160, temp=27.0, LUX=900)
    db.execute("insert into public.weather_telemetry (station_id, metric_type, data, valid_start, valid_end) "
               "values ('S43', 'realtime_sensor', %s, now() - interval '5 minutes', now() + interval '10 minutes') "
               "on conflict (station_id, metric_type) do update set data = excluded.data",
               (json.dumps(TELEMETRY),))
    db.execute("insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period) values "
               "('2hr', 'Serangoon', '{\"forecast\": \"Cloudy\"}', null), "
               "('24hr', 'REG_CENTRAL', '{\"code\": \"TL\", \"text\": \"Thundery Showers\"}', null), "
               "('24hr', 'GENERAL', %s, null) "
               "on conflict (forecast_type, slot_id) do update set data = excluded.data",
               (json.dumps(GENERAL_24HR),))

    p = _dashboard(db, POND_A)
    assert p["nea_telemetry"]["air_temp"] == {"value": 30.7}
    assert p["nea_forecasts"]["forecast_2hr"] == {"forecast": "Cloudy"}
    now = forecast_utils.current_conditions(p)
    assert now["air_temp_c"] == pytest.approx(30.7)
    assert now["rainfall_mm"] == 0
    assert now["wind_ms"] == pytest.approx(4.1 * 0.514444)
    assert now["humidity_pct"] == pytest.approx(77.5)  # midpoint of 60-95 %
    assert now["water_temp_c"] == pytest.approx(27.0)


def test_sql_four_day_outlook_is_oldest_first(db):
    db.execute("delete from public.weather_forecasts where forecast_type = '4day'")
    for slot, day in (("SAT", "2026-10-03"), ("THU", "2026-10-01"), ("FRI", "2026-10-02")):
        db.execute("insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period) "
                   "values ('4day', %s, %s, %s)",
                   (slot, json.dumps({"day": slot, "relativeHumidity": {"low": 60, "high": 95}}),
                    json.dumps({"timestamp": f"{day}T00:00:00+08:00"})))
    outlook = _dashboard(db, POND_A)["nea_forecasts"]["outlook_4day"]
    assert [o["slot_id"] for o in outlook] == ["THU", "FRI", "SAT"]


# ---------------------------------------------------------------------
# Legacy rows
# ---------------------------------------------------------------------
def test_sql_image_states_stored_as_text_parse(db):
    db.execute('insert into public."imageTable" (created_at, green_ratio, current_state, "user_ID") values '
               "('2026-07-27 08:43+00', 0.05, '[\"base\",0.0569]', %s), "
               "('2026-07-28 08:43+00', 0.06, '[\"obstruction\",0.0569]', %s) "
               "returning id", (POND_A, POND_A))
    db.execute('select created_at, green_ratio, current_state from public."imageTable" '
               'where "user_ID" = %s order by created_at', (POND_A,))
    rows = db.fetchall()
    assert all(isinstance(r["current_state"], str) for r in rows)  # text column, as on live
    samples = parse_image_rows(rows)
    assert [(s.state, s.smoothed_green) for s in samples] == [("base", 0.0569), ("obstruction", 0.0569)]


def test_sql_snapshot_written_before_0002_saves_with_version_1(db):
    # A row as written before 0002: no snapshot_version given.
    db.execute("insert into public.pond_chemistry_state (user_id, snapshot) values (%s, %s) "
               "returning snapshot_version", (POND_A, json.dumps({"version": 1})))
    assert db.fetchone()["snapshot_version"] == 1
    db.execute("select public.save_pond_snapshot(%s, %s, 1) as v", (POND_A, json.dumps({"version": 2})))
    assert db.fetchone()["v"] == 2
    db.execute("select public.save_pond_snapshot(%s, %s, 1) as v", (POND_A, json.dumps({"version": 2})))
    assert db.fetchone()["v"] is None, "a save from a stale base version must be refused"


# ---------------------------------------------------------------------
# Access added by 0002-0005
# ---------------------------------------------------------------------
def test_sql_backend_only_objects_are_closed_to_the_app_key(db):
    for fn in ("public.save_pond_snapshot(bigint, jsonb, bigint)", "public.take_worker_lease(text, text, integer)"):
        db.execute("select has_function_privilege('anon', %s, 'EXECUTE') as ok", (fn,))
        assert db.fetchone()["ok"] is False, fn
    db.execute("select relname, relrowsecurity from pg_class where relnamespace = 'public'::regnamespace "
               "and relname in ('worker_lease', 'worker_status', 'algae_severity_ratings') order by relname")
    assert [(r["relname"], r["relrowsecurity"]) for r in db.fetchall()] == [
        ("algae_severity_ratings", True), ("worker_lease", True), ("worker_status", True)]
    db.execute("select has_table_privilege('anon', 'public.algae_severity_ratings', 'SELECT') as ok")
    assert db.fetchone()["ok"] is False
