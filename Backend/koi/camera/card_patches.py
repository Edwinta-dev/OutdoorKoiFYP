"""Card patches: the named spots of the colour test cards the camera sees,
as small polygons over the frame. The app's "Rate What You See" card draws
its sampled-areas dots inside them, and nowhere else.

A pond's frame shows a white reference area and two colour wheels (the
card-referenced colorimetry of the report): the ammonia wheel and the pH
wheel, each with reference colour segments and a centre pad that takes the
actual reading. The patches name those spots:

    white_ref                 the white reference area
    nh3_ref_1 .. nh3_ref_5    the ammonia wheel's 5 reference segments
    ph_ref_1 .. ph_ref_8      the pH wheel's 8 reference segments
    nh3_reading               the ammonia wheel's centre pad (the reading)
    ph_reading                the pH wheel's centre pad (the reading)

Each patch is {"name", "polygon", "points"}; a polygon is a list of [x, y]
points as fractions of the frame (0..1, origin top-left), as in
koi/camera/mask.py, and points are the sample positions inside it (the
app's dots; the demo layout is 9 per wheel segment, so the 8-segment pH
wheel has 72). Patches are small, so the minimum area is far below the
mask's.

The patches are NOT part of the camera config (camera_config.regions, which
the database constrains to its four named regions) and need no migration:
a deployment names a JSON file of them in Settings.camera_card_patches_file
({"pond_id": N, "patches": [...]}), and GET /v1/ponds/{pond}/camera/mask
returns them as "card_patches" for that pond. No file, another pond or an
unreadable file: no key.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

MAX_PATCHES = 32
MAX_POINTS = 64
MAX_SAMPLES = 400
# Smallest enclosed area, as a fraction of the frame (a 5 x 5 pixel square
# of a 640x480 frame is about 0.00008).
MIN_AREA = 0.0001

_NAME = re.compile(r"^(white_ref|nh3_reading|ph_reading|nh3_ref_[1-5]|ph_ref_[1-8])$")


def patch_kind(name: str) -> str:
    """white_reference, nh3_reference, ph_reference, nh3_reading or ph_reading."""
    if name == "white_ref":
        return "white_reference"
    if name.endswith("_reading"):
        return name
    return name.split("_ref_")[0] + "_reference"


def _area(points: list[list[float]]) -> float:
    twice = 0.0
    for i, (x, y) in enumerate(points):
        nx, ny = points[(i + 1) % len(points)]
        twice += x * ny - nx * y
    return abs(twice) / 2


def _inside(polygon: list[list[float]], x: float, y: float) -> bool:
    inside = False
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[i - 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
    return inside


def validate_card_patches(value: object) -> list[dict]:
    """value as a list of {"name", "polygon"}; raises ValueError naming the
    first patch and rule it breaks. Returns normalised copies."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("card patches must be a list of {name, polygon}")
    if not 1 <= len(value) <= MAX_PATCHES:
        raise ValueError(f"card patches must be 1 to {MAX_PATCHES}, not {len(value)}")
    out, seen = [], set()
    for item in value:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"unknown card patch {name!r}; names are white_ref, nh3_ref_1..5, ph_ref_1..8, "
                             "nh3_reading, ph_reading")
        if name in seen:
            raise ValueError(f"card patch {name!r} is listed twice")
        seen.add(name)
        points = item.get("polygon")
        if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= MAX_POINTS:
            raise ValueError(f"card patch {name!r}: the polygon must have 3 to {MAX_POINTS} points")
        polygon = []
        for i, point in enumerate(points):
            if (not isinstance(point, (list, tuple)) or len(point) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in point)):
                raise ValueError(f"card patch {name!r}: point {i} must be two numbers [x, y]")
            x, y = float(point[0]), float(point[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"card patch {name!r}: point {i} must have x and y between 0 and 1, "
                                 f"not [{x}, {y}]")
            polygon.append([x, y])
        if _area(polygon) < MIN_AREA:
            raise ValueError(f"card patch {name!r}: the polygon encloses {_area(polygon):.5f} of the frame; "
                             f"it must enclose at least {MIN_AREA}")
        samples = item.get("points", [])
        if not isinstance(samples, (list, tuple)) or len(samples) > MAX_SAMPLES:
            raise ValueError(f"card patch {name!r}: points must be a list of at most {MAX_SAMPLES}")
        points_out = []
        for i, point in enumerate(samples):
            if (not isinstance(point, (list, tuple)) or len(point) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in point)):
                raise ValueError(f"card patch {name!r}: sample {i} must be two numbers [x, y]")
            x, y = float(point[0]), float(point[1])
            if not _inside(polygon, x, y):
                raise ValueError(f"card patch {name!r}: sample {i} [{x}, {y}] lies outside its polygon")
            points_out.append([x, y])
        out.append({"name": name, "polygon": polygon, "points": points_out})
    return out


def patches_for_pond(path: str, pond_id: int) -> Optional[dict]:
    """{"version", "patches": [{"name", "kind", "polygon"}]} from the JSON
    file at path when it holds patches for pond_id, else None (also for no
    path, a missing or unreadable file, or invalid patches)."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(data.get("pond_id")) != int(pond_id):
            return None
        patches = validate_card_patches(data.get("patches"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return {"version": 1, "patches": [{**p, "kind": patch_kind(p["name"])} for p in patches]}


__all__ = ["MAX_PATCHES", "MIN_AREA", "patch_kind", "patches_for_pond", "validate_card_patches"]
