"""Builds seed.json, the demo data the local development stack loads
(python -m koi.dev, docs/dev.md).

    python Backend/fixtures/demo_pond/build_seed.py           # rewrite seed.json
    python Backend/fixtures/demo_pond/build_seed.py --check   # exit 1 if seed.json is out of date

Every value is synthetic, built from the real sample rows in
tests/models/test_new_engines.py and the recorded NEA responses in
tests/fixtures/nea_responses.json. Times are fixed instants ending at
ANCHOR (the NEA recording's fetch time); koi.dev moves them to the
current time when it loads the seed.

Pond 1, the demo pond: 14 days of hourly pH, TDS, water temperature and
light readings, six camera frames a day, a feed every morning, a 30%
water change and a top-up, and a pond profile.

Pond 2, older data shapes the services must still read: no pond profile
(the profile comes from UserData), pH but no TDS, two pH rows with the
same created_at (the higher id is the newest), a camera row whose
current_state is the old plain-text label, and an engine snapshot written
before snapshot_version existed (tests/fixtures/snapshot_v2_utc_days.json).

Also: one SensorData row with no userID (written before the node sent
one), and the weather caches and station lookup that one recorded NEA
fetch produces through the ingestion job (koi/weather).

seed["backtest"] holds the demo pond's station weather over the same 14
days for the backtest (python -m koi.tools.backtest, issue #33): hourly
air temperature, wind speed and rainfall at S43 and the 24-hour
forecast's humidity every six hours. It is outside seed["tables"], so the
development stack does not load it. The air temperature peaks at 14:00
Singapore time, an hour before the water, with a wider daily swing that
follows the same cloud factor as the light; rain falls on the two
cloudiest afternoons.

Measured values have at most six significant digits (pH and temperature
to 0.01, green ratio to 0.0001), which is what a 4-byte real column
(SensorData.data1, imageTable.green_ratio) returns when the database turns
it into JSON, so the in-memory and the local database profiles read back
the same numbers.
"""
from __future__ import annotations

import json
import math
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))

from koi.settings import Settings  # noqa: E402
from koi.storage import MemoryStorage  # noqa: E402
from koi.weather import job  # noqa: E402
from koi.weather.nea import NeaClient  # noqa: E402

SEED = HERE / "seed.json"
NEA_FIXTURE = BACKEND / "tests" / "fixtures" / "nea_responses.json"
OLD_SNAPSHOT = BACKEND / "tests" / "fixtures" / "snapshot_v2_utc_days.json"

# The NEA recording's fetch time (tests/nea_fakes.py FETCHED_AT), 10:06 in
# Singapore. The newest sensor reading is taken at this instant.
ANCHOR = datetime(2026, 10, 2, 2, 6, tzinfo=timezone.utc)
SGT = timezone(timedelta(hours=8))
DAYS = 14

DEMO_POND = 1
LEGACY_POND = 2

# The real sample rows of tests/models/test_new_engines.py: the sensor
# reading in PAYLOAD["raw_sensor"], the pond in CONFIG (5000 L, 8000 g of
# fish) and BLOOM's camera growth (0.02 green ratio doubling every ~3.5
# days, TRUE_MU = 0.198 per day).
SAMPLE_READING = {"pH": 7.64, "TDS": 220, "temp": 27.65, "LUX": 22755}
SAMPLE_VOLUME_L = 5000
SAMPLE_BIOMASS_G = 8000
BLOOM_START = 0.02
BLOOM_MU = 0.198

FEED_GRAMS = 40
TAP_TDS_PPM = 100
WATER_CHANGE = {"day": 5, "hour": 10, "percent": 30}  # day 5 of 14, 10:00 Singapore
TOP_UP = {"day": 10, "hour": 18, "percent": 2}
# Day-to-day cloud factor applied to the light curve.
CLOUD = (1.0, 0.8, 0.55, 0.95, 0.9, 0.7, 1.0, 0.85, 0.6, 0.95, 1.0, 0.75, 0.9, 0.85, 1.0)
CAMERA_HOURS = (8, 10, 12, 14, 16, 18)

# Backtest weather (seed["backtest"]): the demo pond's air-temperature,
# wind and rainfall station, and the afternoon rain on the cloudiest days.
BACKTEST_STATION = "S43"
RAIN_CLOUD_BELOW = 0.65
RAIN_MM_BY_HOUR = {15: 4.0, 16: 2.4, 17: 0.6}
HUMIDITY_EVERY_HOURS = 6




def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def local(day: int, hour: int, minute: int = 0) -> datetime:
    """Singapore wall time on day `day` of the history (day 0 is the first)."""
    first = (ANCHOR - timedelta(days=DAYS)).astimezone(SGT).date()
    return datetime(first.year, first.month, first.day, hour, minute, tzinfo=SGT) + timedelta(days=day)


# ---------------------------------------------------------------------
# Pond 1
# ---------------------------------------------------------------------
def demo_sensor_rows() -> list[dict]:
    start = ANCHOR - timedelta(days=DAYS)
    change_at = local(WATER_CHANGE["day"], WATER_CHANGE["hour"])
    topup_at = local(TOP_UP["day"], TOP_UP["hour"])
    rows: list[dict] = []
    tds = 250.0
    steps = DAYS * 24
    for i in range(1, steps + 1):
        at = start + timedelta(hours=i)
        hour = at.astimezone(SGT).hour + at.astimezone(SGT).minute / 60
        day = (at.astimezone(SGT).date() - local(0, 0).date()).days
        # Evaporation concentrates dissolved solids about 1.2 ppm a day;
        # the water change and the top-up dilute them with tap water.
        tds += 1.2 / 24
        if at - timedelta(hours=1) < change_at <= at:
            tds = tds * (1 - WATER_CHANGE["percent"] / 100) + TAP_TDS_PPM * WATER_CHANGE["percent"] / 100
        if at - timedelta(hours=1) < topup_at <= at:
            tds = tds * (1 - TOP_UP["percent"] / 100) + TAP_TDS_PPM * TOP_UP["percent"] / 100
        diurnal = math.sin(2 * math.pi * (hour - 9) / 24)  # peaks at 15:00
        temp = SAMPLE_READING["temp"] + 0.9 * diurnal
        ph = SAMPLE_READING["pH"] + 0.08 * diurnal - 0.004 * (steps - i) / 24
        daylight = max(0.0, math.sin(math.pi * (hour - 7) / 12)) if 7 <= hour < 19 else 0.0
        lux = SAMPLE_READING["LUX"] * 1.6 * daylight * CLOUD[day % len(CLOUD)]
        values = {"pH": ph, "TDS": tds, "temp": temp, "LUX": lux}
        if i == steps:
            values = dict(SAMPLE_READING)  # the newest reading is the sample row itself
        rows.extend({"sensor_type": t, "value": v, "created_at": iso(at)} for t, v in values.items())
    out = []
    for n, row in enumerate(rows, 1):
        places = 2 if row["sensor_type"] in ("pH", "temp") else 0
        out.append({"id": 10_000 + n, "created_at": row["created_at"], "sensor_type": row["sensor_type"],
                    "data1": float(round(row["value"], places)), "userID": DEMO_POND})
    return out


def demo_camera_rows() -> list[dict]:
    rows = []
    change_at = local(WATER_CHANGE["day"], WATER_CHANGE["hour"])
    ema = None
    n = 0
    for day in range(DAYS + 1):
        for hour in CAMERA_HOURS:
            at = local(day, hour, 3)
            if at > ANCHOR or at <= ANCHOR - timedelta(days=DAYS):
                continue
            days = (at - local(0, 12)).total_seconds() / 86400
            green = BLOOM_START * math.exp(BLOOM_MU * days)
            if at > change_at:
                green *= 1 - WATER_CHANGE["percent"] / 100
            # Glare and ripples: a repeatable +-4% wobble frame to frame.
            green *= 1 + 0.04 * math.sin(n * 2.3)
            green = round(green, 4)
            ema = green if ema is None else round(0.3 * green + 0.7 * ema, 4)
            n += 1
            rows.append({"id": 20_000 + n, "created_at": iso(at), "user_ID": DEMO_POND, "green_ratio": green,
                         # imageTable.current_state is a text column; the
                         # camera stores its state array as JSON text.
                         "current_state": json.dumps(["base", ema]),
                         "imageURL": f"https://example.invalid/demo/{DEMO_POND}/frame_{n:03d}.jpg"})
    return rows


def demo_interventions() -> list[dict]:
    rows = []
    for day in range(1, DAYS + 1):
        at = local(day, 9)
        if at <= ANCHOR:
            rows.append({"event_type": "FEEDING", "event_timestamp": iso(at), "food_grams": FEED_GRAMS,
                         "protein_percentage": 40})
    change = local(WATER_CHANGE["day"], WATER_CHANGE["hour"])
    rows.append({"event_type": "WATER_CHANGE", "event_timestamp": iso(change),
                 "volume_percentage": WATER_CHANGE["percent"],
                 "volume_litres": SAMPLE_VOLUME_L * WATER_CHANGE["percent"] / 100})
    topup = local(TOP_UP["day"], TOP_UP["hour"])
    rows.append({"event_type": "WATER_TOPUP", "event_timestamp": iso(topup), "volume_percentage": TOP_UP["percent"],
                 "volume_litres": SAMPLE_VOLUME_L * TOP_UP["percent"] / 100})
    rows.sort(key=lambda r: r["event_timestamp"])
    out = []
    for n, row in enumerate(rows, 1):
        # The app writes the row when the keeper logs it, a few minutes on.
        created = datetime.fromisoformat(row["event_timestamp"]) + timedelta(minutes=5)
        out.append({"id": 30_000 + n, "created_at": iso(created), "userID": DEMO_POND, "volume_percentage": None,
                    "volume_litres": None, "food_grams": None, "protein_percentage": None, "algae_method": None,
                    **row})
    return out


# ---------------------------------------------------------------------
# Pond 2 and the unassigned row
# ---------------------------------------------------------------------
def legacy_rows() -> dict[str, list[dict]]:
    at = iso(ANCHOR - timedelta(minutes=2))
    sensors = [
        {"id": 40_001, "created_at": at, "sensor_type": "pH", "data1": 6.75, "userID": LEGACY_POND},
        {"id": 40_002, "created_at": at, "sensor_type": "pH", "data1": 6.5, "userID": LEGACY_POND},
        {"id": 40_003, "created_at": at, "sensor_type": "temp", "data1": 29.25, "userID": LEGACY_POND},
        {"id": 40_004, "created_at": at, "sensor_type": "LUX", "data1": 15000.0, "userID": LEGACY_POND},
        # Firmware from before the node sent a userID: belongs to no pond.
        {"id": 40_005, "created_at": iso(ANCHOR - timedelta(days=3)), "sensor_type": "pH", "data1": 7.25,
         "userID": None},
    ]
    images = [{"id": 40_001, "created_at": iso(ANCHOR - timedelta(days=2, hours=4)), "user_ID": LEGACY_POND,
               "green_ratio": 0.0625, "current_state": "base",
               "imageURL": f"https://example.invalid/demo/{LEGACY_POND}/frame_001.jpg"}]
    snapshot = json.loads(OLD_SNAPSHOT.read_text(encoding="utf-8"))
    # No snapshot_version: the row was written before migration 0002.
    state = [{"user_id": LEGACY_POND, "snapshot": snapshot, "updated_at": snapshot["saved_at"]}]
    return {"SensorData": sensors, "imageTable": images, "pond_chemistry_state": state}


# ---------------------------------------------------------------------
# Weather: one recorded NEA fetch through the ingestion job
# ---------------------------------------------------------------------
def _recorded(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    product = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    body = json.loads(NEA_FIXTURE.read_text(encoding="utf-8"))[product]
    return 200, {}, json.dumps(body).encode("utf-8")


def weather_tables() -> dict[str, list[dict]]:
    store = MemoryStorage(clock=lambda: ANCHOR)
    client = NeaClient("https://nea.test.invalid/v2/real-time/api", transport=_recorded, sleep=lambda s: None,
                       monotonic=lambda: 0.0, min_interval_seconds=0.0)
    settings = Settings(_env_file=None, storage="memory", env="test", sentry_dsn="")
    runs = job.run_live(store, client, settings, clock=lambda: ANCHOR)
    failed = [r.product for r in runs if r.status == "failed"]
    if failed:
        raise SystemExit(f"recorded NEA products failed to ingest: {failed}")
    return {t: store.rows(t) for t in ("WeatherStationLookup", "weather_telemetry", "weather_forecasts")}


# ---------------------------------------------------------------------
# Backtest weather
# ---------------------------------------------------------------------
def backtest_weather() -> dict:
    """Hourly station weather for the demo pond's 14 days, as compact
    lists; koi.tools.backtest expands them into weather history rows."""
    start = ANCHOR - timedelta(days=DAYS)
    hours = [start.replace(minute=0) + timedelta(hours=i) for i in range(DAYS * 24 + 1)]
    air, wind, rain = [], [], []
    for at in hours:
        sgt = at.astimezone(SGT)
        day = (sgt.date() - local(0, 0).date()).days
        cloud = CLOUD[day % len(CLOUD)]
        air.append(round(28.2 + 1.2 * (cloud - 0.8) + 2.6 * cloud * math.sin(2 * math.pi * (sgt.hour - 8) / 24), 1))
        wind.append(round(5.0 + 2.0 * math.sin(2 * math.pi * (sgt.hour - 10) / 24) + 0.5 * (day % 3), 1))
        rain.append(RAIN_MM_BY_HOUR.get(sgt.hour, 0.0) if cloud < RAIN_CLOUD_BELOW else 0.0)
    humidity = []
    for at in hours[::HUMIDITY_EVERY_HOURS]:
        day = (at.astimezone(SGT).date() - local(0, 0).date()).days
        low = 60 + round(20 * (1 - CLOUD[day % len(CLOUD)]))
        humidity.append({"issued_at": iso(at), "low": low, "high": low + 25})
    return {
        "_comment": "Station weather for python -m koi.tools.backtest; not loaded by the development stack.",
        "station_id": BACKTEST_STATION,
        "first_hour": iso(hours[0]),
        "air_temperature_c": air,
        "wind_speed_knots": wind,
        "rainfall_mm": rain,
        "humidity_forecast": humidity,
    }


# ---------------------------------------------------------------------
def build() -> dict:
    first = ANCHOR - timedelta(days=DAYS)
    legacy = legacy_rows()
    weather = weather_tables()
    stations_1 = {"air-temperature": "S43", "rainfall": "S43", "wind-speed": "S43", "two-hr-forecast": "Serangoon",
                  "twenty-four-hr-forecast": "REG_CENTRAL"}
    # S06 reports no wind: the dashboard shows that station as unassigned.
    stations_2 = {"air-temperature": "S06", "rainfall": "S06", "two-hr-forecast": "Woodlands",
                  "twenty-four-hr-forecast": "REG_NORTH"}
    tables = {
        "UserData": [
            {"userID": DEMO_POND, "created_at": iso(first - timedelta(days=30)), "volume": SAMPLE_VOLUME_L,
             "biomass": SAMPLE_BIOMASS_G / 1000, "ClosestStations": stations_1},
            {"userID": LEGACY_POND, "created_at": iso(first - timedelta(days=90)), "volume": 800, "biomass": 1.5,
             "ClosestStations": stations_2},
        ],
        "pond_profile": [
            {"id": 1, "pond_id": DEMO_POND, "effective_from": iso(first - timedelta(days=1)),
             "volume_l": SAMPLE_VOLUME_L, "depth_m": 0.6, "biomass_g": SAMPLE_BIOMASS_G, "fish_type": "koi",
             "fish_count": 6, "tap_tds_ppm": TAP_TDS_PPM, "tap_nitrate_ppm": 2, "aeration": True, "source": "api",
             "created_at": iso(first - timedelta(days=1))},
        ],
        "SensorData": demo_sensor_rows() + legacy["SensorData"],
        "imageTable": demo_camera_rows() + legacy["imageTable"],
        "pondInterventions": demo_interventions(),
        "pond_chemistry_state": legacy["pond_chemistry_state"],
        **weather,
    }
    return {
        "_comment": "Generated by build_seed.py; do not edit by hand. Synthetic demo data for python -m koi.dev.",
        "anchor": iso(ANCHOR),
        "ponds": [DEMO_POND, LEGACY_POND],
        "tables": tables,
        "backtest": backtest_weather(),
    }


def render() -> str:
    return json.dumps(build(), indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not SEED.exists() or SEED.read_text(encoding="utf-8") != text:
            sys.stderr.write(f"{SEED} is out of date; run python {Path(__file__).name}\n")
            return 1
        return 0
    SEED.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
