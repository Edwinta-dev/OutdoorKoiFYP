"""Per-pond water mask (issue #39): polygon validation, the HSV ratio
over the mask only, the mask_version stored on each frame, and the
baseline reset when the mask changes. Against MemoryStorage.

Frames are 320x320: plants (green) on the left half, pond water on the
right half with a band of algae across its top. The mask covers the right
half, so the plants are outside it. Edges are multiples of 16 pixels so
JPEG's 16x16 chroma blocks do not blur them.

Run from Backend/: python -m pytest -q -k "mask or camera"
"""
import cv2
import numpy as np
import pytest

from conftest import make_settings, ticking_clock
from koi.camera import camera as camera_routes
from koi.camera import create_app, hsvEngine
from koi.camera import mask as camera_mask
from koi.storage import MemoryStorage

SIZE = 320
WATER = (110, 90, 60)   # BGR, hue ~200 degrees: not green
ALGAE = (40, 160, 140)  # BGR, hue ~70 degrees: inside the HSV green band
PLANT = (40, 140, 110)  # BGR, hue ~78 degrees: inside the HSV green band
USER_ID = "15"
TOL = 0.005  # JPEG error on a generated frame's green ratio

RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
# fillPoly includes the polygon's edge pixels, so this stops one 16-pixel
# block short of the water at x = 160.
PLANTS_ONLY = [[0.0, 0.0], [0.45, 0.0], [0.45, 1.0], [0.0, 1.0]]


def frame(algae_fraction=0.0, plants=True):
    """Plants filling the left half (when plants) and water on the right
    half, its top algae_fraction covered in algae."""
    rows = round(algae_fraction * SIZE)
    assert rows % 16 == 0, "keep band edges on JPEG block boundaries"
    img = np.empty((SIZE, SIZE, 3), dtype=np.uint8)
    img[:] = WATER
    if plants:
        img[:, :SIZE // 2] = PLANT
    img[:rows, SIZE // 2:] = ALGAE
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _storage(*history):
    """MemoryStorage with pond 15 and the given previous imageTable rows
    (dicts of imageTable columns), oldest first."""
    storage = MemoryStorage(clock=ticking_clock())
    storage.add_rows("UserData", [{"userID": 15, "volume": 2000.0, "biomass": 5.0}])
    storage.add_rows("imageTable", [{"user_ID": USER_ID, "imageURL": "x", **row} for row in history])
    return storage


def _client(storage):
    app = create_app(make_settings(test_mode=True), storage=storage)
    app.config["TESTING"] = True
    return app.test_client()


def post(client, data):
    resp = client.post("/upload", data=data, headers={"X-User-ID": USER_ID})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def latest(storage):
    return storage.rows("imageTable")[-1]


# --- polygon validation -----------------------------------------------

def test_mask_polygon_accepts_a_normalised_polygon():
    assert camera_mask.validate_polygon([[0.5, 0], [1, 0], [1, 1], [0.5, 1]]) == RIGHT_HALF
    assert camera_mask.polygon_area(RIGHT_HALF) == pytest.approx(0.5)


@pytest.mark.parametrize("points, message", [
    ("not a list", "list of"),
    ([[0, 0], [1, 0]], "3 to 64 points"),
    ([[i / 70, 0.0] for i in range(65)], "3 to 64 points"),
    ([[0, 0], [1, 0], [1]], "point 2 must be two numbers"),
    ([[0, 0], [1, 0], [True, 1]], "point 2 must be two numbers"),
    ([[0, 0], [1, 0], ["1", 1]], "point 2 must be two numbers"),
    ([[0, 0], [1.2, 0], [1, 1]], "between 0 and 1"),
    ([[0, -0.1], [1, 0], [1, 1]], "between 0 and 1"),
    ([[0, 0], [0.05, 0], [0.05, 0.05]], "at least 0.01"),
    ([[0, 0], [0.5, 0.5], [1, 1]], "at least 0.01"),  # collinear
])
def test_mask_polygon_rejects_bad_shapes(points, message):
    with pytest.raises(ValueError, match=message):
        camera_mask.validate_polygon(points)


def test_mask_polygon_point_limit_is_inclusive():
    circle = [[0.5 + 0.4 * np.cos(2 * np.pi * i / 64), 0.5 + 0.4 * np.sin(2 * np.pi * i / 64)] for i in range(64)]
    assert len(camera_mask.validate_polygon(circle)) == camera_mask.MAX_POINTS


# --- the ratio over the mask --------------------------------------------

def test_mask_green_outside_the_mask_is_not_counted():
    data = frame(algae_fraction=0.0)
    assert hsvEngine.analyze_image_bytes(data) == pytest.approx(0.5, abs=TOL)  # the plants
    assert hsvEngine.analyze_image_bytes(data, polygon=RIGHT_HALF) == pytest.approx(0.0, abs=TOL)
    assert hsvEngine.analyze_image_bytes(data, polygon=PLANTS_ONLY) == pytest.approx(1.0, abs=TOL)


@pytest.mark.parametrize("fraction", [0.1, 0.2, 0.5])
def test_mask_ratio_is_over_the_mask_area(fraction):
    """Algae over the top fraction of the water half: the masked ratio is
    that fraction, whatever the plants outside do."""
    for plants in (True, False):
        ratio = hsvEngine.analyze_image_bytes(frame(fraction, plants=plants), polygon=RIGHT_HALF)
        assert ratio == pytest.approx(fraction, abs=TOL)


# --- uploads -----------------------------------------------------------

def test_mask_camera_without_a_mask_keeps_whole_frame_behaviour():
    """No mask saved: whole-frame ratio, no mask_version, and an old row
    is continued, not reset."""
    storage = _storage({"green_ratio": 0.5, "current_state": ["base", 0.5, 0, 2]})
    body = post(_client(storage), frame(0.0))
    row = latest(storage)
    assert row["green_ratio"] == pytest.approx(0.5, abs=TOL)
    assert row.get("mask_version") is None and row.get("baseline_reset") is None
    assert body["state"][2:] == [0, 3]  # the stable streak carried on


def test_mask_camera_upload_ignores_green_outside_the_mask():
    storage = _storage()
    storage.save_camera_mask(15, RIGHT_HALF)
    post(_client(storage), frame(0.0))
    row = latest(storage)
    assert row["green_ratio"] == pytest.approx(0.0, abs=TOL)
    assert row.get("mask_version") == 1
    assert row.get("baseline_reset") is None  # the first frame: nothing to reset


def test_mask_camera_reply_keys_are_unchanged():
    storage = _storage()
    storage.save_camera_mask(15, RIGHT_HALF)
    body = post(_client(storage), frame(0.1))
    assert set(body) == {"status", "state", "sleep_sec", "reason", "next_at"}


def test_mask_camera_first_masked_frame_after_old_rows_resets_the_baseline():
    """An old row (no mask_version, whole-frame baseline 0.5 from the
    plants, mid dynamic streak) then a mask: the old baseline would read
    the masked frame as a large fall. It restarts from this frame."""
    storage = _storage({"green_ratio": 0.5, "current_state": '["dynamic", 0.5, 2, 1]'})
    storage.save_camera_mask(15, RIGHT_HALF)
    body = post(_client(storage), frame(0.1))
    row = latest(storage)
    assert row.get("mask_version") == 1
    assert row.get("baseline_reset") == camera_routes.RESET_MASK_CHANGED
    assert body["state"][0] == "base"
    assert body["state"][1] == pytest.approx(0.1, abs=TOL)
    assert body["state"][2:] == [0, 0]
    assert row["current_state"] == body["state"]


def test_mask_camera_reset_clears_an_obstruction_latch():
    storage = _storage({"green_ratio": 0.9, "current_state": ["obstruction", 0.1, 0, 0]})
    storage.save_camera_mask(15, RIGHT_HALF)
    body = post(_client(storage), frame(0.2))
    assert body["state"][0] == "base"
    assert latest(storage).get("baseline_reset") == "mask_changed"


def test_mask_camera_same_version_continues_the_baseline():
    storage = _storage()
    storage.save_camera_mask(15, RIGHT_HALF)
    client = _client(storage)
    post(client, frame(0.1))
    body = post(client, frame(0.1))
    row = latest(storage)
    assert row.get("mask_version") == 1 and row.get("baseline_reset") is None
    assert body["state"][2:] == [0, 2]  # evalstate counted a second stable frame


def test_mask_camera_version_change_resets_across_a_restart():
    """Version 1 frames, then a new mask is saved and the service is
    restarted (a new app over the same database): the first version 2
    frame still resets, because the decision uses only stored rows."""
    storage = _storage()
    storage.save_camera_mask(15, RIGHT_HALF)
    post(_client(storage), frame(0.1))
    post(_client(storage), frame(0.1))
    storage.save_camera_mask(15, [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]])  # top of the water only

    body = post(_client(storage), frame(0.1))
    row = latest(storage)
    assert row.get("mask_version") == 2 and row.get("baseline_reset") == "mask_changed"
    assert row["green_ratio"] == pytest.approx(0.2, abs=TOL)  # 32 algae rows of 160 masked rows
    assert body["state"] == ["base", row["green_ratio"], 0, 0]

    post(_client(storage), frame(0.1))
    assert latest(storage).get("baseline_reset") is None
    assert [r.get("mask_version") for r in storage.rows("imageTable")] == [1, 1, 2, 2]


def test_mask_camera_save_during_an_upload_keeps_polygon_and_version_together(monkeypatch):
    """The mask is saved again while a frame is being analysed. The frame
    keeps the polygon and version it read (version 1), and the next frame
    uses version 2 and resets the baseline."""
    storage = _storage()
    storage.save_camera_mask(15, RIGHT_HALF)
    analyse = hsvEngine.analyze_image_bytes
    used = []

    def analyse_while_saving(data, roi_bounds=None, polygon=None):
        used.append(polygon)
        if len(used) == 1:
            storage.save_camera_mask(15, PLANTS_ONLY)
        return analyse(data, roi_bounds, polygon)

    monkeypatch.setattr(hsvEngine, "analyze_image_bytes", analyse_while_saving)
    client = _client(storage)
    post(client, frame(0.0))
    first = latest(storage)
    assert used[0] == RIGHT_HALF
    assert first.get("mask_version") == 1
    assert first["green_ratio"] == pytest.approx(0.0, abs=TOL)  # over version 1, not the plants

    post(client, frame(0.0))
    second = latest(storage)
    assert used[1] == PLANTS_ONLY
    assert second.get("mask_version") == 2 and second.get("baseline_reset") == "mask_changed"
    assert second["green_ratio"] == pytest.approx(1.0, abs=TOL)


def test_mask_camera_unreadable_mask_falls_back_to_the_whole_frame():
    """A failed mask read analyses the whole frame and stores no version;
    as that differs from the previous masked frame, the baseline resets."""
    storage = _storage()
    storage.save_camera_mask(15, RIGHT_HALF)
    client = _client(storage)
    post(client, frame(0.0))
    storage.failing.add("fetch_camera_mask")
    post(client, frame(0.0))
    row = latest(storage)
    assert row["green_ratio"] == pytest.approx(0.5, abs=TOL)
    assert row.get("mask_version") is None and row.get("baseline_reset") == "mask_changed"


def test_mask_camera_metric_transition_uses_the_previous_state():
    storage = _storage({"green_ratio": 0.5, "current_state": ["dynamic", 0.5, 2, 0]})
    storage.save_camera_mask(15, RIGHT_HALF)
    client = _client(storage)
    post(client, frame(0.1))
    text = client.get("/metrics").get_data(as_text=True)
    assert 'koi_camera_state_transitions_total{from_state="dynamic",to_state="base"} 1' in text
