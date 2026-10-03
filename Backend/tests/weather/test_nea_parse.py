"""NEA response parsing: units, observation windows and semantics,
forecast issue and validity times, and malformed records."""
from __future__ import annotations

from nea_fakes import FETCHED_AT, response

from koi.weather import nea


def _parse(product: str, body: dict | None = None) -> nea.Parsed:
    return nea.parse(product, [(body or response(product))["data"]], FETCHED_AT)


def _by_station(parsed: nea.Parsed) -> dict:
    return {(o["station_id"], o["observed_to"]): o for o in parsed.observations}


def test_air_temperature_is_an_instantaneous_reading_in_degc():
    parsed = _parse("air-temperature")
    assert (parsed.records, parsed.malformed) == (4, 0)
    obs = _by_station(parsed)[("S43", "2026-10-02T02:05:00+00:00")]
    assert obs["value"] == 30.7 and obs["unit"] == "degC" and obs["semantics"] == "instantaneous"
    assert obs["observed_from"] == obs["observed_to"]
    assert obs["fetched_at"] == "2026-10-02T02:06:00+00:00"
    assert obs["series"] == "station" and obs["regime_id"] is None
    assert obs["provenance"] == {"product": "air-temperature", "reading_type": "DBT 1M F", "reading_unit": "deg C"}


def test_rainfall_is_a_five_minute_total_ending_at_the_timestamp():
    obs = _by_station(_parse("rainfall"))[("S211", "2026-10-02T02:05:00+00:00")]
    assert (obs["value"], obs["unit"], obs["semantics"]) == (1.4, "mm", "interval_total")
    assert obs["observed_from"] == "2026-10-02T02:00:00+00:00"


def test_rainfall_with_an_unknown_reading_type_is_rejected_not_guessed():
    body = response("rainfall")
    body["data"]["readingType"] = "TB1 Rainfall F"
    parsed = _parse("rainfall", body)
    assert parsed.observations == []
    assert (parsed.records, parsed.malformed) == (3, 3)
    assert "not a known total" in parsed.reasons[0]


def test_cumulative_rainfall_is_stored_as_cumulative():
    body = response("rainfall")
    body["data"]["readingType"] = "Rainfall Cumulative Since Midnight"
    parsed = _parse("rainfall", body)
    assert {o["semantics"] for o in parsed.observations} == {"cumulative_total"}
    assert all(o["observed_from"] == o["observed_to"] for o in parsed.observations)


def test_humidity_and_wind_units_are_normalised_and_kept():
    rh = _parse("relative-humidity").observations[0]
    wind = _parse("wind-speed").observations[0]
    direction = _parse("wind-direction").observations[0]
    assert (rh["metric"], rh["unit"], rh["value"]) == ("relative_humidity", "%", 71.5)
    assert (wind["metric"], wind["unit"], wind["value"]) == ("wind_speed", "knot", 4.1)
    assert (direction["metric"], direction["unit"]) == ("wind_direction", "deg")


def test_unexpected_unit_rejects_the_readings():
    body = response("wind-speed")
    body["data"]["readingUnit"] = "m/s"
    parsed = _parse("wind-speed", body)
    assert parsed.observations == [] and parsed.malformed == 1


def test_bad_readings_are_counted_and_the_rest_kept():
    body = response("air-temperature")
    body["data"]["readings"].append({"timestamp": "not a time", "data": [{"stationId": "S43", "value": 30}]})
    body["data"]["readings"][0]["data"].append({"stationId": "S99", "value": "n/a"})
    body["data"]["readings"][0]["data"].append({"value": 30.0})
    parsed = _parse("air-temperature", body)
    assert (parsed.records, parsed.malformed, len(parsed.observations)) == (7, 3, 4)


def test_station_metadata_carries_the_lookup_tags():
    stations = _parse("air-temperature").stations
    assert stations[0] == {"station_id": "S43", "station_name": "Kim Chuan Road", "latitude": 1.3399,
                           "longitude": 103.8878, "tag": "AirTemp"}
    assert {s["tag"] for s in _parse("rainfall").stations} == {"Rainfall"}
    assert {s["tag"] for s in _parse("wind-speed").stations} == {"Wind"}
    assert _parse("wind-direction").stations == []  # no lookup tag for direction


def test_uv_is_an_hourly_mean_at_national_level():
    parsed = _parse("uv")
    ten = next(o for o in parsed.observations if o["observed_to"] == "2026-10-02T02:00:00+00:00")
    assert (ten["station_id"], ten["metric"], ten["value"], ten["unit"], ten["semantics"]) == (
        "NATIONAL", "uv_index", 5, "UV index", "interval_mean")
    assert ten["observed_from"] == "2026-10-02T01:00:00+00:00"


def test_two_hour_forecast_keeps_issue_revision_and_validity():
    parsed = _parse("two-hr-forecast")
    serangoon = next(f for f in parsed.forecasts if f["slot_id"] == "Serangoon")
    assert serangoon == {
        "source": "nea_v2", "product": "2hr", "slot_id": "Serangoon",
        "issued_at": "2026-10-02T02:00:00+00:00", "source_updated_at": "2026-10-02T02:02:00+00:00",
        "valid_from": "2026-10-02T02:00:00+00:00", "valid_to": "2026-10-02T04:00:00+00:00",
        "payload": {"forecast": "Cloudy"}, "fetched_at": "2026-10-02T02:06:00+00:00",
        "provenance": {"product": "two-hr-forecast", "valid_text": "10 am to 12 pm"}}
    assert {s["tag"] for s in parsed.stations} == {"2HourForecast"}
    assert all(s["station_id"] is None for s in parsed.stations)


def test_missing_issue_time_stays_unknown():
    body = response("two-hr-forecast")
    del body["data"]["items"][0]["timestamp"]
    del body["data"]["items"][0]["update_timestamp"]
    parsed = _parse("two-hr-forecast", body)
    assert {(f["issued_at"], f["source_updated_at"]) for f in parsed.forecasts} == {(None, None)}


def test_twenty_four_hour_general_and_regional_periods():
    parsed = _parse("twenty-four-hr-forecast")
    general = next(f for f in parsed.forecasts if f["slot_id"] == "GENERAL")
    assert "validPeriod" not in general["payload"]
    assert general["payload"]["relativeHumidity"] == {"low": 60, "high": 95, "unit": "Percentage"}
    assert (general["valid_from"], general["valid_to"]) == ("2026-10-01T22:00:00+00:00", "2026-10-02T22:00:00+00:00")
    central = sorted((f for f in parsed.forecasts if f["slot_id"] == "REG_CENTRAL"), key=lambda f: f["valid_from"])
    assert [f["payload"] for f in central] == [{"code": "CL", "text": "Cloudy"},
                                              {"code": "TL", "text": "Thundery Showers"}]
    assert (parsed.records, parsed.malformed) == (11, 0)


def test_four_day_outlook_slots_are_singapore_dates_valid_all_day():
    parsed = _parse("four-day-outlook")
    assert [f["slot_id"] for f in parsed.forecasts] == ["2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"]
    first = parsed.forecasts[0]
    assert (first["valid_from"], first["valid_to"]) == ("2026-10-02T16:00:00+00:00", "2026-10-03T16:00:00+00:00")
    assert first["issued_at"] == "2026-10-01T21:30:00+00:00"
    assert first["payload"]["day"] == "Saturday"


def test_forecast_records_without_a_window_are_rejected():
    body = response("twenty-four-hr-forecast")
    del body["data"]["records"][0]["general"]["validPeriod"]
    body["data"]["records"][0]["periods"][0]["timePeriod"] = {}
    parsed = _parse("twenty-four-hr-forecast", body)
    assert parsed.malformed == 6  # general + five regions of the first period
    assert len(parsed.forecasts) == 5


def test_time_without_an_offset_is_singapore_time():
    assert nea.iso(nea.parse_time("2026-10-02T08:00:00")) == "2026-10-02T00:00:00+00:00"
    assert nea.parse_time("") is None and nea.parse_time(None) is None
