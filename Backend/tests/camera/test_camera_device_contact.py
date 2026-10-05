"""Issue #23: each stored camera frame is recorded as a contact of the
pond's camera (devices.last_seen_at, migration 0018), expected again at
the reply's next_at. The POST /upload contract (request, auth, reply) is
unchanged, whether or not the contact can be recorded.
"""
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from conftest import make_settings
from koi.camera import create_app
from koi.storage import MemoryStorage


def _jpeg():
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = (0, 200, 0)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


def _post(storage, user="15"):
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    return app.test_client().post("/upload", data=_jpeg(), headers={"X-User-ID": user})


@pytest.fixture
def storage():
    return MemoryStorage()


def _when(value):
    return datetime.fromisoformat(value)


def test_camera_upload_records_a_device_contact(storage):
    before = datetime.now(timezone.utc)
    resp = _post(storage)
    assert resp.status_code == 200
    body = resp.get_json()
    [device] = storage.rows("devices")
    assert (device["pond_id"], device["kind"]) == (15, "camera")
    assert before <= _when(device["last_seen_at"]) <= datetime.now(timezone.utc)
    assert _when(device["next_expected_at"]) == _when(body["next_at"])
    [contact] = storage.rows("device_contact")
    assert _when(contact["expected_next_at"]) == _when(body["next_at"])


def test_camera_device_contact_failure_leaves_the_reply_unchanged(storage):
    expected = _post(MemoryStorage()).get_json()
    storage.failing.add("record_device_contacts")
    resp = _post(storage)
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == set(expected) == {"status", "state", "sleep_sec", "reason", "next_at"}
    assert len(storage.rows("imageTable")) == 1
    assert storage.rows("devices") == []


def test_camera_device_contact_skipped_without_a_pond_number(storage):
    resp = _post(storage, user="default_user")
    assert resp.status_code == 200
    assert storage.rows("devices") == [] and len(storage.rows("imageTable")) == 1
