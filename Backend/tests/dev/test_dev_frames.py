from types import SimpleNamespace

import pytest

from koi.camera.hsvEngine import analyze_image_bytes
from koi.dev import frames


class FakeStorage:
    """The two Storage methods frames.py uses (koi/storage/base.py)."""

    def __init__(self):
        self.uploads, self.removed = [], []

    def upload_image(self, bucket, path, data):
        self.uploads.append((bucket, path, data))
        return f"http://127.0.0.1:54321/storage/v1/object/public/{bucket}/{path}"

    def delete_images(self, bucket, paths):
        self.removed.append((bucket, paths))


def _settings(public_url=""):
    return SimpleNamespace(pond_image_bucket="imageAnalysisBucket", dev_supabase_public_url=public_url)


@pytest.mark.parametrize("ratio", [0.0, 0.02, 0.0569, 0.5, 1.0])
def test_synthetic_frame_matches_seed_green_ratio(ratio):
    assert analyze_image_bytes(frames.synthetic_frame(ratio)) == pytest.approx(ratio, abs=0.001)


def test_upload_seed_frames_writes_matching_objects_and_local_urls():
    storage = FakeStorage()
    tables = {"imageTable": [{"id": 7, "user_ID": 1, "green_ratio": 0.0569,
                               "imageURL": "https://example.invalid/frame.jpg"}]}

    assert frames.upload_seed_frames(tables, storage, _settings()) == 1
    bucket, path, data = storage.uploads[0]
    assert (bucket, path) == ("imageAnalysisBucket", "local-dev/1/7.jpg")
    assert analyze_image_bytes(data) == pytest.approx(0.0569, abs=0.001)
    assert tables["imageTable"][0]["imageURL"].startswith("http://127.0.0.1:54321/")
    assert "example.invalid" not in tables["imageTable"][0]["imageURL"]


def test_public_url_override_keeps_physical_device_urls_reachable():
    tables = {"imageTable": [{"id": 7, "user_ID": 1, "green_ratio": 0.02}]}
    frames.upload_seed_frames(tables, FakeStorage(), _settings("http://192.168.1.20:54321/"))
    assert tables["imageTable"][0]["imageURL"].startswith("http://192.168.1.20:54321/")


def test_remove_seed_frames_deletes_deterministic_objects():
    storage = FakeStorage()
    tables = {"imageTable": [{"id": 7, "user_ID": 1}, {"id": 8, "user_ID": 2}]}

    frames.remove_seed_frames(tables, storage, _settings())

    assert storage.removed == [("imageAnalysisBucket", ["local-dev/1/7.jpg", "local-dev/2/8.jpg"])]
