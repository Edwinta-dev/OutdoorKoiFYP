from types import SimpleNamespace

import pytest

from koi.camera.hsvEngine import analyze_image_bytes
from koi.dev import frames


@pytest.mark.parametrize("ratio", [0.0, 0.02, 0.0569, 0.5, 1.0])
def test_synthetic_frame_matches_seed_green_ratio(ratio):
    assert analyze_image_bytes(frames.synthetic_frame(ratio)) == pytest.approx(ratio, abs=0.001)


def test_upload_seed_frames_writes_matching_objects_and_local_urls(monkeypatch):
    calls = []

    class Bucket:
        def upload(self, path, data, file_options):
            calls.append((path, data, file_options))

        def get_public_url(self, path):
            return f"http://127.0.0.1:54321/storage/v1/object/public/imageAnalysisBucket/{path}"

        def remove(self, paths):
            calls.append(("remove", paths))

    class Client:
        storage = SimpleNamespace(from_=lambda bucket: Bucket())

    monkeypatch.setitem(__import__("sys").modules, "supabase",
                        SimpleNamespace(create_client=lambda url, key: Client()))
    tables = {"imageTable": [{"id": 7, "user_ID": 1, "green_ratio": 0.0569,
                               "imageURL": "https://example.invalid/frame.jpg"}]}
    settings = SimpleNamespace(supabase_url="http://127.0.0.1:54321",
                               supabase_servicerole_key=SimpleNamespace(get_secret_value=lambda: "local"),
                               pond_image_bucket="imageAnalysisBucket", dev_supabase_public_url="")

    assert frames.upload_seed_frames(tables, settings) == 1
    assert calls[0][0] == "local-dev/1/7.jpg"
    assert calls[0][2] == {"content-type": "image/jpeg", "upsert": "true"}
    assert analyze_image_bytes(calls[0][1]) == pytest.approx(0.0569, abs=0.001)
    assert tables["imageTable"][0]["imageURL"].startswith("http://127.0.0.1:54321/")
    assert "example.invalid" not in tables["imageTable"][0]["imageURL"]


def test_public_url_override_keeps_physical_device_urls_reachable(monkeypatch):
    class Bucket:
        def upload(self, *args, **kwargs):
            pass

        def get_public_url(self, path):
            return f"http://127.0.0.1:54321/storage/v1/object/public/imageAnalysisBucket/{path}"

    class Client:
        storage = SimpleNamespace(from_=lambda bucket: Bucket())

    monkeypatch.setitem(__import__("sys").modules, "supabase",
                        SimpleNamespace(create_client=lambda url, key: Client()))
    tables = {"imageTable": [{"id": 7, "user_ID": 1, "green_ratio": 0.02}]}
    settings = SimpleNamespace(supabase_url="http://127.0.0.1:54321",
                               supabase_servicerole_key=SimpleNamespace(get_secret_value=lambda: "local"),
                               pond_image_bucket="imageAnalysisBucket",
                               dev_supabase_public_url="http://192.168.1.20:54321/")

    frames.upload_seed_frames(tables, settings)
    assert tables["imageTable"][0]["imageURL"].startswith("http://192.168.1.20:54321/")


def test_remove_seed_frames_deletes_deterministic_objects(monkeypatch):
    removed = []

    class Bucket:
        def remove(self, paths):
            removed.extend(paths)

    class Client:
        storage = SimpleNamespace(from_=lambda bucket: Bucket())

    monkeypatch.setitem(__import__("sys").modules, "supabase",
                        SimpleNamespace(create_client=lambda url, key: Client()))
    tables = {"imageTable": [{"id": 7, "user_ID": 1}, {"id": 8, "user_ID": 2}]}
    settings = SimpleNamespace(supabase_url="http://127.0.0.1:54321",
                               supabase_servicerole_key=SimpleNamespace(get_secret_value=lambda: "local"),
                               pond_image_bucket="imageAnalysisBucket")

    frames.remove_seed_frames(tables, settings)

    assert removed == ["local-dev/1/7.jpg", "local-dev/2/8.jpg"]
