"""
koi.camera.quality

Frame quality gate and thumbnails for the camera service (issue #40,
migration 0011).

Every uploaded frame is measured over the pixels the green ratio is
computed over (the pond's water mask, or the whole frame):

  v_mean, v_std      mean and standard deviation of the HSV V channel, 0..255
  clipped_low        fraction of pixels with V <= CLIP_LOW_V (crushed black)
  clipped_high       fraction of pixels with V >= CLIP_HIGH_V (blown white)
  clipped_fraction   clipped_low + clipped_high
  laplacian_var      variance of the Laplacian of the grey frame: low when
                     the frame is out of focus, fogged or smeared
  blur_judged        false when v_std < DETAIL_MIN_V_STD: a featureless frame
                     has no edges to be sharp or blurred, so it is not failed
                     for blur (a calm, evenly lit pond can look like this)

A frame fails, with one reason code per rule it breaks, when:

  too_dark     v_mean < V_MEAN_MIN
  too_bright   v_mean > V_MEAN_MAX
  clipped      clipped_fraction > CLIPPED_FRACTION_MAX
  blurred      blur_judged and laplacian_var < BLUR_LAPLACIAN_MIN

The thresholds are starting values chosen against synthetic frames, not
yet checked against real ESP32-CAM frames from the pond; the measured
metrics and the thresholds in force are stored with every result so they
can be re-judged later.

The stored result (imageTable.quality, jsonb) is versioned:

  {"version": 1, "status": "pass" | "fail", "reasons": [...],
   "metrics": {...above...}, "thresholds": {...}}

Readers go through status_of(): a null column (every row stored before
migration 0011), a malformed value or an unknown version is "unknown",
never "pass". A failed frame is stored and shown, but does not move the
camera's baseline or state counters and is left out of the algae fit;
"unknown" frames are used as they always were.

Thumbnails: a JPEG THUMBNAIL_WIDTH pixels wide (height in proportion),
stored in the same bucket beside the frame under thumbnail_path().
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

QUALITY_VERSION = 1

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

# Reason codes
TOO_DARK = "too_dark"
TOO_BRIGHT = "too_bright"
CLIPPED = "clipped"
BLURRED = "blurred"
REASONS = (TOO_DARK, TOO_BRIGHT, CLIPPED, BLURRED)

# Thresholds (V is 0..255)
V_MEAN_MIN = 40.0
V_MEAN_MAX = 220.0
CLIP_LOW_V = 5
CLIP_HIGH_V = 250
CLIPPED_FRACTION_MAX = 0.25
DETAIL_MIN_V_STD = 8.0
BLUR_LAPLACIAN_MIN = 5.0

THUMBNAIL_WIDTH = 320
THUMBNAIL_JPEG_QUALITY = 80


def thresholds() -> dict:
    return {
        "v_mean_min": V_MEAN_MIN, "v_mean_max": V_MEAN_MAX,
        "clip_low_v": CLIP_LOW_V, "clip_high_v": CLIP_HIGH_V,
        "clipped_fraction_max": CLIPPED_FRACTION_MAX,
        "detail_min_v_std": DETAIL_MIN_V_STD, "blur_laplacian_min": BLUR_LAPLACIAN_MIN,
    }


def decode(image_bytes: bytes) -> np.ndarray:
    """BGR frame from JPEG bytes; ValueError when they do not decode."""
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode image bytes from ESP32 payload.")
    return img


def _region(shape: tuple, polygon: list | None) -> np.ndarray | None:
    """Boolean mask of the pixels inside polygon (as hsvEngine fills it),
    or None for the whole frame."""
    if polygon is None:
        return None
    height, width = shape[:2]
    region = np.zeros((height, width), dtype=np.uint8)
    corners = np.array([[round(x * width), round(y * height)] for x, y in polygon], dtype=np.int32)
    cv2.fillPoly(region, [corners], 255)
    return region > 0


def assess(img: np.ndarray, polygon: list | None = None) -> dict:
    """The versioned quality result for a decoded BGR frame, measured over
    polygon (the water mask, fractions of the frame) or the whole frame."""
    v = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 2]
    lap = cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F)
    inside = _region(img.shape, polygon)
    if inside is not None:
        v, lap = v[inside], lap[inside]
    v = v.astype(np.float64).ravel()
    lap = lap.ravel()

    v_mean = float(v.mean())
    v_std = float(v.std())
    clipped_low = float(np.count_nonzero(v <= CLIP_LOW_V)) / v.size
    clipped_high = float(np.count_nonzero(v >= CLIP_HIGH_V)) / v.size
    clipped_fraction = clipped_low + clipped_high
    laplacian_var = float(lap.var())
    blur_judged = v_std >= DETAIL_MIN_V_STD

    reasons = []
    if v_mean < V_MEAN_MIN:
        reasons.append(TOO_DARK)
    if v_mean > V_MEAN_MAX:
        reasons.append(TOO_BRIGHT)
    if clipped_fraction > CLIPPED_FRACTION_MAX:
        reasons.append(CLIPPED)
    if blur_judged and laplacian_var < BLUR_LAPLACIAN_MIN:
        reasons.append(BLURRED)

    height, width = img.shape[:2]
    return {
        "version": QUALITY_VERSION,
        "status": FAIL if reasons else PASS,
        "reasons": reasons,
        "metrics": {
            "v_mean": round(v_mean, 2),
            "v_std": round(v_std, 2),
            "clipped_low": round(clipped_low, 4),
            "clipped_high": round(clipped_high, 4),
            "clipped_fraction": round(clipped_fraction, 4),
            "laplacian_var": round(laplacian_var, 3),
            "blur_judged": blur_judged,
            "width": width,
            "height": height,
            "pixels": int(v.size),
        },
        "thresholds": thresholds(),
    }


def status_of(row_or_quality: Any) -> str:
    """"pass", "fail" or "unknown" for an imageTable row (or its quality
    value). Null, malformed or unknown-version results are "unknown"."""
    quality = row_or_quality
    if isinstance(row_or_quality, dict) and "quality" in row_or_quality:
        quality = row_or_quality["quality"]
    if not isinstance(quality, dict) or quality.get("version") != QUALITY_VERSION:
        return UNKNOWN
    status = quality.get("status")
    return status if status in (PASS, FAIL) else UNKNOWN


def is_failed(row: dict) -> bool:
    return status_of(row) == FAIL


def thumbnail(img: np.ndarray) -> bytes:
    """JPEG bytes of img scaled to THUMBNAIL_WIDTH wide, aspect kept."""
    height, width = img.shape[:2]
    new_height = max(1, round(height * THUMBNAIL_WIDTH / width))
    interp = cv2.INTER_AREA if width > THUMBNAIL_WIDTH else cv2.INTER_LINEAR
    small = cv2.resize(img, (THUMBNAIL_WIDTH, new_height), interpolation=interp)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, THUMBNAIL_JPEG_QUALITY])
    if not ok:
        raise ValueError("Thumbnail could not be encoded.")
    return buf.tobytes()


def thumbnail_path(frame_path: str) -> str:
    """The thumbnail's object path beside the frame's:
    "15/1700000000_photo.jpg" -> "15/1700000000_photo_thumb.jpg"."""
    stem, dot, ext = frame_path.rpartition(".")
    if not dot:
        return f"{frame_path}_thumb.jpg"
    return f"{stem}_thumb.{ext}"
