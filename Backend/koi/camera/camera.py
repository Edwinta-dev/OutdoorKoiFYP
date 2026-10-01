"""
koi.camera.camera

Routes of the camera service (formerly Backend/Camera/camera.py; the app is
built by koi.camera.create_app). Receives an ESP32-CAM frame, runs the HSV
analysis, stores the frame and its reading, and replies with the next sleep.

Configuration comes from koi.settings.Settings:
  pond_image_bucket  storage bucket for the frames. The Flutter client
                     builds its own URLs against the same bucket, so it must
                     equal POND_IMAGE_BUCKET in MobileUI/mobile_app/env/*.json.
  device_token       the service is on the public internet; when set,
                     uploads without a matching X-Device-Token are rejected.
  test_mode          short fixed sleep times for bench testing.
"""
import time
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from koi.camera import hsvEngine, imageSchedule
from koi.errors import STORAGE_RETRY_AFTER_SEC, error_response
from koi.settings import Settings
from koi.storage import Storage, StorageError, fail_soft

# NEW: sleep time returned whenever something fails, so the ESP32 never loops fast
FALLBACK_SLEEP_SEC = 7200
MAX_UPLOAD_BYTES = 1_000_000

bp = Blueprint("camera", __name__)


def _settings() -> Settings:
    return current_app.config["KOI_SETTINGS"]


def _storage() -> Storage:
    return current_app.extensions["koi_storage"]


def error_reply(status: int, code: str, message: str, sleep_sec: int = FALLBACK_SLEEP_SEC,
                details: dict | None = None, headers: dict | None = None):
    """The koi.errors envelope; every error still carries a sleep time
    for the ESP32."""
    print(f"[ERROR] {message}")
    response, status = error_response(status, code, message, details, sleep_sec=sleep_sec)
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

    print("First reading or no previous data found. Defaulting baselines.")
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
    print(f"Processing upload for User_ID: {user_id}")

    # 1. Compute HSV Green Ratio for current frame
    # CHANGED: a corrupt JPEG used to raise here, outside any try, and the ESP32
    # got an HTML 500 page with no sleep time in it.
    try:
        green_ratio = hsvEngine.analyze_image_bytes(file_bytes)
    except Exception as e:
        return error_reply(422, "image_unreadable", f"Image analysis failed: {e}")

    # 2. Fetch historical state & baseline
    prev_green_ratio, prev_state = get_prev_image_data(user_id)

    # 3. Compute new state transition.
    state = hsvEngine.evalstate(green_ratio, prev_green_ratio, prev_state)
    print(f"New computed state: {state}")

    # 4. Next sleep interval
    # CHANGED: one call, local pond time, night-time fallback to base schedule
    time_to_next_image = imageSchedule.sleep_for_state(
        state[0], now=datetime.now(settings.tz), test_mode=settings.test_mode)
    print(f"Next scheduled sleep: {time_to_next_image} seconds")

    try:
        # 5. Upload the frame to the configured bucket.
        # Object path is "{user_id}/{unix_seconds}_photo.jpg" - the same
        # id the app onboards with, so the client can resolve a user's
        # frames without any help from this service.
        filename = f"{user_id}/{int(time.time())}_photo.jpg"
        public_url = _storage().upload_image(settings.pond_image_bucket, filename, file_bytes)
        print(f"Storage Upload Complete: {public_url}")

        # 6. Save the reading to imageTable
        push_current_data(green_ratio, user_id, state, public_url)

        # 7. Return payload to ESP32
        return jsonify({
            "status": "success",
            "state": state,
            "sleep_sec": time_to_next_image
        }), 200

    except StorageError as e:
        # The analysis succeeded, so the computed schedule is still valid
        return error_reply(
            503, "storage_unavailable",
            f"The pond database could not be reached. Try again in {STORAGE_RETRY_AFTER_SEC} seconds.",
            sleep_sec=time_to_next_image,
            details={"operation": e.operation, "retry_after_sec": STORAGE_RETRY_AFTER_SEC},
            headers={"Retry-After": str(STORAGE_RETRY_AFTER_SEC)},
        )

