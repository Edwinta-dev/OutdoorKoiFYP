"""koi.camera.create_app: the ESP32-CAM upload service, against
MemoryStorage."""
from datetime import datetime

import cv2
import numpy as np
import pytest

from conftest import make_settings
from koi.camera import camera as camera_routes
from koi.camera import create_app, imageSchedule
from koi.storage import MemoryStorage


def _jpeg(bgr):
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = bgr
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


@pytest.fixture
def storage():
    return MemoryStorage()


def _client(storage=None, **overrides):
    app = create_app(make_settings(**overrides), storage=storage)
    app.config["TESTING"] = True
    return app.test_client()


def test_health():
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "OutdoorKoi camera API OK"


def test_upload_rejects_wrong_device_token_with_a_sleep_time(storage):
    resp = _client(storage, device_token="secret").post("/upload", data=b"x", headers={"X-Device-Token": "nope"})
    assert resp.status_code == 401
    assert resp.get_json()["sleep_sec"] == camera_routes.FALLBACK_SLEEP_SEC
    assert storage.uploads == {}


def test_upload_rejects_empty_body(storage):
    resp = _client(storage).post("/upload", data=b"")
    assert resp.status_code == 400
    assert resp.get_json()["sleep_sec"] == camera_routes.FALLBACK_SLEEP_SEC


def test_upload_stores_frame_in_configured_bucket_and_returns_test_mode_sleep(storage):
    resp = _client(storage, device_token="secret", test_mode=True, pond_image_bucket="frames").post(
        "/upload", data=_jpeg((0, 200, 0)),
        headers={"X-Device-Token": "secret", "X-User-ID": "15"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["sleep_sec"] == imageSchedule.TEST_SLEEP_SEC[body["state"][0]]
    assert [(b, p.split("/")[0]) for b, p in storage.uploads] == [("frames", "15")]
    [row] = storage.rows("imageTable")
    assert row["user_ID"] == "15"
    assert row["imageURL"].startswith("memory://frames/15/")
    assert row["current_state"] == body["state"]


def test_upload_reads_the_previous_frame_state(storage):
    storage.add_rows("imageTable", [{"user_ID": 15, "green_ratio": 0.2,
                                     "current_state": ["base", 0.2], "imageURL": "x"}])
    resp = _client(storage).post("/upload", data=_jpeg((0, 200, 0)), headers={"X-User-ID": "15"})
    assert resp.status_code == 200
    assert len(storage.rows("imageTable")) == 2


def test_upload_survives_a_failed_previous_frame_read(storage):
    """No previous state is the first-reading case: the default baseline."""
    storage.failing.add("fetch_image_history")
    resp = _client(storage).post("/upload", data=_jpeg((0, 200, 0)), headers={"X-User-ID": "15"})
    assert resp.status_code == 200
    assert len(storage.rows("imageTable")) == 1


def test_failed_upload_still_returns_the_computed_sleep(storage, monkeypatch):
    monkeypatch.setattr(imageSchedule, "sleep_for_state", lambda state, now=None, test_mode=False: 900)
    storage.failing.add("upload_image")
    resp = _client(storage).post("/upload", data=_jpeg((0, 200, 0)), headers={"X-User-ID": "15"})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["sleep_sec"] == 900
    assert "upload_image failed" in body["message"]
    assert storage.rows("imageTable") == []


def test_upload_uses_the_configured_time_zone(storage, monkeypatch):
    seen = []

    def fake_sleep(state, now=None, test_mode=False):
        seen.append((now.tzinfo, test_mode))
        return 600

    monkeypatch.setattr(imageSchedule, "sleep_for_state", fake_sleep)
    resp = _client(storage, timezone="UTC").post("/upload", data=_jpeg((0, 0, 0)))
    assert resp.get_json()["sleep_sec"] == 600
    assert [(str(tz), tm) for tz, tm in seen] == [("UTC", False)]


def test_sleep_for_state_test_mode_is_an_argument():
    noon = datetime(2026, 8, 1, 12, 0, tzinfo=imageSchedule.TZ)
    assert imageSchedule.sleep_for_state("obstruction", now=noon, test_mode=True) == 80
    assert imageSchedule.sleep_for_state("obstruction", now=noon) == imageSchedule.OBSTRUCTION_SLEEP_SEC
