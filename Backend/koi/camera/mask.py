"""The pond's water mask: a normalised polygon over the camera frame
(issue #39, migration 0010).

A mask is a list of [x, y] points, x and y in 0..1 as fractions of the
frame's width and height, origin at the top-left corner. The camera
service computes the green ratio over the pixels inside it only
(hsvEngine.analyze_image_bytes), so planting, edges and decking in view
do not change the reading.

The limits below are the same ones the database checks
(camera_mask_is_valid in migration 0010); change both together.
"""
from __future__ import annotations

from typing import Sequence

MIN_POINTS = 3
MAX_POINTS = 64
# Smallest enclosed area, as a fraction of the frame. 0.01 of a 320x240
# frame is 768 pixels.
MIN_AREA = 0.01


def polygon_area(points: Sequence[Sequence[float]]) -> float:
    """Enclosed area of the polygon (shoelace formula), as a fraction of
    the frame for normalised points."""
    twice = 0.0
    for i, (x, y) in enumerate(points):
        nx, ny = points[(i + 1) % len(points)]
        twice += x * ny - nx * y
    return abs(twice) / 2


def validate_polygon(points: object) -> list[list[float]]:
    """points as a list of [x, y] floats; raises ValueError naming the
    first rule it breaks."""
    if not isinstance(points, (list, tuple)):
        raise ValueError("the mask must be a list of [x, y] points")
    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        raise ValueError(f"the mask must have {MIN_POINTS} to {MAX_POINTS} points, not {len(points)}")
    polygon = []
    for i, point in enumerate(points):
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in point)):
            raise ValueError(f"point {i} must be two numbers [x, y]")
        x, y = float(point[0]), float(point[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"point {i} must have x and y between 0 and 1 (fractions of the frame), "
                             f"not [{x}, {y}]")
        polygon.append([x, y])
    area = polygon_area(polygon)
    if area < MIN_AREA:
        raise ValueError(f"the mask encloses {area:.4f} of the frame; it must enclose at least {MIN_AREA}")
    return polygon


# Named regions (issue #91, migration 0023). Each is a polygon checked by
# validate_polygon; camera_regions_are_valid in migration 0023 enforces the
# same rules in the database.
REQUIRED_REGIONS = ("water_gap", "rim", "plants")
OPTIONAL_REGIONS = ("reference",)
REGION_NAMES = REQUIRED_REGIONS + OPTIONAL_REGIONS


def validate_regions(value: object) -> dict[str, list[list[float]]]:
    """value as {name: polygon}: water_gap, rim and plants, optionally
    reference, and nothing else. Raises ValueError naming the first
    region and rule it breaks."""
    if not isinstance(value, dict):
        raise ValueError("regions must be an object of named polygons")
    unknown = sorted(str(name) for name in value if name not in REGION_NAMES)
    if unknown:
        raise ValueError(f"unknown region {unknown[0]!r}; regions are {', '.join(REGION_NAMES)}")
    missing = [name for name in REQUIRED_REGIONS if name not in value]
    if missing:
        raise ValueError(f"region {missing[0]!r} is required ({', '.join(REQUIRED_REGIONS)} are)")
    regions = {}
    for name in REGION_NAMES:
        if name in value:
            try:
                regions[name] = validate_polygon(value[name])
            except ValueError as exc:
                raise ValueError(f"region {name!r}: {exc}") from None
    return regions


__all__ = ["MAX_POINTS", "MIN_AREA", "MIN_POINTS", "OPTIONAL_REGIONS", "REGION_NAMES", "REQUIRED_REGIONS",
           "polygon_area", "validate_polygon", "validate_regions"]
