"""Issue #36: the notification outbox's triggers, its dedupe and
cool-down, the senders, and the poller writing entries."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, add_upload, make_storage
from koi import notifications as nt
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage
from koi.worker import poller

NOW = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
CADENCE = timedelta(minutes=15)


def red(category="Nitrite Risk", advisory="NO2 estimate is 0.40ppm."):
    return {"status": "Red", "category": category, "advisory": advisory}


def pond(user_id=7):
    storage = MemoryStorage(clock=lambda: NOW)
    storage.add_rows("UserData", [{"userID": user_id, "volume": 800, "biomass": 1.5}])
    return storage


# ----------------------------------------------------------------------
# Triggers
# ----------------------------------------------------------------------
def test_outbox_status_red_fires_only_on_the_change_to_red():
    entry = nt.status_red("chemistry", red(), {"status": "Amber"}, NOW)
    assert entry["kind"] == "status_red" and entry["severity"] == "critical"
    assert entry["title"] == "Water chemistry: Nitrite Risk" and entry["body"] == "NO2 estimate is 0.40ppm."
    assert entry["dedupe_key"] == "status_red:chemistry" and entry["created_at"] == NOW.isoformat()
    assert entry["data"]["previous_status"] == "Amber"
    assert nt.status_red("algae", red("Scrub Due"), None, NOW)["dedupe_key"] == "status_red:algae"
    assert nt.status_red("chemistry", red(), {"status": "Red"}, NOW) is None, "staying red is not a change"
    assert nt.status_red("chemistry", {"status": "Amber"}, {"status": "Green"}, NOW) is None
    assert nt.status_red("evaporation", None, {"status": "Green"}, NOW) is None


def test_outbox_ladder_action_with_lead_time_is_one_entry_per_rule_and_local_day():
    evaluated = {"actions": [
        {"rule": "REACT", "lead_time": "next morning", "action": "Check the pond after yesterday's heavy rain.",
         "evidence": "62.0 mm at station S24 on 2026-10-08."},
        {"rule": "NOWCAST", "lead_time": None, "action": "no lead time", "evidence": ""}]}
    [entry] = nt.ladder_actions(evaluated, NOW)
    assert entry["kind"] == "ladder_action" and entry["severity"] == "warning"
    assert entry["title"] == "Weather action (next morning)"
    assert entry["body"] == "Check the pond after yesterday's heavy rain."
    # 02:00 UTC is 10:00 in Singapore on the 9th.
    assert entry["dedupe_key"] == "ladder_action:REACT:2026-10-09"
    assert entry["data"]["evidence"].startswith("62.0 mm")
    assert nt.ladder_actions({"actions": []}, NOW) == []


class _WeatherStorage:
    def __init__(self, rain, nowcast):
        self.rain, self.nowcast, self.calls = rain, nowcast, []

    def fetch_rainfall_total(self, station, start, end, as_of=None):
        self.calls.append(("rain", station, start, end, as_of))
        return self.rain

    def fetch_forecast_as_of(self, product, slot, as_of, valid_at=None):
        self.calls.append(("forecast", product, slot, as_of, valid_at))
        return self.nowcast


def test_outbox_lead_time_actions_read_retained_weather_as_of_the_cycle():
    storage = _WeatherStorage({"status": "complete", "total_mm": 62.0, "station_id": "S24"},
                              {"payload": {"forecast": {"text": "Thundery Showers"}}})
    evaluated = nt.lead_time_actions(storage, {"stations": {"rainfall": "S24", "two-hr-forecast": "Bishan"}}, NOW)
    assert {a["rule"] for a in evaluated["actions"]} == {"REACT", "NOWCAST"}
    rain_call, forecast_call = storage.calls
    assert rain_call[1] == "S24" and rain_call[4] == NOW
    assert forecast_call == ("forecast", "2hr", "Bishan", NOW, NOW)
    # No assigned stations: nothing is read and nothing fires.
    assert nt.lead_time_actions(_WeatherStorage(None, None), {"stations": None}, NOW)["actions"] == []


def devices(last_seen, next_expected=None, interval_seconds=None):
    return {"devices": [{"kind": "sensor", "last_seen_at": last_seen.isoformat(),
                         "next_expected_at": next_expected.isoformat() if next_expected else None,
                         "expected_interval_seconds": interval_seconds}]}


def test_outbox_node_silent_after_three_expected_intervals():
    seen = NOW - timedelta(minutes=46)
    entry = nt.node_silent(devices(seen, seen + CADENCE), CADENCE, NOW)
    assert entry["kind"] == "node_silent" and entry["dedupe_key"] == "node_silent:sensor"
    assert "15 minutes" in entry["body"] and entry["data"]["last_seen_at"] == seen.isoformat()
    # 45 minutes is three intervals: late, not yet silent.
    seen = NOW - timedelta(minutes=45)
    assert nt.node_silent(devices(seen, seen + CADENCE), CADENCE, NOW) is None
    # A configured hourly cadence moves the line to three hours.
    seen = NOW - timedelta(hours=2)
    assert nt.node_silent(devices(seen, interval_seconds=3600), CADENCE, NOW) is None
    assert nt.node_silent(devices(NOW - timedelta(hours=3, minutes=1), interval_seconds=3600), CADENCE, NOW)
    # Never seen, no camera-only rows, or activity unknown: nothing.
    assert nt.node_silent({"devices": [{"kind": "camera", "last_seen_at": "2026-10-01T00:00:00Z"}]},
                          CADENCE, NOW) is None
    assert nt.node_silent(None, CADENCE, NOW) is None


def test_outbox_camera_obstruction_from_the_newest_recent_frame():
    frames = [{"id": 1, "created_at": (NOW - timedelta(hours=3)).isoformat(), "current_state": "base"},
              {"id": 2, "created_at": (NOW - timedelta(hours=1)).isoformat(), "current_state": "obstruction"}]
    entry = nt.camera_obstructed(frames, NOW)
    assert entry["kind"] == "camera_obstructed" and entry["data"]["image_id"] == 2
    assert nt.camera_obstructed(list(reversed(frames)), NOW)["data"]["image_id"] == 2, "order does not matter"
    assert nt.camera_obstructed(frames[:1], NOW) is None
    assert nt.camera_obstructed(frames, NOW + timedelta(hours=24)) is None, "an old frame does not notify"
    assert nt.camera_obstructed(None, NOW) is None


# ----------------------------------------------------------------------
# Dedupe and cool-down
# ----------------------------------------------------------------------
def test_outbox_dedupe_a_flapping_status_does_not_repeat_within_the_cool_down():
    storage = pond()
    times = [NOW + timedelta(minutes=15 * i) for i in range(5)]
    statuses = ["Red", "Amber", "Red", "Amber", "Red"]
    previous = None
    for at, status in zip(times, statuses, strict=True):
        current = {**red(), "status": status}
        nt.enqueue(storage, 7, [e for e in [nt.status_red("chemistry", current, previous, at)] if e])
        previous = current
    assert len(storage.fetch_notifications(7)) == 1
    # After the 12-hour cool-down the next change to red notifies again.
    later = NOW + nt.COOLDOWNS["status_red"] + timedelta(minutes=1)
    [row] = nt.enqueue(storage, 7, [nt.status_red("chemistry", red(), {"status": "Amber"}, later)])
    assert row["created_at"] == later.isoformat()
    # Keys are separate: another domain is not held back by chemistry's.
    assert nt.enqueue(storage, 7, [nt.status_red("algae", red("Scrub Due"), None, NOW)])
    assert len(storage.fetch_notifications(7)) == 3


def test_outbox_dedupe_ladder_once_per_rule_per_local_day_and_cool_downs_per_kind():
    assert nt.COOLDOWNS == {"status_red": timedelta(hours=12), "ladder_action": timedelta(hours=24),
                            "node_silent": timedelta(hours=24), "camera_obstructed": timedelta(hours=24)}
    storage = pond()
    evaluated = {"actions": [{"rule": "REACT", "lead_time": "next morning", "action": "Check the pond."}]}
    for minutes in range(0, 12 * 60, 15):  # every cycle of the same Singapore day
        nt.enqueue(storage, 7, nt.ladder_actions(evaluated, NOW + timedelta(minutes=minutes)))
    assert len(storage.fetch_notifications(7)) == 1
    next_day = NOW + timedelta(days=1)
    assert nt.enqueue(storage, 7, nt.ladder_actions(evaluated, next_day))


def test_outbox_write_failure_is_skipped_and_never_raises():
    storage = pond()
    storage.failing.add("enqueue_notification")
    assert nt.enqueue(storage, 7, [nt.status_red("chemistry", red(), None, NOW)]) == []
    storage.failing.update({"fetch_dashboard_sources", "fetch_device_activity"})
    assert nt.notify_cycle(storage, 7, NOW, {"chemistry": red()}, {}, None, CADENCE) == []


# ----------------------------------------------------------------------
# Senders
# ----------------------------------------------------------------------
def test_outbox_noop_sender_leaves_rows_waiting():
    storage = pond()
    nt.enqueue(storage, 7, [nt.status_red("chemistry", red(), None, NOW)])
    sender = nt.build_sender("noop")
    assert sender.delivers is False
    assert nt.deliver_pending(storage, 7, sender, NOW) == {"sent": 0, "failed": 0, "waiting": 1}
    assert storage.fetch_notifications(7, unsent_only=True)[0]["sent_at"] is None


def test_outbox_fcm_stub_records_why_it_did_not_send():
    storage = pond()
    nt.enqueue(storage, 7, [nt.status_red("chemistry", red(), None, NOW)])
    sender = nt.build_sender("fcm", project_id="koi-test")
    assert isinstance(sender, nt.FcmSender) and sender.project_id == "koi-test"
    assert nt.deliver_pending(storage, 7, sender, NOW) == {"sent": 0, "failed": 1, "waiting": 0}
    [row] = storage.fetch_notifications(7)
    assert row["sent_at"] is None and row["error"] == "FCM delivery is not implemented yet"
    assert storage.fetch_notifications(7, unsent_only=True) == []


def test_outbox_delivering_sender_marks_rows_sent_oldest_first():
    class Recording:
        name, delivers = "recording", True

        def __init__(self):
            self.sent = []

        def send(self, entry):
            self.sent.append(entry["dedupe_key"])

    storage = pond()
    nt.enqueue(storage, 7, [nt.status_red("algae", red("Scrub Due"), None, NOW + timedelta(minutes=1)),
                            nt.status_red("chemistry", red(), None, NOW)])
    sender = Recording()
    assert nt.deliver_pending(storage, 7, sender, NOW)["sent"] == 2
    assert sender.sent == ["status_red:chemistry", "status_red:algae"]
    assert all(r["sent_at"] == NOW.isoformat() for r in storage.fetch_notifications(7))
    with pytest.raises(ValueError):
        nt.build_sender("sms")


# ----------------------------------------------------------------------
# The poller writes entries
# ----------------------------------------------------------------------
def _poll(storage, now):
    row = next(r for r in storage.fetch_active_pond_configs() if r["user_id"] == USER)
    return poller._poll_user(EngineRegistry(storage), USER, row, now=now)


def test_outbox_poller_passes_the_assessments_before_and_after_the_cycle(monkeypatch):
    storage = make_storage()
    seen = []
    monkeypatch.setattr(nt, "notify_cycle", lambda *a, **k: seen.append(k))
    start = datetime(2026, 8, 20, 0, 5, tzinfo=timezone.utc)
    _poll(storage, start)
    _poll(storage, start + timedelta(minutes=15))
    first, second = seen
    assert first["previous"]["chemistry"] is None
    stored = storage.rows("pond_chemistry_evaluations")
    assert second["previous"]["chemistry"]["id"] == stored[0]["id"], "read before the second push"
    assert second["current"]["chemistry"]["status"] == stored[1]["status"]
    assert second["default_sensor_interval"] == CADENCE


def test_outbox_poller_writes_node_silent_once_per_cool_down():
    storage = make_storage()
    start = datetime(2026, 8, 20, 0, 5, tzinfo=timezone.utc)
    _poll(storage, start)
    assert storage.fetch_notifications(USER) == [], "the node reported a minute before the cycle"
    _poll(storage, start + timedelta(hours=2))
    _poll(storage, start + timedelta(hours=2, minutes=15))
    [row] = storage.fetch_notifications(USER)
    assert row["kind"] == "node_silent" and row["created_at"] == (start + timedelta(hours=2)).isoformat()
    # A new upload: no longer silent, and nothing new is written.
    add_upload(storage, {"pH": 7.4}, at=start + timedelta(hours=3))
    _poll(storage, start + timedelta(hours=3, minutes=1))
    assert len(storage.fetch_notifications(USER)) == 1


def test_outbox_poller_writes_camera_obstruction_and_ladder_action(monkeypatch):
    storage = make_storage()
    start = datetime(2026, 8, 20, 0, 5, tzinfo=timezone.utc)
    storage.add_rows("imageTable", [{"user_ID": USER, "green_ratio": 0.02, "current_state": "obstruction",
                                     "imageURL": "https://storage.invalid/f.jpg",
                                     "created_at": (start - timedelta(minutes=10)).isoformat()}])
    monkeypatch.setattr(nt, "lead_time_actions", lambda storage, sources, now: {"actions": [
        {"rule": "NOWCAST", "lead_time": "within 2 hours", "action": "Rain is forecast soon."}]})
    _poll(storage, start)
    _poll(storage, start + timedelta(minutes=15))
    kinds = sorted(r["kind"] for r in storage.fetch_notifications(USER))
    assert kinds == ["camera_obstructed", "ladder_action"]


def test_outbox_poller_survives_a_failing_outbox():
    storage = make_storage()
    storage.failing.add("enqueue_notification")
    storage.add_rows("imageTable", [{"user_ID": USER, "green_ratio": 0.02, "current_state": "obstruction",
                                     "imageURL": "https://storage.invalid/f.jpg",
                                     "created_at": "2026-08-20T00:00:00+00:00"}])
    _poll(storage, datetime(2026, 8, 20, 0, 5, tzinfo=timezone.utc))
    assert storage.rows("pond_chemistry_evaluations"), "the pond was still polled"
    assert storage.rows("notification_outbox") == []
