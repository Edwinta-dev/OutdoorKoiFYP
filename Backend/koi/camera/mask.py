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


__all__ = ["MAX_POINTS", "MIN_AREA", "MIN_POINTS", "polygon_area", "validate_polygon"]
