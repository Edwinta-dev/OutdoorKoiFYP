"""Obstruction off-ramp (issue #80).

A latched obstruction used to clear only by returning near the frozen
baseline. Now consecutive obstructed frames within the tolerance of their
running mean restart the baseline there after confirm_frames of them,
while a lone spike confirms nothing.
"""
import cv2
import numpy as np
import pytest

from conftest import make_settings, ticking_clock
from koi.camera import camera, create_app, hsvEngine
from koi.storage import MemoryStorage

USER_ID = "455"
SIZE = 320
WATER = (110, 90, 60)   # BGR, not green
ALGAE = (40, 160, 140)  # BGR, inside the HSV green band
TOL = 0.005


def _evolve(readings, state, **kwargs):
    """Feeds readings through evalstate, returning every state."""
    states = []
    for green in readings:
        state = hsvEngine.evalstate(green, 0.0, list(state), **kwargs)
        states.append(state)
    return states


# --- the state machine -----------------------------------------------

def test_a_lone_spike_stays_obstruction_and_starts_a_candidate():
    state = hsvEngine.evalstate(0.60, 0.0, ["base", 0.10, 0, 1])
    assert state == ("obstruction", 0.10, 0, 0, 0.60, 1)


def test_consistent_frames_rebaseline_on_the_confirming_frame():
    states = _evolve([0.19, 0.18, 0.20], ["obstruction", 0.0125])
    assert [s[0] for s in states] == ["obstruction", "obstruction", "base"]
    assert states[0][4:] == (0.19, 1)
    assert states[1][4:] == (0.185, 2)
    assert states[2] == ("base", 0.19, 0, 0)


def test_the_live_stuck_pond_unsticks_after_three_frames():
    """Pond 455 on 2026-10-03: latched at a 0.0125 baseline, reading ~0.19.
    0.17 above the baseline never cleared, and is below the 0.40 jump."""
    states = _evolve([0.1869, 0.1913, 0.1890], ["obstruction", 0.0125])
    assert states[-1][0] == "base"
    assert states[-1][1] == pytest.approx((0.1869 + 0.1913 + 0.1890) / 3, abs=1e-4)
    assert hsvEngine.rebaselined(["obstruction", 0.0125, 0, 0, *states[1][4:]], states[-1])


def test_an_inconsistent_frame_restarts_the_candidate():
    states = _evolve([0.30, 0.31, 0.60, 0.61], ["obstruction", 0.10])
    assert [s[5] for s in states] == [1, 2, 1, 2]
    assert all(s[0] == "obstruction" for s in states)
    assert states[1][1] == 0.10  # the baseline stays frozen meanwhile


def test_tolerance_is_measured_against_the_running_mean():
    states = _evolve([0.30, 0.35], ["obstruction", 0.10], tolerance=0.05)
    assert states[1][4:] == (0.325, 2)
    states = _evolve([0.30, 0.351], ["obstruction", 0.10], tolerance=0.05)
    assert states[1][4:] == (0.351, 1)


def test_zero_confirm_frames_keeps_the_old_latch():
    states = _evolve([0.19] * 10, ["obstruction", 0.0125], confirm_frames=0)
    assert all(s[0] == "obstruction" and s[1] == 0.0125 for s in states)


@pytest.mark.parametrize("confirm", [1, 2, 5])
def test_confirm_frames_is_configurable(confirm):
    states = _evolve([0.19] * confirm, ["obstruction", 0.0125], confirm_frames=confirm)
    assert [s[0] for s in states] == ["obstruction"] * (confirm - 1) + ["base"]


def test_a_return_near_the_baseline_still_clears_normally():
    state = hsvEngine.evalstate(0.12, 0.0, ["obstruction", 0.10, 0, 0, 0.30, 2])
    assert state[0] == "base"
    assert state[1] == pytest.approx(0.2 * 0.12 + 0.8 * 0.10)
    assert not hsvEngine.rebaselined(["obstruction", 0.10, 0, 0, 0.30, 2], state)


@pytest.mark.parametrize("prev, new, expected", [
    (["obstruction", 0.0125, 0, 0, 0.19, 2], ("base", 0.19, 0, 0), True),
    (["obstruction", 0.10], ("base", 0.104, 0, 1), False),
    (["obstruction", 0.10], ("obstruction", 0.10, 0, 0, 0.3, 1), False),
    (["base", 0.10, 0, 1], ("base", 0.30, 0, 0), False),
    ('["obstruction", 0.0125]', ("base", 0.19, 0, 0), True),
])
def test_rebaselined(prev, new, expected):
    assert hsvEngine.rebaselined(prev, new) is expected


@pytest.mark.parametrize("stored, candidate", [
    (["obstruction", 0.1, 0, 0, 0.3, 2], (0.3, 2)),
    ('["obstruction", 0.1, 0, 0, 0.3, 2]', (0.3, 2)),
    (["obstruction", 0.1, 0, 0], (None, 0)),
    (["obstruction", 0.1], (None, 0)),
    (["obstruction", 0.1, 0, 0, "x", 2], (None, 0)),
    (["obstruction", 0.1, 0, 0, 0.3, 0], (None, 0)),
    (None, (None, 0)),
])
def test_candidate_read_back_from_old_and_new_rows(stored, candidate):
    assert hsvEngine._coerce_candidate(stored) == candidate


# --- through POST /upload ---------------------------------------------

def _frame(green_fraction):
    rows = round(green_fraction * SIZE)
    frame = np.empty((SIZE, SIZE, 3), dtype=np.uint8)
    frame[:] = WATER
    frame[:rows] = ALGAE
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


def _client(storage, **settings):
    app = create_app(make_settings(test_mode=True, **settings), storage=storage)
    app.config["TESTING"] = True
    return app.test_client()


def _post(client, frame):
    resp = client.post("/upload", data=frame, headers={"X-User-ID": USER_ID})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_upload_rebaselines_and_records_the_reason():
    storage = MemoryStorage(clock=ticking_clock())
    storage.add_rows("imageTable", [{"user_ID": USER_ID, "green_ratio": 0.19,
                                     "current_state": ["obstruction", 0.0125], "imageURL": "x"}])
    client = _client(storage)
    replies = [_post(client, _frame(0.2)) for _ in range(3)]
    assert [r["state"][0] for r in replies] == ["obstruction", "obstruction", "base"]
    assert replies[-1]["state"][1] == pytest.approx(0.2, abs=TOL)
    rows = storage.rows("imageTable")[-3:]
    assert [r.get("baseline_reset") for r in rows] == [None, None, camera.RESET_OBSTRUCTION_PERSISTED]
    assert replies[-1]["reason"] != "obstruction_hold"


def test_settings_reach_the_off_ramp():
    storage = MemoryStorage(clock=ticking_clock())
    storage.add_rows("imageTable", [{"user_ID": USER_ID, "green_ratio": 0.19,
                                     "current_state": ["obstruction", 0.0125], "imageURL": "x"}])
    reply = _post(_client(storage, camera_obstruction_confirm_frames=1), _frame(0.2))
    assert reply["state"][0] == "base"
    assert storage.rows("imageTable")[-1]["baseline_reset"] == camera.RESET_OBSTRUCTION_PERSISTED


def test_a_failed_quality_frame_keeps_the_candidate():
    prev = ["obstruction", 0.0125, 0, 0, 0.2, 2]
    assert camera.carried_state(prev, 0.2) == prev
    assert camera.carried_state(["base", 0.1, 0, 1], 0.1) == ["base", 0.1, 0, 1]
