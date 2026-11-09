"""Colour-card patches (koi/camera/card_patches.py): validation, the demo
file's layout, and GET /v1/ponds/{pond}/camera/mask serving them.

Run from Backend/: python -m pytest -q -k card_patches
"""
import json
import math
from pathlib import Path

import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app
from koi.camera import card_masks, card_patches

DEMO = Path(__file__).resolve().parents[2] / "fixtures" / "demo_pond" / "card_patches.json"
SQUARE = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]


def patch(name="white_ref", points=None):
    return {"name": name, "polygon": SQUARE, "points": points if points is not None else [[0.15, 0.15]]}


def test_card_patches_accept_the_named_spots_and_small_areas():
    out = card_patches.validate_card_patches([patch("ph_ref_8"), patch("nh3_ref_5"), patch("ph_reading")])
    assert [p["name"] for p in out] == ["ph_ref_8", "nh3_ref_5", "ph_reading"]
    tiny = [[0.5, 0.5], [0.52, 0.5], [0.52, 0.52]]  # far below the mask's minimum area
    assert card_patches.validate_card_patches([{"name": "white_ref", "polygon": tiny}])[0]["points"] == []


@pytest.mark.parametrize("bad, message", [
    ([patch("ph_ref_9")], "unknown card patch 'ph_ref_9'"),
    ([patch("nh3_ref_6")], "unknown card patch 'nh3_ref_6'"),
    ([patch("ph_ref_0")], "unknown card patch"),
    ([patch(), patch()], "listed twice"),
    ([], "1 to 32"),
    ("x", "list of"),
    ([{"name": "white_ref", "polygon": [[0, 0], [1, 0]]}], "3 to 64 points"),
    ([{"name": "white_ref", "polygon": [[0, 0], [1.5, 0], [1, 1]]}], "between 0 and 1"),
    ([{"name": "white_ref", "polygon": [[0, 0], [0.001, 0], [0, 0.001]]}], "at least"),
    ([patch(points=[[0.5, 0.5]])], "outside its polygon"),
])
def test_card_patches_reject_bad_configs(bad, message):
    with pytest.raises(ValueError, match=message):
        card_patches.validate_card_patches(bad)


def test_demo_card_patches_layout_is_72_samples_on_the_ph_wheel():
    data = json.loads(DEMO.read_text(encoding="utf-8"))
    by_name = {p["name"]: p for p in data["patches"]}
    assert data["pond_id"] == 1
    assert set(by_name) == ({"white_ref", "nh3_reading", "ph_reading"}
                            | {f"nh3_ref_{i}" for i in range(1, 6)} | {f"ph_ref_{i}" for i in range(1, 9)})
    assert sum(len(by_name[f"ph_ref_{i}"]["points"]) for i in range(1, 9)) == 72
    assert all(len(by_name[f"nh3_ref_{i}"]["points"]) == 9 for i in range(1, 6))
    assert len(by_name["ph_reading"]["points"]) == len(by_name["nh3_reading"]["points"]) == 7
    # validated: every dot lies inside its own patch
    assert card_patches.validate_card_patches(data["patches"])


def test_card_patches_for_pond_only_for_the_named_pond(tmp_path):
    assert card_patches.patches_for_pond("", 1) is None
    assert card_patches.patches_for_pond(str(tmp_path / "missing.json"), 1) is None
    assert card_patches.patches_for_pond(str(DEMO), 2) is None
    got = card_patches.patches_for_pond(str(DEMO), 1)
    kinds = {p["name"]: p["kind"] for p in got["patches"]}
    assert kinds["white_ref"] == "white_reference" and kinds["ph_ref_3"] == "ph_reference"
    assert kinds["nh3_ref_2"] == "nh3_reference" and kinds["ph_reading"] == "ph_reading"
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert card_patches.patches_for_pond(str(broken), 1) is None


def test_mask_get_serves_card_patches_only_when_configured(tmp_path):
    plain = create_app(make_settings(), storage=make_storage())
    assert "card_patches" not in api_client(plain).get(f"/v1/ponds/{USER}/camera/mask").get_json()
    mine = tmp_path / "patches.json"
    mine.write_text(json.dumps({**json.loads(DEMO.read_text(encoding="utf-8")), "pond_id": USER}), encoding="utf-8")
    served = create_app(make_settings(camera_card_patches_file=str(mine)), storage=make_storage())
    body = api_client(served).get(f"/v1/ponds/{USER}/camera/mask").get_json()
    assert body["card_patches"]["version"] == 1 and len(body["card_patches"]["patches"]) == 16
    assert body["mask"] is None  # the rest of the response is unchanged
    elsewhere = create_app(make_settings(camera_card_patches_file=str(DEMO)), storage=make_storage())
    assert "card_patches" not in api_client(elsewhere).get(f"/v1/ponds/{USER}/camera/mask").get_json()


def test_card_mask_transform_matches_dart_pixel_space_math():
    placements = card_masks.CardPlacements.validate({
        "white": {"cx": 0.4, "cy": 0.5, "scale": 1.25, "angle": 1.5707963267948966},
        "nh3": {"cx": 0.5, "cy": 0.5, "scale": 1, "angle": 0},
        "ph": {"cx": 0.5, "cy": 0.5, "scale": 1, "angle": 0},
    })
    # Dart CardPlacement.map: rotate (x - mask.center.x) * 4/3 and
    # (y - mask.center.y) in pixel space, then divide rotated x by 4/3.
    # This point is the first white_ref sample in the shared template.
    moved = placements.patches()[0]["points"][0]
    assert moved == pytest.approx([0.601421875, 0.3329166666666667])
    # Fixed outputs for the two wheel-reading patches, calculated with the
    # same Dart pixel-space formula (identity placement for nh3 and ph).
    patches = {p["name"]: p for p in placements.patches()}
    assert patches["nh3_reading"]["points"][0] == pytest.approx([0.50035, 0.49985])
    assert patches["ph_reading"]["points"][0] == pytest.approx([0.49995, 0.5])


def test_card_mask_transform_is_rigid_and_keeps_template_samples_in_patches():
    placed = card_masks.CardPlacements.validate({
        "white": {"cx": 0.3, "cy": 0.5, "scale": 0.8, "angle": 0.37},
        "nh3": {"cx": 0.7, "cy": 0.65, "scale": 1.1, "angle": -0.41},
        "ph": {"cx": 0.5, "cy": 0.4, "scale": 0.6, "angle": 0.9},
    })
    template = card_masks.card_template()
    base = card_masks.home_placements(template).patches(template)
    output = placed.patches(template)
    assert [p["name"] for p in output] == [p["name"] for p in base]
    for mask_id in card_masks.MASK_IDS:
        home = card_masks.home_placements(template).by_mask[mask_id]
        corners = [q for p in template["patches"] if card_masks.mask_of(p["name"]) == mask_id
                   for q in p["polygon"]]
        center = ((min(q[0] for q in corners) + max(q[0] for q in corners)) / 2,
                  (min(q[1] for q in corners) + max(q[1] for q in corners)) / 2)
        assert placed.by_mask[mask_id].map(center, *center) == [placed.by_mask[mask_id].cx,
                                                                 placed.by_mask[mask_id].cy]
        first = placed.by_mask[mask_id].map(center, *corners[0])
        second = placed.by_mask[mask_id].map(center, *corners[1])
        original = home.map(center, *corners[0])
        original_second = home.map(center, *corners[1])
        # Similarity transform: pairwise distances in pixel coordinates are
        # unchanged apart from the requested scale, independent of rotation.
        moved_distance = math.hypot((first[0] - second[0]) * card_masks.ASPECT, first[1] - second[1])
        home_distance = math.hypot((original[0] - original_second[0]) * card_masks.ASPECT,
                                   original[1] - original_second[1])
        assert moved_distance == pytest.approx(home_distance * placed.by_mask[mask_id].scale)
    for patch_out in output:
        assert len(patch_out["points"]) == len(next(p for p in card_masks.card_template()["patches"]
                                                         if p["name"] == patch_out["name"]).get("points", []))
        for x, y in patch_out["points"]:
            assert card_patches._inside(patch_out["polygon"], x, y)


def test_card_mask_api_versions_validates_and_feeds_legacy_mask_response(tmp_path):
    mine = tmp_path / "patches.json"
    mine.write_text(json.dumps({**json.loads(DEMO.read_text(encoding="utf-8")), "pond_id": USER}), encoding="utf-8")
    storage = make_storage()
    storage.insert_image(USER, 0.1, ["base", 0.1, 0, 0], "memory://frame")
    app = create_app(make_settings(camera_card_patches_file=str(mine)), storage=storage)
    client = api_client(app)
    url = f"/v1/ponds/{USER}/camera/card-masks"
    assert client.get(url).get_json() == {"version": None}
    placements = {mask: {"cx": 0.5, "cy": 0.5, "scale": 1, "angle": 0}
                  for mask in card_masks.MASK_IDS}
    for bad in ({**placements, "x": {}}, {**placements, "white": {**placements["white"], "scale": 4}}):
        assert client.put(url, json={"placements": bad}).status_code == 400
    first = client.put(url, json={"placements": placements}).get_json()
    assert first["version"] == 1 and first["reference_image_id"] is None
    assert len(first["patches"]) == 16
    latest = client.get(url).get_json()
    assert latest == first
    mask_body = client.get(f"/v1/ponds/{USER}/camera/mask").get_json()
    assert mask_body["card_patches"] == {"version": 1, "patches": first["patches"]}
    second = client.put(url, json={"placements": placements, "reference_image_id": 1}).get_json()
    assert second["version"] == 2
    assert client.put(url, json={"placements": placements, "reference_image_id": 999}).status_code == 400
    assert client.get(url).get_json()["version"] == 2


def test_card_masks_get_uses_configured_template_file_until_saved(tmp_path):
    file = tmp_path / "patches.json"
    file.write_text(json.dumps({**json.loads(DEMO.read_text(encoding="utf-8")), "pond_id": USER}), encoding="utf-8")
    app = create_app(make_settings(camera_card_patches_file=str(file)), storage=make_storage())
    got = api_client(app).get(f"/v1/ponds/{USER}/camera/mask").get_json()["card_patches"]
    assert got["version"] == 1 and len(got["patches"]) == 16
