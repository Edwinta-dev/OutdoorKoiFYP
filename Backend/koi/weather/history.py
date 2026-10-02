"""As-of reads over weather history, for the model consumers.

Pure functions over rows shaped like weather_observation and
weather_forecast_issuance (migration 0009). The database functions
weather_forecast_as_of, weather_observations_as_of and
weather_rainfall_total apply the same rules; MemoryStorage uses these.

As-of rules
- A forecast is usable at T when its available_at (provider issue or
  revision time, else fetch time) is not after T. A forecast whose issue
  time is unknown is therefore never used for a time before it was
  fetched. Selection is the newest by available_at, then issued_at
  (unknown last), fetched_at and id, so it is deterministic.
- An observation is usable at T when observed_to is not after T.

Rainfall
- Only interval totals in mm are summed. Intervals are taken in order of
  observed_from; one overlapping an interval already counted is skipped,
  so repeated or overlapping snapshots are never added twice. Cumulative
  totals and rates are never summed.
- Only intervals wholly inside the window count. Uncovered time is
  reported as missing intervals; with no data total_mm is None and the
  status is "no_data", never 0 mm.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from koi.models.local_time import DEFAULT_POND_TIME_ZONE, zone
from koi.weather.cache import available_at
from koi.weather.nea import instant, iso

# Singapore daylight hours in which NEA publishes UV readings; outside
# them a missing reading is expected, inside them it is missing data.
UV_DAY_START_HOUR = 7
UV_DAY_END_HOUR = 19


def _t(value: object) -> datetime:
    return instant(value)


def select_forecast_as_of(issuances: Iterable[dict], as_of: datetime,
                          valid_at: Optional[datetime] = None) -> Optional[dict]:
    """The issuance usable at as_of (with valid_at, only those valid then),
    or None. Rows carry available_at, or it is derived."""
    usable = []
    for row in issuances:
        avail = _t(row.get("available_at") or available_at(row))
        if avail > as_of:
            continue
        if valid_at is not None and not (_t(row["valid_from"]) <= valid_at < _t(row["valid_to"])):
            continue
        usable.append((avail, row))
    if not usable:
        return None

    def key(item: tuple[datetime, dict]) -> tuple:
        avail, row = item
        issued = row.get("issued_at")
        return (avail, issued is not None, _t(issued) if issued else avail, _t(row["fetched_at"]), row.get("id") or 0)

    avail, row = max(usable, key=key)
    return {**row, "available_at": iso(avail)}


def observations_as_of(observations: Iterable[dict], station_id: str, metric: str, start: datetime,
                       end: datetime, as_of: datetime) -> list[dict]:
    """Station observations of metric inside [start, end] observed by as_of,
    oldest first."""
    rows = [o for o in observations
            if o.get("series", "station") == "station" and o["station_id"] == station_id and o["metric"] == metric
            and _t(o["observed_from"]) >= start and _t(o["observed_to"]) <= end and _t(o["observed_to"]) <= as_of]
    return sorted(rows, key=lambda o: (_t(o["observed_to"]), _t(o["observed_from"]), o.get("id") or 0))


def rainfall_total(observations: Iterable[dict], station_id: str, start: datetime, end: datetime,
                   as_of: Optional[datetime] = None) -> dict:
    """Rainfall at one station over [start, end] with its coverage; the
    same output as weather_rainfall_total."""
    if end <= start:
        raise ValueError("end must be after start")
    limit = min(end, as_of) if as_of is not None else end
    rows = sorted(
        (o for o in observations
         if o.get("series", "station") == "station" and o["station_id"] == station_id
         and o["metric"] == "rainfall" and o["semantics"] == "interval_total" and o["unit"] == "mm"
         and _t(o["observed_from"]) >= start and _t(o["observed_to"]) <= limit),
        key=lambda o: (_t(o["observed_from"]), _t(o["observed_to"]), o.get("id") or 0))
    cursor, total, covered, count, skipped = start, 0.0, 0.0, 0, 0
    missing: list[dict] = []
    for o in rows:
        lo, hi = _t(o["observed_from"]), _t(o["observed_to"])
        if lo < cursor:
            skipped += 1
            continue
        if lo > cursor:
            missing.append({"from": iso(cursor), "to": iso(lo)})
        total += float(o["value"])
        covered += (hi - lo).total_seconds()
        count += 1
        cursor = hi
    if cursor < end:
        missing.append({"from": iso(cursor), "to": iso(end)})
    window = (end - start).total_seconds()
    status = "no_data" if count == 0 else ("complete" if covered >= window else "partial")
    return {"station_id": station_id, "from": iso(start), "to": iso(end),
            "as_of": iso(as_of) if as_of is not None else None,
            "total_mm": round(total, 3) if count else None, "unit": "mm", "window_seconds": window,
            "covered_seconds": covered, "coverage": round(covered / window, 6), "intervals": count,
            "overlapping_skipped": skipped, "missing": missing, "status": status}


def local_day_window(day: date, time_zone: str = DEFAULT_POND_TIME_ZONE) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) of day as aware instants."""
    tz = zone(time_zone)
    start = datetime.combine(day, time(0), tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)


def uv_reading_status(observations: Iterable[dict], at: datetime,
                      time_zone: str = DEFAULT_POND_TIME_ZONE) -> dict:
    """The newest hourly UV reading published by `at` and less than an
    hour old. Without one, status is "night" outside the publishing hours
    (no reading expected) or "missing" inside them; value is None in both
    cases, never a substituted 0."""
    current = None
    for o in observations:
        if o.get("metric") != "uv_index":
            continue
        end = _t(o["observed_to"])
        if end <= at < end + timedelta(hours=1) and (current is None or end > _t(current["observed_to"])):
            current = o
    if current is not None:
        return {"value": current["value"], "status": "measured", "observed_from": current["observed_from"],
                "observed_to": current["observed_to"], "fetched_at": current.get("fetched_at")}
    hour = at.astimezone(zone(time_zone)).hour
    status = "missing" if UV_DAY_START_HOUR <= hour < UV_DAY_END_HOUR else "night"
    return {"value": None, "status": status, "observed_from": None, "observed_to": None, "fetched_at": None}
