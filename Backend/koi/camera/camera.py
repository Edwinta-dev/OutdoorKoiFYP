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

Water mask (issue #39, migration 0010): each upload reads the pond's
camera_config row once and computes the green ratio over that row's
polygon only, storing that row's mask_version with the frame, so the
stored version always names the polygon used even if the mask is saved
again mid-upload. When the frame's mask_version differs from the previous
frame's (null counts as the whole frame), the smoothed baseline and the
frame counters restart from this frame and the row records
baseline_reset = "mask_changed". The decision uses only stored rows, so
it holds across service restarts. If the mask cannot be read the frame
is analysed over the whole frame and stored without a mask_version.

Named regions (issue #91, migration 0023): the same camera_config row may
carry regions (koi/camera/mask.py::validate_regions), saved in the same
mask_version as the mask, so changing any region resets the baseline
exactly as a mask change does. When it does, every frame's per-region
metrics (hsvEngine.region_metrics) are stored in imageTable.regions; the
green ratio, quality gate and state machine still use the mask (or the
whole frame) as before. A config with only a mask behaves as before and
stores no regions. If the metrics cannot be computed the frame is stored
without them.

Quality gate (issues #40/#90, migrations 0011/0021, koi/camera/quality.py): every
frame is measured (V mean and spread, clipped fraction, variance of the
Laplacian) and the versioned result is stored in imageTable.quality. A
failed frame is stored and shown, but the state machine skips it: its row
carries the previous accepted frame's state unchanged, the next frame is
evaluated against the newest frame that did not fail (from stored rows, so
this holds across restarts), and the reply wakes the camera within 30
minutes in daylight (imageSchedule.quality_retry_wake).

Thumbnails: a 320 px wide JPEG is stored beside each frame
("{user_id}/{ts}_photo_thumb.jpg") and its object path, not a URL, is
stored in imageTable.thumbnail_path, so who may read it follows the
bucket's access rules (issue #12) rather than a URL fixed at upload time.
imageURL is written as before.

Storage failures, in upload order:
  frame upload fails      503, nothing stored.
  thumbnail fails         the frame is kept without one (thumbnail_path
                          null); the original is what the app shows anyway.
  row insert fails        the objects just uploaded are deleted and the
                          reply is 503, so no frame is left without a row.
                          If that delete fails too, a frame_orphaned event
                          names the bucket and paths for manual removal.

Device contact (issue #23, migration 0018): once the frame's row is
stored, the upload is recorded as a contact of the pond's camera with the
reply's next_at as the time it is expected next, which moves
devices.last_seen_at forward. It is skipped when X-User-ID is not a pond
number, and a storage failure is logged and skipped: the reply is the
same either way.
"""
import logging
import time
from datetime import datetime, timezone

import cv2

# Flask's Blueprint, under a name that the no-print check (grep for a
# print call in koi/, issue #10) does not mistake for one.
from flask import Blueprint as RouteGroup
from flask import current_app, jsonify, request

from koi.camera import hsvEngine, imageSchedule, quality
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


# Baseline reset reason stored in imageTable.baseline_reset (migration 0010).
RESET_MASK_CHANGED = "mask_changed"
RESET_OBSTRUCTION_PERSISTED = "obstruction_persisted"  # issue #80


# Rows searched for the newest frame that did not fail the quality gate.
# After this many failed frames in a row the baseline restarts as for a
# first frame.
PREV_SCAN_ROWS = 50
DEFAULT_BASELINE = 0.15


# Helper Function: Fetch Previous Image Data
def get_prev_image_data(user_id: str):
    """Returns (prev_raw_green_ratio, prev_current_state, prev_row).

    The whole current_state value is returned untouched and handed
    straight to evalstate(), which recovers the smoothed baseline from it
    itself. The raw ratio is still returned as a fallback for the very
    first reading, when no pair exists yet. prev_row is the previous
    imageTable row (for its mask_version), or None for the first reading.

    Frames that failed the quality gate are skipped: the previous frame is
    the newest one whose quality is "pass" or "unknown" (rows stored
    before the gate).
    """
    rows: list[dict] = fail_soft(lambda: _storage().fetch_image_history(user_id, limit=PREV_SCAN_ROWS), [])
    for latest in rows:
        if quality.is_failed(latest):
            continue
        prev_green_ratio = latest.get("green_ratio", DEFAULT_BASELINE)
        prev_state = latest.get("current_state", hsvEngine.DEFAULT_STATE)
        return prev_green_ratio, prev_state, latest

    log_event(log, "camera_baseline_defaulted")
    return DEFAULT_BASELINE, hsvEngine.DEFAULT_STATE, None


def get_camera_config(user_id: str) -> tuple[list | None, dict | None, int | None]:
    """(polygon, regions, mask_version) from one read of the pond's
    camera_config row; (None, None, None) when the pond has no config or
    it cannot be read. polygon or regions is None when not set."""
    config = fail_soft(lambda: _storage().fetch_camera_mask(user_id), None)
    if not config:
        return None, None, None
    return config.get("mask"), config.get("regions"), int(config["mask_version"])


def get_mask(user_id: str) -> tuple[list | None, int | None]:
    """(polygon, mask_version) from one read of the pond's camera_config
    row; (None, None) when the pond has no mask or it cannot be read."""
    polygon, _, mask_version = get_camera_config(user_id)
    return polygon, mask_version


def measure_regions(img, regions: dict | None) -> dict | None:
    """hsvEngine.region_metrics for the pond's regions, or None when it has
    none or they cannot be measured (logged); the frame is stored either way."""
    if not regions:
        return None
    try:
        return hsvEngine.region_metrics(img, regions)
    except Exception as exc:  # noqa: BLE001 - an observation, never a reason to refuse the frame
        log_event(log, "region_metrics_failed", level=logging.WARNING, error=str(exc))
        return None


def carried_state(prev_state, prev_green_ratio) -> list:
    """The state a failed frame stores: the previous accepted frame's
    [label, baseline, raised, stable] unchanged, so it is shown as the
    camera's current state and moves nothing, including an obstruction
    candidate (issue #80), which a failed frame neither extends nor breaks."""
    raised, stable = hsvEngine._coerce_counters(prev_state)
    carried = [hsvEngine._coerce_state(prev_state),
               round(hsvEngine._coerce_baseline(prev_state, prev_green_ratio), 4), raised, stable]
    mean, count = hsvEngine._coerce_candidate(prev_state)
    if mean is not None:
        carried += [mean, count]
    return carried


def store_thumbnail(bucket: str, frame_path: str, img) -> str | None:
    """Uploads the frame's thumbnail and returns its object path, or None
    (logged) when it could not be made or stored; the frame is kept."""
    path = quality.thumbnail_path(frame_path)
    try:
        _storage().upload_image(bucket, path, quality.thumbnail(img))
    except (StorageError, ValueError, cv2.error) as exc:
        log_event(log, "thumbnail_failed", level=logging.WARNING, bucket=bucket, path=path, error=str(exc))
        return None
    return path


def discard_objects(bucket: str, paths: list[str]) -> None:
    """Deletes objects whose imageTable row could not be written. If that
    fails as well, logs frame_orphaned with the paths to remove by hand."""
    try:
        _storage().delete_images(bucket, paths)
        log_event(log, "frame_discarded", level=logging.WARNING, bucket=bucket, paths=paths)
    except StorageError as exc:
        log_event(log, "frame_orphaned", level=logging.ERROR, bucket=bucket, paths=paths, error=str(exc))


# Helper Function: Insert Current Reading Record
def push_current_data(green_ratio: float, user_id: str, state, public_url: str,
                      mask_version: int | None = None, baseline_reset: str | None = None,
                      frame_quality: dict | None = None, thumbnail_path: str | None = None,
                      gcc: float | None = None, colour: dict | None = None, regions: dict | None = None):
    """Raises StorageError when the row cannot be written."""
    _storage().insert_image(user_id, green_ratio, state, public_url, mask_version=mask_version,
                            baseline_reset=baseline_reset, quality=frame_quality, thumbnail_path=thumbnail_path,
                            gcc=gcc, colour=colour, regions=regions)


def record_contact(user_id: str, wake: imageSchedule.NextWake) -> None:
    """Records this upload as a contact of the pond's camera, expected
    again at the reply's next_at. Never raises."""
    try:
        pond = int(user_id)
    except (TypeError, ValueError):
        return
    contact = {"received_at": datetime.now(timezone.utc).isoformat(), "expected_next_at": wake.next_at.isoformat()}
    fail_soft(lambda: _storage().record_device_contacts(pond, "camera", [contact]), None)


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

    # 1. Compute HSV Green Ratio for current frame, over the pond's water
    # mask. The polygon and its version come from one read, and that
    # version is the one stored with the frame.
    # CHANGED: a corrupt JPEG used to raise here, outside any try, and the ESP32
    # got an HTML 500 page with no sleep time in it.
    polygon, regions, mask_version = get_camera_config(user_id)
    started = time.perf_counter()
    try:
        green_ratio = hsvEngine.analyze_image_bytes(file_bytes, polygon=polygon)
        img = quality.decode(file_bytes)
        gcc, colour = quality.colour_metrics(img, polygon, rows=settings.camera_colour_grid_rows,
                                             cols=settings.camera_colour_grid_cols)
        frame_quality = quality.assess(img, polygon, gcc_min=settings.camera_gcc_min,
                                       gcc_max=settings.camera_gcc_max,
                                       s_mean_min=settings.camera_s_mean_min,
                                       s_mean_max=settings.camera_s_mean_max)
    except Exception as e:
        return error_reply(422, "image_unreadable", f"Image analysis failed: {e}")
    region_values = measure_regions(img, regions)
    analysis_time = time.perf_counter() - started
    passed = frame_quality["status"] != quality.FAIL

    # 2. Fetch historical state & baseline
    prev_green_ratio, prev_state, prev_row = get_prev_image_data(user_id)

    # 3. Compute new state transition. A failed frame changes nothing: it
    # carries the previous state. A different mask from the previous
    # frame's means the stored baseline covers another area: restart it
    # from this frame, with the counters at zero.
    started = time.perf_counter()
    baseline_reset = None
    if not passed:
        log_event(log, "frame_quality_failed", reasons=frame_quality["reasons"], **frame_quality["metrics"])
        state = carried_state(prev_state, prev_green_ratio)
        rise = 0.0
    elif prev_row is not None and prev_row.get("mask_version") != mask_version:
        baseline_reset = RESET_MASK_CHANGED
        log_event(log, "camera_baseline_reset", reason=baseline_reset,
                  from_mask_version=prev_row.get("mask_version"), to_mask_version=mask_version)
        state = (hsvEngine.DEFAULT_STATE, green_ratio, 0, 0)
        rise = 0.0
    else:
        state = hsvEngine.evalstate(green_ratio, prev_green_ratio, prev_state,
                                    enter_frames=settings.camera_dynamic_enter_frames,
                                    exit_frames=settings.camera_dynamic_exit_frames,
                                    confirm_frames=settings.camera_obstruction_confirm_frames,
                                    tolerance=settings.camera_obstruction_tolerance)
        rise = hsvEngine.frame_rise(green_ratio, prev_green_ratio, prev_state)
        if hsvEngine.rebaselined(prev_state, state):
            # A persistent, consistent level is a new view, not an
            # obstruction: the baseline restarted there (issue #80).
            baseline_reset = RESET_OBSTRUCTION_PERSISTED
            rise = 0.0
            log_event(log, "camera_baseline_reset", reason=baseline_reset,
                      from_baseline=hsvEngine._coerce_baseline(prev_state, prev_green_ratio),
                      to_baseline=state[1])
    analysis_seconds.observe(analysis_time + time.perf_counter() - started)
    from_state = hsvEngine._coerce_state(prev_state)
    transitions.inc(from_state=from_state, to_state=state[0])

    # 4. Next wake
    # CHANGED: one call, local pond time, night-time fallback to base schedule
    now = datetime.now(settings.tz)
    wake = imageSchedule.next_wake(
        state[0], now=now, test_mode=settings.test_mode,
        rise=rise, levels=settings.camera_dynamic_rate_levels)
    if not passed:
        wake = imageSchedule.quality_retry_wake(wake, now=now, test_mode=settings.test_mode)
    log_event(log, "frame_analysed", green_ratio=green_ratio, mask_version=mask_version,
              quality=frame_quality["status"],
              gap_usable=(region_values["regions"]["water_gap"].get("usable") if region_values else None),
              from_state=from_state, to_state=state[0],
              analysis_ms=round(analysis_time * 1000, 1), rise=round(rise, 4), sleep_sec=wake.sleep_sec,
              reason=wake.reason, next_at=wake.reply()["next_at"])

    bucket = settings.pond_image_bucket
    try:
        # 5. Upload the frame and its thumbnail to the configured bucket.
        # Object path is "{user_id}/{unix_seconds}_photo.jpg" - the same
        # id the app onboards with, so the client can resolve a user's
        # frames without any help from this service.
        filename = f"{user_id}/{int(time.time())}_photo.jpg"
        public_url = _storage().upload_image(bucket, filename, file_bytes)
        log_event(log, "frame_stored", bucket=bucket, path=filename)
        thumb_path = store_thumbnail(bucket, filename, img)

        # 6. Save the reading to imageTable. Without a row, the objects
        # just stored are removed again.
        try:
            push_current_data(green_ratio, user_id, state, public_url, mask_version, baseline_reset,
                              frame_quality, thumb_path, gcc, colour, region_values)
        except StorageError:
            discard_objects(bucket, [filename] + ([thumb_path] if thumb_path else []))
            raise

        record_contact(user_id, wake)

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

