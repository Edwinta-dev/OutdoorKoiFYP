"""Named camera regions (issue #91): region validation, per-region metrics
on synthetic frames with known colours, the reference-patch correction,
the water_gap plant-cover check, and uploads storing imageTable.regions
next to a legacy mask-only config. Against MemoryStorage.

Metric tests use raw BGR arrays (no JPEG), so colours are exact. Frames
are 320x320 and every colour block is wider than the polygon over it,
because fillPoly includes the polygon's edge pixels.

Run from Backend/: python -m pytest -q -k regions
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
GREEN_WATER = (80, 115, 90)  # BGR: dull green water, below the plant saturation
LEAF = (40, 180, 70)          # BGR: saturated floating leaf
CONCRETE = (128, 128, 128)
GREY_PATCH = (180, 180, 180)
BOTTLE = (0, 0, 255)          # outside every region
USER_ID = "15"


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# Blocks in pixels (rows y0:y1, cols x0:x1) and the polygons inside them.
GAP = square(0.0, 0.0, 0.2, 0.2)            # pixels 0..64, block 0:96
RIM = square(0.8, 0.0, 1.0, 1.0)            # pixels 256..320, block 240:320
PLANTS = square(0.0, 0.6, 0.6, 1.0)         # pixels 192..320 x 0..192, block 176:320 x 0:208
REFERENCE = square(0.4, 0.0, 0.52, 0.1)     # pixels 128..166 x 0..32, block 0:48 x 112:176
REGIONS = {"water_gap": GAP, "rim": RIM, "plants": PLANTS}


def scene(gap_leaf_cols=0, cast=(1.0, 1.0, 1.0)):
    """BGR frame: green water in the gap block (its first gap_leaf_cols
    columns covered by leaves), concrete rim, plants, a grey patch, and a
    bottle everywhere else. cast multiplies (B, G, R)."""
    img = np.empty((SIZE, SIZE, 3), dtype=np.uint8)
    img[:] = BOTTLE
    img[0:96, 0:96] = GREEN_WATER
    img[0:96, 0:gap_leaf_cols] = LEAF
    img[:, 240:320] = CONCRETE
    img[176:320, 0:208] = LEAF
    img[0:48, 112:176] = GREY_PATCH
    return np.clip(img * np.array(cast), 0, 255).astype(np.uint8)


def gcc(bgr):
    b, g, r = bgr
    return g / (r + g + b)


# --- validation ----------------------------------------------------------

def test_regions_validation_accepts_required_regions_and_an_optional_reference():
    assert camera_mask.validate_regions(REGIONS) == REGIONS
    with_ref = camera_mask.validate_regions({**REGIONS, "reference": REFERENCE})
    assert list(with_ref) == ["water_gap", "rim", "plants", "reference"]
    assert camera_mask.validate_regions({**REGIONS, "rim": [[0.8, 0], [1, 0], [1, 1], [0.8, 1]]})["rim"] == RIM


@pytest.mark.parametrize("value, message", [
    ([GAP], "object of named polygons"),
    (None, "object of named polygons"),
    ({"water_gap": GAP, "rim": RIM}, "'plants' is required"),
    ({**REGIONS, "bottle": GAP}, "unknown region 'bottle'"),
    ({**REGIONS, "rim": [[0, 0], [1, 0]]}, "region 'rim': the mask must have 3 to 64 points"),
    ({**REGIONS, "reference": [[0, 0], [0.05, 0], [0.05, 0.05]]}, "region 'reference': .*at least 0.01"),
    ({**REGIONS, "water_gap": [[0, 0], [1.5, 0], [1, 1]]}, "region 'water_gap': .*between 0 and 1"),
])
def test_regions_validation_rejects_bad_configs(value, message):
    with pytest.raises(ValueError, match=message):
        camera_mask.validate_regions(value)


# --- per-region metrics -----------------------------------------------------

def test_regions_metrics_report_each_region_colour_and_ignore_the_rest():
    result = hsvEngine.region_metrics(scene(), REGIONS)
    assert result["version"] == hsvEngine.REGIONS_VERSION and result["reference"] is None
    regions = result["regions"]
    assert set(regions) == {"water_gap", "rim", "plants"}
    assert regions["water_gap"]["gcc"] == pytest.approx(gcc(GREEN_WATER))
    assert regions["rim"]["gcc"] == pytest.approx(1 / 3)
    assert regions["plants"]["gcc"] == pytest.approx(gcc(LEAF))
    assert regions["plants"]["r"] == pytest.approx(70 / 255) and regions["plants"]["b"] == pytest.approx(40 / 255)
    # The bottle's red, everywhere outside the regions, reaches none of them.
    assert all(r["r"] < 0.6 for r in regions.values())
    gap = regions["water_gap"]
    assert gap["pixels"] == 65 * 65 and gap["usable_pixels"] == gap["pixels"]
    assert gap["plant_fraction"] == 0 and gap["open_fraction"] == 1
    assert gap["usable"] is True and gap["unusable_reason"] is None
    assert "usable" not in regions["rim"] and "plant_fraction" not in regions["plants"]


def test_regions_metrics_skip_clipped_pixels():
    img = scene()
    img[0:32, 0:96] = (255, 255, 255)  # glare over the top of the gap
    gap = hsvEngine.region_metrics(img, REGIONS)["regions"]["water_gap"]
    assert gap["usable_pixels"] == 65 * (65 - 32)
    assert gap["gcc"] == pytest.approx(gcc(GREEN_WATER))


def test_regions_reference_patch_brings_a_colour_cast_grey_back_to_one_third():
    cast = (0.6, 1.0, 1.3)  # warm cast: blue down, red up
    img = scene(cast=cast)
    uncorrected = hsvEngine.region_metrics(img, REGIONS)["regions"]
    assert abs(uncorrected["rim"]["gcc"] - 1 / 3) > 0.01

    result = hsvEngine.region_metrics(img, {**REGIONS, "reference": REFERENCE})
    ref = result["reference"]
    assert ref["applied"] is True and ref["pixels"] == 39 * 33
    assert (ref["r"], ref["g"], ref["b"]) == pytest.approx((234 / 255, 180 / 255, 108 / 255))
    assert result["regions"]["reference"]["gcc"] == pytest.approx(1 / 3)
    assert result["regions"]["rim"]["gcc"] == pytest.approx(1 / 3, abs=0.003)
    # The cast is cancelled for the water too, to within uint8 rounding.
    assert result["regions"]["water_gap"]["gcc"] == pytest.approx(gcc(GREEN_WATER), abs=0.005)


def test_regions_reference_without_usable_pixels_is_not_applied():
    img = scene()
    img[0:48, 112:176] = (255, 255, 255)  # a blown-out patch
    result = hsvEngine.region_metrics(img, {**REGIONS, "reference": REFERENCE})
    assert result["reference"] == {"applied": False, "pixels": 0, "r": None, "g": None, "b": None}
    assert result["regions"]["rim"]["gcc"] == pytest.approx(1 / 3)
    assert result["regions"]["reference"]["gcc"] is None


def test_regions_gap_drifted_over_by_plants_is_unusable():
    gap = hsvEngine.region_metrics(scene(gap_leaf_cols=40), REGIONS)["regions"]["water_gap"]
    assert gap["plant_fraction"] == pytest.approx(40 / 65, abs=1e-4)
    assert gap["open_fraction"] == pytest.approx(25 / 65, abs=1e-4)
    assert gap["usable"] is False and gap["unusable_reason"] == hsvEngine.GAP_PLANTS_OVER


def test_regions_gap_edge_leaves_are_left_out_of_the_water_colour():
    """A few leaves at the edge of the gap: still usable, and the GCC is
    the water's, not shifted towards the leaf colour."""
    gap = hsvEngine.region_metrics(scene(gap_leaf_cols=8), REGIONS)["regions"]["water_gap"]
    assert gap["plant_fraction"] == pytest.approx(8 / 65, abs=1e-4)
    assert gap["usable"] is True
    assert gap["usable_pixels"] == 65 * 57
    assert gap["gcc"] == pytest.approx(gcc(GREEN_WATER))


def test_regions_green_water_is_not_counted_as_plant():
    img = scene()
    img[0:96, 0:96] = (75, 125, 85)  # greener water, saturation below PLANT_LOWER
    gap = hsvEngine.region_metrics(img, REGIONS)["regions"]["water_gap"]
    assert gap["plant_fraction"] == 0 and gap["usable"] is True


# --- uploads -------------------------------------------------------------------

def jpeg(img):
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _storage(*history):
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


def test_regions_legacy_mask_only_config_is_unchanged():
    """A config with only a mask: the same row as before regions existed,
    with no regions column written."""
    storage = _storage()
    storage.save_camera_mask(15, GAP)
    body = post(_client(storage), jpeg(scene()))
    row = latest(storage)
    assert "regions" not in row
    assert row["mask_version"] == 1 and row.get("baseline_reset") is None
    assert row["green_ratio"] == hsvEngine.analyze_image_bytes(jpeg(scene()), polygon=GAP)
    assert set(body) == {"status", "state", "sleep_sec", "reason", "next_at"}


def test_regions_upload_stores_per_region_metrics():
    storage = _storage()
    storage.save_camera_mask(15, None, {**REGIONS, "reference": REFERENCE})
    data = jpeg(scene())
    body = post(_client(storage), data)
    row = latest(storage)
    assert set(body) == {"status", "state", "sleep_sec", "reason", "next_at"}
    assert row["mask_version"] == 1
    # No mask: the green ratio is over the whole frame, as before.
    assert row["green_ratio"] == hsvEngine.analyze_image_bytes(data)
    stored = row["regions"]
    assert stored["version"] == 1 and stored["reference"]["applied"] is True
    assert set(stored["regions"]) == {"water_gap", "rim", "plants", "reference"}
    assert stored["regions"]["water_gap"]["usable"] is True
    assert stored["regions"]["water_gap"]["gcc"] == pytest.approx(gcc(GREEN_WATER), abs=0.01)
    assert stored["regions"]["rim"]["gcc"] == pytest.approx(1 / 3, abs=0.01)


def test_regions_change_resets_the_baseline():
    """The same mask with a moved water_gap is a new version: the next
    frame restarts the baseline with baseline_reset = mask_changed."""
    storage = _storage()
    storage.save_camera_mask(15, GAP, REGIONS)
    client = _client(storage)
    post(client, jpeg(scene()))
    post(client, jpeg(scene()))
    assert latest(storage).get("baseline_reset") is None
    storage.save_camera_mask(15, GAP, {**REGIONS, "water_gap": square(0.05, 0.05, 0.25, 0.25)})
    body = post(client, jpeg(scene()))
    row = latest(storage)
    assert row["mask_version"] == 2 and row["baseline_reset"] == camera_routes.RESET_MASK_CHANGED
    assert body["state"][2:] == [0, 0]


def test_regions_metrics_failure_keeps_the_frame(monkeypatch):
    def broken(img, regions):
        raise RuntimeError("boom")

    monkeypatch.setattr(hsvEngine, "region_metrics", broken)
    storage = _storage()
    storage.save_camera_mask(15, None, REGIONS)
    post(_client(storage), jpeg(scene()))
    row = latest(storage)
    assert "regions" not in row and row["mask_version"] == 1
