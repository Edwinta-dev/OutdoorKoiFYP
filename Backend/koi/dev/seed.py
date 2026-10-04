"""The demo seed (Backend/fixtures/demo_pond/seed.json): loading it, moving
it to the current time, and reading it as of a past instant.

shift() moves every time in the seed so the seed's anchor (its newest
sensor reading) lands on `now`: the pond rows by exactly now - anchor, the
weather cache rows by whole hours (so the UV report of the anchor's hour
becomes the report of the current hour), with the weekday and hour labels
that NEA derives from those times rewritten to match. It then adds the
daily_sensor_averages rows that aggregate_daily_sensor_data would have
written for the shifted readings.

SeedHistory answers the reads the poller makes (the bundled dashboard
payload, camera frames, feeds) as they stood at a past instant, for the
fast-forward in koi/dev/replay.py.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from koi.models.engine import EventKind, PondEvent
from koi.models.local_time import zone
from koi.storage.base import IMAGE_COLUMNS, INTERVENTION_COLUMNS, parse_timestamp
from koi.weather.nea import SINGAPORE

SEED_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "demo_pond" / "seed.json"

# Shifted by whole hours; every other table by the exact offset.
WEATHER_TABLES = ("weather_telemetry", "weather_forecasts")

_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# pondInterventions.event_type -> the engine's event kind.
EVENT_KINDS = {"FEEDING": EventKind.FEEDING, "WATER_CHANGE": EventKind.WATER_CHANGE,
               "WATER_TOPUP": EventKind.TOP_UP, "ALGAE_SCRUB": EventKind.ALGAL_SCRUB}
FEEDING_COLUMNS = ("food_grams", "protein_percentage", "event_timestamp")


def load(path: str | Path = SEED_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _local_date(at: datetime) -> date:
    return at.astimezone(zone(SINGAPORE)).date()


def _shift_value(value: Any, delta: timedelta, days: int) -> Any:
    if isinstance(value, dict):
        return {k: _shift_value(v, delta, days) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift_value(v, delta, days) for v in value]
    if isinstance(value, str):
        if _DATETIME.match(value):
            return (parse_timestamp(value) + delta).isoformat()
        if _DATE.match(value):
            return (date.fromisoformat(value) + timedelta(days=days)).isoformat()
    return value


def _hour(at: datetime) -> datetime:
    return at.replace(minute=0, second=0, microsecond=0)


def shift(seed: dict, now: datetime) -> dict:
    """A copy of seed with its anchor moved to now (see the module
    docstring). seed["anchor"] becomes now."""
    anchor = parse_timestamp(seed["anchor"])
    now = now.astimezone(timezone.utc)
    offsets = {
        "rows": (now - anchor, (_local_date(now) - _local_date(anchor)).days),
        "weather": (_hour(now) - _hour(anchor),
                    (_local_date(anchor + (_hour(now) - _hour(anchor))) - _local_date(anchor)).days),
    }
    tables: dict[str, list[dict]] = {}
    for name, rows in seed["tables"].items():
        delta, days = offsets["weather" if name in WEATHER_TABLES else "rows"]
        tables[name] = [_shift_value(row, delta, days) for row in rows]
    for row in tables.get("weather_forecasts", []):
        _relabel_forecast(row)
    tables["daily_sensor_averages"] = daily_averages(tables.get("SensorData", []))
    return {**seed, "anchor": now.isoformat(), "tables": tables}


def _relabel_forecast(row: dict) -> None:
    """Rewrites the labels NEA derives from a forecast's time: the 4-day
    outlook's weekday slot and day name, and the UV report's hour slot."""
    value = row.get("valid_period")
    period: dict = value if isinstance(value, dict) else {}
    if row.get("forecast_type") == "4day" and period.get("timestamp"):
        day = parse_timestamp(period["timestamp"]).astimezone(zone(SINGAPORE))
        row["slot_id"] = _WEEKDAYS[day.weekday()]
        if isinstance(row.get("data"), dict) and "day" in row["data"]:
            row["data"]["day"] = _WEEKDAY_NAMES[day.weekday()]
    elif row.get("forecast_type") == "uv" and period.get("hour"):
        row["slot_id"] = parse_timestamp(period["hour"]).astimezone(zone(SINGAPORE)).strftime("%H:00")


def daily_averages(sensor_rows: list[dict]) -> list[dict]:
    """daily_sensor_averages rows for sensor_rows: one per pond, sensor
    type and Singapore day, as aggregate_daily_sensor_data writes them.
    Rows with no pond are left out."""
    groups: dict[tuple[int, str, date], list[float]] = {}
    for row in sensor_rows:
        if row.get("userID") is None or row.get("sensor_type") is None or row.get("data1") is None:
            continue
        key = (int(row["userID"]), str(row["sensor_type"]), _local_date(parse_timestamp(row["created_at"])))
        groups.setdefault(key, []).append(float(row["data1"]))
    return [{"id": n, "userid": pond, "sensor_type": sensor, "avg_value": round(sum(v) / len(v), 4),
             "min_value": min(v), "max_value": max(v), "record_date": day.isoformat()}
            for n, ((pond, sensor, day), v) in enumerate(sorted(groups.items()), 1)]


def _stations(user: Optional[dict]) -> dict:
    value = (user or {}).get("ClosestStations")
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


class SeedHistory:
    """The seed's rows read as of a past instant."""

    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables

    def _rows(self, table: str, column: str, pond: int) -> list[dict]:
        return [r for r in self.tables.get(table, []) if str(r.get(column)) == str(pond)]

    def sensor_rows(self, pond: int) -> list[dict]:
        """The pond's SensorData rows, ordered by created_at, then id."""
        return sorted(self._rows("SensorData", "userID", pond),
                      key=lambda r: (parse_timestamp(r["created_at"]), int(r["id"])))

    def poll_times(self, pond: int) -> list[datetime]:
        """Each distinct time the pond's node reported, oldest first."""
        return sorted({parse_timestamp(r["created_at"]) for r in self._rows("SensorData", "userID", pond)})

    def events(self, pond: int) -> list[PondEvent]:
        """The pond's logged interventions as engine events, oldest first."""
        events = []
        for r in self._rows("pondInterventions", "userID", pond):
            kind = EVENT_KINDS.get(r.get("event_type") or "")
            if kind is None:
                continue
            events.append(PondEvent(
                kind=kind, time=parse_timestamp(r["event_timestamp"]),
                volume_percent=r.get("volume_percentage"), volume_litres=r.get("volume_litres"),
                scrub_type=r.get("algae_method"), food_grams=r.get("food_grams"),
                protein_percent=r.get("protein_percentage")))
        return sorted(events, key=lambda e: e.time)

    def interventions(self, pond: int, at: datetime, since: Optional[datetime] = None) -> list[dict]:
        """pondInterventions rows timed by at (and at or after since),
        ordered by event_timestamp, then id (fetch_interventions)."""
        rows = [r for r in self._rows("pondInterventions", "userID", pond)
                if parse_timestamp(r["event_timestamp"]) <= at
                and (since is None or parse_timestamp(r["event_timestamp"]) >= since)]
        rows.sort(key=lambda r: (parse_timestamp(r["event_timestamp"]), int(r["id"])))
        return [{c: r.get(c) for c in INTERVENTION_COLUMNS} for r in rows]

    def images(self, pond: int, at: datetime, limit: int = 200) -> list[dict]:
        """imageTable rows stored by at, newest first (fetch_image_history)."""
        rows = [(i, r) for i, r in enumerate(self._rows("imageTable", "user_ID", pond))
                if parse_timestamp(r["created_at"]) <= at]
        rows.sort(key=lambda item: (parse_timestamp(item[1]["created_at"]), item[0]), reverse=True)
        return [{c: r.get(c) for c in IMAGE_COLUMNS} for _, r in rows[:limit]]

    def feeds(self, pond: int, at: datetime, limit: int = 30) -> list[dict]:
        """FEEDING rows timed by at, newest first (fetch_recent_feeding_events)."""
        rows = [r for r in self._rows("pondInterventions", "userID", pond)
                if r.get("event_type") == "FEEDING" and parse_timestamp(r["event_timestamp"]) <= at]
        rows.sort(key=lambda r: parse_timestamp(r["event_timestamp"]), reverse=True)
        return [{c: r.get(c) for c in FEEDING_COLUMNS} for r in rows[:limit]]

    def payload(self, pond: int, at: datetime) -> dict:
        """What get_bundled_dashboard_payload returns for the pond at `at`:
        the newest reading of each sensor type stored by then, and the
        weather caches of the pond's assigned stations and areas.
        telemetry_history is left empty; the services do not read it."""
        users = self._rows("UserData", "userID", pond)
        stations = _stations(users[0] if users else None)

        latest: dict[str, dict] = {}
        for row in self._rows("SensorData", "userID", pond):
            sensor_type, created = row.get("sensor_type"), parse_timestamp(row["created_at"])
            if sensor_type is None or created > at:
                continue
            current = latest.get(sensor_type)
            if current is None or (created, row["id"]) > (parse_timestamp(current["created_at"]), current["id"]):
                latest[sensor_type] = row

        telemetry_rows = self.tables.get("weather_telemetry", [])

        def telemetry(slot: str, key: str) -> dict:
            station = stations.get(slot)
            row = next((r for r in telemetry_rows if station is not None and r.get("station_id") == station
                        and isinstance(r.get("data"), dict) and key in r["data"]), None)
            return {"value": row["data"][key]} if row else {}

        forecasts = self.tables.get("weather_forecasts", [])

        def forecast(kind: str, slot: Optional[str]) -> dict:
            row = next((r for r in forecasts if slot is not None and r.get("forecast_type") == kind
                        and r.get("slot_id") == slot), None)
            return row["data"] if row else {}

        outlook = sorted((r for r in forecasts if r.get("forecast_type") == "4day"),
                         key=lambda r: parse_timestamp(r.get("updated_at") or "1970-01-01T00:00:00+00:00"),
                         reverse=True)[:4]
        outlook.sort(key=lambda r: parse_timestamp(r["valid_period"]["timestamp"]))
        local = at.astimezone(zone(SINGAPORE))
        uv = next((r for r in forecasts if r.get("forecast_type") == "uv"
                   and r.get("slot_id") == local.strftime("%H:00")), None)
        uv_index = ({"slot_id": uv["slot_id"], "data": uv["data"], "valid_period": uv["valid_period"]} if uv else
                    {"slot_id": None, "data": {"value": 0, "unit": "UV Index", "status": "Nighttime (Inactive)"},
                     "valid_period": {"timestamp": local.replace(microsecond=0).isoformat(), "is_daylight": False}})
        return {
            "raw_sensor": {t: r.get("data1") for t, r in sorted(latest.items())},
            "telemetry_history": [],
            "nea_telemetry": {"air_temp": telemetry("air-temperature", "air_temperature"),
                              "rainfall": telemetry("rainfall", "rainfall"),
                              "wind_speed": telemetry("wind-speed", "wind_speed")},
            "nea_forecasts": {
                "forecast_2hr": forecast("2hr", stations.get("two-hr-forecast")),
                "forecast_24hr": {"regional": forecast("24hr", stations.get("twenty-four-hr-forecast")),
                                  "general": forecast("24hr", "GENERAL")},
                "outlook_4day": [{"slot_id": r["slot_id"], "data": r["data"], "valid_period": r["valid_period"]}
                                 for r in outlook],
                "uv_index": uv_index,
            },
        }
