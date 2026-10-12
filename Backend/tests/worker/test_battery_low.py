"""Issue #42: the sensor node's low-battery warning in the notification
outbox (koi/notifications.py::battery_low), the battery and reset readings
MemoryStorage returns from payload v2 rows (migration 0028), and the reset
reason names in device_health.

Run from Backend/: python -m pytest -q -k battery
"""
from datetime import datetime, timedelta, timezone

from conftest import USER, make_storage
from koi import notifications as nt
from koi.models import device_health as dh
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage
from koi.worker import poller

NOW = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
BOOT_1, BOOT_2 = "0000000100a1b2c3", "0000000200d4e5f6"
LOW = dh.FLAG_BATTERY_LOW


def pond(user_id=7):
    storage = MemoryStorage(clock=lambda: NOW)
    storage.add_rows("UserData", [{"userID": user_id, "volume": 800, "biomass": 1.5}])
    return storage


def cycle(at, seq, mv, flags=0, boot=BOOT_1, reset="power_on", user_id=7, channels=("temp", "pH")):
    """One v2 cycle as ingest_readings stores it: every row repeats the
    battery reading, the flags and the boot's reset reason."""
    return [{"created_at": (at + timedelta(seconds=30)).isoformat(), "sampled_at": at.isoformat(),
             "sensor_type": channel, "data1": 7.0, "userID": user_id, "boot_id": boot, "cycle_seq": seq,
             "flags": flags, "reset_reason": reset, "battery_mv": mv} for channel in channels]


def activity(*readings):
    return {"devices": [], "contacts": [], "battery": list(readings), "reset": None}


def reading(at, mv, low):
    return {"at": at.isoformat(), "mv": mv, "low": low}


# ----------------------------------------------------------------------
# The trigger
# ----------------------------------------------------------------------
def test_battery_low_warns_when_the_newest_reading_is_flagged():
    entry = nt.battery_low(activity(reading(NOW - timedelta(hours=1), 4500, False),
                                    reading(NOW - timedelta(minutes=10), 4310, True)), NOW)
    assert entry["kind"] == "battery_low" and entry["severity"] == "warning"
    assert entry["dedupe_key"] == "battery_low:sensor"
    assert entry["data"]["battery_mv"] == 4310
    assert entry["data"]["measured_at"] == (NOW - timedelta(minutes=10)).isoformat()
    assert "4.31 V at 09:50" in entry["body"], entry["body"]   # Singapore time
    assert "Replace or recharge the batteries" in entry["body"]


def test_battery_low_says_how_fast_it_is_falling():
    falling = [reading(NOW - timedelta(hours=10 - i), 4600 - 30 * i, i == 10) for i in range(11)]
    entry = nt.battery_low(activity(*falling), NOW)
    assert entry["data"]["trend"] == "falling"
    assert "falling by about 720 mV a day" in entry["body"], entry["body"]


def test_battery_low_needs_the_newest_reading_flagged():
    # Recovered (new cells, or the sun on a solar pack): the newest is not low.
    assert nt.battery_low(activity(reading(NOW - timedelta(hours=1), 4300, True),
                                   reading(NOW - timedelta(minutes=10), 5900, False)), NOW) is None
    # Order of the list does not matter, only reading time.
    assert nt.battery_low(activity(reading(NOW - timedelta(minutes=10), 4300, True),
                                   reading(NOW - timedelta(hours=1), 5900, False)), NOW)["data"]["battery_mv"] == 4300


def test_battery_low_ignores_unflagged_and_legacy_readings():
    # A legacy battery_mv row has no flag (low None): the threshold is the
    # node's, so the server does not guess one.
    assert nt.battery_low(activity(reading(NOW, 3000, None)), NOW) is None
    assert nt.battery_low(activity(), NOW) is None
    assert nt.battery_low(None, NOW) is None
    assert nt.battery_low(activity({"at": NOW.isoformat(), "mv": None, "low": True}), NOW) is None


def test_battery_low_once_per_cool_down():
    storage = pond()
    flagged = activity(reading(NOW, 4300, True))
    assert nt.enqueue(storage, 7, [nt.battery_low(flagged, NOW)])
    for minutes in range(15, 24 * 60, 15):   # every cycle of the next day
        later = NOW + timedelta(minutes=minutes)
        assert nt.enqueue(storage, 7, [nt.battery_low(activity(reading(later, 4300, True)), later)]) == []
    after = NOW + nt.COOLDOWNS["battery_low"] + timedelta(minutes=1)
    assert nt.enqueue(storage, 7, [nt.battery_low(activity(reading(after, 4250, True)), after)])
    assert [r["kind"] for r in storage.fetch_notifications(7)] == ["battery_low", "battery_low"]


def test_battery_low_from_stored_rows_through_notify_cycle():
    storage = pond()
    storage.add_rows("SensorData", cycle(NOW - timedelta(minutes=30), 1, 4450)
                     + cycle(NOW - timedelta(minutes=15), 2, 4390, flags=LOW))
    [row] = nt.notify_cycle(storage, 7, NOW, current={}, previous={}, image_rows=None,
                            default_sensor_interval=timedelta(minutes=15))
    assert row["kind"] == "battery_low" and row["data"]["battery_mv"] == 4390


def test_battery_low_written_by_the_poller():
    storage = make_storage()
    start = datetime(2026, 8, 20, 0, 5, tzinfo=timezone.utc)
    storage.add_rows("SensorData", cycle(start - timedelta(minutes=2), 1, 4380, flags=LOW, user_id=USER,
                                         channels=("temp", "TDS", "pH", "LUX")))
    config = next(r for r in storage.fetch_active_pond_configs() if r["user_id"] == USER)
    poller._poll_user(EngineRegistry(storage), USER, config, now=start)
    assert [r["kind"] for r in storage.fetch_notifications(USER)] == ["battery_low"]


# ----------------------------------------------------------------------
# MemoryStorage.fetch_device_activity on v2 rows (mirrors 0028)
# ----------------------------------------------------------------------
def test_battery_activity_one_reading_per_v2_cycle_with_its_flag():
    storage = pond()
    storage.add_rows("SensorData", [
        {"created_at": (NOW - timedelta(hours=5)).isoformat(), "sensor_type": "battery_mv", "data1": 4700.0,
         "userID": 7},
        *cycle(NOW - timedelta(hours=2), 1, 4500),
        *cycle(NOW - timedelta(hours=1), 2, 4390, flags=LOW | 1),
        *cycle(NOW - timedelta(hours=30), 9, 5000),   # before the window
        *cycle(NOW - timedelta(minutes=5), 3, 4380, flags=LOW, user_id=8),   # another pond
    ])
    since = NOW - timedelta(hours=24)
    battery = storage.fetch_device_activity(7, since)["battery"]
    assert [(b["mv"], b["low"]) for b in battery] == [(4700.0, None), (4500, False), (4390, True)]
    assert battery[1]["at"] == (NOW - timedelta(hours=2)).isoformat(), "timed by the sample time"


def test_battery_activity_reset_is_the_newest_boot_dated_from_its_first_cycle():
    storage = pond()
    storage.add_rows("SensorData", [
        {"created_at": (NOW - timedelta(hours=8)).isoformat(), "sensor_type": "reset_reason", "data1": 4.0,
         "userID": 7},
        *cycle(NOW - timedelta(hours=6), 1, 5000, boot=BOOT_1, reset="power_on"),
        *cycle(NOW - timedelta(hours=3), 1, 4600, boot=BOOT_2, reset="brownout"),
        *cycle(NOW - timedelta(hours=2), 2, 4590, boot=BOOT_2, reset="brownout"),
    ])
    reset = storage.fetch_device_activity(7, NOW - timedelta(hours=24))["reset"]
    assert reset == {"at": (NOW - timedelta(hours=3)).isoformat(), "code": None, "reason": "brownout"}
    assert dh.last_reset(reset) == {"reason": "brownout", "code": 9, "at": (NOW - timedelta(hours=3)).isoformat()}

    # A newer legacy reset_reason row wins over older v2 rows.
    storage.add_rows("SensorData", [{"created_at": (NOW - timedelta(hours=1)).isoformat(),
                                     "sensor_type": "reset_reason", "data1": 4.0, "userID": 7}])
    reset = storage.fetch_device_activity(7, NOW - timedelta(hours=24))["reset"]
    assert (reset["code"], reset["reason"]) == (4.0, None)


def test_battery_reset_reason_names_cover_every_name_the_node_sends():
    # koi_sensing/sample_cycle.h resetReasonName and 0027's accepted names.
    names = ["unknown", "power_on", "external_pin", "software", "panic", "interrupt_watchdog", "task_watchdog",
             "other_watchdog", "deep_sleep_wake", "brownout", "sdio", "usb", "jtag", "efuse", "power_glitch",
             "cpu_lockup"]
    assert [dh.RESET_REASONS[code] for code in range(len(names))] == names
    assert dh.last_reset({"at": NOW.isoformat(), "reason": "cpu_lockup"})["code"] == 15
    assert dh.last_reset({"at": NOW.isoformat(), "reason": "made_up"}) == {
        "reason": "unknown", "code": 0, "at": NOW.isoformat()}
    assert dh.last_reset({"at": NOW.isoformat(), "code": None, "reason": None}) is None
    assert dh.last_reset({"at": NOW.isoformat(), "code": 9.0, "reason": None})["reason"] == "brownout"
