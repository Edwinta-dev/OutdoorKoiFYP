"""Issue #41: camera obstruction alerts in the notification outbox. One
warning per obstruction episode, deduplicated until the view clears, and
an info "view clear" entry when an episode that lasted more than 2 hours
ends."""
from datetime import datetime, timedelta, timezone

from conftest import USER, make_storage
from koi import notifications as nt
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage
from koi.worker import poller

# 06:10 UTC is 14:10 in Singapore.
START = datetime(2026, 10, 9, 6, 10, tzinfo=timezone.utc)
FRAME = timedelta(hours=2)  # the camera's obstruction retry interval


def frame(frame_id, at, label):
    state = [label, 0.12, 0, 0] + ([0.6, 1] if label == "obstruction" else [])
    return {"id": frame_id, "created_at": at.isoformat(), "green_ratio": 0.6 if label == "obstruction" else 0.12,
            "current_state": state}


def history(*labels):
    """One frame per label, FRAME apart, the first at START - FRAME;
    returned newest first, as the poller reads them."""
    rows = [frame(i + 1, START + (i - 1) * FRAME, label) for i, label in enumerate(labels)]
    return list(reversed(rows))


def pond():
    storage = MemoryStorage(clock=lambda: START)
    storage.add_rows("UserData", [{"userID": 7, "volume": 800, "biomass": 1.5}])
    return storage


def test_obstruction_entering_writes_blocked_since_the_first_obstructed_frame():
    rows = history("base", "obstruction", "obstruction")
    now = START + FRAME + timedelta(minutes=5)
    entry = nt.camera_obstructed(rows, now)
    assert entry["kind"] == "camera_obstructed" and entry["severity"] == "warning"
    assert entry["body"].startswith("Camera view blocked since 14:10. Check the lens and the view.")
    assert entry["dedupe_key"] == f"camera_obstructed:camera:{START.isoformat()}"
    assert entry["data"] == {"state": "obstruction", "image_id": 3, "since": START.isoformat(),
                             "captured_at": (START + FRAME).isoformat()}
    assert nt.camera_obstructed(list(reversed(rows)), now) == entry, "row order does not matter"


def test_obstruction_state_is_read_from_every_stored_shape():
    at = START + timedelta(minutes=5)
    for state in (["obstruction", 0.12, 0, 0, 0.6, 1], '["obstruction", 0.12, 0, 0, 0.6, 1]', "obstruction"):
        rows = [{"id": 1, "created_at": START.isoformat(), "current_state": state}]
        assert nt.camera_obstructed(rows, at)["severity"] == "warning", state
    for state in (["base", 0.12], '["dynamic", 0.2, 2, 0]', "base", None, [], "[not json"):
        rows = [{"id": 1, "created_at": START.isoformat(), "current_state": state}]
        assert nt.camera_obstructed(rows, at) is None, state
    assert nt.camera_obstructed(None, at) is None


def test_obstruction_is_deduplicated_until_the_state_clears():
    storage = pond()
    labels = ["base"]
    # 15 obstructed frames (28 hours), each followed by poll cycles.
    for _ in range(15):
        labels.append("obstruction")
        newest = START + (len(labels) - 2) * FRAME
        for minutes in (5, 20, 35):
            nt.enqueue(storage, 7, [nt.camera_obstructed(history(*labels), newest + timedelta(minutes=minutes))])
    [blocked] = storage.fetch_notifications(7)
    assert blocked["severity"] == "warning" and "since 14:10" in blocked["body"]

    labels.append("base")
    cleared = START + (len(labels) - 2) * FRAME
    for minutes in (5, 20):
        nt.enqueue(storage, 7, [nt.camera_obstructed(history(*labels), cleared + timedelta(minutes=minutes))])
    rows = storage.fetch_notifications(7)
    assert [r["severity"] for r in rows] == ["warning", "info"], "the clearing is written once"
    assert rows[1]["title"] == "Camera view is clear"
    assert rows[1]["dedupe_key"] == f"camera_obstructed:clear:{cleared.isoformat()}"
    assert rows[1]["data"]["obstructed_hours"] == 30.0

    # A new episode is a new key: it notifies again.
    labels.append("obstruction")
    again = START + (len(labels) - 2) * FRAME
    nt.enqueue(storage, 7, [nt.camera_obstructed(history(*labels), again + timedelta(minutes=5))])
    rows = storage.fetch_notifications(7)
    assert len(rows) == 3 and rows[2]["dedupe_key"] == f"camera_obstructed:camera:{again.isoformat()}"


def test_obstruction_clear_is_written_only_after_more_than_two_hours():
    assert nt.OBSTRUCTION_CLEAR_AFTER == timedelta(hours=2)
    base = frame(1, START - FRAME, "base")
    blocked = frame(2, START, "obstruction")

    def cleared_after(lasted):
        clear = frame(3, START + lasted, "base")
        return nt.camera_obstructed([clear, blocked, base], START + lasted + timedelta(minutes=5))

    assert cleared_after(timedelta(minutes=30)) is None
    assert cleared_after(timedelta(hours=2)) is None, "exactly 2 hours is not more than 2 hours"
    entry = cleared_after(timedelta(hours=2, minutes=1))
    assert entry["severity"] == "info" and entry["kind"] == "camera_obstructed"
    assert entry["body"].startswith("Camera view clear again at 16:11 after being blocked since 14:10 (2.0 hours).")


def test_obstruction_old_frames_do_not_notify():
    rows = history("base", "obstruction")
    assert nt.camera_obstructed(rows, START + nt.OBSTRUCTION_MAX_AGE + timedelta(minutes=1)) is None
    cleared = history("base", "obstruction", "obstruction", "base")
    clear_at = START + 2 * FRAME
    assert nt.camera_obstructed(cleared, clear_at + timedelta(hours=1))["severity"] == "info"
    assert nt.camera_obstructed(cleared, clear_at + nt.OBSTRUCTION_MAX_AGE + timedelta(minutes=1)) is None


def test_obstruction_that_started_before_a_full_window_is_not_reported_again():
    newest = START + (nt.IMAGE_WINDOW - 2) * FRAME
    full = history(*["obstruction"] * nt.IMAGE_WINDOW)
    assert nt.camera_obstructed(full, newest + timedelta(minutes=5)) is None, "its warning was written at the start"
    shorter = history(*["obstruction"] * (nt.IMAGE_WINDOW - 1))
    assert nt.camera_obstructed(shorter, newest)["severity"] == "warning", \
        "a window shorter than the fetch is the whole history"
    # Cleared after a run that fills the rest of the window: the duration
    # is a lower bound, and the message says so.
    cleared = history(*(["obstruction"] * (nt.IMAGE_WINDOW - 1) + ["base"]))
    entry = nt.camera_obstructed(cleared, newest + timedelta(minutes=5))
    assert entry["severity"] == "info" and "since at least" in entry["body"]


def test_obstruction_since_another_day_names_the_date():
    entry = nt.camera_obstructed(history("base", "obstruction"), START + timedelta(hours=20))
    assert entry["body"].startswith("Camera view blocked since 14:10 on 9 Oct.")


def test_obstruction_poller_writes_blocked_then_clear(monkeypatch):
    storage = make_storage()
    monkeypatch.setattr(nt, "lead_time_actions", lambda storage, sources, now: {"actions": []})
    start = datetime(2026, 8, 20, 0, 5, tzinfo=timezone.utc)
    config = next(r for r in storage.fetch_active_pond_configs() if r["user_id"] == USER)
    registry = EngineRegistry(storage)

    def add(frame_id, at, label):
        storage.add_rows("imageTable", [{**frame(frame_id, at, label), "user_ID": USER,
                                         "imageURL": "https://storage.invalid/f.jpg"}])

    add(1, start - timedelta(hours=3), "base")
    add(2, start - timedelta(minutes=10), "obstruction")
    for minutes in (0, 15, 30):
        poller._poll_user(registry, USER, config, now=start + timedelta(minutes=minutes))
    add(3, start + timedelta(hours=3), "base")
    poller._poll_user(registry, USER, config, now=start + timedelta(hours=3, minutes=5))
    rows = [r for r in storage.fetch_notifications(USER) if r["kind"] == "camera_obstructed"]
    assert [r["severity"] for r in rows] == ["warning", "info"]
    # 23:55 UTC on the 19th is 07:55 on the 20th in Singapore.
    assert rows[0]["body"].startswith("Camera view blocked since 07:55.")
