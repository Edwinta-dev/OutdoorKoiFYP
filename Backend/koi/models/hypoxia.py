"""Night-window advisory for low dissolved oxygen, which is not measured."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from koi.models.hypoxia_state import HypoxiaState
from koi.models.local_time import DEFAULT_POND_TIME_ZONE, zone

NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6
WARMUP_HOURS = 24
MODEL_ID = "hypoxia-night-advisory-v2"
HORIZON = "overnight"
ISSUE_TIME_PRECISION_SECONDS = 3600
EXPLANATION = "DO is not measured; low oxygen is common before dawn; aerate overnight."
ADVICE = ["Run aeration (air pump, air stone, waterfall or fountain) through the night."]
SHADOW_WINDOW_DAYS = 14
SHADOW_MIN_SAMPLES = 48
MAD_TO_STD = 0.6744897501960817
SHADOW_RANGE_MADS = 3.0


def shadow_ph_relative(state: HypoxiaState, now: datetime) -> dict:
    """Compute a pond-relative pH signal from retained event-time samples."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cutoff = now.astimezone(timezone.utc) - timedelta(days=SHADOW_WINDOW_DAYS)
    samples = [(at, ph) for at, ph, _ in state.samples
               if cutoff <= at.astimezone(timezone.utc) <= now.astimezone(timezone.utc)]
    n = len(samples)
    current = samples[-1][1] if samples else None
    if n < SHADOW_MIN_SAMPLES or current is None:
        return {"ph_z": None, "ph_below_own_range": None, "n": n}
    values = [ph for _, ph in samples]
    center = median(values)
    mad = median([abs(value - center) for value in values])
    difference = current - center
    ph_z = 0.0 if mad == 0 and difference == 0 else (
        MAD_TO_STD * difference / mad if mad else None)
    return {"ph_z": ph_z,
            "ph_below_own_range": current < center - SHADOW_RANGE_MADS * mad,
            "n": n}


@dataclass(frozen=True)
class HypoxiaThresholds:
    """Deprecated settings compatibility; values do not affect this advisory."""
    watch_temp_c: float = 30.0
    high_temp_c: float = 32.0


@dataclass
class HypoxiaFlag:
    level: str
    flagged: bool
    explanation: str
    advice: list[str]
    model_id: str
    horizon: str
    support: Optional[str]
    issue_time: str
    dark: Optional[bool] = None
    lux: Optional[float] = None
    water_temp_c: Optional[float] = None
    aeration: Optional[bool] = None
    algae_high: Optional[bool] = None
    watch_temp_c: Optional[float] = None
    high_temp_c: Optional[float] = None
    night_lux_threshold: Optional[float] = None
    raised_by: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def assess_hypoxia(*, now: datetime, ph: Optional[float], water_temp_c: Optional[float],
                   history_hours: float) -> HypoxiaFlag:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(zone(DEFAULT_POND_TIME_ZONE))
    night = local.hour >= NIGHT_START_HOUR or local.hour < NIGHT_END_HOUR
    level = "unknown" if history_hours < WARMUP_HOURS or ph is None or water_temp_c is None else (
        "watch" if night else "none")
    if history_hours < WARMUP_HOURS:
        explanation = f"History is warming up; {WARMUP_HOURS} hours are required."
    elif ph is None:
        explanation = "pH is missing or stale, so the advisory is unknown."
    elif water_temp_c is None:
        explanation = "Water temperature is missing or stale, so the advisory is unknown."
    elif night:
        explanation = EXPLANATION
    else:
        explanation = "Outside the overnight advisory window."
    return HypoxiaFlag(level, False, explanation, list(ADVICE) if level == "watch" else [],
                       MODEL_ID, HORIZON, f"{history_hours:g} hours of history",
                       datetime.fromtimestamp((now.timestamp() // ISSUE_TIME_PRECISION_SECONDS)
                                              * ISSUE_TIME_PRECISION_SECONDS, tz=now.tzinfo).isoformat(),
                       raised_by=[])


__all__ = [
    "ADVICE", "HORIZON", "ISSUE_TIME_PRECISION_SECONDS", "MAD_TO_STD", "MODEL_ID", "NIGHT_END_HOUR",
    "NIGHT_START_HOUR", "WARMUP_HOURS", "HypoxiaFlag", "HypoxiaThresholds", "assess_hypoxia",
    "SHADOW_MIN_SAMPLES", "SHADOW_RANGE_MADS", "SHADOW_WINDOW_DAYS", "shadow_ph_relative",
]
