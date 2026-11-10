import numpy as np
import pytest

from koi.camera.card_reader import compare_reference
from koi.camera.turbidity import analyse


def _readings(a=(.7, .8, .9), b=(20, 15, 10)):
    clear = [[30, 50, 70], [60, 80, 100], [90, 110, 130], [120, 140, 160],
             [150, 170, 190], [180, 200, 220], [210, 220, 230]]
    names = [f"patch_{i}" for i in range(len(clear))]
    ref = {"white_rgb": [240, 240, 240], "patches": {
        n: {"median_rgb": rgb} for n, rgb in zip(names, clear, strict=True)}}
    obs = {"patches": {n: {"median_rgb": (np.asarray(rgb) * a + b).round().astype(int).tolist()}
                        for n, rgb in zip(names, clear, strict=True)}}
    obs["patches"].update({"ph_reading": {"median_rgb": (np.asarray([100, 120, 140]) * a + b).tolist()},
                           "nh3_reading": {"median_rgb": (np.asarray([110, 130, 150]) * a + b).tolist()}})
    return obs, ref


def test_affine_fit_recovers_transmission_scatter_and_index_and_corrects_discs():
    current, reference = _readings()
    fit = analyse(current, reference)
    assert fit["a"] == pytest.approx([.7, .8, .9], abs=.02)
    assert fit["b"] == pytest.approx([20, 15, 10], abs=.02)
    channel = np.asarray([20, 15, 10]) / (np.asarray([.7, .8, .9]) * 240 + [20, 15, 10])
    assert fit["index"] == pytest.approx(channel @ [.2126, .7152, .0722], abs=.01)
    assert fit["corrected"]["ph"] == pytest.approx([100, 120, 140], abs=1)
    assert fit["r2"] == pytest.approx([1, 1, 1])


def test_clear_frame_index_is_zero_and_missing_or_insufficient_reference_has_reason():
    current, reference = _readings((1, 1, 1), (0, 0, 0))
    assert analyse(current, reference)["index"] == pytest.approx(0)
    assert analyse(current, None)["reason"] == "no_reference_frame"
    reference["patches"].pop("patch_0")
    reference["patches"].pop("patch_1")
    assert analyse(current, reference)["reason"] == "fewer_than_6_usable_patches"


def test_two_pipeline_agreement_and_disagreement():
    patches = {f"p{i}": {"median_rgb": [20 + i * 25, 30 + i * 20, 40 + i * 15]} for i in range(6)}
    patches.update({"ph_ref_1": {"median_rgb": [180, 40, 40]},
                    "ph_ref_2": {"median_rgb": [40, 180, 40]},
                    "nh3_ref_1": {"median_rgb": [40, 40, 180]},
                    "nh3_ref_2": {"median_rgb": [180, 180, 40]},
                    "ph_reading": {"median_rgb": [180, 40, 40]},
                    "nh3_reading": {"median_rgb": [40, 40, 180]}})
    reference = {"white_rgb": [220, 220, 220], "patches": patches}
    current = {"patches": {name: {"median_rgb": item["median_rgb"]} for name, item in patches.items()},
               "ph": {"segment": "ph_ref_1"}, "nh3": {"segment": "nh3_ref_1"}}
    assert compare_reference(current, reference)["frame_agreement"] == "clear"
    current["ph"]["segment"] = "ph_ref_2"
    assert compare_reference(current, reference)["frame_agreement"] == "disagree"


def test_disc_colour_change_alone_does_not_move_the_fit():
    """The reagent discs change with water chemistry; only the printed
    patches may shape the clear-water fit."""
    printed = {f"ph_ref_{i}": {"median_rgb": [30 + i * 30, 50 + i * 25, 70 + i * 20]} for i in range(1, 8)}
    reference = {"white_rgb": [240, 240, 240], "patches": {
        **printed, "ph_reading": {"median_rgb": [200, 60, 40]}, "nh3_reading": {"median_rgb": [220, 210, 60]}}}
    current = {"patches": {
        **printed, "ph_reading": {"median_rgb": [40, 60, 200]}, "nh3_reading": {"median_rgb": [30, 120, 90]}}}
    fit = analyse(current, reference)
    assert fit["reason"] is None
    assert fit["patches_used"] == 7
    assert fit["a"] == pytest.approx([1, 1, 1], abs=1e-6)
    assert fit["b"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert fit["index"] == pytest.approx(0, abs=1e-6)
    # The discs are still corrected (here by the identity fit).
    assert fit["corrected"]["ph"] == pytest.approx([40, 60, 200])
    assert fit["corrected"]["nh3"] == pytest.approx([30, 120, 90])


def test_discs_do_not_count_towards_the_six_patch_minimum():
    printed = {f"ph_ref_{i}": {"median_rgb": [30 + i * 30, 50 + i * 25, 70 + i * 20]} for i in range(1, 6)}
    reading = {"white_rgb": [240, 240, 240], "patches": {
        **printed, "ph_reading": {"median_rgb": [200, 60, 40]}, "nh3_reading": {"median_rgb": [220, 210, 60]}}}
    fit = analyse(reading, reading)
    assert fit["reason"] == "fewer_than_6_usable_patches" and fit["patches_used"] == 5


def test_clear_water_frame_agrees_with_its_colour_cast_reference():
    """Reference frame with a colour cast, current frame identical: the
    corrected disc must be white-balanced like the reference segments."""
    from koi.camera.card_reader import read_frame

    colours = {"white_ref": [220, 220, 220],
               "ph_reading": [150, 110, 100], "ph_ref_1": [150, 110, 100], "ph_ref_2": [100, 130, 100],
               "ph_ref_3": [100, 100, 150],
               "nh3_reading": [170, 170, 90], "nh3_ref_1": [170, 170, 90], "nh3_ref_2": [110, 170, 110],
               "nh3_ref_3": [90, 150, 170]}
    cast = np.array([.5, 1, .6])
    patches = [{"name": name, "points": [[.1 + i * .1, .5]]} for i, name in enumerate(colours)]
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    for i, rgb in enumerate(colours.values()):
        x = 20 + i * 20
        frame[45:56, x-5:x+6] = (np.asarray(rgb) * cast).round()
    reference = read_frame(frame, patches)
    result = compare_reference(read_frame(frame, patches), reference)
    assert result["turbidity"]["index"] == pytest.approx(0, abs=1e-6)
    assert result["ph"]["segment"] == result["ph"]["corrected_segment"] == "ph_ref_1"
    assert result["nh3"]["segment"] == result["nh3"]["corrected_segment"] == "nh3_ref_1"
    assert result["agreement"] == {"ph": True, "nh3": True}
    assert result["frame_agreement"] == "clear"
