"""The forward environments and projection call shared by the API and the
poller, so a forecast the app asks for and the figure the poller caches
are built the same way."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from koi.logs import log_event
from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev

log = logging.getLogger(__name__)

# How far ahead the evaluation rows project the "days until next
# intervention" figure. The detail screens ask for a longer horizon on
# demand via /forecast/*.
CACHED_HORIZON_DAYS = 21

DEFAULT_AIR_TEMP_C = 30.0
DEFAULT_HUMIDITY_PCT = 80.0
DEFAULT_WIND_MS = 2.0


def first_not_none(*values):
    for v in values:
        if v is not None:
            return v
    return None


def evaporation_environment(forecast_days: list, air_temp, humidity, wind_ms) -> list:
    """One DayEnvironment per forecast day, substituting the live air
    temperature, humidity and wind (then the defaults) for any field the
    outlook omits. With no forecast days, one day of the live values."""
    air = float(first_not_none(air_temp, DEFAULT_AIR_TEMP_C))
    hum = float(first_not_none(humidity, DEFAULT_HUMIDITY_PCT))
    wind = float(first_not_none(wind_ms, DEFAULT_WIND_MS))
    env = [
        ev.DayEnvironment(
            air_temp_c=float(first_not_none(d.get("air_temp_c"), air)),
            relative_humidity_pct=float(first_not_none(d.get("humidity_pct"), hum)),
            wind_speed_ms=float(first_not_none(d.get("wind_ms"), wind)),
            rain_category=d.get("rain_category", "unknown"),
        )
        for d in forecast_days
    ]
    return env or [ev.DayEnvironment(air_temp_c=air, relative_humidity_pct=hum, wind_speed_ms=wind)]


def algae_environment(forecast_days: list, baseline_lux, baseline_temp, no3_series: list, no3_now) -> list:
    """NEA has no light forecast, so the pond's own current lux is scaled
    by a cloud-cover multiplier derived from each day's forecast code."""
    env = [
        ae.AlgaeDayEnvironment(
            lux=baseline_lux * d.get("lux_multiplier", 0.85),
            temp_c=float(first_not_none(d.get("air_temp_c"), baseline_temp)),
            no3_ppm=no3_series[i] if i < len(no3_series) else no3_now,
        )
        for i, d in enumerate(forecast_days)
    ]
    return env or [ae.AlgaeDayEnvironment(lux=baseline_lux, temp_c=baseline_temp, no3_ppm=no3_now)]


def days_from_projection(project: Callable[[], dict], key: str, failed_event: str, **fields: Any) -> Optional[Any]:
    """project()[key] (e.g. predicted_topup_days_from_now), or None, with a
    warning logged, when the projection raises: a failed projection leaves
    the figure unknown rather than failing the assessment."""
    try:
        return project().get(key)
    except Exception as exc:  # noqa: BLE001 - the projection is optional
        log_event(log, failed_event, level=logging.WARNING, error=str(exc), **fields)
        return None
