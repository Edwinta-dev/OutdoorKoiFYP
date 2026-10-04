"""
device_health.py

Device freshness and health (issue #23), and the confidence each
assessment carries. Pure: no I/O. Storage supplies the rows (devices,
device_contact and the battery and reset rows of SensorData, migration
0018) and the engine snapshot supplies each channel's newest usable
sample.

Two times are kept apart:

  * last_seen_at is receipt time: when the backend last received
    anything from the device (the newest SensorData.created_at the
    poller ingested, or the time the camera service stored a frame).
  * A channel's last_sample_at is the time of its newest usable reading
    (PondTwin.sensor_channels). Until the node sends sample times
    (issue #17) that is also a receipt time, labelled time_basis
    "ingestion". A node that reconnects with old buffered samples is
    recently seen, but its channels can still be old.

Expected interval: the cadence configured for the device
(devices.expected_interval_seconds), else for the sensor node the
backend default (Settings.sensor_cadence_minutes) and for the camera the
wake it was last given. Missed cycles are counted per contact against
the interval in force when that contact was received
(device_contact.expected_next_at), so an interval change only affects
contacts after it.

Confidence (high, reduced, low) of an assessment:

  * low      no sensor reading has ever been applied, or the newest
             reading of any channel is older than SILENT_AFTER_INTERVALS
             expected intervals (the node has gone quiet);
  * reduced  a channel the domain uses is older than that, or the
             sensor gate has flagged it stale (the same value repeated,
             koi/models/engine.py SensorGate);
  * high     otherwise.
A channel that has never reported (a pond without a TDS probe) is listed
but does not lower the level: the models already run without it.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Optional

from koi.models.engine import _STALE_RUN_LENGTH
from koi.models.sensor_inputs import CHANNELS, TIME_BASIS_INGESTION, TIME_BASIS_SAMPLE, parse_timestamp

SENSOR = "sensor"
CAMERA = "camera"
KINDS = (SENSOR, CAMERA)

# The window the device summary covers.
WINDOW = timedelta(hours=24)
# A device or channel is silent once its newest contact or reading is
# older than this many expected intervals.
SILENT_AFTER_INTERVALS = 3
# A contact counts as on time up to this fraction of its interval after
# the time it was expected (wake-up, Wi-Fi and upload take a while).
GRACE_FRACTION = 0.5

# SensorData.sensor_type spellings for the node's own health (migration
# 0018). data1 holds millivolts, or the esp_reset_reason_t code.
BATTERY_TYPE = "battery_mv"
RESET_TYPE = "reset_reason"

# Battery trend: least-squares slope over the window's battery_mv rows.
# At least this many readings spanning this long are needed; a slope
# beyond the threshold either way is falling or rising.
BATTERY_MIN_SAMPLES = 3
BATTERY_MIN_SPAN = timedelta(hours=2)
BATTERY_TREND_MV_PER_DAY = 50.0

# ESP-IDF esp_reset_reason_t.
RESET_REASONS = {
    0: "unknown",
    1: "power_on",
    2: "external_pin",
    3: "software",
    4: "panic",
    5: "interrupt_watchdog",
    6: "task_watchdog",
    7: "other_watchdog",
    8: "deep_sleep_wake",
    9: "brownout",
    10: "sdio",
}

# The sensor channels each assessment domain reads.
DOMAIN_CHANNELS = {
    "chemistry": CHANNELS,
    "evaporation": ("temp",),
    "algae": ("lux", "temp"),
}

HIGH, REDUCED, LOW = "high", "reduced", "low"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _time(value: object) -> Optional[datetime]:
    return parse_timestamp(value) if value is not None else None


# ----------------------------------------------------------------------
# Contacts
# ----------------------------------------------------------------------
def sensor_contacts(rows: list[dict], interval: timedelta) -> list[dict]:
    """The sensor contacts in rows the poller read ({created_at, ...}):
    one per distinct insert time, the node's single batched upload,
    expected next one interval later."""
    times = sorted({parse_timestamp(r["created_at"]) for r in rows if r.get("created_at") is not None})
    return [{"received_at": t.isoformat(), "expected_next_at": (t + interval).isoformat()} for t in times]


def missed_cycles(contacts: list[dict], start: datetime, now: datetime) -> int:
    """How many expected reports in [start, now] did not arrive.

    contacts are one device's {received_at, expected_next_at}, including
    the newest one before start when there is one. After each contact the
    device was due at expected_next_at and every interval after it
    (interval = expected_next_at - received_at) until the next contact;
    a due time counts as missed when nothing arrived within
    GRACE_FRACTION of an interval after it. Contacts without an expected
    time count nothing. A device first seen inside the window is not
    charged for the time before it."""
    ordered = sorted(contacts, key=lambda c: parse_timestamp(c["received_at"]))
    missed = 0
    for i, contact in enumerate(ordered):
        received = parse_timestamp(contact["received_at"])
        due = _time(contact.get("expected_next_at"))
        if due is None or due <= received:
            continue
        interval = due - received
        end = parse_timestamp(ordered[i + 1]["received_at"]) if i + 1 < len(ordered) else now
        latest_due = end - interval * GRACE_FRACTION
        if latest_due < due:
            continue
        first = max(0, math.ceil((start - due) / interval)) if start > due else 0
        last = math.floor((latest_due - due) / interval)
        missed += max(0, last - first + 1)
    return missed


# ----------------------------------------------------------------------
# Battery and reset reason
# ----------------------------------------------------------------------
def battery_trend(samples: list[dict]) -> dict:
    """{latest_mv, latest_at, samples, slope_mv_per_day, trend} from
    battery_mv rows ({at, mv}). trend is falling, steady or rising, or
    unknown with too few readings (BATTERY_MIN_SAMPLES over
    BATTERY_MIN_SPAN)."""
    points = []
    for s in samples:
        mv = s.get("mv")
        if isinstance(mv, bool) or not isinstance(mv, (int, float)) or not math.isfinite(mv) or mv <= 0:
            continue
        points.append((parse_timestamp(s["at"]), float(mv)))
    points.sort(key=lambda p: p[0])
    out: dict[str, Any] = {"latest_mv": points[-1][1] if points else None,
                           "latest_at": _iso(points[-1][0]) if points else None,
                           "samples": len(points), "slope_mv_per_day": None, "trend": "unknown"}
    if len(points) < BATTERY_MIN_SAMPLES or points[-1][0] - points[0][0] < BATTERY_MIN_SPAN:
        return out
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400.0 for t, _ in points]
    ys = [mv for _, mv in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    out["slope_mv_per_day"] = round(slope, 1)
    if slope <= -BATTERY_TREND_MV_PER_DAY:
        out["trend"] = "falling"
    elif slope >= BATTERY_TREND_MV_PER_DAY:
        out["trend"] = "rising"
    else:
        out["trend"] = "steady"
    return out


def last_reset(row: Optional[dict]) -> Optional[dict]:
    """{reason, code, at} from the newest reset_reason row ({at, code}),
    or None when the node has never sent one."""
    if not row or row.get("code") is None:
        return None
    try:
        code = int(row["code"])
    except (TypeError, ValueError):
        return None
    return {"reason": RESET_REASONS.get(code, "unknown"), "code": code, "at": _iso(_time(row.get("at")))}


# ----------------------------------------------------------------------
# Channels
# ----------------------------------------------------------------------
def snapshot_channels(snapshot: Optional[dict]) -> dict[str, dict]:
    """Each channel's newest applied reading in an engine snapshot
    (sensor_inputs.channels); {} for a snapshot from before issue #18."""
    inputs = (snapshot or {}).get("sensor_inputs") or {}
    channels = inputs.get("channels")
    return dict(channels) if isinstance(channels, dict) else {}


def snapshot_stale_flags(snapshot: Optional[dict], run_length: int = _STALE_RUN_LENGTH) -> list[str]:
    """The channels the sensor gate currently flags stale in an engine
    snapshot: the same value repeated run_length or more times."""
    gate = ((snapshot or {}).get("chemistry") or {}).get("gate") or {}
    runs = gate.get("run_length") or {}
    return sorted(c for c, n in runs.items() if isinstance(n, int) and n >= run_length)


def channel_health(channels: dict[str, dict], stale_flagged: list[str], interval: timedelta,
                   now: datetime) -> dict[str, dict]:
    """Per channel: last_sample_at, time_basis, age_seconds, status
    (fresh: within two intervals, as the poller uses readings; late:
    within SILENT_AFTER_INTERVALS; old: beyond that; never_seen) and
    stale_flagged."""
    out: dict[str, dict] = {}
    for channel in CHANNELS:
        state = channels.get(channel) or {}
        at = _time(state.get("reading_at"))
        if at is None:
            out[channel] = {"last_sample_at": None, "time_basis": None, "age_seconds": None,
                            "status": "never_seen", "stale_flagged": False}
            continue
        age = now - at
        if age <= 2 * interval:
            status = "fresh"
        elif age <= SILENT_AFTER_INTERVALS * interval:
            status = "late"
        else:
            status = "old"
        out[channel] = {"last_sample_at": at.isoformat(),
                        "time_basis": state.get("time_basis") or TIME_BASIS_INGESTION,
                        "age_seconds": int(age.total_seconds()), "status": status,
                        "stale_flagged": channel in stale_flagged}
    return out


def clock_note(channels: dict[str, dict], last_seen_at: Optional[datetime]) -> dict:
    """How far the channel times can be trusted: {sample_time_basis,
    uncertain, note}."""
    bases = {state.get("time_basis") or TIME_BASIS_INGESTION
             for state in channels.values() if state.get("reading_at")}
    if not bases:
        return {"sample_time_basis": "unknown", "uncertain": True,
                "note": "No sensor reading yet, so there are no sample times."}
    if TIME_BASIS_SAMPLE not in bases:
        return {"sample_time_basis": TIME_BASIS_INGESTION, "uncertain": True,
                "note": "The node sends no sample time yet: each reading is timed when the database stored it, "
                        "so a reading the node buffered and sent late looks newer than it is."}
    newest = max(parse_timestamp(s["reading_at"]) for s in channels.values() if s.get("reading_at"))
    if last_seen_at is not None and newest > last_seen_at + timedelta(minutes=1):
        return {"sample_time_basis": TIME_BASIS_SAMPLE, "uncertain": True,
                "note": "The node's newest sample time is later than when it was received: its clock is ahead."}
    basis = TIME_BASIS_SAMPLE if bases == {TIME_BASIS_SAMPLE} else "mixed"
    return {"sample_time_basis": basis, "uncertain": basis != TIME_BASIS_SAMPLE,
            "note": None if basis == TIME_BASIS_SAMPLE else "Some readings are timed by the database, not the node."}


# ----------------------------------------------------------------------
# Device summary
# ----------------------------------------------------------------------
def expected_interval(kind: str, device: Optional[dict], contacts: list[dict],
                      default_sensor: timedelta) -> tuple[Optional[timedelta], str]:
    """(interval, source): device (configured on the devices row),
    default (the backend's sensor cadence), schedule (the camera's last
    wake) or unknown."""
    configured = (device or {}).get("expected_interval_seconds")
    if isinstance(configured, int) and configured > 0:
        return timedelta(seconds=configured), "device"
    if kind == SENSOR:
        return default_sensor, "default"
    planned = [c for c in contacts if c.get("expected_next_at")]
    if planned:
        newest = max(planned, key=lambda c: parse_timestamp(c["received_at"]))
        gap = parse_timestamp(newest["expected_next_at"]) - parse_timestamp(newest["received_at"])
        if gap > timedelta(0):
            return gap, "schedule"
    return None, "unknown"


def device_status(last_seen: Optional[datetime], next_expected: Optional[datetime],
                  interval: Optional[timedelta], now: datetime) -> str:
    """never_seen, silent (nothing for SILENT_AFTER_INTERVALS intervals
    after it was due), late (past its due time and grace) or ok."""
    if last_seen is None:
        return "never_seen"
    due = next_expected or (last_seen + interval if interval is not None else None)
    if due is None or interval is None:
        return "ok"
    if now > due + (SILENT_AFTER_INTERVALS - 1) * interval:
        return "silent"
    if now > due + interval * GRACE_FRACTION:
        return "late"
    return "ok"


def summarize_devices(activity: dict, channels: dict[str, dict], stale_flagged: list[str],
                      default_sensor: timedelta, now: datetime) -> list[dict]:
    """The GET /v1/ponds/{pond}/devices list: one entry per device kind,
    from pond_device_activity (migration 0018) since now - WINDOW and the
    engine snapshot's channels."""
    devices = {d.get("kind"): d for d in activity.get("devices") or []}
    start = now - WINDOW
    out = []
    for kind in KINDS:
        device = devices.get(kind)
        contacts = [c for c in activity.get("contacts") or [] if c.get("kind") == kind]
        interval, source = expected_interval(kind, device, contacts, default_sensor)
        last_seen = _time((device or {}).get("last_seen_at"))
        next_expected = _time((device or {}).get("next_expected_at"))
        received = sum(1 for c in contacts if parse_timestamp(c["received_at"]) >= start)
        entry: dict[str, Any] = {
            "kind": kind,
            "status": device_status(last_seen, next_expected, interval, now),
            "last_seen_at": _iso(last_seen),
            "next_expected_at": _iso(next_expected),
            "expected_interval_seconds": int(interval.total_seconds()) if interval is not None else None,
            "interval_source": source,
            "window_hours": int(WINDOW.total_seconds() // 3600),
            "received_24h": received,
            "missed_24h": missed_cycles(contacts, start, now) if last_seen is not None else None,
        }
        if kind == SENSOR:
            entry["battery"] = battery_trend(activity.get("battery") or [])
            entry["last_reset"] = last_reset(activity.get("reset"))
            entry["channels"] = channel_health(channels, stale_flagged, interval or default_sensor, now)
            entry["clock"] = clock_note(channels, last_seen)
        else:
            entry["battery"] = battery_trend([])
            entry["last_reset"] = None
            entry["channels"] = None
            entry["clock"] = None
        out.append(entry)
    return out


def sensor_interval(activity_or_devices: Any, default_sensor: timedelta) -> timedelta:
    """The sensor node's expected interval from a list of devices rows or
    a pond_device_activity result."""
    rows = activity_or_devices.get("devices") if isinstance(activity_or_devices, dict) else activity_or_devices
    device = next((d for d in rows or [] if d.get("kind") == SENSOR), None)
    return expected_interval(SENSOR, device, [], default_sensor)[0] or default_sensor


# ----------------------------------------------------------------------
# Confidence
# ----------------------------------------------------------------------
def assess_confidence(domain: str, channels: dict[str, dict], stale_flagged: list[str],
                      interval: timedelta, now: datetime) -> dict:
    """{level, reasons, newest_reading_at, expected_interval_seconds,
    never_seen} for one assessment domain. Each reason is {code, channel,
    message}; codes: no_reading, node_silent, channel_old,
    channel_stale_flagged."""
    limit = SILENT_AFTER_INTERVALS * interval
    times = {c: parse_timestamp(s["reading_at"]) for c, s in channels.items()
             if c in CHANNELS and (s or {}).get("reading_at")}
    newest = max(times.values()) if times else None
    reasons: list[dict] = []
    level = HIGH
    minutes = round(interval.total_seconds() / 60)

    if newest is None:
        level = LOW
        reasons.append({"code": "no_reading", "channel": None,
                        "message": "No sensor reading has reached the model yet, so this is based on the pond "
                                   "profile and logged events only."})
    elif now - newest > limit:
        level = LOW
        hours = (now - newest).total_seconds() / 3600
        reasons.append({"code": "node_silent", "channel": None,
                        "message": f"The newest sensor reading is {hours:.1f} h old, more than "
                                   f"{SILENT_AFTER_INTERVALS} of the node's {minutes}-minute intervals. "
                                   "Check the sensor node's power and Wi-Fi."})

    for channel in DOMAIN_CHANNELS.get(domain, CHANNELS):
        at = times.get(channel)
        if at is not None and newest is not None and now - newest <= limit and now - at > limit:
            hours = (now - at).total_seconds() / 3600
            reasons.append({"code": "channel_old", "channel": channel,
                            "message": f"The {channel} reading is {hours:.1f} h old while the node still reports; "
                                       "the probe may be disconnected."})
            level = LOW if level == LOW else REDUCED
        if channel in stale_flagged and channel in times:
            reasons.append({"code": "channel_stale_flagged", "channel": channel,
                            "message": f"The {channel} probe has sent the same value several times in a row; "
                                       "it may be stuck."})
            level = LOW if level == LOW else REDUCED

    return {"level": level, "reasons": reasons, "newest_reading_at": _iso(newest),
            "expected_interval_seconds": int(interval.total_seconds()),
            "never_seen": [c for c in DOMAIN_CHANNELS.get(domain, CHANNELS) if c not in times]}
