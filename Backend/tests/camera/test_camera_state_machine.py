"""Camera state machine (hsvEngine.evalstate) driven through POST /upload
with generated frames and MemoryStorage (issue #37).

Covers every transition in the report's camera state table and the four
evalstate fixes: obstruction is reachable [FIX 1], the stored
(state, smoothed) pair is read back [FIX 2], the smoothed baseline is
used rather than the raw ratio [FIX 3], and obstruction clears once a
frame is back within CLEAR_THRESHOLD of the baseline [FIX 4].

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


def _approx_pair(state, label, baseline):
    return state[0] == label and state[1] == pytest.approx(baseline, abs=TOL)


# --- the generated frames -------------------------------------------

@pytest.mark.parametrize("fraction", [0.0, 0.1, 0.2, 0.3, 0.5])
def test_water_frame_green_ratio_matches_the_algae_band(fraction):
    assert hsvEngine.analyze_image_bytes(water_frame(fraction)) == pytest.approx(fraction, abs=TOL)


def test_glare_blob_hides_algae_and_leaf_covers_the_view():
    assert hsvEngine.analyze_image_bytes(glare_frame(0.2)) < 0.2 - 0.03
    assert hsvEngine.analyze_image_bytes(leaf_frame()) > 0.85


# --- transitions, one stored previous frame each ---------------------

def test_base_to_dynamic_on_a_step_above_the_dynamic_threshold():
    storage = _storage((0.10, ["base", 0.10]))
    state = post(_client(storage), water_frame(0.2))
    # delta 0.10 > 0.05; EMA 0.2 * 0.20 + 0.8 * 0.10
    assert _approx_pair(state, "dynamic", 0.12)
    assert storage.rows("imageTable")[-1]["current_state"] == state


def test_dynamic_held_while_the_pond_is_still_moving():
    state = post(_client(_storage((0.20, ["dynamic", 0.12]))), water_frame(0.3))
    assert _approx_pair(state, "dynamic", 0.2 * 0.30 + 0.8 * 0.12)


def test_dynamic_back_to_base_once_the_step_settles():
    state = post(_client(_storage((0.20, ["dynamic", 0.20]))), water_frame(0.2))
    assert _approx_pair(state, "base", 0.20)


def test_base_stays_base_on_a_small_change():
    state = post(_client(_storage((0.10, ["base", 0.10]))), water_frame(0.1))
    assert _approx_pair(state, "base", 0.10)


def test_base_to_obstruction_on_a_leaf_keeps_the_baseline():
    """[FIX 1] obstruction is a reachable state, and the leaf frame does
    not enter the EMA."""
    storage = _storage((0.10, ["base", 0.10]))
    state = post(_client(storage), leaf_frame())
    assert state == ["obstruction", 0.10]
    assert storage.rows("imageTable")[-1]["current_state"] == ["obstruction", 0.10]


def test_dynamic_to_obstruction_on_a_leaf():
    state = post(_client(_storage((0.30, ["dynamic", 0.20]))), leaf_frame())
    assert state == ["obstruction", 0.20]


@pytest.mark.parametrize("frame", [leaf_frame(), water_frame(0.3)], ids=["leaf", "partly-blocked"])
def test_obstruction_held_while_the_view_is_blocked(frame):
    """[FIX 4] a frame more than CLEAR_THRESHOLD from the baseline keeps
    the latch; 0.30 against 0.10 is below the 0.40 anomaly jump."""
    state = post(_client(_storage((0.95, ["obstruction", 0.10]))), frame)
    assert state == ["obstruction", 0.10]


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
    assert state == ["obstruction", 0.10]


def test_smoothed_baseline_is_used_not_the_raw_ratio():
    """[FIX 3] the previous raw ratio is 0.50 but its smoothed baseline is
    0.10. Against the baseline 0.20 is a dynamic step; against the raw
    ratio it would have been a drop and stayed base."""
    state = post(_client(_storage((0.50, ["base", 0.10]))), water_frame(0.2))
    assert _approx_pair(state, "dynamic", 0.12)


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
    storage = _storage((0.10, ["base", 0.10]))
    client = _client(storage)
    states = [post(client, water_frame(f)) for f in (0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2)]
    labels = [s[0] for s in states]

    # The baseline climbs toward 0.20 by 20% of the gap per frame; once the
    # gap is under 0.05 the camera drops back to the base schedule.
    assert labels[0] == "dynamic"
    first_base = labels.index("base")
    assert labels[:first_base] == ["dynamic"] * first_base
    assert set(labels[first_base:]) == {"base"}
    baselines = [s[1] for s in states]
    assert baselines == sorted(baselines) and baselines[-1] < 0.2 + TOL
    assert 0.2 - baselines[first_base - 1] <= hsvEngine.DYNAMIC_RATE_THRESHOLD + TOL
