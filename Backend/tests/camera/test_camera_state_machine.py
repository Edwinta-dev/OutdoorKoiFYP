"""Camera state machine (hsvEngine.evalstate) driven through POST /upload
with generated frames and MemoryStorage (issue #37).

Covers every transition in the report's camera state table and the four
evalstate fixes: obstruction is reachable [FIX 1], the stored
(state, smoothed) pair is read back [FIX 2], the smoothed baseline is
used rather than the raw ratio [FIX 3], and obstruction clears once a
frame is back within CLEAR_THRESHOLD of the baseline [FIX 4].

Issue #38 adds hysteresis: dynamic is entered after 2 consecutive raised
frames and left after 3 consecutive stable ones, and the stored state is
[label, smoothed, raised_frames, stable_frames].

Frames are drawn with numpy and encoded with OpenCV: a muddy water colour
with a band of algae green whose height sets the green fraction. Band
heights are multiples of 16 rows so JPEG's 16x16 chroma blocks do not
blur the edge into a partial fraction.
"""
import cv2
import numpy as np
import pytest

from conftest import make_settings, ticking_clock
from koi.camera import create_app, hsvEngine
from koi.storage import MemoryStorage

SIZE = 320
WATER = (110, 90, 60)   # BGR, hue ~200 degrees: not green
ALGAE = (40, 160, 140)  # BGR, hue ~70 degrees: inside the HSV green band
LEAF = (30, 120, 90)    # BGR, darker leaf green, also inside the band
GLARE = (255, 255, 255)
USER_ID = "15"
TOL = 0.005  # JPEG error on a generated frame's green ratio


def _encode(frame):
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


def _canvas(green_fraction):
    rows = round(green_fraction * SIZE)
    assert rows % 16 == 0, "keep band edges on JPEG block boundaries"
    frame = np.empty((SIZE, SIZE, 3), dtype=np.uint8)
    frame[:] = WATER
    frame[:rows] = ALGAE
    return frame


def water_frame(green_fraction):
    """Water with the top green_fraction of the frame covered in algae."""
    return _encode(_canvas(green_fraction))


def glare_frame(green_fraction, radius=70):
    """water_frame with a sun-glare blob centred on the algae edge."""
    frame = _canvas(green_fraction)
    cv2.circle(frame, (SIZE // 2, round(green_fraction * SIZE)), radius, GLARE, -1)
    return _encode(frame)


def leaf_frame():
    """A leaf lying across the lens: it covers almost all of the frame."""
    frame = _canvas(0.0)
    cv2.ellipse(frame, (SIZE // 2, SIZE // 2), (SIZE // 2 + 40, SIZE // 2 + 10), 30, 0, 360, LEAF, -1)
    return _encode(frame)


def _storage(*history):
    """MemoryStorage holding the given previous imageTable rows, oldest first."""
    storage = MemoryStorage(clock=ticking_clock())
    storage.add_rows("imageTable", [
        {"user_ID": USER_ID, "green_ratio": raw, "current_state": state, "imageURL": "x"}
        for raw, state in history
    ])
    return storage


def _client(storage):
    app = create_app(make_settings(test_mode=True), storage=storage)
    app.config["TESTING"] = True
    return app.test_client()


def post(client, frame):
    resp = client.post("/upload", data=frame, headers={"X-User-ID": USER_ID})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["state"]


def _approx_pair(state, label, baseline, counters=None):
    return (state[0] == label and state[1] == pytest.approx(baseline, abs=TOL)
            and (counters is None or state[2:] == list(counters)))


# --- the generated frames -------------------------------------------

@pytest.mark.parametrize("fraction", [0.0, 0.1, 0.2, 0.3, 0.5])
def test_water_frame_green_ratio_matches_the_algae_band(fraction):
    assert hsvEngine.analyze_image_bytes(water_frame(fraction)) == pytest.approx(fraction, abs=TOL)


def test_glare_blob_hides_algae_and_leaf_covers_the_view():
    assert hsvEngine.analyze_image_bytes(glare_frame(0.2)) < 0.2 - 0.03
    assert hsvEngine.analyze_image_bytes(leaf_frame()) > 0.85


# --- transitions, one stored previous frame each ---------------------

def test_one_raised_frame_stays_base_and_counts_it():
    storage = _storage((0.10, ["base", 0.10]))
    state = post(_client(storage), water_frame(0.2))
    # delta 0.10 > 0.05; EMA 0.2 * 0.20 + 0.8 * 0.10; first raised frame
    assert _approx_pair(state, "base", 0.12, (1, 0))
    assert storage.rows("imageTable")[-1]["current_state"] == state


def test_base_to_dynamic_on_the_second_raised_frame():
    state = post(_client(_storage((0.20, ["base", 0.12, 1, 0]))), water_frame(0.3))
    assert _approx_pair(state, "dynamic", 0.2 * 0.30 + 0.8 * 0.12, (2, 0))


def test_a_stable_frame_resets_the_raised_count():
    state = post(_client(_storage((0.20, ["base", 0.12, 1, 0]))), water_frame(0.1))
    assert _approx_pair(state, "base", 0.2 * 0.10 + 0.8 * 0.12, (0, 1))


def test_dynamic_held_while_the_pond_is_still_moving():
    state = post(_client(_storage((0.20, ["dynamic", 0.12, 2, 0]))), water_frame(0.3))
    assert _approx_pair(state, "dynamic", 0.2 * 0.30 + 0.8 * 0.12, (3, 0))


@pytest.mark.parametrize("stable_before", [0, 1])
def test_dynamic_held_until_the_third_stable_frame(stable_before):
    state = post(_client(_storage((0.20, ["dynamic", 0.20, 0, stable_before]))), water_frame(0.2))
    assert _approx_pair(state, "dynamic", 0.20, (0, stable_before + 1))


def test_dynamic_back_to_base_on_the_third_stable_frame():
    state = post(_client(_storage((0.20, ["dynamic", 0.20, 0, 2]))), water_frame(0.2))
    assert _approx_pair(state, "base", 0.20, (0, 3))


def test_a_raised_frame_resets_the_stable_count_in_dynamic():
    state = post(_client(_storage((0.20, ["dynamic", 0.20, 0, 2]))), water_frame(0.3))
    assert _approx_pair(state, "dynamic", 0.2 * 0.30 + 0.8 * 0.20, (1, 0))


@pytest.mark.parametrize("enter, exit_, labels", [
    (2, 3, ["base", "dynamic", "dynamic", "dynamic", "dynamic", "base"]),
    (1, 1, ["dynamic", "dynamic", "dynamic", "base", "base", "base"]),
    (3, 2, ["base", "base", "dynamic", "dynamic", "base", "base"]),
])
def test_enter_and_exit_frame_counts_are_configurable(enter, exit_, labels):
    """Three frames 0.10 above the baseline, then three level with it."""
    state = ("base", 0.10)
    seen = []
    for offset in (0.10, 0.10, 0.10, 0.0, 0.0, 0.0):
        state = hsvEngine.evalstate(state[1] + offset, 0.0, list(state), enter_frames=enter, exit_frames=exit_)
        seen.append(state[0])
    assert seen == labels


def test_settings_reach_the_state_machine():
    storage = _storage((0.10, ["base", 0.10]))
    app = create_app(make_settings(test_mode=True, camera_dynamic_enter_frames=1), storage=storage)
    resp = app.test_client().post("/upload", data=water_frame(0.2), headers={"X-User-ID": USER_ID})
    assert _approx_pair(resp.get_json()["state"], "dynamic", 0.12, (1, 0))


@pytest.mark.parametrize("stored, counters", [
    (["base", 0.10], (0, 0)),
    ('["dynamic", 0.1]', (0, 0)),
    (["base", 0.10, 1, 0], (1, 0)),
    ('["dynamic", 0.1, 0, 2]', (0, 2)),
    (["base", 0.10, "x", None], (0, 0)),
    (["base", 0.10, -3, 1], (0, 1)),
    ("not json [", (0, 0)),
    (None, (0, 0)),
])
def test_counters_read_back_from_old_and_new_rows(stored, counters):
    assert hsvEngine._coerce_counters(stored) == counters


def test_old_dynamic_row_without_counters_still_loads():
    """A row written before issue #38 left the camera in dynamic with no
    counters: it stays dynamic and starts counting stable frames."""
    state = post(_client(_storage((0.20, ["dynamic", 0.20]))), water_frame(0.2))
    assert _approx_pair(state, "dynamic", 0.20, (0, 1))


def test_base_stays_base_on_a_small_change():
    state = post(_client(_storage((0.10, ["base", 0.10]))), water_frame(0.1))
    assert _approx_pair(state, "base", 0.10, (0, 1))


def test_base_to_obstruction_on_a_leaf_keeps_the_baseline():
    """[FIX 1] obstruction is a reachable state, and the leaf frame does
    not enter the EMA."""
    storage = _storage((0.10, ["base", 0.10]))
    state = post(_client(storage), leaf_frame())
    assert state == ["obstruction", 0.10, 0, 0]
    assert storage.rows("imageTable")[-1]["current_state"] == ["obstruction", 0.10, 0, 0]


def test_dynamic_to_obstruction_on_a_leaf_resets_the_counters():
    state = post(_client(_storage((0.30, ["dynamic", 0.20, 3, 0]))), leaf_frame())
    assert state == ["obstruction", 0.20, 0, 0]


@pytest.mark.parametrize("frame", [leaf_frame(), water_frame(0.3)], ids=["leaf", "partly-blocked"])
def test_obstruction_held_while_the_view_is_blocked(frame):
    """[FIX 4] a frame more than CLEAR_THRESHOLD from the baseline keeps
    the latch; 0.30 against 0.10 is below the 0.40 anomaly jump."""
    state = post(_client(_storage((0.95, ["obstruction", 0.10]))), frame)
    assert state == ["obstruction", 0.10, 0, 0]


@pytest.mark.parametrize("fraction", [0.2, 0.0], ids=["above-baseline", "below-baseline"])
def test_obstruction_cleared_within_the_clear_threshold(fraction):
    """[FIX 4] back within 0.15 of the baseline: return to base and
    resume smoothing from this frame."""
    state = post(_client(_storage((0.95, ["obstruction", 0.10]))), water_frame(fraction))
    assert _approx_pair(state, "base", 0.2 * fraction + 0.8 * 0.10)


@pytest.mark.parametrize("current, expected", [(0.24, "base"), (0.26, "obstruction"),
                                               (-0.04, "base"), (0.0, "base")])
def test_clear_threshold_edge(current, expected):
    """Either side of baseline +/- 0.15, with no JPEG error in the way."""
    assert hsvEngine.evalstate(current, 0.0, ["obstruction", 0.10])[0] == expected


def test_glare_does_not_trip_obstruction_or_dynamic():
    """Glare lowers the green ratio, so it can only pull the baseline
    down slightly; it never reads as a leaf."""
    frame = glare_frame(0.1)
    measured = hsvEngine.analyze_image_bytes(frame)
    state = post(_client(_storage((0.10, ["base", 0.10]))), frame)
    assert state[0] == "base"
    assert state[1] == pytest.approx(0.2 * measured + 0.8 * 0.10, abs=1e-4)


# --- how the previous frame is read back -----------------------------

def test_obstruction_latch_read_back_from_a_json_text_pair():
    """[FIX 2] current_state may come back as text rather than an array."""
    state = post(_client(_storage((0.95, '["obstruction", 0.1]'))), water_frame(0.3))
    assert state == ["obstruction", 0.10, 0, 0]


def test_smoothed_baseline_is_used_not_the_raw_ratio():
    """[FIX 3] the previous raw ratio is 0.50 but its smoothed baseline is
    0.10. Against the baseline 0.20 is a raised frame; against the raw
    ratio it would have been a drop and counted as stable."""
    state = post(_client(_storage((0.50, ["base", 0.10]))), water_frame(0.2))
    assert _approx_pair(state, "base", 0.12, (1, 0))


def test_first_frame_uses_the_default_baseline():
    storage = _storage()
    state = post(_client(storage), water_frame(0.1))
    assert _approx_pair(state, "base", 0.2 * 0.10 + 0.8 * 0.15)
    [row] = storage.rows("imageTable")
    assert row["current_state"] == state
    assert row["green_ratio"] == pytest.approx(0.10, abs=TOL)


def test_unrecognised_stored_state_falls_back_to_base():
    state = post(_client(_storage((0.10, ["mystery", 0.10]))), water_frame(0.1))
    assert _approx_pair(state, "base", 0.10)


# --- a sequence of frames from one camera -----------------------------

def test_leaf_episode_leaves_the_baseline_untouched():
    """Each frame reads the previous frame's stored pair: base, then a
    leaf lands, stays, half lifts, and blows off."""
    storage = _storage((0.10, ["base", 0.10]))
    client = _client(storage)
    frames = [leaf_frame(), leaf_frame(), water_frame(0.3), leaf_frame(), water_frame(0.1)]
    states = [post(client, f) for f in frames]

    assert [s[0] for s in states] == ["obstruction"] * 4 + ["base"]
    assert [s[1] for s in states[:4]] == [0.10] * 4
    assert _approx_pair(states[-1], "base", 0.10)
    assert [r["current_state"] for r in storage.rows("imageTable")[1:]] == states


def test_bloom_then_settle_walks_base_dynamic_base():
    """A step from 0.10 to 0.20 algae cover. The baseline closes 20% of the
    gap per frame, so the rises are 0.10, 0.08, 0.064, 0.0512 (raised) and
    then 0.041, 0.033, 0.026 (stable): base on the first raised frame,
    dynamic from the second, base again on the third stable frame."""
    state = ("base", 0.10)
    labels = []
    for _ in range(8):
        state = hsvEngine.evalstate(0.2, 0.0, list(state))
        labels.append(state[0])
    assert labels == ["base"] + ["dynamic"] * 5 + ["base", "base"]
    assert state[1] < 0.2


def test_bloom_then_settle_over_http():
    """The same step as real frames. JPEG error can put the fourth rise
    (0.0512) either side of 0.05, so the return to base is on frame 6 or 7."""
    storage = _storage((0.10, ["base", 0.10]))
    client = _client(storage)
    states = [post(client, water_frame(0.2)) for _ in range(9)]
    labels = [s[0] for s in states]

    assert labels[:2] == ["base", "dynamic"]
    assert [s[2:] for s in states[:2]] == [[1, 0], [2, 0]]
    first_base = labels.index("base", 1)
    assert first_base in (5, 6)
    assert labels[1:first_base] == ["dynamic"] * (first_base - 1)
    assert set(labels[first_base:]) == {"base"}
    assert states[first_base][2:] == [0, 3]
    baselines = [s[1] for s in states]
    assert baselines == sorted(baselines) and baselines[-1] < 0.2 + TOL
    assert [r["current_state"] for r in storage.rows("imageTable")[1:]] == states
