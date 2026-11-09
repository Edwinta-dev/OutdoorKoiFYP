"""Rigid, versioned placements of the shared colour-card template."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

MASK_IDS = ("white", "nh3", "ph")
MIN_SCALE = 0.2
MAX_SCALE = 3.0
ASPECT = 4 / 3
TEMPLATE_FILE = Path(__file__).with_name("card_template.json")


@dataclass(frozen=True)
class CardPlacement:
    cx: float
    cy: float
    scale: float
    angle: float

    def to_json(self) -> dict[str, float]:
        return {"cx": self.cx, "cy": self.cy, "scale": self.scale, "angle": self.angle}

    def map(self, center: tuple[float, float], x: float, y: float, aspect: float = ASPECT) -> list[float]:
        dx = (x - center[0]) * aspect * self.scale
        dy = (y - center[1]) * self.scale
        c, s = math.cos(self.angle), math.sin(self.angle)
        return [self.cx + (dx * c - dy * s) / aspect, self.cy + (dx * s + dy * c)]


@dataclass(frozen=True)
class CardPlacements:
    by_mask: dict[str, CardPlacement]

    @classmethod
    def validate(cls, raw: object) -> "CardPlacements":
        if not isinstance(raw, dict) or set(raw) != set(MASK_IDS):
            raise ValueError("placements must contain exactly white, nh3 and ph")
        result = {}
        for mask_id in MASK_IDS:
            item = raw[mask_id]
            if not isinstance(item, dict) or set(item) != {"cx", "cy", "scale", "angle"}:
                raise ValueError(f"placements.{mask_id} must contain cx, cy, scale and angle")
            values = {}
            for key, value in item.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"placements.{mask_id}.{key} must be a finite number")
                values[key] = float(value)
            if not MIN_SCALE <= values["scale"] <= MAX_SCALE:
                raise ValueError(f"placements.{mask_id}.scale must be between {MIN_SCALE} and {MAX_SCALE}")
            result[mask_id] = CardPlacement(**values)
        return cls(result)

    def to_json(self) -> dict[str, dict[str, float]]:
        return {mask_id: self.by_mask[mask_id].to_json() for mask_id in MASK_IDS}

    def patches(self, template: dict | None = None) -> list[dict]:
        template = template or card_template()
        output = []
        patches = template["patches"]
        for mask_id in MASK_IDS:
            own = [p for p in patches if mask_of(p["name"]) == mask_id]
            xs = [q[0] for p in own for q in p["polygon"]]
            ys = [q[1] for p in own for q in p["polygon"]]
            center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
            placement = self.by_mask[mask_id]
            for patch in own:
                output.append({
                    "name": patch["name"], "kind": patch_kind(patch["name"]),
                    "polygon": [placement.map(center, point[0], point[1], template.get("frame_aspect", ASPECT))
                                for point in patch["polygon"]],
                    "points": [placement.map(center, point[0], point[1], template.get("frame_aspect", ASPECT))
                               for point in patch.get("points", [])],
                })
        return output


def mask_of(name: str) -> str:
    return "white" if name.startswith("white") else "nh3" if name.startswith("nh3") else "ph"


def patch_kind(name: str) -> str:
    if name == "white_ref":
        return "white_reference"
    if name.endswith("_reading"):
        return name
    return name.split("_ref_")[0] + "_reference"


@lru_cache(maxsize=1)
def card_template() -> dict:
    return json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))


def home_placements(template: dict | None = None) -> CardPlacements:
    template = template or card_template()
    placements = {}
    for mask_id in MASK_IDS:
        points = [q for p in template["patches"] if mask_of(p["name"]) == mask_id for q in p["polygon"]]
        placements[mask_id] = CardPlacement((min(p[0] for p in points) + max(p[0] for p in points)) / 2,
                                            (min(p[1] for p in points) + max(p[1] for p in points)) / 2, 1, 0)
    return CardPlacements(placements)


def patches_for_pond(path: str, pond_id: int) -> Optional[dict]:
    """Legacy unplaced file layout used until a pond saves placements."""
    if not path:
        return None
    try:
        from koi.camera.card_patches import patches_for_pond as legacy
        return legacy(path, pond_id)
    except (OSError, ValueError, TypeError):
        return None


__all__ = ["ASPECT", "MAX_SCALE", "MIN_SCALE", "MASK_IDS", "CardPlacement", "CardPlacements",
           "card_template", "home_placements", "mask_of", "patch_kind", "patches_for_pond"]
