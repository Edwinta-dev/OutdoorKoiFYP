"""The ingestion job against MemoryStorage and a fake NEA transport:
history filtering, idempotent reruns, cache updates, backfill windows,
per-product observability and the command line."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from nea_fakes import FETCHED_AT, FakeTransport, make_client, response, stepping_clock

from conftest import make_settings
from koi.storage import MemoryStorage
from koi.weather import job
from koi.weather.__main__ import main

STATIONS = {"rainfall": "S43", "wind-speed": "S43", "air-temperature": "S43",
            "two-hr-forecast": "Serangoon", "twenty-four-hr-forecast": "REG_CENTRAL"}


def _storage() -> MemoryStorage:
    storage = MemoryStorage(clock=stepping_clock(FETCHED_AT + timedelta(minutes=1)))
    storage.add_rows("UserData", [{"userID": 455, "volume": 2500, "biomass": 55, "ClosestStations": STATIONS}])
    storage.add_rows("WeatherStationLookup", [
        {"Id": 1, "station__id": "S43", "station__name": "Kim Chuan Road", "location__latitude": 1.3399,
         "location__longitude": 103.8878, "Measurement": ["AirTemp", "Rainfall", "Wind"]},
        {"Id": 2, "station__id": None, "station__name": "Serangoon", "location__latitude": 1.357,
         "location__longitude": 103.865, "Measurement": ["2HourForecast"]}])
    return storage


def _live(storage, transport=None, products=job.nea.PRODUCTS, **settings):
    return job.run_live(storage, make_client(transport or FakeTransport()), make_settings(**settings), products,
                        clock=lambda: FETCHED_AT)


def test_live_cycle_keeps_history_only_for_assigned_and_listed_stations():
    storage = _storage()
    runs = _live(storage, weather_history_stations="S06", weather_history_areas="")
    assert {r.status for r in runs} == {"ok"}
    stations = {(o["metric"], o["station_id"]) for o in storage.rows("weather_observation")}
    assert ("rainfall", "S211") not in stations and ("air_temperature", "S109") not in stations
    assert {("rainfall", "S43"), ("rainfall", "S06"), ("air_temperature", "S06"), ("uv_index", "NATIONAL"),
            ("relative_humidity", "S43")} <= stations
    areas = {f["slot_id"] for f in storage.rows("weather_forecast_issuance") if f["product"] == "2hr"}
    assert areas == {"Serangoon"}
    # 24-hour and 4-day forecasts are kept whole.
    assert {f["product"] for f in storage.rows("weather_forecast_issuance")} == {"2hr", "24hr", "4day"}


def test_star_keeps_every_station_and_area():
    storage = _storage()
    _live(storage, weather_history_stations="*", weather_history_areas="*")
    assert {"S211", "S109"} <= {o["station_id"] for o in storage.rows("weather_observation")}
    assert {"Serangoon", "Woodlands"} <= {f["slot_id"] for f in storage.rows("weather_forecast_issuance")}


def test_caches_are_filled_for_every_station_in_the_contract_shape():
    storage = _storage()
    _live(storage)
    telemetry = {r["station_id"]: r for r in storage.rows("weather_telemetry")}
    assert set(telemetry) == {"S43", "S06", "S109", "S211"}
    assert telemetry["S43"]["metric_type"] == "realtime_sensor"
    assert telemetry["S43"]["data"] == {"air_temperature": 30.7, "rainfall": 0.2, "wind_speed": 4.1}
    forecasts = {(r["forecast_type"], r["slot_id"]): r for r in storage.rows("weather_forecasts")}
    assert forecasts[("2hr", "Serangoon")]["data"] == {"forecast": "Cloudy"}
    assert forecasts[("24hr", "REG_CENTRAL")]["data"] == {"code": "CL", "text": "Cloudy"}
    assert {k[1] for k in forecasts if k[0] == "4day"} == {"SAT", "SUN", "MON", "TUE"}
    assert forecasts[("uv", "10:00")]["data"] == {"uv": 5}


def test_repeated_live_cycle_is_idempotent():
    storage = _storage()
    first = _live(storage)
    before = {t: storage.rows(t) for t in ("weather_observation", "weather_forecast_issuance",
                                           "weather_telemetry", "weather_forecasts", "WeatherStationLookup")}
    second = _live(storage)
    assert sum(r.inserted for r in first) > 0
    assert sum(r.inserted for r in second) == 0
    assert sum(r.cache_updated for r in second) == 0
    assert sum(r.stations_changed for r in second) == 0
    assert sum(r.duplicates for r in second) == sum(r.observations + r.forecasts for r in second)
    for table, rows in before.items():
        assert storage.rows(table) == rows, table


def test_station_lookup_gains_new_stations_and_tags_without_losing_any():
    storage = _storage()
    _live(storage, products=("relative-humidity", "air-temperature", "two-hr-forecast"))
    lookup = {(r["station__id"], r["station__name"]): r for r in storage.rows("WeatherStationLookup")}
    assert lookup[("S43", "Kim Chuan Road")]["Measurement"] == ["AirTemp", "Rainfall", "Wind", "RelativeHumidity"]
    assert lookup[("S06", "Woodlands")]["Measurement"] == ["AirTemp"]
    assert lookup[(None, "Woodlands")]["Measurement"] == ["2HourForecast"]
    assert lookup[(None, "Serangoon")]["Id"] == 2
    assert sorted(r["Id"] for r in storage.rows("WeatherStationLookup")) == [1, 2, 3, 4, 5]


def test_a_failing_product_does_not_stop_the_others_and_is_logged(caplog):
    transport = FakeTransport()
    transport.queue("rainfall", (500, {}, b""), (500, {}, b""))
    storage = _storage()
    with caplog.at_level(logging.INFO, logger="koi.weather"):
        runs = job.run_live(storage, make_client(transport, max_attempts=2), make_settings(),
                            ("rainfall", "uv"), clock=lambda: FETCHED_AT)
    assert [(r.product, r.status) for r in runs] == [("rainfall", "failed"), ("uv", "ok")]
    assert runs[0].error == "http: HTTP 500"
    events = [r for r in caplog.records if r.getMessage() == "weather_product_ingested"]
    assert [(e.koi_fields["product"], e.levelno) for e in events] == [
        ("rainfall", logging.WARNING), ("uv", logging.INFO)]
    assert {"pages", "records", "malformed", "inserted", "duplicates", "cache_updated"} <= set(events[1].koi_fields)


def test_all_records_malformed_fails_the_product():
    transport = FakeTransport()
    body = response("rainfall")
    body["data"]["readingType"] = "unknown"
    transport.queue("rainfall", body)
    (run,) = _live(_storage(), transport, products=("rainfall",))
    assert run.status == "failed" and run.malformed == 3 and "malformed" in run.error


def test_empty_response_is_empty_not_failed():
    transport = FakeTransport()
    transport.queue("uv", {"code": 0, "data": {"records": []}})
    (run,) = _live(_storage(), transport, products=("uv",))
    assert run.status == "empty"


def test_storage_failure_fails_the_product():
    storage = _storage()
    storage.failing.add("ingest_weather_batch")
    (run,) = _live(storage, products=("uv",))
    assert run.status == "failed" and "ingest_weather_batch" in run.error


# --- backfill -----------------------------------------------------------
def _old_day(product: str, day: str) -> dict:
    """The fixture body moved back to an earlier Singapore day."""
    text = json.dumps(response(product)).replace("2026-10-02", day)
    return json.loads(text)


def test_backfill_writes_history_and_never_touches_the_caches():
    storage = _storage()
    _live(storage)
    cache_before = (storage.rows("weather_telemetry"), storage.rows("weather_forecasts"))
    transport = FakeTransport()
    transport.handler = lambda product, params: _old_day(product, params["date"])
    runs = job.run_backfill(storage, make_client(transport), make_settings(), date(2026, 9, 29), date(2026, 9, 30),
                            ("rainfall", "four-day-outlook"), clock=lambda: FETCHED_AT)
    assert [r.status for r in runs] == ["ok"] * 4
    assert (storage.rows("weather_telemetry"), storage.rows("weather_forecasts")) == cache_before
    days = {o["observed_to"][:10] for o in storage.rows("weather_observation") if o["metric"] == "rainfall"}
    assert days == {"2026-09-29", "2026-09-30", "2026-10-02"}
    # Several issuances for one future day are all kept.
    issued = {f["issued_at"] for f in storage.rows("weather_forecast_issuance") if f["product"] == "4day"}
    assert len(issued) == 3


def test_backfill_resumes_failed_windows_and_skips_done_ones():
    storage = _storage()
    transport = FakeTransport()
    transport.queue("uv", (503, {}, b""), date="2026-09-30")
    settings = make_settings()
    client = make_client(transport, max_attempts=1)
    first = job.run_backfill(storage, client, settings, date(2026, 9, 29), date(2026, 9, 30), ("uv",),
                             clock=lambda: FETCHED_AT)
    assert [r.status for r in first] == ["ok", "failed"]
    windows = {w["window_start"]: w for w in storage.fetch_weather_windows("uv")}
    assert windows["2026-09-28T16:00:00+00:00"]["status"] == "done"
    failed = windows["2026-09-29T16:00:00+00:00"]
    assert (failed["status"], failed["attempts"], failed["last_error"]) == ("failed", 1, "http: HTTP 503")
    assert failed["window_end"] == "2026-09-30T16:00:00+00:00"

    second = job.run_backfill(storage, client, settings, date(2026, 9, 29), date(2026, 9, 30), ("uv",),
                              clock=lambda: FETCHED_AT)
    assert [r.status for r in second] == ["skipped", "ok"]
    retried = {w["window_start"]: w for w in storage.fetch_weather_windows("uv")}["2026-09-29T16:00:00+00:00"]
    assert (retried["status"], retried["attempts"], retried["last_error"]) == ("done", 2, None)

    forced = job.run_backfill(storage, client, settings, date(2026, 9, 29), date(2026, 9, 29), ("uv",),
                              force=True, clock=lambda: FETCHED_AT)
    assert [(r.status, r.inserted) for r in forced] == [("ok", 0)]


def test_backfill_records_empty_windows_and_rejects_reversed_ranges():
    storage = _storage()
    transport = FakeTransport()
    transport.handler = lambda product, params: {"code": 0, "data": {"records": []}}
    (run,) = job.run_backfill(storage, make_client(transport), make_settings(), date(2026, 9, 1), date(2026, 9, 1),
                              ("uv",), clock=lambda: FETCHED_AT)
    assert run.status == "empty"
    assert storage.fetch_weather_windows("uv")[0]["status"] == "empty"
    with pytest.raises(ValueError):
        job.run_backfill(storage, make_client(transport), make_settings(), date(2026, 9, 2), date(2026, 9, 1))


# --- as-of reads through storage -----------------------------------------
def test_storage_as_of_reads_distinguish_observed_issued_and_fetched_times():
    storage = _storage()
    _live(storage, weather_history_stations="*")
    as_of = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
    fc = storage.fetch_forecast_as_of("2hr", "Serangoon", as_of)
    assert (fc["issued_at"], fc["source_updated_at"], fc["available_at"], fc["fetched_at"]) == (
        "2026-10-02T02:00:00+00:00", "2026-10-02T02:02:00+00:00", "2026-10-02T02:02:00+00:00",
        "2026-10-02T02:06:00+00:00")
    assert storage.fetch_forecast_as_of("2hr", "Serangoon", datetime(2026, 10, 2, 2, 1, tzinfo=timezone.utc)) is None
    (obs,) = storage.fetch_weather_observations("S211", "rainfall", as_of - timedelta(hours=2), as_of, as_of)
    assert (obs["observed_from"], obs["observed_to"], obs["fetched_at"]) == (
        "2026-10-02T02:00:00+00:00", "2026-10-02T02:05:00+00:00", "2026-10-02T02:06:00+00:00")
    total = storage.fetch_rainfall_total("S211", as_of - timedelta(hours=24), as_of)
    assert total["total_mm"] == 1.4 and total["status"] == "partial"
    assert total["covered_seconds"] == 300


# --- command line ---------------------------------------------------------
def test_cli_live_and_backfill_exit_codes():
    storage = _storage()
    settings = make_settings()
    assert main(["live", "--products", "uv"], settings=settings, storage=storage,
                client=make_client(FakeTransport())) == 0
    transport = FakeTransport()
    transport.handler = lambda product, params: (404, {}, b"")
    assert main(["backfill", "--start", "2026-09-01", "--end", "2026-09-01", "--products", "rainfall"],
                settings=settings, storage=storage, client=make_client(transport)) == 1


def test_cli_rejects_unknown_products():
    with pytest.raises(SystemExit):
        main(["live", "--products", "pollen"], settings=make_settings(), storage=_storage(),
             client=make_client(FakeTransport()))


def test_settings_split_history_lists():
    settings = make_settings(weather_history_stations="S06, S104", weather_history_areas="*")
    assert settings.weather_history_stations == ("S06", "S104")
    assert job.build_filter(settings, [STATIONS]).areas is None
    assert job.build_filter(settings, [STATIONS]).stations == frozenset({"S06", "S104", "S43"})
