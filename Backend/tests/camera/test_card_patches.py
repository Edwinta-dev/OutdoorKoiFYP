"""Colour-card patches (koi/camera/card_patches.py): validation, the demo
file's layout, and GET /v1/ponds/{pond}/camera/mask serving them.

Run from Backend/: python -m pytest -q -k card_patches
"""
import json
from pathlib import Path

import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app
from koi.camera import card_patches

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
