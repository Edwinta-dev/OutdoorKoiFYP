"""
forecast_utils.py

Shared helpers for turning the NEA forecast blob (the "nea_forecasts" key
inside get_bundled_dashboard_payload's JSON) into inputs the chemistry
engine can use - both for the poller's "is rain incoming right now" check
and for project_forward's day-by-day forward assumptions.

Kept separate from poller.py/app.py so both share exactly one
implementation instead of poller.py's rain-detection logic silently
drifting from whatever the forecast endpoint ends up doing.
"""
from __future__ import annotations

# Rough cloud-cover discount applied to the historical average lux
# baseline on a forecast day, keyed off the NEA outlook's forecast code.
# NEA doesn't forecast light directly, so this is a coarse estimate, not
# a measurement - the further out a projection runs through several
# thundery/cloudy days, the less confidence to place in the NO3-uptake
# side of it specifically (the temperature side is still a real forecast).
_CLOUD_LUX_MULTIPLIER = {
    "TL": 0.5,   # Thundery Showers
    "SH": 0.6,   # Showers
    "LR": 0.6,   # Light Rain
    "RA": 0.55,  # Rain
    "CL": 0.8,   # Cloudy
    "PC": 0.9,   # Partly Cloudy
    "WD": 0.95,  # Windy (usually fair)
    "FA": 1.0,   # Fair (Day)
    "FN": 1.0,   # Fair (Night)
    "HZ": 0.9,   # Hazy
}
_DEFAULT_LUX_MULTIPLIER = 0.85  # unrecognised code - assume mild cloud cover


def rain_context_from_text(text: str | None) -> tuple[bool, str]:
    """Same heuristic poller.py used inline before this was pulled out -
    2hr-nowcast-style text -> (rain_incoming, intensity)."""
    text = (text or "").lower()
    if "thundery" in text or "heavy" in text:
        return True, "heavy"
    if "shower" in text or "rain" in text:
        return True, "moderate"
    return False, "unknown"


def today_rain_context(payload: dict) -> tuple[bool, str]:
    """Looks at the 2hr nowcast first (most reliable near-term signal),
    falls back to today's slot in the 4-day outlook. Identical logic to
    poller.py's original _rain_context_from_forecast - poller.py now
    calls this instead of keeping its own copy."""
    nea = payload.get("nea_forecasts") or {}
    forecast_2hr = nea.get("forecast_2hr", {})
    incoming, intensity = rain_context_from_text(forecast_2hr.get("forecast"))
    if incoming:
        return incoming, intensity

    outlook = nea.get("outlook_4day", [])
    if outlook:
        today_text = outlook[0].get("data", {}).get("forecast", {}).get("text")
        return rain_context_from_text(today_text)
    return False, "unknown"


def _midpoint(block: dict | None) -> float | None:
    """NEA reports temperature and relativeHumidity as {low, high} bands.
    Midpoint is the usable single value for a daily projection."""
    if not isinstance(block, dict):
        return None
    low, high = block.get("low"), block.get("high")
    if low is None or high is None:
        return None
    return (low + high) / 2.0


def _wind_speed_ms(wind: dict | None) -> float | None:
    """NEA wind speed comes as {speed: {low, high}, direction} in KNOTS.
    Returns the midpoint converted to m/s (1 knot = 0.514444 m/s), which
    is what the Penman wind function in evaporation_engine expects."""
    if not isinstance(wind, dict):
        return None
    mid_knots = _midpoint(wind.get("speed"))
    if mid_knots is None:
        return None
    return mid_knots * 0.514444


def daily_environment_from_outlook(outlook_4day: list[dict]) -> list[dict]:
    """Extracts the full per-day environmental picture the evaporation and
    algae engines need from NEA's 4-day outlook.

    Returns one dict per outlook entry with air_temp_c, humidity_pct,
    wind_ms, rain_category and lux_multiplier. Any field NEA omits comes
    back as None so the caller can substitute its own historical baseline
    rather than silently receiving a fabricated number.
    """
    days = []
    for entry in outlook_4day or []:
        data = entry.get("data", {}) if isinstance(entry, dict) else {}
        forecast = data.get("forecast") or {}
        code = (forecast.get("code") or "").upper()
        rain_incoming, rain_intensity = rain_context_from_text(
            forecast.get("text") or forecast.get("summary")
        )

        days.append({
            "air_temp_c": _midpoint(data.get("temperature")),
            "humidity_pct": _midpoint(data.get("relativeHumidity")),
            "wind_ms": _wind_speed_ms(data.get("wind")),
            "rain_incoming": rain_incoming,
            "rain_category": rain_intensity if rain_incoming else "unknown",
            "lux_multiplier": _CLOUD_LUX_MULTIPLIER.get(code, _DEFAULT_LUX_MULTIPLIER),
            "forecast_code": code,
            "forecast_text": forecast.get("text"),
            "day_label": data.get("day"),
        })
    return days


def current_conditions(payload: dict) -> dict:
    """Pulls the live 'right now' environmental readings out of the
    bundled dashboard payload - used as the fallback for any field the
    4-day outlook doesn't carry, and as the 'recent conditions' anchor
    for the algae engine's intrinsic-rate back-calculation."""
    telemetry = payload.get("nea_telemetry") or {}
    raw = payload.get("raw_sensor") or {}

    def _val(block):
        if isinstance(block, dict):
            return block.get("value")
        return block

    wind_knots = _val(telemetry.get("wind_speed"))
    general = ((payload.get("nea_forecasts") or {}).get("forecast_24hr") or {}).get("general") or {}

    return {
        "air_temp_c": _val(telemetry.get("air_temp")),
        "rainfall_mm": _val(telemetry.get("rainfall")),
        # nea_telemetry wind is in knots like the forecast blocks.
        "wind_ms": (wind_knots * 0.514444) if wind_knots is not None else None,
        "humidity_pct": _midpoint(general.get("relativeHumidity")),
        "water_temp_c": raw.get("temp"),
        "lux": raw.get("LUX"),
        "tds": raw.get("TDS"),
        "ph": raw.get("pH"),
    }


def daily_forecasts_from_outlook(outlook_4day: list[dict]) -> dict:
    """Turns NEA's outlook_4day list into parallel per-day lists usable by
    WaterChemistryEngine.project_forward:

      temp_c          - midpoint of that day's low/high, or None if the
                         entry is missing temperature data
      lux_multiplier  - cloud-cover discount to apply to a historical
                         average lux baseline (NEA has no lux forecast)
      rain            - (rain_incoming, intensity) tuple for that day

    Each list is in the same order as the outlook entries (typically
    tomorrow .. +4 days), so index 0 corresponds to project_forward's
    day_index 0 (i.e. "1 day from now").
    """
    temps: list[float | None] = []
    lux_multipliers: list[float] = []
    rain: list[tuple[bool, str]] = []

    for entry in outlook_4day or []:
        data = entry.get("data", {}) if isinstance(entry, dict) else {}

        temp = data.get("temperature") or {}
        low, high = temp.get("low"), temp.get("high")
        temps.append((low + high) / 2.0 if low is not None and high is not None else None)

        forecast = data.get("forecast") or {}
        code = (forecast.get("code") or "").upper()
        lux_multipliers.append(_CLOUD_LUX_MULTIPLIER.get(code, _DEFAULT_LUX_MULTIPLIER))

        rain.append(rain_context_from_text(forecast.get("text") or forecast.get("summary")))

    return {"temp_c": temps, "lux_multiplier": lux_multipliers, "rain": rain}