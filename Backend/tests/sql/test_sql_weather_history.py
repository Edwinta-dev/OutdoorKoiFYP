"""SQL tests for migration 0009_weather_history.sql (issue #24): history
kept beside the latest caches, idempotent ingestion, cache guards against
older backfills, rainfall with coverage, as-of reads, the regime rule,
the station lookup and access.

Batches are built by the real job code (koi.weather) from the synthetic
responses in tests/fixtures/nea_responses.json, so the cache rows the
dashboard reads are the ones production would write.

Same rules as test_sql_rpcs.py: local database only, every test rolled
back, skipped when no local database answers.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import pytest
from nea_fakes import FETCHED_AT, response

from koi.models import forecast_utils
from koi.weather import cache, nea
from koi.weather.job import HistoryFilter

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND = 9301
STATIONS = {"rainfall": "S43", "wind-speed": "S43", "air-temperature": "S43",
            "two-hr-forecast": "Serangoon", "twenty-four-hr-forecast": "REG_CENTRAL"}
KEEP_ALL = HistoryFilter(None, None)
UTC = timezone.utc


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
        cur.execute("select to_regclass('public.weather_observation') is not null as ok")
        row = cur.fetchone()
    c.rollback()
    if not row["ok"]:
        c.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        cur.execute("select 1")
        yield cur
    conn.rollback()


def _batch(product: str, body: dict | None = None, fetched_at: datetime = FETCHED_AT, live: bool = True) -> dict:
    """The batch run_product would send for one response."""
    parsed = nea.parse(product, [(body or response(product))["data"]], fetched_at)
    batch = {"observations": [o for o in parsed.observations if KEEP_ALL.keep_observation(o)],
             "forecasts": parsed.forecasts, "telemetry_cache": [], "forecast_cache": [], "stations": parsed.stations}
    if live:
        batch["telemetry_cache"] = cache.telemetry_candidates(parsed)
        batch["forecast_cache"] = (cache.uv_candidates(parsed) if product == "uv"
                                   else cache.forecast_candidates(parsed, fetched_at))
    return batch


def _ingest(db: Any, batch: dict) -> dict:
    db.execute("select public.ingest_weather_batch(%s::jsonb) as r", (json.dumps(batch),))
    return db.fetchone()["r"]


def _shift(product: str, old: str, new: str) -> dict:
    return json.loads(json.dumps(response(product)).replace(old, new))


def _count(db: Any, sql: str, *args: Any) -> int:
    db.execute(f"select count(*) as n from {sql}", args)
    return db.fetchone()["n"]


def _telemetry(db: Any, station: str) -> dict:
    db.execute("select * from public.weather_telemetry where station_id = %s", (station,))
    return db.fetchone()


def _pond(db: Any) -> None:
    db.execute('insert into public."UserData" ("userID", volume, biomass, "ClosestStations") values (%s, 2500, 55, %s)',
               (POND, json.dumps(STATIONS)))


# ---------------------------------------------------------------------
# History beside the latest cache
# ---------------------------------------------------------------------
def test_sql_two_observations_kept_while_the_cache_holds_the_current_one(db):
    first = _ingest(db, _batch("air-temperature"))
    later = _shift("air-temperature", "10:05:00", "10:10:00")
    later["data"]["readings"][0]["data"][0]["value"] = 31.2
    second = _ingest(db, _batch("air-temperature", later, FETCHED_AT + timedelta(minutes=5)))
    assert first["observations"]["inserted"] == 4 and second["observations"]["inserted"] == 3

    db.execute("select value, observed_to from public.weather_observation "
               "where station_id = 'S43' and metric = 'air_temperature' order by observed_to")
    rows = db.fetchall()
    assert [r["value"] for r in rows] == [30.6, 30.7, 31.2]
    assert _count(db, "public.weather_telemetry where station_id = 'S43'") == 1
    row = _telemetry(db, "S43")
    assert row["metric_type"] == "realtime_sensor"
    assert row["data"] == {"air_temperature": 31.2}
    assert row["valid_start"] == datetime(2026, 10, 2, 2, 10, tzinfo=UTC)
    assert row["source_times"] == {"air_temperature": "2026-10-02T02:10:00+00:00"}


def test_sql_two_issuances_for_one_valid_date_kept_and_cache_has_the_newer(db):
    _ingest(db, _batch("four-day-outlook"))
    next_issue = _shift("four-day-outlook", "2026-10-02T05:", "2026-10-02T11:")
    next_issue["data"]["records"][0]["forecasts"][0]["forecast"]["code"] = "SH"
    _ingest(db, _batch("four-day-outlook", next_issue, FETCHED_AT + timedelta(hours=6)))

    db.execute("select issued_at, payload->'forecast'->>'code' as code from public.weather_forecast_issuance "
               "where product = '4day' and slot_id = '2026-10-03' order by issued_at")
    assert [r["code"] for r in db.fetchall()] == ["TL", "SH"]
    db.execute("select data->'forecast'->>'code' as code, source_issued_at from public.weather_forecasts "
               "where forecast_type = '4day' and slot_id = 'SAT'")
    (row,) = db.fetchall()
    assert row["code"] == "SH"
    assert row["source_issued_at"] == datetime(2026, 10, 2, 3, 40, tzinfo=UTC)


def test_sql_duplicate_fetch_and_backfill_are_idempotent(db):
    for product in nea.PRODUCTS:
        _ingest(db, _batch(product))
    tables = ("weather_observation", "weather_forecast_issuance", "weather_telemetry", "weather_forecasts",
              '"WeatherStationLookup"')

    def snapshot() -> dict:
        out = {}
        for t in tables:
            db.execute(f"select to_jsonb(x) - 'id' - 'created_at' - 'updated_at' as r from public.{t} x")
            out[t] = sorted(json.dumps(r["r"], sort_keys=True) for r in db.fetchall())
        return out

    before = snapshot()
    for product in nea.PRODUCTS:
        again = _ingest(db, _batch(product))
        assert again["observations"]["inserted"] == 0 and again["forecasts"]["inserted"] == 0, product
        assert again["telemetry_cache_updated"] == 0 and again["forecast_cache_updated"] == 0, product
        assert again["stations_changed"] == 0, product
        backfill = _ingest(db, _batch(product, live=False))
        assert backfill["observations"]["inserted"] == 0 and backfill["forecasts"]["inserted"] == 0, product
    assert snapshot() == before


# ---------------------------------------------------------------------
# An older backfill never regresses the cache
# ---------------------------------------------------------------------
def test_sql_older_readings_cannot_regress_the_telemetry_cache(db):
    _ingest(db, _batch("rainfall"))
    older = _shift("rainfall", "2026-10-02T10:05", "2026-09-30T10:05")
    for row in older["data"]["readings"][0]["data"]:
        row["value"] = 42.0
    result = _ingest(db, _batch("rainfall", older))  # even sent as live candidates
    assert result["observations"]["inserted"] == 3
    assert result["telemetry_cache_updated"] == 0
    assert _telemetry(db, "S211")["data"] == {"rainfall": 1.4}


def test_sql_lagging_metric_still_updates_its_own_key(db):
    _ingest(db, _batch("rainfall"))  # 10:05
    lagging = _shift("air-temperature", "10:05:00", "10:03:00")
    lagging["data"]["readings"] = lagging["data"]["readings"][:1]
    _ingest(db, _batch("air-temperature", lagging))
    row = _telemetry(db, "S43")
    assert row["data"] == {"rainfall": 0.2, "air_temperature": 30.7}
    assert row["valid_start"] == datetime(2026, 10, 2, 2, 3, tzinfo=UTC)  # the stalest value's time
    assert row["valid_end"] == datetime(2026, 10, 2, 2, 10, tzinfo=UTC)


def test_sql_old_writer_rows_are_respected(db):
    db.execute("insert into public.weather_telemetry "
               "(station_id, metric_type, data, valid_start, valid_end, updated_at) values ('S43', 'realtime_sensor', "
               "'{\"rainfall\": 0, \"wind_speed\": 4.1, \"air_temperature\": 30.7}', "
               "'2026-10-02 02:08+00', '2026-10-02 02:20+00', '2026-08-21 00:00+00')")
    db.execute("insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period, updated_at) "
               "values ('2hr', 'Serangoon', '{\"forecast\": \"Fair (Day)\"}', null, '2026-10-02 02:30+00')")
    result = _ingest(db, _batch("rainfall"))
    assert _telemetry(db, "S43")["data"]["rainfall"] == 0  # 10:05 reading is older than the row's 10:08
    _ingest(db, _batch("two-hr-forecast"))
    db.execute("select data, source_issued_at from public.weather_forecasts where forecast_type = '2hr' "
               "and slot_id = 'Serangoon'")
    assert db.fetchone() == {"data": {"forecast": "Fair (Day)"}, "source_issued_at": None}
    assert result["observations"]["inserted"] == 3  # history is still recorded

    newer = _shift("rainfall", "10:05:00", "10:10:00")
    _ingest(db, _batch("rainfall", newer))
    row = _telemetry(db, "S43")
    assert row["data"] == {"rainfall": 0.2, "wind_speed": 4.1, "air_temperature": 30.7}
    assert row["updated_at"] > datetime(2026, 8, 21, tzinfo=UTC)  # D13: updated_at now moves


def test_sql_older_forecast_issuances_cannot_regress_the_cache(db):
    _ingest(db, _batch("two-hr-forecast"))
    older = _shift("two-hr-forecast", "2026-10-02T10:0", "2026-10-02T09:3")
    older["data"]["items"][0]["forecasts"][0]["forecast"] = "Heavy Rain"
    result = _ingest(db, _batch("two-hr-forecast", older))
    assert result["forecasts"]["inserted"] == 2 and result["forecast_cache_updated"] == 0
    db.execute("select data from public.weather_forecasts where forecast_type = '2hr' and slot_id = 'Serangoon'")
    assert db.fetchone()["data"] == {"forecast": "Cloudy"}


def test_sql_older_four_day_issuance_cannot_take_a_free_weekday(db):
    _ingest(db, _batch("four-day-outlook"))  # SAT..TUE, issued 2 Oct
    older = _shift("four-day-outlook", "2026-10-02T05:", "2026-09-29T05:")
    older["data"]["records"][0]["forecasts"][0]["timestamp"] = "2026-09-30T00:00:00+08:00"  # a Wednesday
    result = _ingest(db, _batch("four-day-outlook", older))
    assert result["forecast_cache_updated"] == 0
    assert _count(db, "public.weather_forecasts where forecast_type = '4day'") == 4


# ---------------------------------------------------------------------
# The dashboard reads what the job writes
# ---------------------------------------------------------------------
def test_sql_dashboard_reads_the_caches_the_job_writes(db):
    _pond(db)
    db.execute("delete from public.weather_forecasts where forecast_type = '4day'")
    db.execute("insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period, updated_at) values "
               "('4day', 'FRI', '{\"day\": \"Friday\"}', '{\"timestamp\": \"2026-09-25T00:00:00+08:00\"}', "
               "now() - interval '1 week')")
    for product in ("air-temperature", "rainfall", "wind-speed", "two-hr-forecast", "twenty-four-hr-forecast",
                    "four-day-outlook"):
        _ingest(db, _batch(product))
    db.execute("select public.get_bundled_dashboard_payload(%s) as p", (POND,))
    p = db.fetchone()["p"]
    assert p["nea_telemetry"] == {"air_temp": {"value": 30.7}, "rainfall": {"value": 0.2},
                                  "wind_speed": {"value": 4.1}}
    assert p["nea_forecasts"]["forecast_2hr"] == {"forecast": "Cloudy"}
    assert p["nea_forecasts"]["forecast_24hr"]["regional"] == {"code": "CL", "text": "Cloudy"}
    assert [o["slot_id"] for o in p["nea_forecasts"]["outlook_4day"]] == ["SAT", "SUN", "MON", "TUE"]
    now = forecast_utils.current_conditions(p)
    assert now["humidity_pct"] == pytest.approx(77.5)
    assert now["wind_ms"] == pytest.approx(4.1 * 0.514444)
    days = forecast_utils.daily_environment_from_outlook(p["nea_forecasts"]["outlook_4day"])
    assert [d["day_label"] for d in days] == ["Saturday", "Sunday", "Monday", "Tuesday"]


# ---------------------------------------------------------------------
# Rainfall over 24 hours with coverage
# ---------------------------------------------------------------------
def _rain_rows(db: Any, start: datetime, count: int, value: float, station: str = "S43", minutes: int = 5,
               semantics: str = "interval_total") -> None:
    rows = [{"source": "nea_v2", "series": "station", "station_id": station, "metric": "rainfall", "value": value,
             "unit": "mm", "semantics": semantics,
             "observed_from": (start + timedelta(minutes=minutes * i)).isoformat(),
             "observed_to": (start + timedelta(minutes=minutes * (i + 1))).isoformat()
             if semantics != "cumulative_total" else (start + timedelta(minutes=minutes * i)).isoformat(),
             "fetched_at": FETCHED_AT.isoformat(), "regime_id": None, "provenance": {}} for i in range(count)]
    _ingest(db, {"observations": rows})


def _rainfall(db: Any, station: str, start: datetime, end: datetime, as_of: datetime | None = None) -> dict:
    db.execute("select public.weather_rainfall_total(%s, %s, %s, %s) as r", (station, start, end, as_of))
    return db.fetchone()["r"]


DAY = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)  # 2 Oct, Singapore midnight


def test_sql_24_hour_rainfall_reports_value_and_coverage(db):
    _rain_rows(db, DAY, 144, 0.1)  # first 12 hours only
    result = _rainfall(db, "S43", DAY, DAY + timedelta(hours=24))
    assert float(result["total_mm"]) == pytest.approx(14.4)
    assert (result["status"], float(result["coverage"]), result["intervals"]) == ("partial", 0.5, 144)
    assert result["missing"] == [{"from": "2026-10-02T04:00:00+00:00", "to": "2026-10-02T16:00:00+00:00"}]
    _rain_rows(db, DAY + timedelta(hours=12), 144, 0.0)
    full = _rainfall(db, "S43", DAY, DAY + timedelta(hours=24))
    assert (full["status"], float(full["coverage"]), full["missing"]) == ("complete", 1.0, [])
    assert float(full["total_mm"]) == pytest.approx(14.4)


def test_sql_rainfall_never_turns_missing_into_zero_or_double_counts(db):
    empty = _rainfall(db, "S43", DAY, DAY + timedelta(hours=24))
    assert (empty["total_mm"], empty["status"], float(empty["coverage"])) == (None, "no_data", 0)
    _rain_rows(db, DAY, 12, 0.5)                               # 6 mm in 5-minute totals
    _rain_rows(db, DAY, 1, 6.0, minutes=60)                    # an overlapping hourly total
    _rain_rows(db, DAY, 3, 20.0, semantics="cumulative_total")  # never summed
    _rain_rows(db, DAY, 12, 9.0, station="S06")                 # another station
    result = _rainfall(db, "S43", DAY, DAY + timedelta(hours=1))
    assert float(result["total_mm"]) == pytest.approx(6.0)
    assert result["overlapping_skipped"] == 1
    as_of = _rainfall(db, "S43", DAY, DAY + timedelta(hours=1), DAY + timedelta(minutes=20))
    assert float(as_of["total_mm"]) == pytest.approx(2.0) and as_of["status"] == "partial"


def test_sql_rainfall_matches_the_python_reference(db):
    from koi.weather.history import rainfall_total

    _rain_rows(db, DAY + timedelta(minutes=7), 30, 0.3)
    db.execute("select source, series, station_id, metric, value, unit, semantics, observed_from, observed_to "
               "from public.weather_observation where station_id = 'S43'")
    rows = [{**r, "observed_from": r["observed_from"].isoformat(), "observed_to": r["observed_to"].isoformat()}
            for r in db.fetchall()]
    sql = _rainfall(db, "S43", DAY, DAY + timedelta(hours=3))
    py = rainfall_total(rows, "S43", DAY, DAY + timedelta(hours=3))
    assert float(sql["total_mm"]) == pytest.approx(py["total_mm"])
    assert float(sql["coverage"]) == pytest.approx(py["coverage"])
    assert [(m["from"][:19], m["to"][:19]) for m in sql["missing"]] == [
        (m["from"][:19], m["to"][:19]) for m in py["missing"]]


# ---------------------------------------------------------------------
# As-of reads and their timestamps
# ---------------------------------------------------------------------
def test_sql_as_of_forecast_never_uses_a_later_issuance(db):
    _ingest(db, _batch("four-day-outlook"))
    next_issue = _shift("four-day-outlook", "2026-10-02T05:", "2026-10-02T11:")
    _ingest(db, _batch("four-day-outlook", next_issue, FETCHED_AT + timedelta(hours=6)))

    def at(t: datetime) -> dict | None:
        db.execute("select * from public.weather_forecast_as_of('4day', '2026-10-03', %s)", (t,))
        return db.fetchone()

    assert at(datetime(2026, 10, 1, 21, 39, tzinfo=UTC)) is None  # issued 21:30, revised 21:40
    early = at(datetime(2026, 10, 2, 1, 0, tzinfo=UTC))
    assert early["issued_at"] == datetime(2026, 10, 1, 21, 30, tzinfo=UTC)
    assert early["source_updated_at"] == datetime(2026, 10, 1, 21, 40, tzinfo=UTC)
    assert early["available_at"] == early["source_updated_at"]
    assert early["fetched_at"] == FETCHED_AT
    late = at(datetime(2026, 10, 2, 4, 0, tzinfo=UTC))
    assert late["issued_at"] == datetime(2026, 10, 2, 3, 30, tzinfo=UTC)


def test_sql_unknown_issue_time_is_usable_only_after_fetch(db):
    body = response("two-hr-forecast")
    for key in ("timestamp", "update_timestamp"):
        del body["data"]["items"][0][key]
    _ingest(db, _batch("two-hr-forecast", body))
    db.execute("select issued_at, available_at from public.weather_forecast_issuance where slot_id = 'Serangoon'")
    row = db.fetchone()
    assert row == {"issued_at": None, "available_at": FETCHED_AT}
    db.execute("select count(*) as n from public.weather_forecast_as_of('2hr', 'Serangoon', %s)",
               (FETCHED_AT - timedelta(seconds=1),))
    assert db.fetchone()["n"] == 0


def test_sql_as_of_observations_carry_observed_and_fetched_times(db):
    _ingest(db, _batch("rainfall"))
    db.execute("select * from public.weather_observations_as_of('S211', 'rainfall', %s, %s, %s)",
               (DAY, DAY + timedelta(days=1), datetime(2026, 10, 2, 3, tzinfo=UTC)))
    (row,) = db.fetchall()
    assert (row["observed_from"], row["observed_to"], row["fetched_at"]) == (
        datetime(2026, 10, 2, 2, 0, tzinfo=UTC), datetime(2026, 10, 2, 2, 5, tzinfo=UTC), FETCHED_AT)
    db.execute("select count(*) as n from public.weather_observations_as_of('S211', 'rainfall', %s, %s, %s)",
               (DAY, DAY + timedelta(days=1), datetime(2026, 10, 2, 2, 4, tzinfo=UTC)))
    assert db.fetchone()["n"] == 0


# ---------------------------------------------------------------------
# Regimes, constraints, station lookup and access
# ---------------------------------------------------------------------
def _obs(**overrides: Any) -> dict:
    row = {"source": "nea_v2", "series": "station", "station_id": "S43", "metric": "air_temperature", "value": 30.0,
           "unit": "degC", "semantics": "instantaneous", "observed_from": "2025-06-10T06:00:00+00:00",
           "observed_to": "2025-06-10T06:00:00+00:00", "fetched_at": FETCHED_AT.isoformat(), "regime_id": None,
           "provenance": {}}
    return {**row, **overrides}


def test_sql_regimes_are_seeded_from_the_analysis(db):
    db.execute("select regime_id, series, valid_from::text, valid_to::text, station_count_min, station_count_max "
               "from public.weather_station_regime order by valid_from")
    assert db.fetchall() == [
        {"regime_id": "nea_cluster_2stn", "series": "network_mean", "valid_from": "2020-02-01",
         "valid_to": "2025-06-01", "station_count_min": 2, "station_count_max": 2},
        {"regime_id": "nea_network_mean", "series": "network_mean", "valid_from": "2025-06-02", "valid_to": None,
         "station_count_min": 11, "station_count_max": 33}]


def test_sql_a_network_regime_cannot_be_attached_to_one_station(db):
    _ingest(db, {"observations": [_obs(series="network_mean", station_id="NETWORK",
                                       regime_id="nea_network_mean")]})
    with pytest.raises(errors.ForeignKeyViolation):
        with db.connection.transaction():
            _ingest(db, {"observations": [_obs(regime_id="nea_network_mean")]})


@pytest.mark.parametrize("bad", [
    {"unit": "F"}, {"semantics": "instantaneous", "observed_from": "2025-06-10T05:55:00+00:00"},
    {"metric": "pollen"}, {"observed_from": "2025-06-10T07:00:00+00:00", "semantics": "interval_total"},
])
def test_sql_observation_checks(db, bad):
    with pytest.raises(errors.CheckViolation):
        with db.connection.transaction():
            _ingest(db, {"observations": [_obs(**bad)]})


def test_sql_station_lookup_is_maintained_and_resolvable(db):
    db.execute('insert into public."WeatherStationLookup" ("Id", station__id, station__name, location__latitude, '
               'location__longitude, "Measurement") values '
               "(901, 'S43', 'Kim Chuan Road', 1.3399, 103.8878, '[\"AirTemp\", \"Rainfall\", \"Wind\"]'), "
               "(902, null, 'Serangoon', 1.357, 103.865, '[\"2HourForecast\"]')")
    first = _ingest(db, {"stations": _batch("relative-humidity")["stations"] + _batch("rainfall")["stations"]
                         + _batch("two-hr-forecast")["stations"]})
    assert first["stations_changed"] == 4  # S43 gains RelativeHumidity; S06, S211, Woodlands area are new
    db.execute('select "Id", station__id, station__name, "Measurement" from public."WeatherStationLookup" '
               'order by "Id"')
    rows = db.fetchall()
    assert rows[0]["Measurement"] == ["AirTemp", "Rainfall", "Wind", "RelativeHumidity"]
    assert {(r["station__id"], r["station__name"]) for r in rows} == {
        ("S43", "Kim Chuan Road"), (None, "Serangoon"), ("S06", "Woodlands"), ("S211", "Yishun Avenue 5"),
        (None, "Woodlands")}
    again = _ingest(db, {"stations": _batch("rainfall")["stations"]})
    assert again["stations_changed"] == 0
    db.execute("select station_id from public.resolve_closest_station(1.43, 103.79, 'Rainfall')")
    assert db.fetchone()["station_id"] == "S06"
    db.execute("select station_name from public.resolve_closest_station(1.35, 103.87, '2HourForecast')")
    assert db.fetchone()["station_name"] == "Serangoon"


def test_sql_weather_history_is_closed_to_the_app_key(db):
    for fn in ("public.ingest_weather_batch(jsonb)",
               "public.weather_forecast_as_of(text, text, timestamptz, timestamptz)",
               "public.weather_observations_as_of(text, text, timestamptz, timestamptz, timestamptz)",
               "public.weather_rainfall_total(text, timestamptz, timestamptz, timestamptz)"):
        for role in ("anon", "authenticated"):
            db.execute("select has_function_privilege(%s, %s, 'EXECUTE') as ok", (role, fn))
            assert db.fetchone()["ok"] is False, (role, fn)
        db.execute("select has_function_privilege('service_role', %s, 'EXECUTE') as ok", (fn,))
        assert db.fetchone()["ok"] is True, fn
    names = ("weather_station_regime", "weather_observation", "weather_forecast_issuance", "weather_ingest_window")
    db.execute("select relname, relrowsecurity from pg_class where relnamespace = 'public'::regnamespace "
               "and relname = any(%s) order by relname", (list(names),))
    assert [r["relrowsecurity"] for r in db.fetchall()] == [True] * 4
    for name in names:
        db.execute("select has_table_privilege('anon', %s, 'SELECT') as ok", (f"public.{name}",))
        assert db.fetchone()["ok"] is False, name


def test_sql_ingest_window_rows_upsert_by_product_and_start(db):
    row = {"product": "uv", "window_start": "2026-09-28T16:00:00+00:00", "window_end": "2026-09-29T16:00:00+00:00"}
    db.execute("insert into public.weather_ingest_window (product, window_start, window_end, status, attempts) "
               "values (%(product)s, %(window_start)s, %(window_end)s, 'failed', 1)", row)
    db.execute("insert into public.weather_ingest_window (product, window_start, window_end, status, attempts) "
               "values (%(product)s, %(window_start)s, %(window_end)s, 'done', 2) "
               "on conflict (product, window_start) do update set status = excluded.status, "
               "attempts = excluded.attempts", row)
    db.execute("select status, attempts from public.weather_ingest_window where product = 'uv'")
    assert db.fetchall() == [{"status": "done", "attempts": 2}]


def test_sql_cache_contract_for_legacy_payload_keys_is_unchanged(db):
    """The new nullable cache columns do not disturb a writer that names
    only the original columns, as the edge functions do."""
    db.execute("insert into public.weather_telemetry (station_id, metric_type, data, valid_start, valid_end) "
               "values ('S99', 'realtime_sensor', '{\"rainfall\": 0}', now(), now()) "
               "on conflict (station_id, metric_type) do update set data = excluded.data")
    db.execute("insert into public.weather_forecasts (forecast_type, slot_id, data) values ('uv', '12:00', "
               "'{\"uv\": 7}') on conflict (forecast_type, slot_id) do update set data = excluded.data")
    assert _telemetry(db, "S99")["source_times"] is None
    db.execute("select source_issued_at from public.weather_forecasts where forecast_type = 'uv' and slot_id = '12:00'")
    assert db.fetchone()["source_issued_at"] is None
