"""
koi.camera.camera

Routes of the camera service (formerly Backend/Camera/camera.py; the app is
built by koi.camera.create_app). Receives an ESP32-CAM frame, runs the HSV
analysis, stores the frame and its reading, and replies with the next wake:
sleep_sec, reason (an imageSchedule.REASONS code) and next_at (local ISO
time). Error replies carry the same three keys.

Configuration comes from koi.settings.Settings:
  pond_image_bucket  storage bucket for the frames. The Flutter client
                     builds its own URLs against the same bucket, so it must
                     equal POND_IMAGE_BUCKET in MobileUI/mobile_app/env/*.json.
  device_token       the service is on the public internet; when set,
                     uploads without a matching X-Device-Token are rejected.
  test_mode          short fixed sleep times for bench testing.
  camera_dynamic_*   hysteresis frame counts and dynamic interval levels.

Metrics (GET /metrics, see camera_metrics): uploads by result, the time
the HSV analysis and state evaluation take, and state transitions from
the previous frame's state to the new one.
"""
import logging
import time
from datetime import datetime

# Flask's Blueprint, under a name that the no-print check (grep for a
# print call in koi/, issue #10) does not mistake for one.
from flask import Blueprint as RouteGroup
from flask import current_app, jsonify, request

from koi.camera import hsvEngine, imageSchedule
from koi.errors import STORAGE_RETRY_AFTER_SEC, error_response
from koi.logs import log_event
from koi.metrics import Counter, Histogram, Metrics
from koi.settings import Settings
from koi.storage import Storage, StorageError, fail_soft

# NEW: sleep time returned whenever something fails, so the ESP32 never loops fast
FALLBACK_SLEEP_SEC = 7200
MAX_UPLOAD_BYTES = 1_000_000

bp = RouteGroup("camera", __name__)

log = logging.getLogger(__name__)

# Seconds. A 640x480 JPEG analyses in tens of milliseconds.
ANALYSIS_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


def camera_metrics(metrics: Metrics) -> tuple[Counter, Histogram, Counter]:
    """The camera's metric families in metrics (created on first call):
    uploads by result, analysis time, and state transitions."""
    return (
        metrics.counter("koi_camera_uploads_total",
                        "Frame uploads by result: stored, or the error code returned.", ["result"]),
        metrics.histogram("koi_camera_analysis_seconds",
                          "Time to run the HSV green-ratio analysis and state evaluation on one frame.",
                          buckets=ANALYSIS_BUCKETS),
        metrics.counter("koi_camera_state_transitions_total",
                        "Camera state changes from the previous frame's state to the new one.",
                        ["from_state", "to_state"]),
    )


def _metrics() -> tuple[Counter, Histogram, Counter]:
    return camera_metrics(current_app.extensions["koi_metrics"])


def _settings() -> Settings:
    return current_app.config["KOI_SETTINGS"]


def _storage() -> Storage:
    return current_app.extensions["koi_storage"]


def error_reply(status: int, code: str, message: str, wake: imageSchedule.NextWake | None = None,
                details: dict | None = None, headers: dict | None = None):
    """The koi.errors envelope; every error still carries the next wake
    (the fallback sleep unless a schedule was already computed)."""
    log_event(log, "upload_rejected", level=logging.WARNING, status=status, code=code, message=message)
    _metrics()[0].inc(result=code)
    if wake is None:
        wake = imageSchedule.fallback_wake(FALLBACK_SLEEP_SEC, datetime.now(_settings().tz))
    response, status = error_response(status, code, message, details, **wake.reply())
    if headers:
        response.headers.update(headers)
    return response, status


# Helper Function: Fetch Previous Image Data
def get_prev_image_data(user_id: str):
    """Returns (prev_raw_green_ratio, prev_current_state).

    The whole current_state value is returned untouched and handed
    straight to evalstate(), which recovers the smoothed baseline from it
    itself. The raw ratio is still returned as a fallback for the very
    first reading, when no pair exists yet.
    """
    rows: list[dict] = fail_soft(lambda: _storage().fetch_image_history(user_id, limit=1), [])
    if rows:
        latest = rows[0]
        prev_green_ratio = latest.get("green_ratio", 0.15)
        prev_state = latest.get("current_state", hsvEngine.DEFAULT_STATE)
        return prev_green_ratio, prev_state

    log_event(log, "camera_baseline_defaulted")
    return 0.15, hsvEngine.DEFAULT_STATE


# Helper Function: Insert Current Reading Record
def push_current_data(green_ratio: float, user_id: str, state, public_url: str):
    fail_soft(lambda: _storage().insert_image(user_id, green_ratio, state, public_url), None)


@bp.route('/', methods=['GET'])
def health():
    return "OutdoorKoi camera API OK"


@bp.route('/upload', methods=['POST'])
def upload_image():
    settings = _settings()
    device_token = settings.device_token.get_secret_value()
    if device_token and request.headers.get("X-Device-Token") != device_token:
        return error_reply(401, "unauthorised", "Missing or wrong X-Device-Token.")

    file_bytes = request.data
    if not file_bytes:
        return error_reply(400, "empty_body", "Empty data packet from ESP32")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return error_reply(413, "payload_too_large",
                           f"Payload too large ({len(file_bytes)} bytes, limit {MAX_UPLOAD_BYTES})")

    user_id = request.headers.get('X-User-ID', 'default_user')
    uploads, analysis_seconds, transitions = _metrics()

    # 1. Compute HSV Green Ratio for current frame
    # CHANGED: a corrupt JPEG used to raise here, outside any try, and the ESP32
    # got an HTML 500 page with no sleep time in it.
    started = time.perf_counter()
    try:
        green_ratio = hsvEngine.analyze_image_bytes(file_bytes)
    except Exception as e:
        return error_reply(422, "image_unreadable", f"Image analysis failed: {e}")
    analysis_time = time.perf_counter() - started

    # 2. Fetch historical state & baseline
    prev_green_ratio, prev_state = get_prev_image_data(user_id)

    # 3. Compute new state transition.
    started = time.perf_counter()
    state = hsvEngine.evalstate(green_ratio, prev_green_ratio, prev_state,
                                enter_frames=settings.camera_dynamic_enter_frames,
                                exit_frames=settings.camera_dynamic_exit_frames)
    rise = hsvEngine.frame_rise(green_ratio, prev_green_ratio, prev_state)
    analysis_seconds.observe(analysis_time + time.perf_counter() - started)
    from_state = hsvEngine._coerce_state(prev_state)
    transitions.inc(from_state=from_state, to_state=state[0])

    # 4. Next wake
    # CHANGED: one call, local pond time, night-time fallback to base schedule
    wake = imageSchedule.next_wake(
        state[0], now=datetime.now(settings.tz), test_mode=settings.test_mode,
        rise=rise, levels=settings.camera_dynamic_rate_levels)
    log_event(log, "frame_analysed", green_ratio=green_ratio, from_state=from_state, to_state=state[0],
              analysis_ms=round(analysis_time * 1000, 1), rise=round(rise, 4), sleep_sec=wake.sleep_sec,
              reason=wake.reason, next_at=wake.reply()["next_at"])

    try:
        # 5. Upload the frame to the configured bucket.
        # Object path is "{user_id}/{unix_seconds}_photo.jpg" - the same
        # id the app onboards with, so the client can resolve a user's
        # frames without any help from this service.
        filename = f"{user_id}/{int(time.time())}_photo.jpg"
        public_url = _storage().upload_image(settings.pond_image_bucket, filename, file_bytes)
        log_event(log, "frame_stored", bucket=settings.pond_image_bucket, path=filename)

        # 6. Save the reading to imageTable
        push_current_data(green_ratio, user_id, state, public_url)

        # 7. Return payload to ESP32
        uploads.inc(result="stored")
        return jsonify({
            "status": "success",
            "state": state,
            **wake.reply(),
        }), 200

    except StorageError as e:
        # The analysis succeeded, so the computed schedule is still valid
        return error_reply(
            503, "storage_unavailable",
            f"The pond database could not be reached. Try again in {STORAGE_RETRY_AFTER_SEC} seconds.",
            wake=wake,
            details={"operation": e.operation, "retry_after_sec": STORAGE_RETRY_AFTER_SEC},
            headers={"Retry-After": str(STORAGE_RETRY_AFTER_SEC)},
        )

