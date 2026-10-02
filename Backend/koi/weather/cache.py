"""The latest-cache rows (weather_telemetry, weather_forecasts) built from
one live fetch, in the exact keys and JSON get_bundled_dashboard_payload
reads, and the rules that keep an older value out of the cache.

Cache contract (docs/database-reconciliation.md, weather cache shapes):
  weather_telemetry  (station_id, 'realtime_sensor'), data
                     {"air_temperature", "rainfall", "wind_speed"}
  weather_forecasts  '2hr'  slot = area name, data {"forecast": text},
                            valid_period {"start", "end"}
                     '24hr' slot 'GENERAL', data = the general block
                            without validPeriod; slots 'REG_<REGION>',
                            data {"code", "text"} of the period in force
                     '4day' slot = weekday (MON..SUN), data = the outlook
                            day, valid_period {"timestamp"}
                     'uv'   slot 'HH:00' (Singapore hour), data {"uv"},
                            valid_period {"hour"}

apply_telemetry_candidate and apply_forecast_candidate are the Python
form of the guards in ingest_weather_batch (migration 0009), used by
MemoryStorage; tests/sql checks the SQL form against the same cases.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from koi.models.local_time import zone
from koi.weather.nea import OBSERVATION_PRODUCTS, REPORTING_INTERVAL, SINGAPORE, Parsed, instant, iso

_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _sg(value: str) -> datetime:
    return instant(value).astimezone(zone(SINGAPORE))


def available_at(issuance: dict) -> str:
    """The issuance's provider time (latest of issued and revised), else
    its fetch time; the same rule as weather_forecast_issuance.available_at."""
    times = [instant(issuance[k]) for k in ("issued_at", "source_updated_at") if issuance.get(k)]
    return iso(max(times)) if times else iso(instant(issuance["fetched_at"]))


def telemetry_candidates(parsed: Parsed) -> list[dict]:
    """The latest reading of each cached metric at each station."""
    spec = OBSERVATION_PRODUCTS.get(parsed.product)
    if spec is None or spec.cache_key is None:
        return []
    latest: dict[str, dict] = {}
    for obs in parsed.observations:
        if obs["semantics"] == "cumulative_total":
            continue
        current = latest.get(obs["station_id"])
        if current is None or instant(obs["observed_to"]) > instant(current["observed_to"]):
            latest[obs["station_id"]] = obs
    return [{"station_id": sid, "metric": spec.cache_key, "value": obs["value"], "observed_at": obs["observed_to"],
             "valid_until": iso(instant(obs["observed_to"]) + REPORTING_INTERVAL)}
            for sid, obs in sorted(latest.items())]


def uv_candidates(parsed: Parsed) -> list[dict]:
    """One 'uv' row per Singapore hour of the newest readings."""
    by_slot: dict[str, dict] = {}
    for obs in parsed.observations:
        hour = _sg(obs["observed_to"])
        slot = hour.strftime("%H:00")
        current = by_slot.get(slot)
        if current is None or instant(obs["observed_to"]) > instant(current["source_time"]):
            by_slot[slot] = {"forecast_type": "uv", "slot_id": slot, "data": {"uv": obs["value"]},
                             "valid_period": {"hour": hour.isoformat()}, "source_time": obs["observed_to"]}
    return [by_slot[k] for k in sorted(by_slot)]


def _latest_issuance(rows: list[dict]) -> list[dict]:
    """The rows of the newest issuance among rows (by available_at)."""
    if not rows:
        return []
    newest = max(instant(available_at(r)) for r in rows)
    return [r for r in rows if instant(available_at(r)) == newest]


def _window(row: dict) -> dict:
    return {"start": _sg(row["valid_from"]).isoformat(), "end": _sg(row["valid_to"]).isoformat()}


def forecast_candidates(parsed: Parsed, fetched_at: datetime) -> list[dict]:
    out: list[dict] = []
    rows = parsed.forecasts
    if parsed.product == "two-hr-forecast":
        by_area: dict[str, list[dict]] = {}
        for r in rows:
            by_area.setdefault(r["slot_id"], []).append(r)
        for area in sorted(by_area):
            for r in _latest_issuance(by_area[area])[:1]:
                out.append({"forecast_type": "2hr", "slot_id": area, "data": r["payload"],
                            "valid_period": _window(r), "source_time": available_at(r)})
    elif parsed.product == "twenty-four-hr-forecast":
        latest = _latest_issuance(rows)
        for r in latest:
            if r["slot_id"] == "GENERAL":
                out.append({"forecast_type": "24hr", "slot_id": "GENERAL", "data": r["payload"],
                            "valid_period": _window(r), "source_time": available_at(r)})
        regions = sorted({r["slot_id"] for r in latest if r["slot_id"] != "GENERAL"})
        for region in regions:
            periods = sorted((r for r in latest if r["slot_id"] == region),
                             key=lambda r: instant(r["valid_from"]))
            chosen = _period_in_force(periods, fetched_at)
            out.append({"forecast_type": "24hr", "slot_id": region, "data": chosen["payload"],
                        "valid_period": _window(chosen), "source_time": available_at(chosen)})
    elif parsed.product == "four-day-outlook":
        for r in sorted(_latest_issuance(rows), key=lambda r: r["valid_from"]):
            day = _sg(r["valid_from"])
            stamp = r["payload"].get("timestamp") or day.isoformat()
            out.append({"forecast_type": "4day", "slot_id": _WEEKDAYS[day.weekday()], "data": r["payload"],
                        "valid_period": {"timestamp": stamp}, "source_time": available_at(r)})
    return out


def _period_in_force(periods: list[dict], at: datetime) -> dict:
    """The period containing at, else the next one to start, else the last."""
    for r in periods:
        if instant(r["valid_from"]) <= at < instant(r["valid_to"]):
            return r
    upcoming = [r for r in periods if instant(r["valid_from"]) > at]
    return upcoming[0] if upcoming else periods[-1]


# ---------------------------------------------------------------------
# Guards (mirror ingest_weather_batch)
# ---------------------------------------------------------------------
def apply_telemetry_candidate(row: Optional[dict], cand: dict, now: str) -> Optional[dict]:
    """The weather_telemetry row after cand, or None to leave it as is."""
    observed = instant(cand["observed_at"])
    if row is None:
        return {"station_id": cand["station_id"], "metric_type": "realtime_sensor",
                "data": {cand["metric"]: cand["value"]}, "valid_start": cand["observed_at"],
                "valid_end": cand["valid_until"], "updated_at": now,
                "source_times": {cand["metric"]: cand["observed_at"]}}
    times = row.get("source_times")
    prev = (times or {}).get(cand["metric"])
    if prev is not None and observed <= instant(prev):
        return None
    if times is None and observed < instant(row["valid_start"]):
        return None
    new_times = {**(times or {}), cand["metric"]: cand["observed_at"]}
    valid_end = max(instant(row["valid_end"]), instant(cand["valid_until"]))
    return {**row, "data": {**row["data"], cand["metric"]: cand["value"]}, "source_times": new_times,
            "valid_start": iso(min(instant(t) for t in new_times.values())),
            "valid_end": iso(valid_end), "updated_at": now}


def apply_forecast_candidate(row: Optional[dict], cand: dict, newest_4day: Optional[str],
                             now: str) -> Optional[dict]:
    """The weather_forecasts row after cand, or None to leave it as is.
    newest_4day is the newest source_issued_at of any '4day' row."""
    source = instant(cand["source_time"])
    if cand["forecast_type"] == "4day" and newest_4day is not None and source < instant(newest_4day):
        return None
    if row is not None:
        issued = row.get("source_issued_at")
        if issued is not None and source <= instant(issued):
            return None
        if issued is None and row.get("updated_at") is not None and source < instant(row["updated_at"]):
            return None
    return {"forecast_type": cand["forecast_type"], "slot_id": cand["slot_id"], "data": cand["data"],
            "valid_period": cand["valid_period"], "updated_at": now, "source_issued_at": cand["source_time"]}
