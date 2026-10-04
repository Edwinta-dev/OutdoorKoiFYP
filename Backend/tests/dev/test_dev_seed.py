"""Issue #14: the demo seed (Backend/fixtures/demo_pond/seed.json) and how
koi.dev moves it to the current time and reads it as of a past instant.

Run from Backend/: python -m pytest tests/dev
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from koi.dev import seed as seed_data
from koi.models.engine import EventKind
from koi.storage import MemoryStorage
from koi.storage.base import parse_timestamp

BACKEND = Path(__file__).resolve().parents[2]
SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 4, 7, 42, 13, tzinfo=timezone.utc)  # 15:42 Sunday in Singapore


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _module("build_seed", BACKEND / "fixtures" / "demo_pond" / "build_seed.py")


@pytest.fixture(scope="module")
def seed():
    return seed_data.load()


@pytest.fixture(scope="module")
def shifted(seed):
    return seed_data.shift(seed, NOW)


def rows(seed, table, **equal):
    return [r for r in seed["tables"][table] if all(r.get(k) == v for k, v in equal.items())]


# ---------------------------------------------------------------------
# The committed seed
# ---------------------------------------------------------------------
def test_committed_seed_is_what_the_builder_writes(builder):
    assert seed_data.SEED_PATH.read_text(encoding="utf-8") == builder.render(), \
        "run python Backend/fixtures/demo_pond/build_seed.py"


def test_seed_loads_into_memory_storage_as_is(seed):
    storage = MemoryStorage(seed={"tables": seed["tables"]})
    assert {c["user_id"] for c in storage.fetch_active_pond_configs()} == {1, 2}


def test_seed_is_built_from_the_real_sample_rows(builder, seed):
    sample = _module("sample_rows", BACKEND / "tests" / "models" / "test_new_engines.py")
    newest = {r["sensor_type"]: r["data1"] for r in rows(seed, "SensorData", userID=1, created_at=seed["anchor"])}
    assert newest == pytest.approx(sample.PAYLOAD["raw_sensor"], abs=0.005)
    profile = rows(seed, "pond_profile", pond_id=1)[0]
    assert profile["volume_l"] == sample.CONFIG.volume_litres
    assert profile["biomass_g"] == sample.CONFIG.estimated_biomass_grams
    assert builder.BLOOM_MU == sample.TRUE_MU
    assert builder.BLOOM_START == sample.BLOOM[0]["green_ratio"]
    # Weather is the recorded NEA fetch, at the recording's fetch time.
    assert builder.ANCHOR == _module("nea_fakes_copy", BACKEND / "tests" / "nea_fakes.py").FETCHED_AT
    assert seed["anchor"] == builder.ANCHOR.isoformat()


def test_demo_pond_covers_fourteen_days_of_every_channel(seed):
    anchor = parse_timestamp(seed["anchor"])
    for channel in ("pH", "TDS", "temp", "LUX"):
        times = sorted(parse_timestamp(r["created_at"]) for r in rows(seed, "SensorData", userID=1,
                                                                       sensor_type=channel))
        assert times[-1] == anchor
        assert anchor - times[0] >= timedelta(days=14) - timedelta(hours=1), channel
        assert len(times) == 14 * 24


def test_demo_pond_has_camera_rows_feeds_a_water_change_and_a_top_up(seed):
    frames = rows(seed, "imageTable", user_ID=1)
    assert len(frames) >= 14 * 5
    assert all(json.loads(f["current_state"])[0] == "base" for f in frames)
    kinds = [r["event_type"] for r in rows(seed, "pondInterventions", userID=1)]
    assert kinds.count("FEEDING") >= 14
    assert kinds.count("WATER_CHANGE") == 1 and kinds.count("WATER_TOPUP") == 1


def test_measured_values_survive_a_real_column(seed):
    # A real column turned into JSON keeps 6 significant digits.
    for r in seed["tables"]["SensorData"]:
        assert float(f"{r['data1']:.6g}") == r["data1"], r
    for r in seed["tables"]["imageTable"]:
        assert float(f"{r['green_ratio']:.6g}") == r["green_ratio"], r


def test_seed_carries_the_older_shapes(seed):
    assert sorted(seed["ponds"]) == [1, 2]
    # A row from before the node sent a userID.
    assert [r["sensor_type"] for r in rows(seed, "SensorData", userID=None)] == ["pH"]
    # Pond 2: pH but no TDS, and two pH rows at the same instant.
    legacy = rows(seed, "SensorData", userID=2)
    assert "TDS" not in {r["sensor_type"] for r in legacy}
    ph = [r for r in legacy if r["sensor_type"] == "pH"]
    assert len(ph) == 2 and ph[0]["created_at"] == ph[1]["created_at"]
    # The camera's old plain-text state.
    assert [r["current_state"] for r in rows(seed, "imageTable", user_ID=2)] == ["base"]
    # A snapshot written before snapshot_version existed, and no profile rows.
    state = rows(seed, "pond_chemistry_state", user_id=2)[0]
    assert "snapshot_version" not in state and state["snapshot"]["version"] == 2
    assert rows(seed, "pond_profile", pond_id=2) == []


def test_seed_has_weather_caches_and_station_metadata(seed):
    kinds = {r["forecast_type"] for r in seed["tables"]["weather_forecasts"]}
    assert kinds == {"2hr", "24hr", "4day", "uv"}
    assert {r["station_id"] for r in seed["tables"]["weather_telemetry"]} >= {"S43", "S06"}
    names = {r["station__name"] for r in seed["tables"]["WeatherStationLookup"]}
    assert {"Kim Chuan Road", "Serangoon"} <= names


# ---------------------------------------------------------------------
# Moving the seed to now
# ---------------------------------------------------------------------
def test_shift_puts_the_newest_reading_at_now(seed, shifted):
    assert parse_timestamp(shifted["anchor"]) == NOW
    newest = max(parse_timestamp(r["created_at"]) for r in shifted["tables"]["SensorData"])
    assert newest == NOW
    delta = NOW - parse_timestamp(seed["anchor"])
    before = {r["id"]: parse_timestamp(r["created_at"]) for r in seed["tables"]["imageTable"]}
    for r in shifted["tables"]["imageTable"]:
        assert parse_timestamp(r["created_at"]) - before[r["id"]] == delta


def test_shift_leaves_the_seed_unchanged(seed):
    before = json.dumps(seed, sort_keys=True)
    seed_data.shift(seed, NOW)
    assert json.dumps(seed, sort_keys=True) == before


def test_shift_moves_weather_by_whole_hours_and_relabels(shifted):
    forecasts = shifted["tables"]["weather_forecasts"]
    uv = {r["slot_id"]: r for r in forecasts if r["forecast_type"] == "uv"}
    # The recorded 10:00 report becomes the report of the current hour (15:00).
    assert "15:00" in uv
    assert parse_timestamp(uv["15:00"]["valid_period"]["hour"]) == datetime(2026, 10, 4, 15, tzinfo=SGT)
    for r in uv.values():
        hour = parse_timestamp(r["valid_period"]["hour"]).astimezone(SGT)
        assert r["slot_id"] == hour.strftime("%H:00")
    for r in (r for r in forecasts if r["forecast_type"] == "4day"):
        day = parse_timestamp(r["valid_period"]["timestamp"]).astimezone(SGT)
        assert r["slot_id"] == day.strftime("%a").upper()
        assert r["data"]["day"] == day.strftime("%A")
    for r in shifted["tables"]["weather_telemetry"]:
        stamp = parse_timestamp(r["updated_at"])
        assert stamp.minute == 6 and stamp.hour == NOW.hour  # recorded at :06


def test_shift_keeps_offsets_of_provider_times(shifted):
    two_hour = next(r for r in shifted["tables"]["weather_forecasts"] if r["forecast_type"] == "2hr")
    assert two_hour["valid_period"]["start"].endswith("+08:00")


def test_shift_adds_daily_averages_per_singapore_day(shifted):
    daily = shifted["tables"]["daily_sensor_averages"]
    ph = [r for r in daily if r["userid"] == 1 and r["sensor_type"] == "pH"]
    assert len(ph) == 15  # 14 days of hourly readings touch 15 Singapore dates
    assert ph[-1]["record_date"] == "2026-10-04"
    readings = [r["data1"] for r in shifted["tables"]["SensorData"] if r["userID"] == 1 and r["sensor_type"] == "pH"
                and parse_timestamp(r["created_at"]).astimezone(SGT).date().isoformat() == "2026-10-04"]
    assert ph[-1]["avg_value"] == round(sum(readings) / len(readings), 4)
    assert ph[-1]["min_value"] == min(readings) and ph[-1]["max_value"] == max(readings)
    # The row without a pond is not aggregated.
    assert {r["userid"] for r in daily} == {1, 2}


# ---------------------------------------------------------------------
# Reading the seed as of a past instant
# ---------------------------------------------------------------------
def test_payload_holds_only_readings_stored_by_then(shifted):
    history = seed_data.SeedHistory(shifted["tables"])
    earlier = NOW - timedelta(days=3, minutes=30)
    payload = history.payload(1, earlier)
    stored = sorted((r for r in shifted["tables"]["SensorData"]
                     if r["userID"] == 1 and parse_timestamp(r["created_at"]) <= earlier),
                    key=lambda r: parse_timestamp(r["created_at"]))
    newest = {r["sensor_type"]: r["data1"] for r in stored}  # later rows overwrite earlier ones
    assert payload["raw_sensor"] == newest
    assert parse_timestamp(stored[-1]["created_at"]) > earlier - timedelta(hours=1)
    assert payload["nea_telemetry"]["air_temp"] == {"value": 30.7}
    assert payload["nea_forecasts"]["forecast_2hr"]["forecast"]
    assert [d["slot_id"] for d in payload["nea_forecasts"]["outlook_4day"]] == ["MON", "TUE", "WED", "THU"]


def test_payload_takes_the_higher_id_at_the_same_instant(shifted):
    history = seed_data.SeedHistory(shifted["tables"])
    assert history.payload(2, NOW)["raw_sensor"] == {"LUX": 15000.0, "pH": 6.5, "temp": 29.25}


def test_payload_uv_is_the_current_hours_report_or_the_night_fallback(shifted):
    history = seed_data.SeedHistory(shifted["tables"])
    assert history.payload(1, NOW)["nea_forecasts"]["uv_index"]["slot_id"] == "15:00"
    night = history.payload(1, NOW + timedelta(hours=8))["nea_forecasts"]["uv_index"]
    assert night["slot_id"] is None and night["data"]["status"] == "Nighttime (Inactive)"


def test_images_and_feeds_as_of_exclude_the_future(shifted):
    history = seed_data.SeedHistory(shifted["tables"])
    at = NOW - timedelta(days=7)
    images = history.images(1, at)
    assert images and all(parse_timestamp(r["created_at"]) <= at for r in images)
    assert [r["created_at"] for r in images] == sorted((r["created_at"] for r in images), reverse=True)
    assert set(images[0]) >= {"id", "green_ratio", "current_state", "quality"}
    feeds = history.feeds(1, at)
    assert feeds and all(parse_timestamp(r["event_timestamp"]) <= at for r in feeds)
    assert len(history.feeds(1, NOW)) > len(feeds)


def test_events_become_engine_events_in_order(shifted):
    events = seed_data.SeedHistory(shifted["tables"]).events(1)
    assert [e.time for e in events] == sorted(e.time for e in events)
    change = next(e for e in events if e.kind == EventKind.WATER_CHANGE)
    assert change.volume_percent == 30 and change.volume_litres == 1500
    top_up = next(e for e in events if e.kind == EventKind.TOP_UP)
    assert top_up.volume_percent == 2
    assert {e.kind for e in events} == {EventKind.FEEDING, EventKind.WATER_CHANGE, EventKind.TOP_UP}
