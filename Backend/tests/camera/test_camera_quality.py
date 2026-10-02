"""Frame quality gate and thumbnails (issue #40): the quality metrics on
synthetic well-exposed, under-exposed, over-exposed, clipped and blurred
frames, the stored versioned result, a failed frame leaving the state
machine alone (also across a service restart), the 30-minute daylight
retry, thumbnails beside each frame, and the storage-failure policy.
Against MemoryStorage.

Frames are 320x240: muddy water with a band of algae across the top and
seeded pixel noise standing in for sensor texture (real ESP32-CAM frames
are never perfectly flat).

Run from Backend/: python -m pytest -q -k "quality or thumbnail"
"""
import logging
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from conftest import make_settings, ticking_clock
from koi.camera import camera as camera_routes
from koi.camera import create_app, hsvEngine, imageSchedule, quality
from koi.models import algae_engine as ae
from koi.storage import MemoryStorage, StorageError

W, H = 320, 240
WATER = (110, 90, 60)   # BGR, hue ~200 degrees: not green
ALGAE = (40, 160, 140)  # BGR, inside the HSV green band
USER_ID = "15"


def _scene(algae_rows=48, noise=6.0, seed=1):
    img = np.empty((H, W, 3), dtype=np.float64)
    img[:] = WATER
    img[:algae_rows] = ALGAE
    rng = np.random.default_rng(seed)
    img += rng.normal(0, noise, (H, W, 1))
    return np.clip(img, 0, 255).astype(np.uint8)


def _jpeg(img):
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def good_frame(algae_rows=48):
    return _jpeg(_scene(algae_rows))


def dark_frame():
    """Under-exposed: the same scene at a tenth of the brightness."""
    return _jpeg((_scene().astype(np.float64) * 0.1).astype(np.uint8))


def bright_frame():
    """Over-exposed: the scene pushed up by 200 levels, mostly blown white."""
    return _jpeg(np.clip(_scene().astype(np.int32) + 200, 0, 255).astype(np.uint8))


def glare_frame():
    """Normal exposure with a sun-glare blob over 40% of the frame."""
    img = _scene()
    img[:, :int(W * 0.4)] = 255
    return _jpeg(img)


def blurred_frame():
    """Out of focus: the scene through a wide Gaussian blur."""
    return _jpeg(cv2.GaussianBlur(_scene(), (0, 0), 6))


def _decode(data):
    return quality.decode(data)


# --- the metrics ------------------------------------------------------------

def test_quality_good_frame_passes_with_its_metrics():
    result = quality.assess(_decode(good_frame()))
    assert result["version"] == quality.QUALITY_VERSION == 1
    assert result["status"] == "pass" and result["reasons"] == []
    m = result["metrics"]
    assert set(m) == {"v_mean", "v_std", "clipped_low", "clipped_high", "clipped_fraction", "laplacian_var",
                      "blur_judged", "width", "height", "pixels"}
    assert 100 < m["v_mean"] < 140 and m["v_std"] > quality.DETAIL_MIN_V_STD
    assert m["clipped_fraction"] == 0
    assert m["laplacian_var"] > 10 * quality.BLUR_LAPLACIAN_MIN
    assert m["blur_judged"] is True
    assert (m["width"], m["height"], m["pixels"]) == (W, H, W * H)
    assert result["thresholds"] == quality.thresholds()


@pytest.mark.parametrize("make, reasons", [
    (dark_frame, ["too_dark"]),
    (bright_frame, ["too_bright", "clipped"]),
    (glare_frame, ["clipped"]),
    (blurred_frame, ["blurred"]),
], ids=["under-exposed", "over-exposed", "glare", "blurred"])
def test_quality_bad_frames_fail_with_reason_codes(make, reasons):
    result = quality.assess(_decode(make()))
    assert result["status"] == "fail"
    assert result["reasons"] == reasons
    assert set(result["reasons"]) <= set(quality.REASONS)


def test_quality_black_frame_is_dark_and_clipped():
    result = quality.assess(np.zeros((48, 64, 3), dtype=np.uint8))
    assert result["reasons"] == ["too_dark", "clipped"]
    assert result["metrics"]["clipped_low"] == 1.0


def test_quality_blur_is_measured_by_the_laplacian_variance():
    sharp = quality.assess(_decode(good_frame()))["metrics"]["laplacian_var"]
    blurred = quality.assess(_decode(blurred_frame()))["metrics"]["laplacian_var"]
    assert blurred < quality.BLUR_LAPLACIAN_MIN < sharp


def test_quality_featureless_frame_is_not_failed_for_blur():
    """An evenly lit, flat view has no edges to judge: it is measured but
    not called blurred."""
    flat = np.full((H, W, 3), WATER, dtype=np.uint8)
    result = quality.assess(flat)
    assert result["metrics"]["blur_judged"] is False
    assert result["status"] == "pass"


def test_quality_is_measured_inside_the_water_mask():
    """Glare outside the mask does not fail the frame."""
    img = _scene()
    img[:, :int(W * 0.4)] = 255
    right = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
    assert quality.assess(img)["status"] == "fail"
    inside = quality.assess(img, right)
    assert inside["status"] == "pass"
    assert inside["metrics"]["pixels"] < W * H // 2 + 2 * H


@pytest.mark.parametrize("value, status", [
    (None, "unknown"),
    ({}, "unknown"),
    ("pass", "unknown"),
    ({"version": 2, "status": "pass"}, "unknown"),
    ({"version": 1, "status": "maybe"}, "unknown"),
    ({"version": 1, "status": "unknown"}, "unknown"),
    ({"version": 1, "status": "pass"}, "pass"),
    ({"version": 1, "status": "fail"}, "fail"),
])
def test_quality_status_of_reads_legacy_and_malformed_as_unknown(value, status):
    assert quality.status_of({"id": 1, "quality": value}) == status
    if value is None:
        assert quality.status_of({"id": 1, "green_ratio": 0.1}) == "unknown"  # row from before 0011


# --- thumbnails ----------------------------------------------------------------

@pytest.mark.parametrize("size, expected", [((640, 480), (320, 240)), ((1600, 1200), (320, 240)),
                                            ((160, 120), (320, 240)), ((800, 600), (320, 240))])
def test_thumbnail_is_a_320_px_wide_jpeg(size, expected):
    img = cv2.resize(_scene(), size)
    data = quality.thumbnail(img)
    assert data[:2] == b"\xff\xd8"
    thumb = _decode(data)
    assert (thumb.shape[1], thumb.shape[0]) == expected


def test_thumbnail_path_sits_beside_the_frame():
    assert quality.thumbnail_path("15/1700000000_photo.jpg") == "15/1700000000_photo_thumb.jpg"
    assert quality.thumbnail_path("15/frame") == "15/frame_thumb.jpg"


# --- the upload route ------------------------------------------------------------

class _Noon(datetime):
    """camera.py's datetime, fixed at 12:00 in Singapore (daylight)."""
    INSTANT = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.INSTANT.astimezone(tz)


class _Night(_Noon):
    INSTANT = datetime(2026, 8, 3, 13, 0, tzinfo=timezone.utc)  # 21:00 in Singapore


def _storage(*history):
    storage = MemoryStorage(clock=ticking_clock())
    storage.add_rows("imageTable", [{"user_ID": USER_ID, "imageURL": "x", **row} for row in history])
    return storage


def _client(storage, **overrides):
    app = create_app(make_settings(**{"test_mode": True, **overrides}), storage=storage)
    app.config["TESTING"] = True
    return app.test_client()


def post(client, data, status=200):
    resp = client.post("/upload", data=data, headers={"X-User-ID": USER_ID})
    assert resp.status_code == status, resp.get_data(as_text=True)
    return resp.get_json()


def latest(storage):
    return storage.rows("imageTable")[-1]


def test_quality_result_is_stored_on_every_frame():
    storage = _storage()
    post(_client(storage), good_frame())
    post(_client(storage), dark_frame())
    good, dark = storage.rows("imageTable")
    assert good["quality"]["status"] == "pass" and good["quality"]["version"] == 1
    assert dark["quality"]["status"] == "fail" and dark["quality"]["reasons"] == ["too_dark"]
    assert dark["quality"]["metrics"]["v_mean"] < quality.V_MEAN_MIN
    assert dark["imageURL"].startswith("memory://")  # stored and shown like any frame


def test_quality_failed_frame_leaves_baseline_and_counters_alone():
    storage = _storage({"green_ratio": 0.1, "current_state": ["base", 0.1, 1, 0]})
    body = post(_client(storage), dark_frame())
    assert body["state"] == ["base", 0.1, 1, 0]
    assert latest(storage)["current_state"] == ["base", 0.1, 1, 0]

    # The next good frame is judged against the stored good state, not the dark one.
    green = hsvEngine.analyze_image_bytes(good_frame())
    body = post(_client(storage), good_frame())
    assert body["state"] == list(hsvEngine.evalstate(green, 0.1, ["base", 0.1, 1, 0]))


@pytest.mark.parametrize("make", [dark_frame, bright_frame, blurred_frame],
                         ids=["under-exposed", "over-exposed", "blurred"])
def test_quality_failed_frames_never_change_state(make):
    """Even a frame whose green ratio would trip obstruction or dynamic."""
    state = ["dynamic", 0.05, 0, 1]
    storage = _storage({"green_ratio": 0.05, "current_state": state})
    for _ in range(3):
        assert post(_client(storage), make())["state"] == state
    assert [r["current_state"] for r in storage.rows("imageTable")] == [state] * 4


def test_quality_rejection_holds_across_a_service_restart():
    """Each upload goes to a fresh app (a restart): the decision to skip
    the failed frame is made from the stored rows alone."""
    storage = _storage()
    first = post(_client(storage), good_frame(48))["state"]
    post(_client(storage), bright_frame())
    post(_client(storage), blurred_frame())
    green = hsvEngine.analyze_image_bytes(good_frame(96))
    third = post(_client(storage), good_frame(96))["state"]
    assert third == list(hsvEngine.evalstate(green, first[1], first))
    assert [quality.status_of(r) for r in storage.rows("imageTable")] == ["pass", "fail", "fail", "pass"]


def test_quality_first_frame_failing_stores_the_default_state():
    storage = _storage()
    body = post(_client(storage), dark_frame())
    assert body["state"] == [hsvEngine.DEFAULT_STATE, camera_routes.DEFAULT_BASELINE, 0, 0]


def test_quality_legacy_rows_without_a_result_still_seed_the_state_machine():
    """Rows from before migration 0011 are "unknown", not "pass", but the
    state machine reads them as it always has."""
    storage = _storage({"green_ratio": 0.2, "current_state": ["base", 0.2]})
    [legacy] = storage.rows("imageTable")
    assert quality.status_of(legacy) == "unknown"
    green = hsvEngine.analyze_image_bytes(good_frame())
    assert post(_client(storage), good_frame())["state"] == list(hsvEngine.evalstate(green, 0.2, ["base", 0.2]))


def test_quality_failed_frame_does_not_reset_the_baseline_on_a_mask_change():
    """The mask-change reset waits for the next frame that passes."""
    storage = _storage({"green_ratio": 0.1, "current_state": ["base", 0.1, 0, 0]})
    storage.save_camera_mask(15, [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    post(_client(storage), dark_frame())
    failed = latest(storage)
    assert failed.get("baseline_reset") is None and failed["current_state"] == ["base", 0.1, 0, 0]
    post(_client(storage), good_frame())
    assert latest(storage)["baseline_reset"] == camera_routes.RESET_MASK_CHANGED


def test_quality_failed_frame_retries_in_30_minutes_in_daylight(monkeypatch):
    monkeypatch.setattr(camera_routes, "datetime", _Noon)
    storage = _storage()
    good = post(_client(storage, test_mode=False), good_frame())
    assert (good["reason"], good["sleep_sec"]) == ("slot", 2 * 3600)  # 12:00 -> 14:00 slot
    failed = post(_client(storage, test_mode=False), dark_frame())
    assert failed["reason"] == "quality_retry"
    assert failed["sleep_sec"] == imageSchedule.QUALITY_RETRY_SEC == 30 * 60
    assert failed["next_at"] == "2026-08-03T12:30:00+08:00"


def test_quality_failed_frame_at_night_keeps_the_night_schedule(monkeypatch):
    monkeypatch.setattr(camera_routes, "datetime", _Night)
    body = post(_client(_storage(), test_mode=False), dark_frame())
    assert body["reason"] == "night_base"
    assert body["next_at"] == "2026-08-04T08:00:00+08:00"


def test_quality_retry_never_lengthens_a_shorter_wake():
    now = datetime(2026, 8, 3, 12, 0, tzinfo=imageSchedule.TZ)
    short = imageSchedule.next_wake("dynamic", now=now, rise=0.5)
    assert short.sleep_sec == 30 * 60
    assert imageSchedule.quality_retry_wake(short, now=now) is short
    obstruction = imageSchedule.next_wake("obstruction", now=now)
    assert imageSchedule.quality_retry_wake(obstruction, now=now).reason == "quality_retry"
    test_wake = imageSchedule.next_wake("base", now=now, test_mode=True)
    assert imageSchedule.quality_retry_wake(test_wake, now=now, test_mode=True) is test_wake


def test_quality_reply_keeps_the_upload_contract_keys():
    body = post(_client(_storage()), dark_frame())
    assert set(body) == {"status", "state", "sleep_sec", "reason", "next_at"}


# --- thumbnails through the route ------------------------------------------------

def test_thumbnail_stored_beside_each_frame_with_its_path_on_the_row():
    storage = _storage()
    post(_client(storage, pond_image_bucket="frames"), cv2.imencode(".jpg", cv2.resize(_scene(), (640, 480)))[1]
         .tobytes())
    row = latest(storage)
    frame_path = row["imageURL"].removeprefix("memory://frames/")
    assert row["thumbnail_path"] == quality.thumbnail_path(frame_path)
    assert "://" not in row["thumbnail_path"]  # a path for the bucket's access rules, not a URL
    thumb = _decode(storage.uploads[("frames", row["thumbnail_path"])])
    assert thumb.shape[:2] == (240, 320)
    assert set(storage.uploads) == {("frames", frame_path), ("frames", row["thumbnail_path"])}


def test_thumbnail_also_stored_for_a_failed_frame():
    storage = _storage()
    post(_client(storage), dark_frame())
    assert latest(storage)["thumbnail_path"].endswith("_photo_thumb.jpg")
    assert len(storage.uploads) == 2


def test_thumbnail_upload_failure_keeps_the_frame(monkeypatch, caplog):
    storage = _storage()
    upload = storage.upload_image

    def failing_thumb(bucket, path, data):
        if path.endswith("_thumb.jpg"):
            raise StorageError("upload_image", "bucket unavailable")
        return upload(bucket, path, data)

    monkeypatch.setattr(storage, "upload_image", failing_thumb)
    with caplog.at_level(logging.WARNING):
        post(_client(storage), good_frame())
    row = latest(storage)
    assert row.get("thumbnail_path") is None and row["quality"]["status"] == "pass"
    assert [p for _, p in storage.uploads] == [row["imageURL"].split("/", 3)[-1]]
    assert any(getattr(r, "koi_event", None) == "thumbnail_failed" for r in caplog.records)


def test_thumbnail_encode_failure_keeps_the_frame(monkeypatch):
    def broken(img):
        raise ValueError("Thumbnail could not be encoded.")

    monkeypatch.setattr(quality, "thumbnail", broken)
    storage = _storage()
    post(_client(storage), good_frame())
    assert latest(storage).get("thumbnail_path") is None
    assert len(storage.uploads) == 1


# --- storage failures and malformed uploads ------------------------------------

def test_quality_row_insert_failure_removes_the_uploaded_objects(caplog):
    storage = _storage()
    storage.failing.add("insert_image")
    with caplog.at_level(logging.WARNING):
        body = post(_client(storage), good_frame(), status=503)
    assert body["error"]["code"] == "storage_unavailable"
    assert body["error"]["details"]["operation"] == "insert_image"
    assert "sleep_sec" in body and "next_at" in body
    assert storage.uploads == {} and storage.rows("imageTable") == []
    [discarded] = [r for r in caplog.records if getattr(r, "koi_event", None) == "frame_discarded"]
    assert len(discarded.koi_fields["paths"]) == 2


def test_quality_insert_and_cleanup_failure_logs_the_orphaned_paths(caplog):
    storage = _storage()
    storage.failing.update({"insert_image", "delete_images"})
    with caplog.at_level(logging.WARNING):
        post(_client(storage), good_frame(), status=503)
    [orphaned] = [r for r in caplog.records if getattr(r, "koi_event", None) == "frame_orphaned"]
    assert orphaned.levelno == logging.ERROR
    assert sorted(orphaned.koi_fields["paths"]) == sorted(p for _, p in storage.uploads)
    assert len(storage.uploads) == 2


def test_quality_frame_upload_failure_stores_nothing():
    storage = _storage()
    storage.failing.add("upload_image")
    post(_client(storage), good_frame(), status=503)
    assert storage.uploads == {} and storage.rows("imageTable") == []


@pytest.mark.parametrize("data", [
    b"not a jpeg",
    b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4,
    good_frame()[:200],
], ids=["text", "jpeg-header-then-junk", "truncated-header"])
def test_quality_malformed_upload_is_rejected_and_nothing_stored(data):
    storage = _storage()
    body = post(_client(storage), data, status=422)
    assert body["error"]["code"] == "image_unreadable"
    assert {"sleep_sec", "reason", "next_at"} <= set(body)
    assert storage.uploads == {} and storage.rows("imageTable") == []


# --- the algae fit ---------------------------------------------------------------

def test_quality_failed_frames_are_left_out_of_the_algae_fit():
    rows = [
        {"created_at": "2026-08-01T00:00:00+00:00", "green_ratio": 0.10, "current_state": ["base", 0.1]},
        {"created_at": "2026-08-01T02:00:00+00:00", "green_ratio": 0.90, "current_state": ["base", 0.1],
         "quality": {"version": 1, "status": "fail", "reasons": ["too_bright"]}},
        {"created_at": "2026-08-01T04:00:00+00:00", "green_ratio": 0.12, "current_state": ["base", 0.104],
         "quality": {"version": 1, "status": "pass", "reasons": []}},
        {"created_at": "2026-08-01T06:00:00+00:00", "green_ratio": 0.13, "current_state": ["base", 0.11],
         "quality": {"version": 2, "status": "fail"}},  # unknown version: kept, as "unknown"
    ]
    assert [s.green_ratio for s in ae.parse_image_rows(rows)] == [0.10, 0.12, 0.13]
