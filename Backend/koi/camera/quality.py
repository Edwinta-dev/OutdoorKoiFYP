"""
koi.camera.quality

Frame quality gate, colour observations and thumbnails (issues #40/#90,
migrations 0011/0021).

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
  colour_cast  unclipped mean GCC or mean HSV saturation outside its band

The thresholds are starting values chosen against synthetic frames, not
yet checked against real ESP32-CAM frames from the pond; the measured
metrics and the thresholds in force are stored with every result so they
can be re-judged later.

The stored result (imageTable.quality, jsonb) is versioned:

  {"version": 2, "status": "pass" | "fail", "reasons": [...],
   "metrics": {...above...}, "thresholds": {...}}

Readers accept versions 1 and 2 through status_of(): a null column (every row stored before
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

QUALITY_VERSION = 2

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

# Reason codes
TOO_DARK = "too_dark"
TOO_BRIGHT = "too_bright"
CLIPPED = "clipped"
BLURRED = "blurred"
COLOUR_CAST = "colour_cast"
REASONS = (TOO_DARK, TOO_BRIGHT, CLIPPED, BLURRED, COLOUR_CAST)

# Thresholds (V is 0..255)
V_MEAN_MIN = 40.0
V_MEAN_MAX = 220.0
CLIP_LOW_V = 5
CLIP_HIGH_V = 250
CLIPPED_FRACTION_MAX = 0.25
DETAIL_MIN_V_STD = 8.0
BLUR_LAPLACIAN_MIN = 5.0
GCC_MIN = 0.30
GCC_MAX = 0.45
S_MEAN_MIN = 0.0
S_MEAN_MAX = 200.0

THUMBNAIL_WIDTH = 320
THUMBNAIL_JPEG_QUALITY = 80


def thresholds(gcc_min: float = GCC_MIN, gcc_max: float = GCC_MAX,
               s_mean_min: float = S_MEAN_MIN, s_mean_max: float = S_MEAN_MAX) -> dict:
    return {
        "v_mean_min": V_MEAN_MIN, "v_mean_max": V_MEAN_MAX,
        "clip_low_v": CLIP_LOW_V, "clip_high_v": CLIP_HIGH_V,
        "clipped_fraction_max": CLIPPED_FRACTION_MAX,
        "detail_min_v_std": DETAIL_MIN_V_STD, "blur_laplacian_min": BLUR_LAPLACIAN_MIN,
        "gcc_min": gcc_min, "gcc_max": gcc_max,
        "s_mean_min": s_mean_min, "s_mean_max": s_mean_max,
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


def colour_means(img: np.ndarray, polygon: list | None = None) -> dict:
    """Means over unclipped mask pixels. RGB is scaled by 255; ExG maps
    its theoretical -2..2 range to 0..1. No usable pixels yields nulls."""
    v = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 2]
    usable = (v > CLIP_LOW_V) & (v < CLIP_HIGH_V)
    inside = _region(img.shape, polygon)
    if inside is not None:
        usable &= inside
    rgb = img[..., ::-1][usable].astype(np.float64) / 255.0
    if not rgb.size:
        return dict.fromkeys(("gcc", "exg", "r", "g", "b"))
    r, g, b = rgb.T
    return {"gcc": float((g / (r + g + b)).mean()),
            "exg": float(((2 * g - r - b + 2) / 4).mean()),
            "r": float(r.mean()), "g": float(g.mean()), "b": float(b.mean())}


def colour_metrics(img: np.ndarray, polygon: list | None = None,
                   rows: int = 3, cols: int = 3) -> tuple[float | None, dict]:
    """Frame means and an unmasked row-major GCC grid, including rim/floor.
    Empty or entirely clipped cells have null GCC, never an invented zero."""
    if rows < 1 or cols < 1:
        raise ValueError("Colour grid dimensions must be positive.")
    means = colour_means(img, polygon)
    height, width = img.shape[:2]
    grid = []
    for row in range(rows):
        for col in range(cols):
            cell = img[row * height // rows:(row + 1) * height // rows,
                       col * width // cols:(col + 1) * width // cols]
            grid.append(colour_means(cell)["gcc"] if cell.size else None)
    gcc = means.pop("gcc")
    return gcc, {"version": 1, **means, "grid": {"rows": rows, "cols": cols, "gcc": grid}}


def assess(img: np.ndarray, polygon: list | None = None, *,
           gcc_min: float = GCC_MIN, gcc_max: float = GCC_MAX,
           s_mean_min: float = S_MEAN_MIN, s_mean_max: float = S_MEAN_MAX) -> dict:
    """The versioned quality result for a decoded BGR frame, measured over
    polygon (the water mask, fractions of the frame) or the whole frame."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    lap = cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F)
    inside = _region(img.shape, polygon)
    if inside is not None:
        s, v, lap = s[inside], v[inside], lap[inside]
    # Saturation is a colour mean too: ignore crushed and blown pixels just
    # as colour_means() does for GCC, ExG and RGB.
    usable_s = s[(v > CLIP_LOW_V) & (v < CLIP_HIGH_V)]
    v = v.astype(np.float64).ravel()
    lap = lap.ravel()

    v_mean = float(v.mean())
    v_std = float(v.std())
    clipped_low = float(np.count_nonzero(v <= CLIP_LOW_V)) / v.size
    clipped_high = float(np.count_nonzero(v >= CLIP_HIGH_V)) / v.size
    clipped_fraction = clipped_low + clipped_high
    laplacian_var = float(lap.var())
    blur_judged = v_std >= DETAIL_MIN_V_STD
    gcc = colour_means(img, polygon)["gcc"]
    s_mean = float(usable_s.mean()) if usable_s.size else None

    reasons = []
    if v_mean < V_MEAN_MIN:
        reasons.append(TOO_DARK)
    if v_mean > V_MEAN_MAX:
        reasons.append(TOO_BRIGHT)
    if clipped_fraction > CLIPPED_FRACTION_MAX:
        reasons.append(CLIPPED)
    if blur_judged and laplacian_var < BLUR_LAPLACIAN_MIN:
        reasons.append(BLURRED)
    if ((gcc is not None and not gcc_min <= gcc <= gcc_max)
            or (s_mean is not None and not s_mean_min <= s_mean <= s_mean_max)):
        reasons.append(COLOUR_CAST)

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
            "gcc": gcc,
            "s_mean": round(s_mean, 2) if s_mean is not None else None,
        },
        "thresholds": thresholds(gcc_min, gcc_max, s_mean_min, s_mean_max),
    }


def status_of(row_or_quality: Any) -> str:
    """"pass", "fail" or "unknown" for an imageTable row (or its quality
    value). Null, malformed or unknown-version results are "unknown"."""
    quality = row_or_quality
    if isinstance(row_or_quality, dict) and "quality" in row_or_quality:
        quality = row_or_quality["quality"]
    if not isinstance(quality, dict) or type(quality.get("version")) not in (int, float) \
            or quality.get("version") not in (1, QUALITY_VERSION):
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
