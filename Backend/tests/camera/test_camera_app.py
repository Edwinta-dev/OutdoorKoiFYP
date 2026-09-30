"""koi.camera.create_app: the ESP32-CAM upload service, against an
in-memory stand-in for the shared Supabase client."""
from datetime import datetime

import cv2
import numpy as np
import pytest

from conftest import make_settings
from koi.camera import camera as camera_routes
from koi.camera import create_app, imageSchedule


class _Query:
    def __init__(self, db, table):
        self.db, self.table_name = db, table

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def insert(self, row):
        self.db.inserted.append((self.table_name, row))
        return self

    def execute(self):
        return type("Res", (), {"data": list(self.db.previous)})()


class _Bucket:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def upload(self, path, file, file_options):
        self.db.uploads.append((self.name, path, len(file)))

    def get_public_url(self, path):
        return f"https://storage.invalid/{self.name}/{path}?"


class FakeSupabase:
    def __init__(self, previous=()):
        self.previous = previous
        self.inserted = []
        self.uploads = []
        self.storage = type("Storage", (), {"from_": lambda _s, name: _Bucket(self, name)})()

    def table(self, name):
        return _Query(self, name)


def _jpeg(bgr):
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = bgr
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(camera_routes, "get_client", lambda: db)
    return db


def _client(**overrides):
    app = create_app(make_settings(**overrides))
    app.config["TESTING"] = True
    return app.test_client()


def test_health():
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "OutdoorKoi camera API OK"


def test_upload_rejects_wrong_device_token_with_a_sleep_time(fake_db):
    resp = _client(device_token="secret").post("/upload", data=b"x", headers={"X-Device-Token": "nope"})
    assert resp.status_code == 401
    assert resp.get_json()["sleep_sec"] == camera_routes.FALLBACK_SLEEP_SEC
    assert fake_db.uploads == []


def test_upload_rejects_empty_body(fake_db):
    resp = _client().post("/upload", data=b"")
    assert resp.status_code == 400
    assert resp.get_json()["sleep_sec"] == camera_routes.FALLBACK_SLEEP_SEC


def test_upload_stores_frame_in_configured_bucket_and_returns_test_mode_sleep(fake_db):
    resp = _client(device_token="secret", test_mode=True, pond_image_bucket="frames").post(
        "/upload", data=_jpeg((0, 200, 0)),
        headers={"X-Device-Token": "secret", "X-User-ID": "15"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["sleep_sec"] == imageSchedule.TEST_SLEEP_SEC[body["state"][0]]
    assert [(b, p.split("/")[0]) for b, p, _ in fake_db.uploads] == [("frames", "15")]
    table, row = fake_db.inserted[0]
    assert table == "imageTable"
    assert row["user_ID"] == "15"
    assert row["imageURL"].startswith("https://storage.invalid/frames/15/")
    assert not row["imageURL"].endswith("?")


def test_upload_uses_the_configured_time_zone(fake_db, monkeypatch):
    seen = []

    def fake_sleep(state, now=None, test_mode=False):
        seen.append((now.tzinfo, test_mode))
        return 600

    monkeypatch.setattr(imageSchedule, "sleep_for_state", fake_sleep)
    resp = _client(timezone="UTC").post("/upload", data=_jpeg((0, 0, 0)))
    assert resp.get_json()["sleep_sec"] == 600
    assert [(str(tz), tm) for tz, tm in seen] == [("UTC", False)]


def test_sleep_for_state_test_mode_is_an_argument():
    noon = datetime(2026, 8, 1, 12, 0, tzinfo=imageSchedule.TZ)
    assert imageSchedule.sleep_for_state("obstruction", now=noon, test_mode=True) == 80
    assert imageSchedule.sleep_for_state("obstruction", now=noon) == imageSchedule.OBSTRUCTION_SLEEP_SEC
