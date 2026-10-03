"""Synthetic ponds for GET /v1/ponds/{pond}/dashboard, seeded the same way
into MemoryStorage (tests/api/test_dashboard.py) and into the local
database (tests/sql/test_sql_dashboard.py), so both storages can be
checked to give the same response.

All rows are synthetic. Times are fixed instants around NOW (12:30 in
Singapore on Saturday 2026-10-03); pond_dashboard_sources does not read
the clock, and the normaliser is given `now` explicitly, so every result
is repeatable.

Ponds (one per acceptance case of issue #13):
  9101 complete           every channel fresh, every station assigned
  9102 no TDS             no TDS row at all
  9103 stale channel      pH two hours old; light sensor sent -1
  9104 missing station    no rainfall station, no forecast area or region;
                          another station's rainfall and a non-cache row
                          of its own station must not be used
  9105 old rows           readings three days old; a weather cache row and
                          a 2-hour forecast written before migration 0009
  9106 second pond        other stations and values; two pH rows with the
                          same created_at (the higher id wins)
  9107 UV                 fresh at 13:30, when there is no 13:00 UV report
                          today (only yesterday's 13:00 slot)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

NOW = datetime(2026, 10, 3, 4, 30, tzinfo=timezone.utc)  # 12:30 SGT
SGT = timezone(timedelta(hours=8))


def ago(**kw: float) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def sgt(day: int, hour: int) -> str:
    return datetime(2026, 10, day, hour, tzinfo=SGT).isoformat()


CENTRAL = {"air-temperature": "S43", "rainfall": "S43", "wind-speed": "S43", "two-hr-forecast": "Serangoon",
           "twenty-four-hr-forecast": "REG_CENTRAL"}
PONDS: dict[int, Any] = {
    9101: CENTRAL,
    9102: CENTRAL,
    9103: CENTRAL,
    9104: {"air-temperature": "S50", "wind-speed": "S50"},
    9105: {"air-temperature": "S99", "rainfall": "S99", "wind-speed": "S99", "two-hr-forecast": "Bedok",
           "twenty-four-hr-forecast": "REG_EAST"},
    9106: {"air-temperature": "S24", "rainfall": "S24", "wind-speed": "S24", "two-hr-forecast": "Changi",
           "twenty-four-hr-forecast": "REG_EAST"},
    9107: CENTRAL,
}


def _readings() -> list[dict]:
    rows: list[dict] = []

    def add(pond: int, n: int, sensor: str, value: float, created_at: str) -> None:
        rows.append({"id": pond * 100 + n, "userID": pond, "sensor_type": sensor, "data1": value,
                     "created_at": created_at})

    # 9101: an older cycle, then the current one.
    for n, (sensor, value) in enumerate([("pH", 7.2), ("TDS", 200.0), ("temp", 28.5), ("LUX", 9000.0)], 1):
        add(9101, n, sensor, value, ago(hours=1))
    for n, (sensor, value) in enumerate([("pH", 7.5), ("TDS", 220.0), ("temp", 29.5), ("LUX", 18000.0)], 11):
        add(9101, n, sensor, value, ago(minutes=5))
    for n, (sensor, value) in enumerate([("pH", 7.25), ("temp", 29.0), ("LUX", 15000.0)], 1):
        add(9102, n, sensor, value, ago(minutes=4))
    add(9103, 1, "pH", 7.75, ago(hours=2))
    for n, (sensor, value) in enumerate([("TDS", 210.0), ("temp", 29.25), ("LUX", -1.0)], 2):
        add(9103, n, sensor, value, ago(minutes=3))
    for n, (sensor, value) in enumerate([("pH", 7.0), ("TDS", 190.0), ("temp", 30.0), ("LUX", 500.0)], 1):
        add(9104, n, sensor, value, ago(minutes=2))
    for n, (sensor, value) in enumerate([("pH", 8.0), ("TDS", 250.0), ("temp", 27.5), ("LUX", 100.0)], 1):
        add(9105, n, sensor, value, ago(days=3))
    add(9106, 1, "pH", 6.75, ago(minutes=2))
    add(9106, 2, "pH", 6.5, ago(minutes=2))  # same instant, higher id: this one
    for n, (sensor, value) in enumerate([("TDS", 300.0), ("temp", 30.5), ("LUX", 5000.0)], 3):
        add(9106, n, sensor, value, ago(minutes=2))
    for n, (sensor, value) in enumerate([("pH", 7.5), ("TDS", 230.0), ("temp", 31.0), ("LUX", 40000.0)], 1):
        add(9107, n, sensor, value, (NOW + timedelta(minutes=55)).isoformat())
    return rows


def _telemetry() -> list[dict]:
    def row(station: str, metric_type: str, data: dict, times: Any, updated: str) -> dict:
        return {"station_id": station, "metric_type": metric_type, "data": data, "source_times": times,
                "valid_start": ago(minutes=5), "valid_end": NOW.isoformat(), "updated_at": updated}

    fresh = ago(minutes=5)
    return [
        row("S43", "realtime_sensor", {"air_temperature": 30.7, "rainfall": 0.2, "wind_speed": 4.1},
            {"air_temperature": fresh, "rainfall": fresh, "wind_speed": fresh}, ago(minutes=4)),
        row("S50", "realtime_sensor", {"air_temperature": 31.0, "wind_speed": 3.0},
            {"air_temperature": fresh, "wind_speed": fresh}, ago(minutes=4)),
        # Not the ingestion job's cache row: never used, though it has the key.
        row("S50", "legacy_import", {"rainfall": 9.9}, None, ago(minutes=4)),
        row("S24", "realtime_sensor", {"air_temperature": 29.9, "rainfall": 1.4, "wind_speed": 6.2},
            {"air_temperature": fresh, "rainfall": fresh, "wind_speed": fresh}, ago(minutes=4)),
        # Written before migration 0009: no provider times.
        row("S99", "realtime_sensor", {"air_temperature": 28.0, "rainfall": 0, "wind_speed": 2.0}, None,
            ago(hours=3)),
    ]


GENERAL_24HR = {"wind": {"speed": {"low": 5, "high": 15}, "direction": "VARIABLE"},
                "forecast": {"code": "TL", "text": "Thundery Showers"},
                "temperature": {"low": 25, "high": 35, "unit": "Degrees Celsius"},
                "relativeHumidity": {"low": 60, "high": 95, "unit": "Percentage"}}


def _outlook_day(day: int, weekday: str, text: str, t_low: int, t_high: int) -> dict:
    return {"forecast_type": "4day", "slot_id": weekday[:3].upper(),
            "data": {"day": weekday, "forecast": {"code": "TL", "text": text},
                     "temperature": {"low": t_low, "high": t_high, "unit": "Degrees Celsius"},
                     "relativeHumidity": {"low": 60, "high": 90, "unit": "Percentage"},
                     "wind": {"speed": {"low": 10, "high": 20}, "direction": "S"},
                     "timestamp": sgt(day, 0)},
            "valid_period": {"timestamp": sgt(day, 0)}, "updated_at": ago(hours=6),
            "source_issued_at": sgt(3, 5)}


def _forecasts() -> list[dict]:
    window = {"start": sgt(3, 12), "end": sgt(3, 14)}
    return [
        {"forecast_type": "2hr", "slot_id": "Serangoon", "data": {"forecast": "Partly Cloudy (Day)"},
         "valid_period": window, "updated_at": ago(minutes=25), "source_issued_at": "2026-10-03T11:40:00+08:00"},
        {"forecast_type": "2hr", "slot_id": "Changi", "data": {"forecast": {"code": "SH", "text": "Showers"}},
         "valid_period": window, "updated_at": ago(minutes=25), "source_issued_at": "2026-10-03T11:40:00+08:00"},
        # Written before migration 0009, for yesterday: expired, no issue time.
        {"forecast_type": "2hr", "slot_id": "Bedok", "data": {"forecast": "Fair (Day)"},
         "valid_period": {"start": sgt(2, 12), "end": sgt(2, 14)}, "updated_at": ago(days=1), "source_issued_at": None},
        {"forecast_type": "24hr", "slot_id": "GENERAL", "data": GENERAL_24HR,
         "valid_period": {"start": sgt(3, 6), "end": sgt(4, 6)}, "updated_at": ago(hours=1),
         "source_issued_at": "2026-10-03T05:30:00+08:00"},
        {"forecast_type": "24hr", "slot_id": "REG_CENTRAL", "data": {"code": "TL", "text": "Thundery Showers"},
         "valid_period": {"start": sgt(3, 12), "end": sgt(3, 18)}, "updated_at": ago(hours=1),
         "source_issued_at": "2026-10-03T11:30:00+08:00"},
        _outlook_day(2, "Friday", "Fair", 26, 33),  # yesterday: left out
        _outlook_day(4, "Sunday", "Thundery Showers", 25, 33),
        _outlook_day(5, "Monday", "Showers", 25, 32),
        _outlook_day(6, "Tuesday", "Fair", 26, 34),
        _outlook_day(7, "Wednesday", "Thundery Showers", 25, 33),
        {"forecast_type": "uv", "slot_id": "11:00", "data": {"uv": 5}, "valid_period": {"hour": sgt(3, 11)},
         "updated_at": ago(minutes=80), "source_issued_at": sgt(3, 11)},
        {"forecast_type": "uv", "slot_id": "12:00", "data": {"uv": 7}, "valid_period": {"hour": sgt(3, 12)},
         "updated_at": ago(minutes=20), "source_issued_at": sgt(3, 12)},
        # Yesterday's 13:00 report still in its slot: not this hour's.
        {"forecast_type": "uv", "slot_id": "13:00", "data": {"uv": 9}, "valid_period": {"hour": sgt(2, 13)},
         "updated_at": ago(days=1), "source_issued_at": sgt(2, 13)},
    ]


def tables() -> dict[str, list[dict]]:
    """Every row, keyed by the Supabase table name."""
    users = [{"userID": pond, "volume": 2500, "biomass": 55, "ClosestStations": stations}
             for pond, stations in PONDS.items()]
    return {"UserData": users, "SensorData": _readings(), "weather_telemetry": _telemetry(),
            "weather_forecasts": _forecasts()}


# (name, pond, now)
SCENARIOS: list[tuple[str, int, datetime]] = [
    ("complete", 9101, NOW),
    ("no_tds", 9102, NOW),
    ("stale_channel", 9103, NOW),
    ("missing_station", 9104, NOW),
    ("old_rows", 9105, NOW),
    ("second_pond", 9106, NOW),
    ("daylight_missing_uv", 9107, NOW + timedelta(hours=1)),
    ("night", 9107, NOW + timedelta(hours=10)),
]


def memory_storage():
    from koi.storage import MemoryStorage

    return MemoryStorage(seed={"tables": tables()})


def seed_sql(cur: Any) -> None:
    """Inserts every row with a psycopg cursor, inside the caller's
    transaction. The weather caches are emptied first."""
    cur.execute("delete from public.weather_telemetry")
    cur.execute("delete from public.weather_forecasts")
    data = tables()
    for u in data["UserData"]:
        cur.execute('insert into public."UserData" ("userID", volume, biomass, "ClosestStations") '
                    "values (%s, %s, %s, %s)",
                    (u["userID"], u["volume"], u["biomass"], json.dumps(u["ClosestStations"])))
    for r in data["SensorData"]:
        cur.execute('insert into public."SensorData" (id, created_at, sensor_type, data1, "userID") '
                    "values (%s, %s, %s, %s, %s)",
                    (r["id"], r["created_at"], r["sensor_type"], r["data1"], r["userID"]))
    for t in data["weather_telemetry"]:
        cur.execute("insert into public.weather_telemetry (station_id, metric_type, data, source_times, valid_start, "
                    "valid_end, updated_at) values (%s, %s, %s, %s, %s, %s, %s)",
                    (t["station_id"], t["metric_type"], json.dumps(t["data"]),
                     json.dumps(t["source_times"]) if t["source_times"] is not None else None,
                     t["valid_start"], t["valid_end"], t["updated_at"]))
    for f in data["weather_forecasts"]:
        cur.execute("insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period, updated_at, "
                    "source_issued_at) values (%s, %s, %s, %s, %s, %s)",
                    (f["forecast_type"], f["slot_id"], json.dumps(f["data"]), json.dumps(f["valid_period"]),
                     f["updated_at"], f["source_issued_at"]))


def dashboard_for(storage: Any, pond: int, now: datetime, **assessments: Any) -> dict:
    """The dashboard body the API would send, from storage's rows."""
    from koi.api.dashboard import build_dashboard
    from koi.models.hypoxia import HypoxiaThresholds

    return build_dashboard(
        pond_id=pond, sources=storage.fetch_dashboard_sources(pond),
        chemistry=assessments.get("chemistry"), evaporation=assessments.get("evaporation"),
        algae=assessments.get("algae"), aeration=assessments.get("aeration"),
        hypoxia_thresholds=HypoxiaThresholds(), now=now,
    ).model_dump(mode="json")
