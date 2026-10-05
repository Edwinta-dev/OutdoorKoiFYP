"""Issue #23: device freshness and health, and the confidence carried by
each assessment (koi/models/device_health.py). Pure: no storage.

Run from Backend/: python -m pytest -q -k "device or confidence"
"""
from datetime import datetime, timedelta, timezone

import pytest

from koi.models import device_health as dh

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
INTERVAL = timedelta(minutes=15)


def contacts_every(interval, start, end, kind="sensor", skip=()):
    """Contacts at start, start + interval, ... up to end, leaving out the
    indexes in skip."""
    out, t, i = [], start, 0
    while t <= end:
        if i not in skip:
            out.append({"kind": kind, "received_at": t.isoformat(), "expected_next_at": (t + interval).isoformat()})
        t += interval
        i += 1
    return out


def channels_at(at, *names, basis="ingestion"):
    return {c: {"value": 1.0, "reading_at": at.isoformat(), "time_basis": basis} for c in names}


def activity(contacts, last_seen=None, interval_seconds=None, battery=(), reset=None, kind="sensor"):
    devices = []
    if contacts or last_seen:
        newest = max(contacts, key=lambda c: c["received_at"]) if contacts else None
        devices.append({"pond_id": 1, "kind": kind, "expected_interval_seconds": interval_seconds,
                        "last_seen_at": last_seen or newest["received_at"],
                        "next_expected_at": newest["expected_next_at"] if newest else None})
    return {"devices": devices, "contacts": contacts, "battery": list(battery), "reset": reset}


def sensor_entry(act, channels=None, stale=(), now=NOW):
    return dh.summarize_devices(act, channels or {}, list(stale), INTERVAL, now)[0]


# ---------------------------------------------------------------------
# Missed cycles
# ---------------------------------------------------------------------
def test_device_reporting_on_time_misses_nothing():
    contacts = contacts_every(INTERVAL, NOW - timedelta(hours=26), NOW - timedelta(minutes=5))
    assert dh.missed_cycles(contacts, NOW - dh.WINDOW, NOW) == 0
    entry = sensor_entry(activity(contacts), channels_at(NOW - timedelta(minutes=5), "ph", "temp", "tds", "lux"))
    assert entry["status"] == "ok"
    assert entry["missed_24h"] == 0
    assert entry["received_24h"] == 96


def test_device_with_gaps_counts_each_missed_cycle():
    # Three missed uploads two hours ago, and one missed upload just now.
    start = NOW - timedelta(hours=24)
    contacts = contacts_every(INTERVAL, start, NOW - timedelta(minutes=5), skip={88, 89, 90})
    assert dh.missed_cycles(contacts, start, NOW) == 3
    late = contacts_every(INTERVAL, start, NOW - timedelta(minutes=20), skip={88, 89, 90})
    assert dh.missed_cycles(late, start, NOW) == 4
    entry = sensor_entry(activity(late))
    assert entry["missed_24h"] == 4
    assert entry["status"] == "late"


def test_device_jitter_inside_the_grace_is_not_a_miss():
    t = NOW - timedelta(hours=1)
    contacts = [{"received_at": t.isoformat(), "expected_next_at": (t + INTERVAL).isoformat()},
                {"received_at": (t + INTERVAL + timedelta(minutes=6)).isoformat(),
                 "expected_next_at": (t + 2 * INTERVAL + timedelta(minutes=6)).isoformat()}]
    assert dh.missed_cycles(contacts, NOW - dh.WINDOW, t + 2 * INTERVAL) == 0


def test_silent_device_is_silent_and_counts_the_window():
    # Last heard 10 hours ago: 40 uploads due since, the newest (due now)
    # still inside its grace, so 39 missed.
    last = NOW - timedelta(hours=10)
    contacts = contacts_every(INTERVAL, NOW - timedelta(hours=30), last)
    entry = sensor_entry(activity(contacts), channels_at(last, "ph", "temp", "lux"))
    assert entry["status"] == "silent"
    assert entry["missed_24h"] == 39
    assert entry["last_seen_at"] == last.isoformat()
    assert entry["channels"]["ph"]["status"] == "old"


def test_device_silent_for_the_whole_window_counts_only_the_window():
    last = NOW - timedelta(days=3)
    contacts = [{"kind": "sensor", "received_at": last.isoformat(), "expected_next_at": (last + INTERVAL).isoformat()}]
    assert dh.missed_cycles(contacts, NOW - dh.WINDOW, NOW) == 96


def test_device_first_seen_in_the_window_is_not_charged_before_it():
    contacts = contacts_every(INTERVAL, NOW - timedelta(hours=2), NOW - timedelta(minutes=5))
    assert dh.missed_cycles(contacts, NOW - dh.WINDOW, NOW) == 0


def test_device_interval_change_counts_against_the_interval_in_force():
    # Every 15 minutes until 6 hours ago, then (after #43) every hour.
    change = NOW - timedelta(hours=6)
    before = contacts_every(INTERVAL, NOW - timedelta(hours=24), change - INTERVAL)
    after = contacts_every(timedelta(hours=1), change, NOW - timedelta(minutes=10))
    assert dh.missed_cycles(before + after, NOW - dh.WINDOW, NOW) == 0
    # The same hourly contacts judged against 15 minutes would be 3 misses an hour.
    as_if_fast = [{**c, "expected_next_at": (dh.parse_timestamp(c["received_at"]) + INTERVAL).isoformat()}
                  for c in after]
    assert dh.missed_cycles(before + as_if_fast, NOW - dh.WINDOW, NOW) > 0


def test_device_configured_interval_wins_over_the_default():
    contacts = contacts_every(timedelta(hours=1), NOW - timedelta(hours=5), NOW - timedelta(minutes=10))
    entry = sensor_entry(activity(contacts, interval_seconds=3600))
    assert (entry["expected_interval_seconds"], entry["interval_source"]) == (3600, "device")
    assert dh.sensor_interval(activity(contacts, interval_seconds=3600), INTERVAL) == timedelta(hours=1)
    default = sensor_entry(activity(contacts))
    assert (default["expected_interval_seconds"], default["interval_source"]) == (900, "default")


def test_never_seen_devices_are_listed():
    sensor, camera = dh.summarize_devices(activity([]), {}, [], INTERVAL, NOW)
    assert (sensor["kind"], camera["kind"]) == ("sensor", "camera")
    assert sensor["status"] == camera["status"] == "never_seen"
    assert sensor["missed_24h"] is None and sensor["last_seen_at"] is None
    assert camera["interval_source"] == "unknown" and camera["expected_interval_seconds"] is None
    assert all(c["status"] == "never_seen" for c in sensor["channels"].values())
    assert sensor["clock"]["sample_time_basis"] == "unknown"
    assert sensor["battery"]["trend"] == "unknown" and sensor["last_reset"] is None


def test_device_camera_interval_comes_from_its_last_wake():
    t = NOW - timedelta(hours=1)
    act = {"devices": [{"kind": "camera", "last_seen_at": t.isoformat(),
                        "next_expected_at": (t + timedelta(hours=2)).isoformat()}],
           "contacts": [{"kind": "camera", "received_at": t.isoformat(),
                         "expected_next_at": (t + timedelta(hours=2)).isoformat()}],
           "battery": [], "reset": None}
    camera = dh.summarize_devices(act, {}, [], INTERVAL, NOW)[1]
    assert (camera["expected_interval_seconds"], camera["interval_source"]) == (7200, "schedule")
    assert camera["status"] == "ok" and camera["missed_24h"] == 0 and camera["channels"] is None


def test_device_sensor_contacts_are_one_per_upload():
    t = NOW - timedelta(minutes=30)
    rows = [{"created_at": t.isoformat()}] * 4 + [{"created_at": (t + INTERVAL).isoformat()}]
    assert dh.sensor_contacts(rows, INTERVAL) == [
        {"received_at": t.isoformat(), "expected_next_at": (t + INTERVAL).isoformat()},
        {"received_at": (t + INTERVAL).isoformat(), "expected_next_at": (t + 2 * INTERVAL).isoformat()}]


# ---------------------------------------------------------------------
# Battery and reset reason
# ---------------------------------------------------------------------
def battery(start, step, values):
    return [{"at": (start + i * step).isoformat(), "mv": v} for i, v in enumerate(values)]


def test_device_falling_battery():
    samples = battery(NOW - timedelta(hours=24), timedelta(hours=1), [12600 - 10 * i for i in range(25)])
    trend = dh.battery_trend(samples)
    assert trend["trend"] == "falling"
    assert trend["slope_mv_per_day"] == pytest.approx(-240.0, abs=0.5)
    assert (trend["latest_mv"], trend["samples"]) == (12360.0, 25)
    entry = sensor_entry(activity(contacts_every(INTERVAL, NOW - timedelta(hours=1), NOW), battery=samples))
    assert entry["battery"]["trend"] == "falling"


def test_device_steady_and_rising_battery():
    steady = battery(NOW - timedelta(hours=12), timedelta(hours=1), [12500, 12510, 12495, 12505, 12500] * 2)
    assert dh.battery_trend(steady)["trend"] == "steady"
    rising = battery(NOW - timedelta(hours=6), timedelta(hours=1), [12000 + 20 * i for i in range(7)])
    assert dh.battery_trend(rising)["trend"] == "rising"


def test_device_battery_trend_needs_enough_readings():
    assert dh.battery_trend([])["trend"] == "unknown"
    short = battery(NOW - timedelta(minutes=30), timedelta(minutes=15), [12600, 12000, 11000])
    assert dh.battery_trend(short)["trend"] == "unknown"
    assert dh.battery_trend(short)["latest_mv"] == 11000.0
    bad = [{"at": NOW.isoformat(), "mv": None}, {"at": NOW.isoformat(), "mv": -1}]
    assert dh.battery_trend(bad)["samples"] == 0


def test_device_last_reset_reason():
    assert dh.last_reset({"at": NOW.isoformat(), "code": 9.0}) == {"reason": "brownout", "code": 9,
                                                                    "at": NOW.isoformat()}
    assert dh.last_reset({"at": NOW.isoformat(), "code": 42})["reason"] == "unknown"
    assert dh.last_reset(None) is None
    assert dh.last_reset({"at": NOW.isoformat(), "code": None}) is None


# ---------------------------------------------------------------------
# Channels and clock
# ---------------------------------------------------------------------
def test_device_reconnecting_with_buffered_samples_is_seen_but_stale():
    """Seen now (receipt time), while every channel's last usable sample
    is hours old."""
    old = NOW - timedelta(hours=5)
    contacts = contacts_every(INTERVAL, NOW - timedelta(minutes=2), NOW - timedelta(minutes=2))
    entry = sensor_entry(activity(contacts), channels_at(old, "ph", "temp", "lux", basis="sample"))
    assert entry["status"] == "ok"
    assert entry["last_seen_at"] == (NOW - timedelta(minutes=2)).isoformat()
    assert entry["channels"]["ph"]["status"] == "old"
    assert entry["channels"]["ph"]["last_sample_at"] == old.isoformat()
    assert entry["channels"]["tds"]["status"] == "never_seen"
    assert entry["clock"] == {"sample_time_basis": "sample", "uncertain": False, "note": None}


def test_device_clock_ingestion_basis_is_uncertain():
    note = dh.clock_note(channels_at(NOW, "ph"), NOW)
    assert note["sample_time_basis"] == "ingestion" and note["uncertain"]


def test_device_clock_ahead_of_receipt_is_flagged():
    note = dh.clock_note(channels_at(NOW + timedelta(minutes=10), "ph", basis="sample"), NOW)
    assert note["uncertain"] and "ahead" in note["note"]


def test_device_channel_status_bands_and_stale_flag():
    channels = {**channels_at(NOW - timedelta(minutes=20), "ph"), **channels_at(NOW - timedelta(minutes=40), "temp"),
                **channels_at(NOW - timedelta(minutes=50), "lux")}
    health = dh.channel_health(channels, ["ph"], INTERVAL, NOW)
    assert [health[c]["status"] for c in ("ph", "temp", "lux", "tds")] == ["fresh", "late", "old", "never_seen"]
    assert health["ph"]["stale_flagged"] and not health["temp"]["stale_flagged"]
    assert health["ph"]["age_seconds"] == 1200


def test_device_snapshot_readers_tolerate_old_snapshots():
    assert dh.snapshot_channels(None) == {} and dh.snapshot_channels({"version": 1}) == {}
    assert dh.snapshot_stale_flags({"chemistry": {}}) == []
    snap = {"chemistry": {"gate": {"run_length": {"ph": 5, "temp": 1, "tds": 4}}}}
    assert dh.snapshot_stale_flags(snap) == ["ph", "tds"]


# ---------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------
ALL = ("ph", "tds", "temp", "lux")


def test_confidence_high_with_current_readings():
    c = dh.assess_confidence("chemistry", channels_at(NOW - timedelta(minutes=10), *ALL), [], INTERVAL, NOW)
    assert c["level"] == "high" and c["reasons"] == [] and c["never_seen"] == []
    assert c["expected_interval_seconds"] == 900


def test_confidence_low_when_the_newest_reading_is_older_than_three_intervals():
    channels = channels_at(NOW - 3 * INTERVAL - timedelta(seconds=1), *ALL)
    c = dh.assess_confidence("evaporation", channels, [], INTERVAL, NOW)
    assert c["level"] == "low"
    assert [r["code"] for r in c["reasons"]] == ["node_silent"]
    at_limit = dh.assess_confidence("evaporation", channels_at(NOW - 3 * INTERVAL, *ALL), [], INTERVAL, NOW)
    assert at_limit["level"] == "high"


def test_confidence_follows_the_configured_interval():
    channels = channels_at(NOW - timedelta(hours=2), *ALL)
    assert dh.assess_confidence("chemistry", channels, [], INTERVAL, NOW)["level"] == "low"
    assert dh.assess_confidence("chemistry", channels, [], timedelta(hours=1), NOW)["level"] == "high"


def test_confidence_low_with_no_reading():
    c = dh.assess_confidence("algae", {}, [], INTERVAL, NOW)
    assert c["level"] == "low" and c["reasons"][0]["code"] == "no_reading"
    assert c["newest_reading_at"] is None and c["never_seen"] == ["lux", "temp"]


def test_confidence_reduced_when_a_used_channel_is_old():
    channels = {**channels_at(NOW - timedelta(minutes=5), "ph", "tds", "lux"),
                **channels_at(NOW - timedelta(hours=2), "temp")}
    chem = dh.assess_confidence("chemistry", channels, [], INTERVAL, NOW)
    assert chem["level"] == "reduced"
    assert [(r["code"], r["channel"]) for r in chem["reasons"]] == [("channel_old", "temp")]
    # Evaporation reads temperature only; algae reads light and temperature.
    assert dh.assess_confidence("evaporation", channels, [], INTERVAL, NOW)["level"] == "reduced"
    lux_old = {**channels_at(NOW - timedelta(minutes=5), "ph", "temp"), **channels_at(NOW - timedelta(hours=2), "lux")}
    assert dh.assess_confidence("evaporation", lux_old, [], INTERVAL, NOW)["level"] == "high"
    assert dh.assess_confidence("algae", lux_old, [], INTERVAL, NOW)["level"] == "reduced"


def test_confidence_reduced_when_a_channel_is_flagged_stale():
    channels = channels_at(NOW - timedelta(minutes=5), *ALL)
    c = dh.assess_confidence("chemistry", channels, ["ph"], INTERVAL, NOW)
    assert c["level"] == "reduced"
    assert [(r["code"], r["channel"]) for r in c["reasons"]] == [("channel_stale_flagged", "ph")]
    assert "same value" in c["reasons"][0]["message"]
    assert dh.assess_confidence("evaporation", channels, ["ph"], INTERVAL, NOW)["level"] == "high"


def test_confidence_stays_low_when_silent_and_flagged():
    channels = channels_at(NOW - timedelta(hours=3), *ALL)
    c = dh.assess_confidence("chemistry", channels, ["temp"], INTERVAL, NOW)
    assert c["level"] == "low"
    assert {r["code"] for r in c["reasons"]} == {"node_silent", "channel_stale_flagged"}


def test_confidence_works_without_tds():
    channels = channels_at(NOW - timedelta(minutes=5), "ph", "temp", "lux")
    c = dh.assess_confidence("chemistry", channels, [], INTERVAL, NOW)
    assert c["level"] == "high"
    assert c["never_seen"] == ["tds"]
