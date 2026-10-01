"""Camera sleep schedule (imageSchedule) and the sleep_sec, reason and
next_at on every camera reply (issues #37 and #38).

Base slots are 08:00 to 18:00 every two hours, Singapore time. A wake up
to EARLY_WAKE_GRACE (10 min) before a slot counts as that slot. Outside
daylight every state uses the base schedule; TEST_MODE replaces all of
it with short fixed intervals; every result is clamped to 60 s .. 24 h.
The dynamic interval steps 2 h -> 1 h -> 30 min as the frame's green-ratio
rise passes 0.10 and 0.20.
"""
from datetime import datetime, timedelta, timezone

import pytest
from test_camera_state_machine import leaf_frame, water_frame

from conftest import make_settings
from koi.camera import camera as camera_routes
from koi.camera import create_app, hsvEngine, imageSchedule
from koi.storage import MemoryStorage

SGT = imageSchedule.TZ
HOUR = 3600


def sgt(hour, minute=0):
    return datetime(2026, 8, 3, hour, minute, tzinfo=SGT)


# --- base schedule -----------------------------------------------------

@pytest.mark.parametrize("now, expected", [
    (sgt(0, 0), 8 * HOUR),               # overnight -> 08:00
    (sgt(7, 0), 1 * HOUR),               # -> 08:00
    (sgt(8, 30), 1.5 * HOUR),            # -> 10:00
    (sgt(13, 0), 1 * HOUR),              # -> 14:00
    (sgt(17, 0), 1 * HOUR),              # -> 18:00
    (sgt(18, 30), 13.5 * HOUR),          # -> 08:00 tomorrow
    (sgt(23, 59), 8 * HOUR + 60),        # -> 08:00 tomorrow
])
def test_base_schedule_wakes_at_the_next_singapore_slot(now, expected):
    assert imageSchedule.get_base_schedule_sleep_seconds(now) == expected


@pytest.mark.parametrize("now, expected", [
    (sgt(7, 49), 11 * 60),               # 11 min early: still sleep to 08:00
    (sgt(7, 50), 2 * HOUR + 10 * 60),    # 10 min early: this wake is the 08:00 slot
    (sgt(7, 55), 2 * HOUR + 5 * 60),
    (sgt(8, 0), 2 * HOUR),
    (sgt(15, 51), 2 * HOUR + 9 * 60),    # counts as 16:00, next is 18:00
    (sgt(17, 55), 14 * HOUR + 5 * 60),   # counts as 18:00, next is 08:00 tomorrow
])
def test_early_wake_within_grace_counts_as_the_slot(now, expected):
    assert imageSchedule.get_base_schedule_sleep_seconds(now) == expected


def test_base_schedule_always_lands_on_a_slot_after_the_grace():
    start = sgt(0, 0)
    for minute in range(24 * 60):
        now = start + timedelta(minutes=minute)
        sleep = imageSchedule.get_base_schedule_sleep_seconds(now)
        assert imageSchedule.EARLY_WAKE_GRACE.total_seconds() < sleep <= imageSchedule.MAX_SLEEP_SEC
        assert (now + timedelta(seconds=sleep)).strftime("%H:%M") in imageSchedule.BASE_FIXED_TIMINGS


class _FrozenDatetime(datetime):
    """camera.py's datetime, with now() fixed at INSTANT."""
    INSTANT = datetime(2026, 8, 2, 23, 0, tzinfo=timezone.utc)  # 07:00 in Singapore

    @classmethod
    def now(cls, tz=None):
        return cls.INSTANT.astimezone(tz)


def test_upload_on_a_utc_server_schedules_in_singapore_time(monkeypatch):
    """PythonAnywhere runs in UTC: 23:00 UTC is 07:00 SGT, one hour
    before the 08:00 slot, not nine hours."""
    monkeypatch.setattr(camera_routes, "datetime", _FrozenDatetime)
    client = create_app(make_settings(), storage=MemoryStorage()).test_client()
    resp = client.post("/upload", data=water_frame(0.1), headers={"X-User-ID": "15"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["state"][0] == "base"
    assert body["sleep_sec"] == 1 * HOUR
    assert body["reason"] == "night_base"  # before the 07:50 daylight start
    assert body["next_at"] == "2026-08-03T08:00:00+08:00"


# --- per-state schedule, daylight and night ------------------------------

@pytest.mark.parametrize("now", [sgt(7, 50), sgt(12, 0), sgt(18, 10)], ids=["07:50", "12:00", "18:10"])
def test_daylight_states_use_their_own_interval(now):
    assert imageSchedule.sleep_for_state("obstruction", now=now) == imageSchedule.OBSTRUCTION_SLEEP_SEC
    assert imageSchedule.sleep_for_state("dynamic", now=now) == 2 * HOUR
    base = imageSchedule.get_base_schedule_sleep_seconds(now)
    assert imageSchedule.sleep_for_state("base", now=now) == base
    assert imageSchedule.sleep_for_state("unknown", now=now) == base


@pytest.mark.parametrize("now, expected", [
    (sgt(7, 49), 11 * 60),
    (sgt(18, 11), 13 * HOUR + 49 * 60),
    (sgt(22, 0), 10 * HOUR),
    (sgt(3, 0), 5 * HOUR),
], ids=["07:49", "18:11", "22:00", "03:00"])
@pytest.mark.parametrize("state", ["base", "dynamic", "obstruction"])
def test_night_falls_back_to_the_base_schedule(state, now, expected):
    assert not imageSchedule.is_daylight(now)
    assert imageSchedule.sleep_for_state(state, now=now) == expected


def test_daylight_window_edges():
    assert imageSchedule.is_daylight(sgt(7, 50)) and imageSchedule.is_daylight(sgt(18, 10))
    assert not imageSchedule.is_daylight(sgt(7, 49)) and not imageSchedule.is_daylight(sgt(18, 11))


# --- dynamic steps (issue #38) ---------------------------------------------

@pytest.mark.parametrize("rise, expected", [
    (-0.2, 2 * HOUR), (0.0, 2 * HOUR), (0.06, 2 * HOUR), (0.10, 2 * HOUR),
    (0.1001, 1 * HOUR), (0.15, 1 * HOUR), (0.20, 1 * HOUR),
    (0.2001, 30 * 60), (0.39, 30 * 60),
])
def test_dynamic_interval_steps_down_as_the_rise_grows(rise, expected):
    assert imageSchedule.get_dynamicstate_sleep_seconds(rise) == expected
    assert imageSchedule.sleep_for_state("dynamic", now=sgt(12), rise=rise) == expected


def test_dynamic_levels_are_configurable():
    levels = (0.02, 0.04)
    assert [imageSchedule.sleep_for_state("dynamic", now=sgt(12), rise=r, levels=levels)
            for r in (0.01, 0.03, 0.05)] == [2 * HOUR, 1 * HOUR, 30 * 60]


def test_rise_only_steps_the_dynamic_state():
    for state in ("base", "obstruction"):
        assert (imageSchedule.sleep_for_state(state, now=sgt(12), rise=0.3)
                == imageSchedule.sleep_for_state(state, now=sgt(12)))
    assert imageSchedule.sleep_for_state("dynamic", now=sgt(22), rise=0.3) == 10 * HOUR


@pytest.mark.parametrize("raw, expected", [("0.05,0.3", (0.05, 0.3)), ("0.1, 0.2", (0.1, 0.2))])
def test_rate_levels_setting_parses(raw, expected):
    assert make_settings(camera_dynamic_rate_levels=raw).camera_dynamic_rate_levels == expected


@pytest.mark.parametrize("raw", ["0.2,0.1", "0.1", "0,0.1", "a,b"])
def test_rate_levels_setting_rejects_bad_values(raw):
    with pytest.raises(ValueError):
        make_settings(camera_dynamic_rate_levels=raw)


class _NoonDatetime(_FrozenDatetime):
    INSTANT = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)  # 12:00 in Singapore


@pytest.mark.parametrize("levels, expected", [("0.10,0.25", 1 * HOUR), ("0.10,0.15", 30 * 60)])
def test_rate_levels_setting_reaches_the_reply(levels, expected, monkeypatch):
    """0.10 -> 0.30 is a 0.20 rise: 1 h with an upper level of 0.25, 30 min
    with 0.15. The stored raised count puts this frame into dynamic."""
    monkeypatch.setattr(camera_routes, "datetime", _NoonDatetime)
    storage = MemoryStorage()
    storage.add_rows("imageTable", [{"user_ID": "15", "green_ratio": 0.2,
                                     "current_state": ["base", 0.1, 1, 0], "imageURL": "x"}])
    client = create_app(make_settings(camera_dynamic_rate_levels=levels), storage=storage).test_client()
    body = client.post("/upload", data=water_frame(0.3), headers={"X-User-ID": "15"}).get_json()
    assert body["state"][0] == "dynamic"
    assert (body["sleep_sec"], body["reason"]) == (expected, "dynamic_rising")
    assert datetime.fromisoformat(body["next_at"]) == sgt(12) + timedelta(seconds=expected)


# --- reason codes and next_at (issue #38) -----------------------------------

@pytest.mark.parametrize("state, now, test_mode, reason", [
    ("base", sgt(12), False, "slot"),
    ("unknown", sgt(12), False, "slot"),
    ("dynamic", sgt(12), False, "dynamic_rising"),
    ("obstruction", sgt(12), False, "obstruction_hold"),
    ("base", sgt(22), False, "night_base"),
    ("dynamic", sgt(3), False, "night_base"),
    ("obstruction", sgt(18, 30), False, "night_base"),
    ("dynamic", sgt(12), True, "test_mode"),
    ("base", sgt(23), True, "test_mode"),
])
def test_next_wake_reason(state, now, test_mode, reason):
    wake = imageSchedule.next_wake(state, now=now, test_mode=test_mode)
    assert wake.reason == reason
    assert wake.sleep_sec == imageSchedule.sleep_for_state(state, now=now, test_mode=test_mode)
    assert wake.next_at == now + timedelta(seconds=wake.sleep_sec)


def test_every_reason_code_is_listed():
    assert set(imageSchedule.REASONS) == {"slot", "dynamic_rising", "obstruction_hold",
                                         "night_base", "error_fallback", "test_mode"}


def test_reply_keys_and_next_at_format():
    wake = imageSchedule.next_wake("base", now=datetime(2026, 8, 3, 13, 0, 42, 999_000, tzinfo=SGT))
    assert wake.reply() == {"sleep_sec": 3557, "reason": "slot", "next_at": "2026-08-03T14:00:00+08:00"}


def test_fallback_wake():
    wake = imageSchedule.fallback_wake(camera_routes.FALLBACK_SLEEP_SEC, now=sgt(12))
    assert wake.reply() == {"sleep_sec": 7200, "reason": "error_fallback",
                            "next_at": "2026-08-03T14:00:00+08:00"}
    assert imageSchedule.fallback_wake(5, now=sgt(12)).sleep_sec == imageSchedule.MIN_SLEEP_SEC


# --- TEST_MODE -----------------------------------------------------------

@pytest.mark.parametrize("now", [sgt(12, 0), sgt(23, 0)], ids=["day", "night"])
def test_test_mode_intervals_ignore_the_clock(now):
    for state, seconds in imageSchedule.TEST_SLEEP_SEC.items():
        assert imageSchedule.sleep_for_state(state, now=now, test_mode=True) == seconds
    assert imageSchedule.TEST_SLEEP_SEC == {"base": 100, "obstruction": 80, "dynamic": 60}
    assert imageSchedule.sleep_for_state("unknown", now=now, test_mode=True) == 100


def test_test_mode_env_var_reaches_the_upload_reply(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "1")
    storage = MemoryStorage()
    storage.add_rows("imageTable", [{"user_ID": "15", "green_ratio": 0.1,
                                     "current_state": ["base", 0.1], "imageURL": "x"}])
    client = create_app(make_settings(), storage=storage).test_client()
    expected = {"obstruction": 80, "base": 100}
    for frame, state in ((leaf_frame(), "obstruction"), (water_frame(0.1), "base")):
        body = client.post("/upload", data=frame, headers={"X-User-ID": "15"}).get_json()
        assert body["state"][0] == state
        assert body["sleep_sec"] == expected[state]
        assert body["reason"] == "test_mode"


# --- clamps ----------------------------------------------------------------

@pytest.mark.parametrize("seconds, expected", [
    (-5, 60), (0, 60), (59.9, 60), (60, 60), (61.7, 61),
    (86_400, 86_400), (86_401, 86_400), (10 ** 9, 86_400),
])
def test_clamp_bounds(seconds, expected):
    assert imageSchedule._clamp(seconds) == expected
    assert (imageSchedule.MIN_SLEEP_SEC, imageSchedule.MAX_SLEEP_SEC) == (60, 86_400)


def test_state_intervals_are_clamped(monkeypatch):
    monkeypatch.setattr(imageSchedule, "DYNAMIC_STEP_SEC", (5, 5, 5))
    monkeypatch.setattr(imageSchedule, "OBSTRUCTION_SLEEP_SEC", 3 * 24 * HOUR)
    assert imageSchedule.sleep_for_state("dynamic", now=sgt(12)) == 60
    assert imageSchedule.sleep_for_state("obstruction", now=sgt(12)) == 86_400


# --- every error reply carries sleep_sec --------------------------------------

def _boom(*args, **kwargs):
    raise RuntimeError("simulated fault")


ERROR_CASES = {
    "unauthorised": dict(status=401, data=water_frame(0.1), settings={"device_token": "secret"},
                         headers={"X-Device-Token": "wrong"}),
    "empty_body": dict(status=400, data=b""),
    "payload_too_large": dict(status=413, data=b"\xff" * (camera_routes.MAX_UPLOAD_BYTES + 1)),
    "image_unreadable": dict(status=422, data=b"not a jpeg"),
    "not_found": dict(status=404, path="/no-such-route", method="get"),
    "method_not_allowed": dict(status=405, method="get"),
    "internal_error": dict(status=500, data=water_frame(0.1), patch=(hsvEngine, "evalstate", _boom)),
    "storage_unavailable": dict(status=503, data=water_frame(0.1), failing="upload_image"),
}


@pytest.mark.parametrize("case", ERROR_CASES.values(), ids=ERROR_CASES.keys())
def test_every_error_reply_carries_sleep_sec(case, monkeypatch):
    storage = MemoryStorage()
    if "failing" in case:
        storage.failing.add(case["failing"])
    if "patch" in case:
        monkeypatch.setattr(*case["patch"])
    client = create_app(make_settings(**case.get("settings", {})), storage=storage).test_client()
    call = getattr(client, case.get("method", "post"))
    kwargs = {"data": case["data"]} if "data" in case else {}
    resp = call(case.get("path", "/upload"), headers={"X-User-ID": "15", **case.get("headers", {})}, **kwargs)

    assert resp.status_code == case["status"]
    body = resp.get_json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert isinstance(body["sleep_sec"], int)
    assert imageSchedule.MIN_SLEEP_SEC <= body["sleep_sec"] <= imageSchedule.MAX_SLEEP_SEC
    assert body["reason"] in imageSchedule.REASONS
    next_at = datetime.fromisoformat(body["next_at"])
    assert next_at.utcoffset() == timedelta(hours=8)
    assert abs((next_at - datetime.now(SGT)).total_seconds() - body["sleep_sec"]) < 5
    if case["status"] != 503:
        # Nothing was computed, so the ESP32 backs off for the fallback time.
        assert body["sleep_sec"] == camera_routes.FALLBACK_SLEEP_SEC
        assert body["reason"] == "error_fallback"
    else:
        assert body["reason"] != "error_fallback"
