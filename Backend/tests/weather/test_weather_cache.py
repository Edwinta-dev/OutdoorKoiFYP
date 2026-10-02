"""Latest-cache rows in the dashboard's exact contract, and the guards
that keep an older reading or issuance out of the cache (the Python
mirror of ingest_weather_batch; tests/sql checks the SQL)."""
from __future__ import annotations

from datetime import datetime, timezone

from nea_fakes import FETCHED_AT, response

from koi.weather import cache, nea

NOW = "2026-10-02T02:10:00+00:00"


def _parsed(product: str, body: dict | None = None) -> nea.Parsed:
    return nea.parse(product, [(body or response(product))["data"]], FETCHED_AT)


def test_telemetry_candidates_take_the_latest_reading_per_station():
    cands = cache.telemetry_candidates(_parsed("air-temperature"))
    s43 = next(c for c in cands if c["station_id"] == "S43")
    assert s43 == {"station_id": "S43", "metric": "air_temperature", "value": 30.7,
                   "observed_at": "2026-10-02T02:05:00+00:00", "valid_until": "2026-10-02T02:10:00+00:00"}
    assert {c["station_id"] for c in cands} == {"S43", "S06", "S109"}  # every station, not only history ones


def test_metrics_outside_the_cache_contract_produce_no_cache_rows():
    assert cache.telemetry_candidates(_parsed("relative-humidity")) == []
    assert cache.telemetry_candidates(_parsed("wind-direction")) == []
    assert [c["metric"] for c in cache.telemetry_candidates(_parsed("wind-speed"))] == ["wind_speed"]


def test_cumulative_rainfall_never_reaches_the_cache():
    body = response("rainfall")
    body["data"]["readingType"] = "Rainfall Cumulative"
    assert cache.telemetry_candidates(_parsed("rainfall", body)) == []


def test_two_hour_cache_rows_match_the_observed_shape():
    cands = cache.forecast_candidates(_parsed("two-hr-forecast"), FETCHED_AT)
    assert cands[0] == {"forecast_type": "2hr", "slot_id": "Serangoon", "data": {"forecast": "Cloudy"},
                        "valid_period": {"start": "2026-10-02T10:00:00+08:00", "end": "2026-10-02T12:00:00+08:00"},
                        "source_time": "2026-10-02T02:02:00+00:00"}


def test_two_hour_cache_uses_the_newest_issuance_of_a_day_response():
    body = response("two-hr-forecast")
    older = {**body["data"]["items"][0], "timestamp": "2026-10-02T09:30:00+08:00",
             "update_timestamp": "2026-10-02T09:31:00+08:00",
             "forecasts": [{"area": "Serangoon", "forecast": "Fair (Day)"}]}
    body["data"]["items"].insert(0, older)
    serangoon = [c for c in cache.forecast_candidates(_parsed("two-hr-forecast", body), FETCHED_AT)
                 if c["slot_id"] == "Serangoon"]
    assert [c["data"] for c in serangoon] == [{"forecast": "Cloudy"}]


def test_twenty_four_hour_cache_general_and_the_period_in_force():
    cands = {c["slot_id"]: c for c in cache.forecast_candidates(_parsed("twenty-four-hr-forecast"), FETCHED_AT)}
    assert set(cands) == {"GENERAL", "REG_WEST", "REG_EAST", "REG_CENTRAL", "REG_SOUTH", "REG_NORTH"}
    assert cands["GENERAL"]["data"] == {
        "temperature": {"low": 25, "high": 35, "unit": "Degrees Celsius"},
        "relativeHumidity": {"low": 60, "high": 95, "unit": "Percentage"},
        "forecast": {"code": "TL", "text": "Thundery Showers"},
        "wind": {"speed": {"low": 5, "high": 15}, "direction": "VARIABLE"}}
    # 10:06 is inside the 06:00-12:00 period.
    assert cands["REG_CENTRAL"]["data"] == {"code": "CL", "text": "Cloudy"}
    later = cache.forecast_candidates(_parsed("twenty-four-hr-forecast"),
                                      datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc))
    assert next(c for c in later if c["slot_id"] == "REG_CENTRAL")["data"]["code"] == "TL"


def test_four_day_cache_rows_are_keyed_by_weekday():
    cands = cache.forecast_candidates(_parsed("four-day-outlook"), FETCHED_AT)
    assert [c["slot_id"] for c in cands] == ["SAT", "SUN", "MON", "TUE"]
    assert cands[0]["valid_period"] == {"timestamp": "2026-10-03T00:00:00+08:00"}
    assert cands[0]["data"]["day"] == "Saturday"
    assert {c["source_time"] for c in cands} == {"2026-10-01T21:40:00+00:00"}


def test_uv_cache_rows_are_keyed_by_singapore_hour():
    cands = cache.uv_candidates(_parsed("uv"))
    assert [c["slot_id"] for c in cands] == ["08:00", "09:00", "10:00"]
    assert cands[-1] == {"forecast_type": "uv", "slot_id": "10:00", "data": {"uv": 5},
                         "valid_period": {"hour": "2026-10-02T10:00:00+08:00"},
                         "source_time": "2026-10-02T02:00:00+00:00"}


def test_available_at_is_the_latest_provider_time_else_fetch_time():
    row = {"issued_at": "2026-10-02T02:00:00+00:00", "source_updated_at": "2026-10-02T02:02:00+00:00",
           "fetched_at": "2026-10-02T02:06:00+00:00"}
    assert cache.available_at(row) == "2026-10-02T02:02:00+00:00"
    assert cache.available_at({**row, "source_updated_at": None}) == "2026-10-02T02:00:00+00:00"
    assert cache.available_at({**row, "issued_at": None, "source_updated_at": None}) == row["fetched_at"]


# --- guards -----------------------------------------------------------
def _tel(metric: str, value: float, at: str, until: str | None = None) -> dict:
    return {"station_id": "S43", "metric": metric, "value": value, "observed_at": at,
            "valid_until": until or at}


def test_telemetry_guard_merges_metrics_and_refuses_older_values():
    row = cache.apply_telemetry_candidate(None, _tel("rainfall", 0.2, "2026-10-02T02:05:00+00:00"), NOW)
    assert row["data"] == {"rainfall": 0.2}
    # A lagging metric with an older time is still accepted for its own key.
    row = cache.apply_telemetry_candidate(row, _tel("air_temperature", 30.6, "2026-10-02T02:04:00+00:00"), NOW)
    assert row["data"] == {"rainfall": 0.2, "air_temperature": 30.6}
    assert row["valid_start"] == "2026-10-02T02:04:00+00:00"  # the stalest value's time
    # An older rainfall reading (a backfill) is refused, a repeat is a no-op.
    assert cache.apply_telemetry_candidate(row, _tel("rainfall", 9.9, "2026-10-02T01:00:00+00:00"), NOW) is None
    assert cache.apply_telemetry_candidate(row, _tel("rainfall", 0.2, "2026-10-02T02:05:00+00:00"), NOW) is None


def test_telemetry_guard_against_a_row_from_the_old_writer():
    old = {"station_id": "S43", "metric_type": "realtime_sensor",
           "data": {"rainfall": 0, "wind_speed": 4.1, "air_temperature": 30.7},
           "valid_start": "2026-10-02T02:00:00+00:00", "valid_end": "2026-10-02T02:15:00+00:00",
           "updated_at": "2026-08-21T00:00:00+00:00", "source_times": None}
    assert cache.apply_telemetry_candidate(old, _tel("rainfall", 5.0, "2026-10-02T01:55:00+00:00"), NOW) is None
    new = cache.apply_telemetry_candidate(old, _tel("rainfall", 0.4, "2026-10-02T02:05:00+00:00",
                                                    "2026-10-02T02:10:00+00:00"), NOW)
    assert new["data"] == {"rainfall": 0.4, "wind_speed": 4.1, "air_temperature": 30.7}
    assert new["valid_end"] == "2026-10-02T02:15:00+00:00"
    assert new["updated_at"] == NOW


def _fc(slot: str, at: str, forecast_type: str = "2hr") -> dict:
    return {"forecast_type": forecast_type, "slot_id": slot, "data": {"forecast": at}, "valid_period": {},
            "source_time": at}


def test_forecast_guard_refuses_older_or_repeated_issuances():
    row = cache.apply_forecast_candidate(None, _fc("Serangoon", "2026-10-02T02:00:00+00:00"), None, NOW)
    assert row["source_issued_at"] == "2026-10-02T02:00:00+00:00"
    assert cache.apply_forecast_candidate(row, _fc("Serangoon", "2026-10-02T01:30:00+00:00"), None, NOW) is None
    assert cache.apply_forecast_candidate(row, _fc("Serangoon", "2026-10-02T02:00:00+00:00"), None, NOW) is None
    assert cache.apply_forecast_candidate(row, _fc("Serangoon", "2026-10-02T02:30:00+00:00"), None, NOW)


def test_forecast_guard_against_an_old_writer_row_uses_updated_at():
    old = {"forecast_type": "2hr", "slot_id": "Serangoon", "data": {"forecast": "Cloudy"}, "valid_period": None,
           "updated_at": "2026-10-02T02:05:00+00:00", "source_issued_at": None}
    assert cache.apply_forecast_candidate(old, _fc("Serangoon", "2026-10-02T02:00:00+00:00"), None, NOW) is None
    assert cache.apply_forecast_candidate(old, _fc("Serangoon", "2026-10-02T02:30:00+00:00"), None, NOW)


def test_four_day_guard_is_product_wide():
    # A free weekday slot still refuses an issuance older than the newest 4day row.
    cand = _fc("WED", "2026-09-30T21:30:00+00:00", "4day")
    assert cache.apply_forecast_candidate(None, cand, "2026-10-01T21:30:00+00:00", NOW) is None
    assert cache.apply_forecast_candidate(None, cand, "2026-09-30T21:30:00+00:00", NOW)
