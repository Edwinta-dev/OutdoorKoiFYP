from datetime import datetime, timezone

from test_supabase_storage import _storage as supabase_storage

from koi.camera import card_masks
from koi.storage import MemoryStorage

PLACEMENTS = {mask: {"cx": 0.5, "cy": 0.5, "scale": 1, "angle": 0} for mask in card_masks.MASK_IDS}


def test_card_mask_memory_versions_are_per_pond_and_keep_old_values():
    storage = MemoryStorage(clock=lambda: datetime(2026, 11, 9, tzinfo=timezone.utc))
    first = storage.save_camera_card_masks(15, PLACEMENTS)
    moved = {**PLACEMENTS, "white": {**PLACEMENTS["white"], "cx": 0.25}}
    second = storage.save_camera_card_masks(15, moved, 88)
    assert first["version"] == 1 and second["version"] == 2
    assert first["placements"] == PLACEMENTS and first["reference_image_id"] is None
    assert storage.fetch_camera_card_masks("15") == second
    assert [v["version"] for v in storage.fetch_camera_card_mask_versions(15)] == [1, 2]
    assert storage.fetch_camera_card_masks(16) is None


def test_card_mask_supabase_reads_latest_and_calls_versioned_rpc():
    row = {"pond_id": 15, "version": 1, "placements": PLACEMENTS, "reference_image_id": None}
    storage, client = supabase_storage(data={"camera_card_masks": [row], "rpc:save_camera_card_masks": [row]})
    assert storage.fetch_camera_card_masks(15) == row
    assert ("order", ("version",), {"desc": True}) in client.queries[-1].calls
    assert storage.save_camera_card_masks(15, PLACEMENTS) == row
    assert client.queries[-1].calls == [("rpc", ("save_camera_card_masks", {
        "p_pond_id": 15, "p_placements": PLACEMENTS, "p_reference_image_id": None}), {})]


def test_camera_card_reading_memory_fetches_latest_or_selected_frame():
    storage = MemoryStorage()
    older = {"image_id": 11, "pond_id": 15, "card_masks_version": 1, "white_rgb": [1, 1, 1],
             "patches": {}, "ph": None, "nh3": None, "usable_dots": 0, "total_dots": 1, "quality": "unusable"}
    newer = {**older, "image_id": 12}
    storage.insert_camera_card_reading(older)
    storage.insert_camera_card_reading(newer)
    assert storage.fetch_camera_card_reading(15)["image_id"] == 12
    assert storage.fetch_camera_card_reading(15, 11)["image_id"] == 11


def test_camera_card_reading_supabase_is_scoped_to_pond_and_image():
    row = {"image_id": 12, "pond_id": 15, "card_masks_version": 1, "white_rgb": [1, 1, 1],
           "patches": {}, "ph": None, "nh3": None, "usable_dots": 0, "total_dots": 1,
           "quality": "unusable", "created_at": "2026-11-10T00:00:00+00:00"}
    storage, client = supabase_storage(data={"camera_card_readings": [row]})
    assert storage.fetch_camera_card_reading(15, 12) == row
    assert ("eq", ("pond_id", 15), {}) in client.queries[-1].calls
    assert ("eq", ("image_id", 12), {}) in client.queries[-1].calls


COVERED = {"image_id": 12, "pond_id": 15, "card_masks_version": 1, "white_rgb": None,
           "patches": {"ph_reading": {"median_rgb": None, "dots_used": 0, "dots_total": 7}},
           "ph": None, "nh3": None, "usable_dots": 0, "total_dots": 199, "quality": "unusable"}


def test_camera_card_reading_memory_accepts_a_reading_without_white_reference():
    storage = MemoryStorage()
    assert storage.insert_camera_card_reading(COVERED)["white_rgb"] is None
    stored = storage.fetch_camera_card_reading(15, 12)
    assert stored["white_rgb"] is None and stored["quality"] == "unusable"


def test_camera_card_reading_supabase_inserts_a_null_white_reference():
    storage, client = supabase_storage(data={"camera_card_readings": [COVERED]})
    assert storage.insert_camera_card_reading(COVERED)["white_rgb"] is None
    assert client.queries[-1].calls == [("insert", (COVERED,), {})]
