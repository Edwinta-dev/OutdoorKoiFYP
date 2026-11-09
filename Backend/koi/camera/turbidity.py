"""Clear-water colour fit and corrected disc matching for camera card readings."""
from __future__ import annotations

import numpy as np

MIN_PATCHES = 6
MIN_TRANSMISSION = 0.05
LUMINANCE_WEIGHTS = np.array([0.2126, 0.7152, 0.0722])


def analyse(current: dict, reference: dict | None) -> dict:
    """Return per-channel affine fit, turbidity, and corrected disc RGB values."""
    if not reference:
        return {"index": None, "a": None, "b": None, "r2": None, "patches_used": 0,
                "reason": "no_reference_frame", "corrected": {}}
    now, ref = current.get("patches") or {}, reference.get("patches") or {}
    pairs = [(np.asarray(ref[n]["median_rgb"], float), np.asarray(now[n]["median_rgb"], float))
             for n in ref.keys() & now.keys() if ref[n].get("median_rgb") and now[n].get("median_rgb")]
    if len(pairs) < MIN_PATCHES:
        return {"index": None, "a": None, "b": None, "r2": None, "patches_used": len(pairs),
                "reason": "fewer_than_6_usable_patches", "corrected": {}}
    x, y = np.stack([p[0] for p in pairs]), np.stack([p[1] for p in pairs])
    a = np.zeros(3)
    b = np.zeros(3)
    r2 = np.zeros(3)
    for c in range(3):
        a[c], b[c] = np.polyfit(x[:, c], y[:, c], 1)
        residual = y[:, c] - (a[c] * x[:, c] + b[c])
        denom = np.sum((y[:, c] - y[:, c].mean()) ** 2)
        r2[c] = 1 - np.sum(residual ** 2) / denom if denom > 1e-12 else (1.0 if np.allclose(residual, 0) else 0.0)
    if np.any(a < MIN_TRANSMISSION):
        return {"index": None, "a": a.tolist(), "b": b.tolist(), "r2": r2.tolist(),
                "patches_used": len(pairs), "reason": "transmission_below_0.05", "corrected": {}}
    white = np.asarray(reference.get("white_rgb") or [], float)
    if white.shape != (3,):
        return {"index": None, "a": a.tolist(), "b": b.tolist(), "r2": r2.tolist(),
                "patches_used": len(pairs), "reason": "reference_white_missing", "corrected": {}}
    channel_index = b / (a * white + b)
    corrected = {}
    for key in ("ph", "nh3"):
        reading = ((current.get(key) or {}).get("segment_rgb") or
                   current.get("patches", {}).get(key + "_reading", {}).get("median_rgb"))
        if reading:
            corrected[key] = np.clip((np.asarray(reading, float) - b) / a, 0, 255).tolist()
    return {"index": float(channel_index @ LUMINANCE_WEIGHTS), "index_channels": channel_index.tolist(),
            "a": a.tolist(), "b": b.tolist(), "r2": r2.tolist(), "patches_used": len(pairs),
            "reason": None, "corrected": corrected}
