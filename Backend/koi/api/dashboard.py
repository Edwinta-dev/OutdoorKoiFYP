"""GET /v1/ponds/{pond}/dashboard: the source rows turned into the typed
response (koi.api.responses.Dashboard).

Storage returns the rows uninterpreted (Storage.fetch_dashboard_sources:
pond_dashboard_sources in SQL, the same selection in MemoryStorage), and
build_dashboard is the only place they are interpreted, so both storages
produce the same response. The rules, field by field, are in
docs/api/README.md. In short:

- A value is never substituted. No row, an unusable value (the node's -1
  for a light sensor that did not answer, a non-number) or a station
  that is not assigned gives value null and a status saying which.
- Every reading carries its own row's times. SensorData.created_at is
  the insert time; the sample time is unknown until the node sends one
  (issue #17), so freshness is judged from the insert time.
- A weather value comes only from the cache row of the station assigned
  for that metric, with the metric present in the row.
- Forecast issue time (source_issued_at), validity window and cache
  write time (updated_at) are separate fields.
- UV: the current Singapore hour's report, matched on its hour, not on
  an "HH:00" slot that may be yesterday's. A daylight hour without one is
  missing; a night hour without one is 0, marked night_derived.

Everything that depends on the clock (freshness, expiry, the UV hour,
which outlook days are current) is computed from `now`, so the response,
and the ETag hashed from it, changes when a reading goes stale even if
no row changed.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from koi.api import responses as r
from koi.models.hypoxia import HypoxiaThresholds, algae_is_high, assess_hypoxia
from koi.models.local_time import zone
from koi.storage.base import parse_timestamp
from koi.weather.nea import SINGAPORE
from koi.weather.nea import parse_time as _parse_nea_time

# How long after its insert time a sensor reading counts as fresh. The
# node reports every 15 s when awake and the poller runs every 15 min, so
# 30 min without a row means the node has stopped reporting.
READING_FRESH_FOR = timedelta(minutes=30)
# NEA station readings come every 5 minutes (koi.weather.nea).
WEATHER_FRESH_FOR = timedelta(hours=1)
# Daylight for the UV convention, Singapore hours [start, end).
DAYLIGHT_HOURS = (7, 19)

# Channel -> (SensorData.sensor_type spellings, newest first wins; unit).
CHANNELS: dict[str, tuple[tuple[str, ...], str]] = {
    "ph": (("pH",), "pH"),
    "tds": (("TDS",), "ppm"),
    "water_temp": (("temp",), "degC"),
    "lux": (("LUX", "lux"), "lux"),
}
# Weather metric -> (ClosestStations key, weather_telemetry.data key, unit).
WEATHER_METRICS: dict[str, tuple[str, str, str]] = {
    "air_temperature": ("air-temperature", "air_temperature", "degC"),
    "rainfall": ("rainfall", "rainfall", "mm"),
    "wind_speed": ("wind-speed", "wind_speed", "knot"),
}


def _json(value: Any) -> Any:
    """A jsonb value that an older writer stored as a JSON string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _db_time(value: Any) -> Optional[datetime]:
    """A timestamptz column value in UTC; None when absent or unreadable.
    Every time in the response is UTC, whatever offset the source used
    (the database returns UTC, NEA payloads carry +08:00)."""
    if value is None:
        return None
    try:
        return parse_timestamp(value).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_time(value: Any) -> Optional[datetime]:
    """A time inside an NEA payload in UTC (no offset means Singapore)."""
    dt = _parse_nea_time(value)
    return dt.astimezone(timezone.utc) if dt is not None else None


def _range(block: Any, *path: str) -> tuple[Optional[float], Optional[float]]:
    for key in path:
        block = block.get(key) if isinstance(block, dict) else None
    if not isinstance(block, dict):
        return None, None
    return _number(block.get("low")), _number(block.get("high"))


def _forecast_text(value: Any) -> tuple[Optional[str], Optional[str]]:
    """(text, code) of an NEA forecast value: a string or {"code", "text"}."""
    if isinstance(value, str):
        return value, None
    if isinstance(value, dict):
        text, code = value.get("text"), value.get("code")
        return (text if isinstance(text, str) else None), (code if isinstance(code, str) else None)
    return None, None


# ---------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------
def _reading(channel: str, rows: list[dict], now: datetime) -> r.SensorReading:
    spellings, unit = CHANNELS[channel]
    candidates = [row for row in rows if row.get("sensor_type") in spellings]
    base: dict[str, Any] = {"channel": channel, "unit": unit, "sample_time": None, "sample_time_basis": "unknown"}
    if not candidates:
        return r.SensorReading.model_validate({
            **base, "status": "missing", "value": None, "sensor_type": None, "source_row_id": None,
            "ingested_at": None, "fresh_until": None, "note": "No reading has been stored."})
    row = max(candidates, key=lambda c: (_db_time(c.get("created_at")) or datetime.min.replace(tzinfo=now.tzinfo),
                                         int(c.get("id") or 0)))
    ingested = _db_time(row.get("created_at"))
    fresh_until = ingested + READING_FRESH_FOR if ingested is not None else None
    base.update(sensor_type=row.get("sensor_type"), source_row_id=row.get("id"), ingested_at=ingested,
                fresh_until=fresh_until)
    value = _number(row.get("value"))
    if value is None or (channel == "lux" and value < 0):
        note = ("The light sensor did not answer (the node stores -1)." if value is not None
                else "The stored value is not a number.")
        return r.SensorReading.model_validate({**base, "status": "invalid", "value": None, "note": note})
    if fresh_until is None or now > fresh_until:
        return r.SensorReading.model_validate({
            **base, "status": "stale", "value": value,
            "note": "No newer reading has arrived; the node may have stopped reporting."})
    return r.SensorReading.model_validate({**base, "status": "ok", "value": value, "note": None})


def build_readings(rows: list[dict], now: datetime) -> r.DashboardReadings:
    return r.DashboardReadings.model_validate({"fresh_for_seconds": int(READING_FRESH_FOR.total_seconds()),
                                               **{channel: _reading(channel, rows, now) for channel in CHANNELS}})


# ---------------------------------------------------------------------
# Weather now
# ---------------------------------------------------------------------
def _stations(stations: Any) -> dict[str, Optional[str]]:
    slots = _json(stations)
    slots = slots if isinstance(slots, dict) else {}

    def slot(key: str) -> Optional[str]:
        value = slots.get(key)
        return value if isinstance(value, str) and value else None

    return {"air_temperature": slot("air-temperature"), "rainfall": slot("rainfall"),
            "wind_speed": slot("wind-speed"), "two_hour_area": slot("two-hr-forecast"),
            "twenty_four_hour_region": slot("twenty-four-hr-forecast")}


def _weather_value(metric: str, station: Optional[str], telemetry: list[dict], now: datetime) -> r.WeatherValue:
    _, key, unit = WEATHER_METRICS[metric]
    base: dict[str, Any] = {"metric": metric, "unit": unit, "station_id": station}
    missing = {"status": "missing", "value": None, "observed_at": None, "ingested_at": None, "fresh_until": None}
    if station is None:
        return r.WeatherValue.model_validate({**base, **missing,
                                              "note": "The pond has no assigned station for this reading."})
    row = next((t for t in telemetry if t.get("station_id") == station and t.get("metric_type") == "realtime_sensor"
                and isinstance(_json(t.get("data")), dict) and key in _json(t.get("data"))), None)
    if row is None:
        return r.WeatherValue.model_validate({**base, **missing, "note": f"No cached reading from station {station}."})
    value = _number(_json(row["data"]).get(key))
    times = _json(row.get("source_times"))
    observed = parse_time(times.get(key)) if isinstance(times, dict) else None
    ingested = _db_time(row.get("updated_at"))
    reference = observed or ingested
    fresh_until = reference + WEATHER_FRESH_FOR if reference is not None else None
    note = None if observed is not None else "Observation time not recorded (cache row from before migration 0009)."
    times_known = {"observed_at": observed, "ingested_at": ingested, "fresh_until": fresh_until}
    if value is None:
        return r.WeatherValue.model_validate({**base, **times_known, "status": "missing", "value": None,
                                              "note": "The cached value is not a number."})
    status = "ok" if fresh_until is not None and now <= fresh_until else "stale"
    return r.WeatherValue.model_validate({**base, **times_known, "status": status, "value": value, "note": note})


def _uv_now(forecasts: list[dict], now: datetime) -> r.UvIndex:
    local = now.astimezone(zone(SINGAPORE))
    hour_start = local.replace(minute=0, second=0, microsecond=0)
    daylight = DAYLIGHT_HOURS[0] <= local.hour < DAYLIGHT_HOURS[1]
    for row in forecasts:
        if row.get("forecast_type") != "uv":
            continue
        period = _json(row.get("valid_period"))
        hour = parse_time(period.get("hour")) if isinstance(period, dict) else None
        if hour is None or hour != hour_start:
            continue
        data = _json(row.get("data"))
        value = _number(data.get("uv")) if isinstance(data, dict) else None
        if value is None:
            break
        return r.UvIndex(status="ok", value=value, hour_start=hour_start.astimezone(timezone.utc), daylight=daylight,
                         ingested_at=_db_time(row.get("updated_at")), note=None)
    if daylight:
        return r.UvIndex(status="missing", value=None, hour_start=hour_start.astimezone(timezone.utc), daylight=True,
                         ingested_at=None,
                         note="NEA has not reported the UV index for this hour.")
    return r.UvIndex(status="night_derived", value=0.0, hour_start=hour_start.astimezone(timezone.utc), daylight=False,
                     ingested_at=None,
                     note="Night: no UV report, shown as 0 by convention, not measured.")


# ---------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------
def _period(row: Optional[dict], now: datetime, general: bool = False):
    model = r.GeneralForecast if general else r.ForecastPeriod
    extra = dict.fromkeys(("temperature_low_c", "temperature_high_c", "humidity_low_pct", "humidity_high_pct",
                           "wind_low_kmh", "wind_high_kmh")) if general else {}
    if row is None:
        return model(status="missing", slot_id=None, text=None, code=None, valid_from=None, valid_to=None,
                     issued_at=None, ingested_at=None, **extra)
    data = _json(row.get("data"))
    data = data if isinstance(data, dict) else {}
    period = _json(row.get("valid_period"))
    period = period if isinstance(period, dict) else {}
    text, code = _forecast_text(data.get("forecast") if "forecast" in data else data)
    valid_from, valid_to = parse_time(period.get("start")), parse_time(period.get("end"))
    if general:
        extra["temperature_low_c"], extra["temperature_high_c"] = _range(data, "temperature")
        extra["humidity_low_pct"], extra["humidity_high_pct"] = _range(data, "relativeHumidity")
        extra["wind_low_kmh"], extra["wind_high_kmh"] = _range(data, "wind", "speed")
    status = "expired" if valid_to is not None and now >= valid_to else "ok"
    return model.model_validate({"status": status, "slot_id": row.get("slot_id"), "text": text, "code": code,
                                 "valid_from": valid_from, "valid_to": valid_to,
                                 "issued_at": _db_time(row.get("source_issued_at")),
                                 "ingested_at": _db_time(row.get("updated_at")), **extra})


def _outlook(forecasts: list[dict], now: datetime) -> list[r.OutlookDay]:
    today = now.astimezone(zone(SINGAPORE)).date()
    days: dict[date, r.OutlookDay] = {}
    for row in forecasts:
        if row.get("forecast_type") != "4day":
            continue
        data = _json(row.get("data"))
        data = data if isinstance(data, dict) else {}
        period = _json(row.get("valid_period"))
        stamp = parse_time((period or {}).get("timestamp") if isinstance(period, dict) else None) \
            or parse_time(data.get("timestamp"))
        if stamp is None:
            continue
        day = stamp.astimezone(zone(SINGAPORE)).date()
        if day < today:
            continue
        text, code = _forecast_text(data.get("forecast"))
        t_low, t_high = _range(data, "temperature")
        h_low, h_high = _range(data, "relativeHumidity")
        w_low, w_high = _range(data, "wind", "speed")
        weekday = data.get("day")
        candidate = r.OutlookDay(date=day, weekday=weekday if isinstance(weekday, str) else None, text=text,
                                 code=code, temperature_low_c=t_low, temperature_high_c=t_high,
                                 humidity_low_pct=h_low, humidity_high_pct=h_high, wind_low_kmh=w_low,
                                 wind_high_kmh=w_high, issued_at=_db_time(row.get("source_issued_at")),
                                 ingested_at=_db_time(row.get("updated_at")))
        current = days.get(day)
        if current is None or _issue_key(candidate) > _issue_key(current):
            days[day] = candidate
    return [days[d] for d in sorted(days)]


def _issue_key(day: r.OutlookDay) -> tuple:
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return (day.issued_at or floor, day.ingested_at or floor)


def build_forecast(forecasts: list[dict], stations: dict[str, Optional[str]], now: datetime) -> r.DashboardForecast:
    def find(kind: str, slot: Optional[str]) -> Optional[dict]:
        if slot is None:
            return None
        return next((f for f in forecasts if f.get("forecast_type") == kind and f.get("slot_id") == slot), None)

    return r.DashboardForecast(
        two_hour=_period(find("2hr", stations["two_hour_area"]), now),
        twenty_four_hour_regional=_period(find("24hr", stations["twenty_four_hour_region"]), now),
        twenty_four_hour_general=_period(find("24hr", "GENERAL"), now, general=True),
        outlook=_outlook(forecasts, now),
    )


# ---------------------------------------------------------------------
# The whole response
# ---------------------------------------------------------------------
def build_dashboard(*, pond_id: int, sources: dict, chemistry: Optional[dict], evaporation: Optional[dict],
                    algae: Optional[dict], aeration: Optional[bool], hypoxia_thresholds: HypoxiaThresholds,
                    now: datetime) -> r.Dashboard:
    stations = _stations(sources.get("stations"))
    readings = build_readings(list(sources.get("readings") or []), now)
    telemetry = list(sources.get("telemetry") or [])
    forecasts = list(sources.get("forecasts") or [])

    def fresh(reading: r.SensorReading) -> Optional[float]:
        return reading.value if reading.status == "ok" else None

    hypoxia = assess_hypoxia(lux=fresh(readings.lux), water_temp_c=fresh(readings.water_temp), aeration=aeration,
                             algae_high=algae_is_high(algae), thresholds=hypoxia_thresholds).to_dict()
    return r.Dashboard(
        pond_id=pond_id,
        stations=r.StationAssignment(**stations),
        readings=readings,
        assessments=r.DashboardAssessments(
            chemistry=r.WaterChemistryAssessment.model_validate(chemistry) if chemistry else None,
            evaporation=r.EvaporationAssessment.model_validate(evaporation) if evaporation else None,
            algae=r.AlgaeAssessment.model_validate(algae) if algae else None,
            hypoxia=r.HypoxiaFlag.model_validate(hypoxia),
        ),
        next_actions=[],
        weather=r.WeatherNow(
            air_temperature=_weather_value("air_temperature", stations["air_temperature"], telemetry, now),
            rainfall=_weather_value("rainfall", stations["rainfall"], telemetry, now),
            wind_speed=_weather_value("wind_speed", stations["wind_speed"], telemetry, now),
            uv_index=_uv_now(forecasts, now),
        ),
        forecast=build_forecast(forecasts, stations, now),
    )


__all__ = ["CHANNELS", "DAYLIGHT_HOURS", "READING_FRESH_FOR", "WEATHER_FRESH_FOR", "WEATHER_METRICS",
           "build_dashboard", "build_forecast", "build_readings"]
