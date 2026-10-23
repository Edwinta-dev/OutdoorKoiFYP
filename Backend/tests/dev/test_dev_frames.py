from types import SimpleNamespace

import pytest

from koi.camera import quality
from koi.camera.hsvEngine import analyze_image_bytes, region_metrics
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


def _demo_seed_tables():
    import json
    from pathlib import Path

    seed = json.loads((Path(frames.FRAME_DIR).parent / "seed.json").read_text(encoding="utf-8"))
    return seed["tables"]


def test_real_deployment_frames_are_bundled_and_small():
    files = frames.demo_frame_files()
    assert [f.name for f in files] == ["00062.jpg", "00063.jpg", "00064.jpg", "00065.jpg"]
    assert sum(f.stat().st_size for f in files) < 1_000_000
    assert quality.decode(files[0].read_bytes()).shape == (480, 640, 3)


def test_demo_frames_are_early_clear_and_register_with_each_other():
    """Only clear frames (mean brightness well above the dark and turbid
    later ones), and the card sits within 3 px in all of them, which is what
    lets one set of card patches serve every frame."""
    import cv2
    import numpy as np

    gray = [cv2.cvtColor(quality.decode(f.read_bytes()), cv2.COLOR_BGR2GRAY).astype(np.float32)
            for f in frames.demo_frame_files()]
    assert all(g.mean() > 130 for g in gray)
    template = gray[0][30:440, 360:560]
    for g in gray[1:]:
        result = cv2.matchTemplate(g, template, cv2.TM_CCOEFF_NORMED)
        _, best, _, loc = cv2.minMaxLoc(result)
        assert best > 0.97 and abs(loc[0] - 360) <= 3 and abs(loc[1] - 30) <= 3


def test_demo_pond_uploads_the_real_frames_in_time_order_and_cycles():
    tables = _demo_seed_tables()
    storage = FakeStorage()
    frames.upload_seed_frames(tables, storage, _settings())
    uploaded = {path: data for _, path, data in storage.uploads}
    pond_rows = sorted((r for r in tables["imageTable"] if r["user_ID"] == 1), key=lambda r: r["created_at"])
    files = frames.demo_frame_files()
    assert len(pond_rows) > len(files)  # so the files cycle
    for i, row in enumerate(pond_rows):
        assert uploaded[f"local-dev/1/{row['id']}.jpg"] == files[i % len(files)].read_bytes()
    # The legacy pond keeps its synthetic frame.
    legacy = next(r for r in tables["imageTable"] if r["user_ID"] == 2)
    assert analyze_image_bytes(uploaded[f"local-dev/2/{legacy['id']}.jpg"]) == pytest.approx(0.0625, abs=0.001)


def test_seed_values_are_measured_on_the_real_frames_not_invented():
    tables = _demo_seed_tables()
    config = tables["camera_config"][0]
    versions = tables["camera_mask_version"]
    assert config["pond_id"] == 1 and versions[0]["mask_version"] == config["mask_version"]
    pond_rows = sorted((r for r in tables["imageTable"] if r["user_ID"] == 1), key=lambda r: r["created_at"])
    files = frames.demo_frame_files()
    for i in (0, 1, 7, 19, len(pond_rows) - 1):
        data = files[i % len(files)].read_bytes()
        row = pond_rows[i]
        assert row["green_ratio"] == analyze_image_bytes(data, polygon=config["mask"])
        gcc, _ = quality.colour_metrics(quality.decode(data), config["mask"])
        assert row["gcc"] == pytest.approx(gcc, rel=1e-5)
        assert row["regions"]["regions"]["water_gap"]["pixels"] == region_metrics(
            quality.decode(data), config["regions"])["regions"]["water_gap"]["pixels"]
        assert row["mask_version"] == config["mask_version"]
    assert {"water_gap", "rim", "plants", "reference"} == set(config["regions"])
