"""
koi.notifications

The notification outbox (issue #36, migration 0024). Each poll cycle
the worker (koi/worker/poller.py::_poll_user) asks notify_cycle which
notifications the pond's new state calls for and writes them to
notification_outbox. A sender delivers them later; this module has the
sender interface, a no-op sender and an FCM stub that the push
notification issue completes. Nothing here sends anything yet.

--------------------------------------------------------------------
TRIGGERS
--------------------------------------------------------------------
  status_red         an assessment (chemistry, evaporation, algae) is Red
                     now and its previous stored assessment was not (or
                     there was none). Staying red does not repeat.
  ladder_action      the lead-time ladder (koi/models/ladder.py) has an
                     action with a lead time, evaluated on the retained
                     weather as of the cycle's time, the same way as
                     GET /v1/ponds/{pond}/actions.
  node_silent        the sensor node has sent nothing for
                     device_health.SILENT_AFTER_INTERVALS (3) expected
                     intervals after it was due (device_health.device_status).
  camera_obstructed  issue #41. The camera enters the obstruction state
                     (koi/camera/hsvEngine.py::evalstate): a warning,
                     "Camera view blocked since 14:10", once per episode
                     while its newest frame is within OBSTRUCTION_MAX_AGE.
                     It clears after more than OBSTRUCTION_CLEAR_AFTER
                     (2 h): an info "view clear" entry. Episodes come
                     from the current_state labels of the imageTable
                     rows the poll already read (obstruction_episode);
                     the camera service is unchanged.

--------------------------------------------------------------------
DEDUPE AND COOL-DOWN
--------------------------------------------------------------------
Every entry carries a dedupe_key naming what it is about, and each kind
has a cool-down (COOLDOWNS). Storage.enqueue_notification writes the
entry only when the pond has no entry with the same key created within
the cool-down before it, as one step in the database. So:

  status_red:<domain>              12 h: a status that flaps between
                                   amber and red notifies once per 12 h
  ladder_action:<rule>:<local day> 24 h: each rule once per pond-local day
  node_silent:sensor               24 h: a daily reminder while the node
                                   stays silent, not one per cycle
  camera_obstructed:camera:<start> 365 d: once per episode, until the
                                   view clears (a new episode has a new
                                   start)
  camera_obstructed:clear:<at>     365 d: once per clearing

Writing is best effort: a failed write is logged and skipped, and never
fails the poll.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Optional, Protocol

from koi.logs import log_event
from koi.models import device_health, ladder
from koi.models.local_time import DEFAULT_POND_TIME_ZONE, local_date, zone
from koi.models.sensor_inputs import parse_timestamp
from koi.storage.base import fail_soft
from koi.weather.history import local_day_window

log = logging.getLogger(__name__)

STATUS_RED = "status_red"
LADDER_ACTION = "ladder_action"
NODE_SILENT = "node_silent"
CAMERA_OBSTRUCTED = "camera_obstructed"
KINDS = (STATUS_RED, LADDER_ACTION, NODE_SILENT, CAMERA_OBSTRUCTED)

INFO, WARNING, CRITICAL = "info", "warning", "critical"

COOLDOWNS: dict[str, timedelta] = {
    STATUS_RED: timedelta(hours=12),
    LADDER_ACTION: timedelta(hours=24),
    NODE_SILENT: timedelta(hours=24),
    # Keys are per episode (issue #41), so this only has to outlast one.
    CAMERA_OBSTRUCTED: timedelta(days=365),
}

# A frame older than this does not raise camera_obstructed: a camera that
# stopped on an obstructed frame is not reported every day forever.
OBSTRUCTION_MAX_AGE = timedelta(hours=24)
# An obstruction must last longer than this for its clearing to be
# reported (issue #41); a short one clears without a second message.
OBSTRUCTION_CLEAR_AFTER = timedelta(hours=2)
OBSTRUCTION = "obstruction"
# Rows the poller reads from imageTable each cycle (koi/worker/poller.py
# fetch_image_history(limit=50)). A shorter window is the whole history.
IMAGE_WINDOW = 50

DOMAIN_TITLES = {"chemistry": "Water chemistry", "evaporation": "Water level", "algae": "Algae"}
RED = "Red"


def _entry(kind: str, severity: str, title: str, body: str, data: dict, dedupe_key: str,
           now: datetime) -> dict:
    return {"kind": kind, "severity": severity, "title": title, "body": body, "data": data,
            "dedupe_key": dedupe_key, "created_at": now.isoformat()}


# ----------------------------------------------------------------------
# Triggers (pure)
# ----------------------------------------------------------------------
def status_red(domain: str, current: Optional[dict], previous: Optional[dict], now: datetime) -> Optional[dict]:
    """An entry when the domain's assessment is Red now and its previous
    one was not; None otherwise."""
    if not current or current.get("status") != RED:
        return None
    before = (previous or {}).get("status")
    if before == RED:
        return None
    category = current.get("category") or "Red"
    return _entry(STATUS_RED, CRITICAL, f"{DOMAIN_TITLES.get(domain, domain)}: {category}",
                  current.get("advisory") or f"The {domain} assessment has turned red.",
                  {"domain": domain, "status": RED, "category": category, "previous_status": before},
                  f"{STATUS_RED}:{domain}", now)


def ladder_actions(evaluated: dict, now: datetime) -> list[dict]:
    """One entry per ladder action that has a lead time."""
    day = local_date(now).isoformat()
    out = []
    for action in evaluated.get("actions") or []:
        lead = action.get("lead_time")
        if not lead:
            continue
        rule = action.get("rule") or "unknown"
        out.append(_entry(LADDER_ACTION, WARNING, f"Weather action ({lead})", action.get("action") or rule,
                          {"rule": rule, "lead_time": lead, "evidence": action.get("evidence"), "local_date": day},
                          f"{LADDER_ACTION}:{rule}:{day}", now))
    return out


def node_silent(activity: Optional[dict], default_interval: timedelta, now: datetime) -> Optional[dict]:
    """An entry when the sensor node is silent (device_health.device_status)
    in a pond_device_activity result; None when it is not, was never
    seen, or activity is unknown."""
    if activity is None:
        return None
    device = next((d for d in activity.get("devices") or [] if d.get("kind") == device_health.SENSOR), None)
    if device is None or device.get("last_seen_at") is None:
        return None
    interval, _ = device_health.expected_interval(device_health.SENSOR, device, [], default_interval)
    last_seen = parse_timestamp(device["last_seen_at"])
    next_expected = parse_timestamp(device["next_expected_at"]) if device.get("next_expected_at") else None
    if device_health.device_status(last_seen, next_expected, interval, now) != "silent":
        return None
    interval = interval or default_interval
    hours = (now - last_seen).total_seconds() / 3600.0
    minutes = int(interval.total_seconds() // 60)
    return _entry(NODE_SILENT, WARNING, "Sensor node has gone quiet",
                  f"No reading from the pond's sensor node for {hours:.1f} hours; it should upload every "
                  f"{minutes} minutes. Check its battery, power and Wi-Fi. Until it reports, the water "
                  f"assessments are model estimates without fresh measurements.",
                  {"device": device_health.SENSOR, "last_seen_at": last_seen.isoformat(),
                   "expected_interval_seconds": int(interval.total_seconds()),
                   "silent_after_intervals": device_health.SILENT_AFTER_INTERVALS},
                  f"{NODE_SILENT}:{device_health.SENSOR}", now)


def _frame_state(row: dict) -> Optional[str]:
    """The state machine's label stored in an imageTable row's
    current_state: a [label, baseline, ...] list (or its JSON text) as
    camera.py writes it, or a bare label from older rows. The same reading
    as koi/camera/hsvEngine.py::_coerce_state, without importing OpenCV
    into the worker."""
    value = row.get("current_state")
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return value.strip() if isinstance(value, str) else None


def obstruction_episode(image_rows: Optional[list[dict]]) -> Optional[dict]:
    """The camera's latest obstruction episode in a newest-first window of
    imageTable rows, or None when the window holds none.

    Returns {since, until, cleared_at, image_id, complete}: since is the
    first frame of the run of consecutive "obstruction" frames, until its
    last, cleared_at the first frame after it (None while it lasts) and
    image_id the newest frame of the run. complete is False when the run
    reaches the oldest frame of a full window (IMAGE_WINDOW rows), so
    since is only a bound."""
    rows = [r for r in image_rows or [] if r.get("created_at") is not None]
    rows.sort(key=lambda r: (parse_timestamp(r["created_at"]), r.get("id") or 0), reverse=True)
    i = 0
    while i < len(rows) and _frame_state(rows[i]) != OBSTRUCTION:
        i += 1
    if i == len(rows):
        return None
    j = i
    while j + 1 < len(rows) and _frame_state(rows[j + 1]) == OBSTRUCTION:
        j += 1
    return {"since": parse_timestamp(rows[j]["created_at"]), "until": parse_timestamp(rows[i]["created_at"]),
            "cleared_at": parse_timestamp(rows[i - 1]["created_at"]) if i > 0 else None,
            "image_id": rows[i].get("id"), "complete": j + 1 < len(rows) or len(rows) < IMAGE_WINDOW}


def _clock(at: datetime, now: datetime) -> str:
    """at as pond-local HH:MM, with the date when it is not now's day."""
    local = at.astimezone(zone(DEFAULT_POND_TIME_ZONE))
    if local.date() == local_date(now):
        return local.strftime("%H:%M")
    return f"{local:%H:%M} on {local.day} {local:%b}"


def camera_obstructed(image_rows: Optional[list[dict]], now: datetime) -> Optional[dict]:
    """An entry for the camera's obstruction episode (obstruction_episode):

    - still obstructed, newest frame no older than OBSTRUCTION_MAX_AGE:
      a warning keyed on the episode's first frame, so it is written once
      per episode (the cool-down outlasts any episode the window can see);
    - cleared, by a frame no older than OBSTRUCTION_MAX_AGE, after more
      than OBSTRUCTION_CLEAR_AFTER: an info "view clear" entry keyed on
      the clearing frame.

    None otherwise, including for an ongoing episode whose start lies
    before the window: its warning was written when it began."""
    episode = obstruction_episode(image_rows)
    if episode is None:
        return None
    since, until, cleared_at = episode["since"], episode["until"], episode["cleared_at"]
    if cleared_at is None:
        if not episode["complete"] or now - until > OBSTRUCTION_MAX_AGE:
            return None
        return _entry(CAMERA_OBSTRUCTED, WARNING, "Camera view is blocked",
                      f"Camera view blocked since {_clock(since, now)}. Check the lens and the view. "
                      f"The algae reading is on hold until the view clears.",
                      {"state": OBSTRUCTION, "image_id": episode["image_id"], "since": since.isoformat(),
                       "captured_at": until.isoformat()},
                      f"{CAMERA_OBSTRUCTED}:camera:{since.isoformat()}", now)
    lasted = cleared_at - since
    if lasted <= OBSTRUCTION_CLEAR_AFTER or now - cleared_at > OBSTRUCTION_MAX_AGE:
        return None
    hours = lasted.total_seconds() / 3600.0
    return _entry(CAMERA_OBSTRUCTED, INFO, "Camera view is clear",
                  f"Camera view clear again at {_clock(cleared_at, now)} after being blocked "
                  f"{'since' if episode['complete'] else 'since at least'} {_clock(since, now)} "
                  f"({hours:.1f} hours). The algae reading resumes from the new frames.",
                  {"state": "clear", "since": since.isoformat(), "cleared_at": cleared_at.isoformat(),
                   "obstructed_hours": round(hours, 2)},
                  f"{CAMERA_OBSTRUCTED}:clear:{cleared_at.isoformat()}", now)


# ----------------------------------------------------------------------
# The ladder as of a time, from retained weather
# ----------------------------------------------------------------------
def lead_time_actions(storage: Any, sources: dict, now: datetime) -> dict:
    """ladder.actions evaluated on the retained weather visible at now,
    with the same inputs as koi/api/routes.py::_lead_time_actions (which
    needs the Flask app, so the worker cannot call it)."""
    slots = sources.get("stations") or {}
    rainfall_station = slots.get("rainfall") if isinstance(slots, dict) else None
    start, end = local_day_window(local_date(now) - timedelta(days=1))
    rain = storage.fetch_rainfall_total(rainfall_station, start, end, as_of=now) if rainfall_station else None
    nowcast_slot = slots.get("two-hr-forecast") if isinstance(slots, dict) else None
    nowcast_row = storage.fetch_forecast_as_of("2hr", nowcast_slot, now, valid_at=now) if nowcast_slot else None
    nowcast_data = (nowcast_row or {}).get("payload") or {}
    nowcast_value = nowcast_data.get("forecast") if isinstance(nowcast_data, dict) else None
    nowcast_text = nowcast_value.get("text") if isinstance(nowcast_value, dict) else nowcast_value
    return ladder.actions(decision_time=now, rain=rain,
                          nowcast_text=nowcast_text if isinstance(nowcast_text, str) else None)


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------
def previous_assessments(storage: Any, user_id: int) -> dict[str, Optional[dict]]:
    """The pond's latest stored assessment of each domain, read before the
    cycle pushes its own; None where there is none or it cannot be read."""
    return {
        "chemistry": fail_soft(lambda: storage.fetch_latest_evaluation(user_id), None),
        "evaporation": fail_soft(lambda: storage.fetch_latest_evaporation_evaluation(user_id), None),
        "algae": fail_soft(lambda: storage.fetch_latest_algae_evaluation(user_id), None),
    }


def enqueue(storage: Any, user_id: int, entries: list[dict]) -> list[dict]:
    """Writes each entry with its kind's cool-down; returns the rows
    stored (suppressed and failed writes are left out)."""
    stored = []
    for entry in entries:
        cooldown = int(COOLDOWNS[entry["kind"]].total_seconds())
        row = fail_soft(partial(storage.enqueue_notification, user_id, entry, cooldown), None)
        if row is not None:
            stored.append(row)
            log_event(log, "notification_queued", kind=row["kind"], dedupe_key=row["dedupe_key"],
                      notification_id=row.get("id"))
    return stored


def notify_cycle(storage: Any, user_id: int, now: datetime, current: dict[str, Optional[dict]],
                 previous: dict[str, Optional[dict]], image_rows: Optional[list[dict]],
                 default_sensor_interval: timedelta) -> list[dict]:
    """Every trigger for one pond after a poll cycle, written to the
    outbox. current and previous are each domain's assessment dicts after
    and before the cycle. Returns the rows stored. Never raises: the
    outbox must not fail a poll."""
    try:
        entries: list[Optional[dict]] = [status_red(domain, current.get(domain), previous.get(domain), now)
                                         for domain in DOMAIN_TITLES]
        sources = fail_soft(lambda: storage.fetch_dashboard_sources(user_id), None)
        if sources is not None:
            evaluated = fail_soft(lambda: lead_time_actions(storage, sources, now), None)
            entries.extend(ladder_actions(evaluated or {}, now))
        activity = fail_soft(lambda: storage.fetch_device_activity(user_id, now - device_health.WINDOW), None)
        entries.append(node_silent(activity, default_sensor_interval, now))
        entries.append(camera_obstructed(image_rows, now))
        return enqueue(storage, user_id, [e for e in entries if e is not None])
    except Exception as exc:  # noqa: BLE001 - see docstring
        log_event(log, "notification_triggers_failed", level=logging.WARNING, exc_info=True, error=str(exc))
        return []


# ----------------------------------------------------------------------
# Senders
# ----------------------------------------------------------------------
class SendError(Exception):
    """A sender could not deliver one entry; the message is stored in the
    row's error column."""


class Sender(Protocol):
    """Delivers one outbox row. delivers is False for a sender that does
    not send anything, so deliver_pending leaves the rows waiting for a
    real one rather than marking them sent."""

    name: str
    delivers: bool

    def send(self, entry: dict) -> None:
        """Delivers entry (an outbox row) or raises SendError."""
        ...


class NoopSender:
    """Sends nothing: the default until push notifications exist."""

    name = "noop"
    delivers = False

    def send(self, entry: dict) -> None:
        log_event(log, "notification_not_sent", sender=self.name, kind=entry.get("kind"),
                  notification_id=entry.get("id"))


class FcmSender:
    """Firebase Cloud Messaging. A stub: the push notification issue adds
    the device tokens and the HTTP v1 call. Until then every send fails
    with SendError, so a row given to it records why it was not sent."""

    name = "fcm"
    delivers = True

    def __init__(self, project_id: Optional[str] = None, credentials_path: Optional[str] = None):
        self.project_id = project_id
        self.credentials_path = credentials_path

    def send(self, entry: dict) -> None:
        raise SendError("FCM delivery is not implemented yet")


def build_sender(name: str = "noop", **options: Any) -> Sender:
    """The sender called name: "noop" or "fcm"."""
    if name == NoopSender.name:
        return NoopSender()
    if name == FcmSender.name:
        return FcmSender(**options)
    raise ValueError(f"unknown notification sender {name!r}")


def deliver_pending(storage: Any, user_id: int, sender: Sender, now: datetime, limit: int = 50) -> dict:
    """Hands the pond's waiting rows (no sent_at, no error), oldest first,
    to sender and records each outcome. Returns {sent, failed, waiting}.
    A sender that does not deliver leaves every row waiting."""
    rows: list[dict] = fail_soft(lambda: storage.fetch_notifications(user_id, unsent_only=True, limit=limit), [])
    if not sender.delivers:
        for row in rows:
            sender.send(row)
        return {"sent": 0, "failed": 0, "waiting": len(rows)}
    sent = failed = 0
    for row in rows:
        try:
            sender.send(row)
        except SendError as exc:
            failed += 1
            fail_soft(partial(storage.record_notification_result, user_id, row["id"], None, str(exc)[:500]), None)
            continue
        sent += 1
        fail_soft(partial(storage.record_notification_result, user_id, row["id"], now.isoformat(), None), None)
    return {"sent": sent, "failed": failed, "waiting": 0}
