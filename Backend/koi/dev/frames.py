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
    image[:, :green_columns] = (0, 165, 140)  # BGR hue is within hsvEngine's green range.
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise RuntimeError("could not encode synthetic camera frame")
    return encoded.tobytes()


def upload_seed_frames(tables: dict[str, list[dict[str, Any]]], settings: Any) -> int:
    """Upload seed frames and replace placeholder image URLs with local public URLs."""
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_servicerole_key.get_secret_value())
    bucket = settings.pond_image_bucket
    storage = client.storage.from_(bucket)
    uploaded = 0
    for row in tables.get("imageTable", []):
        pond_id, image_id = row["user_ID"], row["id"]
        path = f"local-dev/{pond_id}/{image_id}.jpg"
        storage.upload(path, synthetic_frame(row["green_ratio"]),
                       file_options={"content-type": "image/jpeg", "upsert": "true"})
        public_base = settings.dev_supabase_public_url.rstrip("/")
        row["imageURL"] = (f"{public_base}/storage/v1/object/public/{bucket}/{path}" if public_base
                            else storage.get_public_url(path))
        uploaded += 1
    return uploaded


def remove_seed_frames(tables: dict[str, list[dict[str, Any]]], settings: Any) -> None:
    """Remove the deterministic objects written for a temporary --check seed."""
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_servicerole_key.get_secret_value())
    paths = [f"local-dev/{row['user_ID']}/{row['id']}.jpg" for row in tables.get("imageTable", [])]
    if paths:
        client.storage.from_(settings.pond_image_bucket).remove(paths)
