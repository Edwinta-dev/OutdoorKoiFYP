"""Synthetic camera frames used by the local Supabase development profile."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

FRAME_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "demo_pond" / "frames"


# The early, clear frames the demo cycles. The first ones (00059-00061) have
# the camera framed differently; from 00062 the wheels and cards sit where
# fixtures/demo_pond/card_patches.json puts the patches (within a pixel).
# The later frames are turbid or dark, or the camera has moved; they stay in
# the folder, unused.
DEMO_FRAME_NAMES = ("00062.jpg", "00063.jpg", "00064.jpg", "00065.jpg")


def demo_frame_files() -> list[Path]:
    """The real deployment frames the demo uses (640x480 JPEGs), in time order."""
    return [FRAME_DIR / name for name in DEMO_FRAME_NAMES]


def demo_frame_bytes(index: int) -> bytes:
    """The index-th camera row frame, cycling through the files."""
    files = demo_frame_files()
    return files[index % len(files)].read_bytes()


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
    image URLs with local public URLs. A pond with a camera_config row (the
    demo pond) gets the real deployment frames, its rows in time order taking
    the files in order and cycling (the same pairing build_seed measured);
    other ponds keep the synthetic frame for their green_ratio."""
    bucket = settings.pond_image_bucket
    public_base = settings.dev_supabase_public_url.rstrip("/")
    real_ponds = {c["pond_id"] for c in tables.get("camera_config", [])}
    order: dict[Any, int] = {}
    uploaded = 0
    for row in sorted(tables.get("imageTable", []), key=lambda r: r.get("created_at", "")):
        path = _path(row)
        if row["user_ID"] in real_ponds:
            index = order.get(row["user_ID"], 0)
            order[row["user_ID"]] = index + 1
            data = demo_frame_bytes(index)
        else:
            data = synthetic_frame(row["green_ratio"])
        url = storage.upload_image(bucket, path, data)
        row["imageURL"] = f"{public_base}/storage/v1/object/public/{bucket}/{path}" if public_base else url
        uploaded += 1
    return uploaded


def remove_seed_frames(tables: dict[str, list[dict[str, Any]]], storage: Any, settings: Any) -> None:
    """Remove the deterministic objects written for a temporary --check seed."""
    paths = [_path(row) for row in tables.get("imageTable", [])]
    if paths:
        storage.delete_images(settings.pond_image_bucket, paths)
