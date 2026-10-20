"""Synthetic camera frames used by the local Supabase development profile."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def synthetic_frame(green_ratio: float) -> bytes:
    """Encode a small, deterministic JPEG with the requested HSV green coverage."""
    height, width = 240, 1280
    green_columns = max(0, round(max(0.0, min(1.0, float(green_ratio))) * width) - 1)
    image = np.full((height, width, 3), (150, 150, 150), dtype=np.uint8)
    image[:, :green_columns] = (40, 170, 90)  # BGR hue ~97 degrees: inside hsvEngine's green range.
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise RuntimeError("could not encode synthetic camera frame")
    return encoded.tobytes()


def _path(row: dict[str, Any]) -> str:
    return f"local-dev/{row['user_ID']}/{row['id']}.jpg"


def upload_seed_frames(tables: dict[str, list[dict[str, Any]]], storage: Any, settings: Any) -> int:
    """Upload seed frames through Storage.upload_image and replace placeholder
    image URLs with local public URLs."""
    bucket = settings.pond_image_bucket
    public_base = settings.dev_supabase_public_url.rstrip("/")
    uploaded = 0
    for row in tables.get("imageTable", []):
        path = _path(row)
        url = storage.upload_image(bucket, path, synthetic_frame(row["green_ratio"]))
        row["imageURL"] = f"{public_base}/storage/v1/object/public/{bucket}/{path}" if public_base else url
        uploaded += 1
    return uploaded


def remove_seed_frames(tables: dict[str, list[dict[str, Any]]], storage: Any, settings: Any) -> None:
    """Remove the deterministic objects written for a temporary --check seed."""
    paths = [_path(row) for row in tables.get("imageTable", [])]
    if paths:
        storage.delete_images(settings.pond_image_bucket, paths)
