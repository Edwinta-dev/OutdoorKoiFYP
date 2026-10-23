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
light readings, six camera frames a day (the real deployment frames in
frames/, cycled; green ratio, GCC and region metrics measured on them by
koi.camera over the camera config mask and regions below), a feed every morning, a 30%
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

from koi.camera import card_patches, hsvEngine, quality  # noqa: E402
from koi.camera.mask import validate_polygon, validate_regions  # noqa: E402
from koi.dev import frames as dev_frames  # noqa: E402
from koi.settings import Settings  # noqa: E402
from koi.storage import MemoryStorage  # noqa: E402
from koi.weather import job  # noqa: E402
from koi.weather.nea import NeaClient  # noqa: E402

SEED = HERE / "seed.json"
PATCHES = HERE / "card_patches.json"
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

# Pond 1's camera config: the water mask and named regions, as fractions of
# a 640x480 frame (origin top-left), hand-drawn on the real deployment
# frames in fixtures/demo_pond/frames (the first frames, where the camera
# sits as in the owner's 15 October deployment): open water on the left, two
# colour test-card wheels on the right.
#   mask       the water rectangle; the green ratio and GCC are measured here
#   water_gap  a smaller patch of open water inside it (the main algae signal)
#   rim        a narrow strip of the pond wall at the left edge
#   plants     a strip along the bottom edge, context only
#   reference  BOTH wheels, as one polygon: a 16-point ellipse per wheel
#              joined by a zero-width bridge (the region schema allows one
#              polygon per name). The app dots every segment of both wheels.
# Later frames drift a little; the polygons are demo-grade, not calibrated.
DEMO_MASK = [[0.15, 0.12], [0.50, 0.12], [0.50, 0.92], [0.15, 0.92]]
WHEELS = (  # (centre x, centre y, radius x, radius y), normalised: top wheel, then bottom wheel
    (0.78, 0.13, 0.135, 0.18),
    (0.80, 0.52, 0.14, 0.18),
)
MASK_VERSION = 1




def _ellipse(cx: float, cy: float, rx: float, ry: float, points: int = 16, start_deg: float = 90.0) -> list:
    """Points of an ellipse clamped into the frame, starting `start_deg`
    degrees from +x (90 is the bottom, y grows downwards)."""
    out = []
    for i in range(points):
        a = math.radians(start_deg + 360.0 * i / points)
        out.append([round(min(1.0, max(0.0, cx + rx * math.cos(a))), 4),
                    round(min(1.0, max(0.0, cy + ry * math.sin(a))), 4)])
    return out


def demo_regions() -> dict:
    top = _ellipse(*WHEELS[0], start_deg=90.0)      # starts at the lowest point of the wheel
    bottom = _ellipse(*WHEELS[1], start_deg=270.0)  # starts at the highest point of the wheel
    # top ring closed back to its start, the bridge to the bottom ring, that
    # ring closed back to its start; the polygon closing edge is the bridge
    # again, so the bridge encloses no area.
    reference = top + [top[0]] + bottom + [bottom[0]]
    return validate_regions({
        "water_gap": [[0.22, 0.30], [0.42, 0.30], [0.42, 0.70], [0.22, 0.70]],
        "rim": [[0.0, 0.0], [0.10, 0.0], [0.10, 1.0], [0.0, 1.0]],
        "plants": [[0.10, 0.93], [0.30, 0.93], [0.30, 1.0], [0.10, 1.0]],
        "reference": reference,
    })


def demo_camera_config() -> dict:
    mask, regions = validate_polygon(DEMO_MASK), demo_regions()
    created = iso(ANCHOR - timedelta(days=DAYS + 1))
    return {"camera_mask_version": [{"pond_id": DEMO_POND, "mask_version": MASK_VERSION, "mask": mask,
                                     "regions": regions, "created_at": created}],
            "camera_config": [{"pond_id": DEMO_POND, "mask": mask, "regions": regions,
                               "mask_version": MASK_VERSION, "updated_at": created}]}


# Colour-card patches (koi/camera/card_patches.py), measured on frames
# 00062-00065 (640x480, the camera does not move between them), served by
# the API from card_patches.json. Pixel geometry, then normalised.
#   pH wheel (warm, 8 segments) and ammonia wheel (5 segments): centre,
#   hub radius, segment angles in degrees clockwise from +x (y down).
FRAME_W, FRAME_H = 640, 480
PH_WHEEL = {"centre": (463.0, 131.0), "hub": 29.0, "outer": 92.0,
            # clockwise from the red segment; segments are 45 degrees wide
            "centres": [210, 255, 300, 345, 30, 75, 120, 165], "half": 45 / 2}
NH3_WHEEL = {"centre": (459.0, 342.0), "hub": 29.0, "outer": 93.0,
             # cream, blue, pale green, green (bottom right), green (bottom left)
             "spans": [(144.5, 236.0), (236.0, 321.0), (321.0, 414.0), (54.0, 104.5), (104.5, 144.5)]}
WHITE_RECT = (105.0, 165.0, 245.0, 385.0)  # x0, y0, x1, y1: cream paper left of the cards, as in the owner's reference
SEGMENT_RADII = (45.0, 60.0, 75.0)
SEGMENT_ARC_PX = 9.0  # distance between the three dots of a row
WHITE_GRID = (12, 16)     # columns, rows


def _norm(x: float, y: float) -> list:
    return [round(min(1.0, max(0.0, x / FRAME_W)), 4), round(min(1.0, max(0.0, y / FRAME_H)), 4)]


def _polar(centre, r: float, deg: float) -> list:
    a = math.radians(deg)
    return _norm(centre[0] + r * math.cos(a), centre[1] + r * math.sin(a))


def _sector(wheel: dict, start: float, end: float) -> dict:
    """An annular sector patch between the hub and the rim, inset 3 degrees
    from the segment borders, with 3 x 3 sample dots (a fan)."""
    lo, hi = start + 3.0, end - 3.0
    r0, r1 = wheel["hub"] + 6.0, wheel["outer"] - 8.0
    steps = 6
    arc = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    polygon = [_polar(wheel["centre"], r1, a) for a in arc] + [_polar(wheel["centre"], r0, a) for a in reversed(arc)]
    mid = (lo + hi) / 2
    points = [_polar(wheel["centre"], r, mid + k * math.degrees(SEGMENT_ARC_PX / r))
              for r in SEGMENT_RADII for k in (-1, 0, 1)]
    return {"polygon": polygon, "points": points}


def _pad(wheel: dict) -> dict:
    """The centre reading pad: a 12-point circle, with 7 dots (centre + ring of 6)."""
    cx, cy = wheel["centre"]
    polygon = [_polar(wheel["centre"], 20.0, 30.0 * i) for i in range(12)]
    points = [_norm(cx, cy)] + [_polar(wheel["centre"], 11.0, 60.0 * i) for i in range(6)]
    return {"polygon": polygon, "points": points}


def demo_card_patches() -> dict:
    """card_patches.json: the white reference grid, 8 pH and 5 ammonia
    reference segments (9 dots each, so the pH wheel samples 72) and the
    two reading pads (7 dots each)."""
    x0, y0, x1, y1 = WHITE_RECT
    cols, rows = WHITE_GRID
    white = {"name": "white_ref",
             "polygon": [_norm(x0, y0), _norm(x1, y0), _norm(x1, y1), _norm(x0, y1)],
             "points": [_norm(x0 + (x1 - x0) * (c + 0.5) / cols, y0 + (y1 - y0) * (r + 0.5) / rows)
                        for r in range(rows) for c in range(cols)]}
    patches = [white]
    for n, span in enumerate(NH3_WHEEL["spans"], 1):
        patches.append({"name": f"nh3_ref_{n}", **_sector(NH3_WHEEL, *span)})
    for n, mid in enumerate(PH_WHEEL["centres"], 1):
        patches.append({"name": f"ph_ref_{n}", **_sector(PH_WHEEL, mid - PH_WHEEL["half"], mid + PH_WHEEL["half"])})
    patches.append({"name": "nh3_reading", **_pad(NH3_WHEEL)})
    patches.append({"name": "ph_reading", **_pad(PH_WHEEL)})
    return {"pond_id": DEMO_POND, "patches": card_patches.validate_card_patches(patches)}


def _six(value):
    """Rounds floats (nested in dicts and lists) to six significant digits."""
    if isinstance(value, float):
        return float(f"{value:.6g}")
    if isinstance(value, dict):
        return {k: _six(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_six(v) for v in value]
    return value


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
    """One row per camera time. Each takes the next real deployment frame
    (cycling through fixtures/demo_pond/frames; koi.dev.frames uploads the
    same file for the same position), and its green_ratio, gcc, colour and
    regions are measured on that frame with the production functions over
    the pond mask and regions."""
    times = []
    for day in range(DAYS + 1):
        for hour in CAMERA_HOURS:
            at = local(day, hour, 3)
            if ANCHOR - timedelta(days=DAYS) < at <= ANCHOR:
                times.append(at)
    mask, regions = validate_polygon(DEMO_MASK), demo_regions()
    rows = []
    ema = None
    for n, at in enumerate(times, 1):
        data = dev_frames.demo_frame_bytes(n - 1)
        img = quality.decode(data)
        green = hsvEngine.analyze_image_bytes(data, polygon=mask)
        gcc, colour = quality.colour_metrics(img, mask)
        measured = hsvEngine.region_metrics(img, regions)
        ema = green if ema is None else round(0.3 * green + 0.7 * ema, 4)
        rows.append({"id": 20_000 + n, "created_at": iso(at), "user_ID": DEMO_POND, "green_ratio": green,
                     # imageTable.current_state is a text column; the
                     # camera stores its state array as JSON text.
                     "current_state": json.dumps(["base", ema]),
                     "mask_version": MASK_VERSION, "gcc": _six(gcc), "colour": _six(colour),
                     "regions": _six(measured),
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
        **demo_camera_config(),
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


def render_patches() -> str:
    return json.dumps(demo_card_patches(), indent=1) + "\n"


def main(argv: list[str]) -> int:
    text, patches = render(), render_patches()
    if "--check" in argv:
        if not SEED.exists() or SEED.read_text(encoding="utf-8") != text:
            sys.stderr.write(f"{SEED} is out of date; run python {Path(__file__).name}\n")
            return 1
        if not PATCHES.exists() or PATCHES.read_text(encoding="utf-8") != patches:
            sys.stderr.write(f"{PATCHES} is out of date; run python {Path(__file__).name}\n")
            return 1
        return 0
    SEED.write_text(text, encoding="utf-8", newline="\n")
    PATCHES.write_text(patches, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
