"""Night-time hypoxia (low dissolved oxygen) risk flag.

Warm water holds less oxygen, and after dark the algae and plants stop
producing it while fish, bacteria and the algae themselves keep using
it (report Section 2.2). Dissolved oxygen is not measured, so this is a
rule over the readings that drive it, not a measurement:

  * dark: the pond's own light sensor below NIGHT_LUX_THRESHOLD, the
    same cut the chemistry engine uses to pick night pH samples;
  * water temperature at or above the watch level (default 30 C) gives
    "watch", at or above the high level (default 32 C) gives "high";
  * a flagged night is raised one level (capped at "high") when the
    pond profile records aeration as off, and again when algae coverage
    is past the pond's scrub level (the algae assessment's scrub_now).

Daylight, or water below the watch level, gives "none" whatever the
other two factors say. A missing temperature or light reading gives
"unknown". Unknown aeration (a profile that leaves it unset) does not
raise the level; the explanation says it is not recorded.

The poller computes the flag each cycle from the reading it ingests;
/assessment/all computes it from the latest reading, the pond's profile
in force now and the latest cached algae assessment.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from koi.models.engine import NIGHT_LUX_THRESHOLD

LEVELS = ("none", "watch", "high")

DEFAULT_WATCH_TEMP_C = 30.0
DEFAULT_HIGH_TEMP_C = 32.0

ADVICE = [
    "Run aeration (air pump, air stone, waterfall or fountain) through the night.",
    "Skip the evening feed: digesting fish use more oxygen, and uneaten food uses more as it breaks down.",
]


@dataclass(frozen=True)
class HypoxiaThresholds:
    watch_temp_c: float = DEFAULT_WATCH_TEMP_C
    high_temp_c: float = DEFAULT_HIGH_TEMP_C

    def __post_init__(self) -> None:
        if not self.watch_temp_c < self.high_temp_c:
            raise ValueError("the hypoxia watch temperature must be below the high temperature")


@dataclass
class HypoxiaFlag:
    level: str                      # "unknown" | "none" | "watch" | "high"
    dark: Optional[bool]
    lux: Optional[float]
    water_temp_c: Optional[float]
    aeration: Optional[bool]
    algae_high: Optional[bool]
    watch_temp_c: float
    high_temp_c: float
    night_lux_threshold: float
    raised_by: list = field(default_factory=list)  # "no_aeration", "high_algae"
    explanation: str = ""
    advice: list = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.level in ("watch", "high")

    def to_dict(self) -> dict:
        return {**asdict(self), "flagged": self.flagged}


def algae_is_high(algae_assessment: Optional[dict]) -> Optional[bool]:
    """Whether an algae assessment (AlgaeAssessment.to_dict() or a cached
    pond_algae_evaluations row) has coverage past the pond's scrub level.
    None when there is no assessment."""
    if not algae_assessment:
        return None
    if algae_assessment.get("scrub_now") is not None:
        return bool(algae_assessment["scrub_now"])
    return algae_assessment.get("status") == "Red"


def assess_hypoxia(
    *,
    lux: Optional[float],
    water_temp_c: Optional[float],
    aeration: Optional[bool],
    algae_high: Optional[bool],
    thresholds: Optional[HypoxiaThresholds] = None,
) -> HypoxiaFlag:
    t = thresholds or HypoxiaThresholds()
    flag = HypoxiaFlag(
        level="unknown",
        dark=None if lux is None else lux < NIGHT_LUX_THRESHOLD,
        lux=lux,
        water_temp_c=water_temp_c,
        aeration=aeration,
        algae_high=algae_high,
        watch_temp_c=t.watch_temp_c,
        high_temp_c=t.high_temp_c,
        night_lux_threshold=NIGHT_LUX_THRESHOLD,
    )

    if lux is None or water_temp_c is None:
        flag.explanation = ("No recent water temperature or light reading, so the night-time "
                            "oxygen risk cannot be checked.")
        return flag

    if not flag.dark:
        flag.level = "none"
        flag.explanation = (f"Light at the pond is {lux:.0f} lux, so algae and plants are still "
                            f"adding oxygen. No night-time oxygen risk now.")
        return flag

    if water_temp_c < t.watch_temp_c:
        flag.level = "none"
        flag.explanation = (f"Dark at the pond and the water is {water_temp_c:.1f} °C, below the "
                            f"{t.watch_temp_c:.1f} °C watch level. Night-time oxygen risk is low.")
        return flag

    index = LEVELS.index("high" if water_temp_c >= t.high_temp_c else "watch")
    if aeration is False:
        flag.raised_by.append("no_aeration")
    if algae_high:
        flag.raised_by.append("high_algae")
    flag.level = LEVELS[min(index + len(flag.raised_by), len(LEVELS) - 1)]

    level_c = t.high_temp_c if water_temp_c >= t.high_temp_c else t.watch_temp_c
    parts = [
        f"Dark at the pond ({lux:.0f} lux) and the water is {water_temp_c:.1f} °C, at or above "
        f"the {level_c:.1f} °C {'high' if level_c == t.high_temp_c else 'watch'} level. Warm water "
        f"holds less oxygen, and at night fish, bacteria and algae keep using it while nothing "
        f"adds it, so oxygen is lowest just before dawn.",
    ]
    if aeration is False:
        parts.append("The pond profile records no aeration, which raises the risk.")
    elif aeration is None:
        parts.append("Aeration is not recorded in the pond profile.")
    if algae_high:
        parts.append("Algae coverage is past this pond's scrub level, and algae use oxygen "
                     "overnight, which raises the risk.")
    parts.append("Dissolved oxygen is not measured; this is a model estimate from water "
                 "temperature, light and the pond profile.")
    flag.explanation = " ".join(parts)
    flag.advice = list(ADVICE)
    return flag


__all__ = ["ADVICE", "DEFAULT_HIGH_TEMP_C", "DEFAULT_WATCH_TEMP_C", "HypoxiaFlag", "HypoxiaThresholds",
           "LEVELS", "algae_is_high", "assess_hypoxia"]
