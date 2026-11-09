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
