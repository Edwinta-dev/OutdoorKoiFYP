"""Issue #90: exact decoded colours, clipping, mask/grid and upload contract."""
import cv2
import numpy as np
import pytest

from conftest import make_settings
from koi.camera import create_app, hsvEngine, quality
from koi.storage import MemoryStorage


@pytest.mark.parametrize("rgb", [(120, 120, 120), (0, 120, 0), (120, 0, 0),
                                 (0, 0, 120), (60, 180, 90)])
def test_known_colour_means(rgb):
    img = np.full((9, 9, 3), rgb[::-1], dtype=np.uint8)
    gcc, colour = quality.colour_metrics(img)
    r, g, b = np.array(rgb) / 255
    assert gcc == pytest.approx(g / (r + g + b))
    assert colour["exg"] == pytest.approx((2 * g - r - b + 2) / 4)
    assert [colour[k] for k in ("r", "g", "b")] == pytest.approx([r, g, b])
    assert colour["version"] == 1
    assert colour["grid"] == {"rows": 3, "cols": 3, "gcc": pytest.approx([gcc] * 9)}


def test_gcc_is_mean_of_pixel_coordinates_and_suppresses_brightness():
    img = np.array([[(20, 40, 20), (120, 60, 120)]], dtype=np.uint8)
    means = quality.colour_means(img)
    assert means["gcc"] == pytest.approx((0.5 + 0.2) / 2)
    assert quality.colour_means(img // 2)["gcc"] == pytest.approx(means["gcc"])


def test_mask_means_use_same_pixels_as_green_ratio_but_grid_keeps_whole_frame():
    img = np.full((12, 12, 3), (120, 120, 120), dtype=np.uint8)
    img[:, 6:] = (80, 140, 120)
    polygon = [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]
    whole, whole_colour = quality.colour_metrics(img)
    masked, masked_colour = quality.colour_metrics(img, polygon)
    assert whole == pytest.approx((1 / 3 + 140 / 340) / 2)
    assert masked == pytest.approx(140 / 340)
    assert [masked_colour[k] for k in ("r", "g", "b")] == pytest.approx([120/255, 140/255, 80/255])
    assert masked_colour["grid"] == whole_colour["grid"]
    ok, data = cv2.imencode(".png", img)
    assert ok
    assert hsvEngine.analyze_image_bytes(data.tobytes(), polygon=polygon) == 1.0
    assert hsvEngine.analyze_image_bytes(data.tobytes()) == 0.5
    result = quality.assess(img, polygon)
    assert result["metrics"]["gcc"] == masked
    assert result["metrics"]["s_mean"] == pytest.approx(109, abs=1)


def test_clipped_pixels_are_excluded_from_every_colour_mean_and_grid():
    img = np.array([[(0, 0, quality.CLIP_LOW_V), (120, 120, 120),
                     (0, quality.CLIP_HIGH_V, 0)]], dtype=np.uint8)
    gcc, colour = quality.colour_metrics(img, rows=1, cols=3)
    assert gcc == pytest.approx(1 / 3)
    assert colour["exg"] == pytest.approx(0.5)
    assert [colour[k] for k in ("r", "g", "b")] == pytest.approx([120 / 255] * 3)
    assert colour["grid"]["gcc"] == [None, pytest.approx(1 / 3), None]
    assert quality.assess(img)["metrics"]["clipped_fraction"] == pytest.approx(2/3, abs=0.0001)


def test_no_unclipped_pixels_and_empty_grid_cells_store_nulls():
    gcc, colour = quality.colour_metrics(np.zeros((1, 1, 3), dtype=np.uint8))
    assert gcc is None
    assert all(colour[k] is None for k in ("r", "g", "b", "exg"))
    assert colour["grid"]["gcc"] == [None] * 9
    assert quality.assess(np.zeros((1, 1, 3), dtype=np.uint8))["reasons"] == ["too_dark", "clipped"]
    assert quality.assess(np.zeros((1, 1, 3), dtype=np.uint8), s_mean_min=1)["metrics"]["s_mean"] is None


@pytest.mark.parametrize("index", range(9))
def test_grid_is_row_major_and_covers_non_divisible_frame(index):
    img = np.full((10, 11, 3), 120, dtype=np.uint8)
    row, col = divmod(index, 3)
    img[row * 10 // 3:(row + 1) * 10 // 3, col * 11 // 3:(col + 1) * 11 // 3] = (60, 180, 90)
    _, colour = quality.colour_metrics(img)
    expected = [1 / 3] * 9
    expected[index] = 180 / 330
    assert colour["grid"]["gcc"] == pytest.approx(expected)


@pytest.mark.parametrize("bgr", [(40, 120, 60), (120, 40, 60)])
def test_colour_cast_outside_either_gcc_bound(bgr):
    result = quality.assess(np.full((12, 12, 3), bgr, dtype=np.uint8))
    assert result["version"] == 2
    assert result["status"] == "fail"
    assert result["reasons"] == ["colour_cast"]


def test_saturation_rule_is_independent_of_gcc_and_configurable():
    img = np.full((12, 12, 3), (0, 80, 120), dtype=np.uint8)  # GCC .4, saturation 255
    assert quality.assess(img)["reasons"] == ["colour_cast"]
    assert quality.assess(img, s_mean_max=255)["status"] == "pass"
    grey = np.full((12, 12, 3), 120, dtype=np.uint8)
    assert quality.assess(grey, s_mean_min=10)["reasons"] == ["colour_cast"]


def test_saturation_mean_excludes_crushed_and_blown_pixels():
    img = np.array([[(0, 255, 0), (120, 120, 120)]], dtype=np.uint8)
    result = quality.assess(img, gcc_min=0, gcc_max=1, s_mean_min=0, s_mean_max=0)
    assert result["metrics"]["s_mean"] == 0
    assert "colour_cast" not in result["reasons"]


def test_upload_stores_colour_and_settings_without_changing_reply_or_green_ratio():
    img = np.full((12, 12, 3), (40, 120, 60), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", img)
    assert ok
    data = encoded.tobytes()
    storage = MemoryStorage()
    state = ["base", 0.1, 1, 0]
    storage.insert_image(15, 0.1, state, "old", quality={"version": 1, "status": "pass"})
    settings = make_settings(test_mode=True, camera_colour_grid_rows=2, camera_colour_grid_cols=4)
    client = create_app(settings, storage=storage).test_client()
    response = client.post("/upload", data=data, headers={"X-User-ID": "15"})
    assert response.status_code == 200
    assert set(response.get_json()) == {"status", "state", "sleep_sec", "reason", "next_at"}
    row = storage.fetch_image_history(15)[0]
    assert row["green_ratio"] == hsvEngine.analyze_image_bytes(data)
    assert row["current_state"] == state
    assert row["quality"]["reasons"] == ["colour_cast"]
    assert row["quality"]["thresholds"] == quality.thresholds()
    gcc, colour = quality.colour_metrics(quality.decode(data), rows=2, cols=4)
    assert row["gcc"] == gcc and row["colour"] == colour
    assert len(row["colour"]["grid"]["gcc"]) == 8
    # A configurable band can accept the very same frame; HSV remains the state input.
    settings = make_settings(test_mode=True, camera_gcc_max=0.6, camera_s_mean_max=255)
    response = create_app(settings, storage=storage).test_client().post(
        "/upload", data=data, headers={"X-User-ID": "15"})
    row = storage.fetch_image_history(15)[0]
    assert row["quality"]["status"] == "pass"
    assert row["quality"]["thresholds"]["gcc_max"] == 0.6
    assert row["quality"]["thresholds"]["s_mean_max"] == 255
    assert response.get_json()["state"] == list(hsvEngine.evalstate(row["green_ratio"], 0.1, state))


@pytest.mark.parametrize("rows,cols", [(0, 3), (3, 0), (-1, 3)])
def test_colour_grid_rejects_nonpositive_size(rows, cols):
    with pytest.raises(ValueError, match="positive"):
        quality.colour_metrics(np.full((3, 3, 3), 120, dtype=np.uint8), rows=rows, cols=cols)
