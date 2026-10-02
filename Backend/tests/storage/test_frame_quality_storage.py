"""Frame quality and thumbnail columns (issue #40, migration 0011):
MemoryStorage and SupabaseStorage (against the recording client of
test_supabase_storage.py, no network).

Run from Backend/: python -m pytest -q -k "quality or thumbnail"
"""
import pytest
from test_supabase_storage import _storage as supabase_storage

from koi.storage import MemoryStorage, StorageError

QUALITY = {"version": 1, "status": "fail", "reasons": ["too_dark"], "metrics": {"v_mean": 12.0},
           "thresholds": {"v_mean_min": 40.0}}


def test_quality_memory_insert_stores_quality_and_thumbnail_only_when_given():
    storage = MemoryStorage()
    storage.insert_image(15, 0.1, ["base", 0.1, 0, 0], "u1")
    storage.insert_image(15, 0.2, ["base", 0.1, 0, 0], "u2", quality=QUALITY,
                         thumbnail_path="15/1_photo_thumb.jpg")
    old, new = storage.rows("imageTable")
    assert "quality" not in old and "thumbnail_path" not in old
    assert new["quality"] == QUALITY and new["thumbnail_path"] == "15/1_photo_thumb.jpg"
    newest, oldest = storage.fetch_image_history(15)
    assert newest["quality"] == QUALITY and oldest["quality"] is None and oldest["thumbnail_path"] is None


def test_thumbnail_memory_delete_removes_objects():
    storage = MemoryStorage()
    storage.upload_image("b", "15/1_photo.jpg", b"a")
    storage.upload_image("b", "15/1_photo_thumb.jpg", b"t")
    storage.delete_images("b", ["15/1_photo.jpg", "15/1_photo_thumb.jpg", "15/missing.jpg"])
    assert storage.uploads == {}
    storage.failing.add("delete_images")
    with pytest.raises(StorageError):
        storage.delete_images("b", ["x"])


def test_quality_supabase_insert_sends_quality_and_thumbnail_path():
    storage, client = supabase_storage()
    storage.insert_image(15, 0.1, ["base", 0.1, 0, 0], "u", quality=QUALITY, thumbnail_path="15/1_photo_thumb.jpg")
    [(method, (row,), _)] = client.queries[-1].calls
    assert method == "insert"
    assert row["quality"] == QUALITY and row["thumbnail_path"] == "15/1_photo_thumb.jpg"
    storage.insert_image(15, 0.1, ["base", 0.1, 0, 0], "u")
    [(_, (row,), _)] = client.queries[-1].calls
    assert "quality" not in row and "thumbnail_path" not in row


def test_quality_supabase_image_history_selects_quality_and_thumbnail():
    storage, client = supabase_storage(data={"imageTable": []})
    storage.fetch_image_history(15, limit=1)
    [select] = [args[0] for method, args, _ in client.queries[-1].calls if method == "select"]
    assert "quality" in select and "thumbnail_path" in select


def test_thumbnail_supabase_delete_removes_from_the_bucket():
    storage, client = supabase_storage()
    storage.delete_images("frames", ["15/1_photo.jpg", "15/1_photo_thumb.jpg"])
    assert client.removed == [("frames", ["15/1_photo.jpg", "15/1_photo_thumb.jpg"])]
    failing, _ = supabase_storage(error=RuntimeError("down"))
    with pytest.raises(StorageError) as exc:
        failing.delete_images("frames", ["15/1_photo.jpg"])
    assert exc.value.operation == "delete_images"
