"""Camera water mask storage (issue #39, migration 0010): MemoryStorage
and SupabaseStorage (against the recording client of
test_supabase_storage.py, no network).

Run from Backend/: python -m pytest -q -k mask
"""
from datetime import datetime, timezone

from test_supabase_storage import _storage as supabase_storage

from koi.storage import MemoryStorage

RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
TOP_HALF = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]


def _memory():
    return MemoryStorage(clock=lambda: datetime(2026, 10, 2, tzinfo=timezone.utc))


def test_mask_memory_save_numbers_versions_per_pond_and_keeps_them():
    storage = _memory()
    assert storage.fetch_camera_mask(15) is None and storage.fetch_camera_mask_versions(15) == []
    assert storage.save_camera_mask(15, RIGHT_HALF)["mask_version"] == 1
    assert storage.save_camera_mask(16, TOP_HALF)["mask_version"] == 1
    saved = storage.save_camera_mask(15, TOP_HALF)
    assert saved == {"pond_id": 15, "mask": TOP_HALF, "mask_version": 2, "updated_at": "2026-10-02T00:00:00+00:00"}
    assert storage.fetch_camera_mask("15") == saved
    assert [(v["mask_version"], v["mask"]) for v in storage.fetch_camera_mask_versions(15)] == [
        (1, RIGHT_HALF), (2, TOP_HALF)]
    assert len(storage.rows("camera_config")) == 2


def test_mask_memory_image_insert_stores_mask_columns_only_when_given():
    storage = _memory()
    storage.insert_image(15, 0.1, ["base", 0.1, 0, 0], "u1")
    storage.insert_image(15, 0.2, ["base", 0.2, 0, 0], "u2", mask_version=3, baseline_reset="mask_changed")
    old, new = storage.rows("imageTable")
    assert "mask_version" not in old and "baseline_reset" not in old
    assert new["mask_version"] == 3 and new["baseline_reset"] == "mask_changed"
    assert storage.fetch_image_history(15, limit=1)[0]["mask_version"] == 3


def test_mask_supabase_reads_config_and_versions_by_pond():
    row = {"pond_id": 15, "mask": RIGHT_HALF, "mask_version": 1, "updated_at": "2026-10-02T00:00:00+00:00"}
    storage, client = supabase_storage(data={"camera_config": [row], "camera_mask_version": []})
    assert storage.fetch_camera_mask(15) == row
    assert ("eq", ("pond_id", 15), {}) in client.queries[-1].calls
    assert storage.fetch_camera_mask_versions(15) == []
    assert ("order", ("mask_version",), {}) in client.queries[-1].calls


def test_mask_supabase_save_calls_the_rpc():
    row = {"pond_id": 15, "mask": RIGHT_HALF, "mask_version": 4, "updated_at": "2026-10-02T00:00:00+00:00"}
    storage, client = supabase_storage(data={"rpc:save_camera_mask": [row]})
    assert storage.save_camera_mask(15, RIGHT_HALF) == row
    assert client.queries[-1].calls == [("rpc", ("save_camera_mask", {"p_pond_id": 15, "p_mask": RIGHT_HALF}), {})]


def test_mask_supabase_image_insert_leaves_out_unset_mask_columns():
    """A frame without a mask is inserted with the pre-0010 columns only,
    so the upload still works against a database without the migration."""
    storage, client = supabase_storage()
    storage.insert_image(15, 0.1, ["base", 0.1, 0, 0], "u")
    [(method, (row,), _)] = client.queries[-1].calls
    assert method == "insert" and set(row) == {"user_ID", "green_ratio", "current_state", "imageURL"}
    storage.insert_image(15, 0.1, ["base", 0.1, 0, 0], "u", mask_version=2, baseline_reset="mask_changed")
    [(_, (row,), _)] = client.queries[-1].calls
    assert row["mask_version"] == 2 and row["baseline_reset"] == "mask_changed"


def test_mask_supabase_image_history_selects_the_mask_columns():
    storage, client = supabase_storage(data={"imageTable": []})
    storage.fetch_image_history(15, limit=1)
    [select] = [args[0] for method, args, _ in client.queries[-1].calls if method == "select"]
    assert "mask_version" in select and "baseline_reset" in select
