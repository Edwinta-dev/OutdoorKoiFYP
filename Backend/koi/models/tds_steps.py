"""
tds_steps.py

Step detector for the TDS channel, the rule that report Section 4.4.3.1
test T4 applies offline: flag a change larger than the detection floor
that persists for more than one reading against a rolling baseline.

The offline viability analysis (analysis/tds_viability/) runs it over the
bench logs. It lives in the koi package so the server can run the same
rule if the bench tests show TDS prompting is worth building.

Pure functions, no I/O. Readings are (time, ppm) pairs in time order.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

# Readings that must deviate, one after another and on the same side of
# the baseline, before a step is flagged. "Persists for more than one
# reading" in the T4 rule means at least two.
DEFAULT_PERSIST_READINGS = 2

# Readings in the rolling baseline (median), 3 h at the 15-minute
# cadence. Also the fewest readings needed before the detector starts
# flagging. With a 3 x spread floor, 6 readings gave about 0.1 noise-only
# flags per week on synthetic data; 12 gave none.
DEFAULT_BASELINE_READINGS = 12


@dataclass(frozen=True)
class TdsStep:
    """One flagged step. time is the first reading of the new level;
    delta_ppm is the new level minus the baseline before it."""
    time: datetime
    index: int
    baseline_ppm: float
    level_ppm: float

    @property
    def delta_ppm(self) -> float:
        return self.level_ppm - self.baseline_ppm


def detect_steps(
    readings: Iterable[tuple[datetime, float]],
    floor_ppm: float,
    persist: int = DEFAULT_PERSIST_READINGS,
    baseline_readings: int = DEFAULT_BASELINE_READINGS,
) -> list[TdsStep]:
    """Returns the steps in a TDS series.

    A reading deviates when it differs from the median of the rolling
    baseline by more than floor_ppm. `persist` consecutive deviating
    readings on the same side flag a step; the baseline then restarts
    from those readings, so a held new level is flagged once. A shorter
    run of deviating readings is treated as a spike and left out of the
    baseline.
    """
    if floor_ppm <= 0:
        raise ValueError("floor_ppm must be positive")
    if persist < 1 or baseline_readings < 1:
        raise ValueError("persist and baseline_readings must be at least 1")

    steps: list[TdsStep] = []
    window: deque[float] = deque(maxlen=baseline_readings)
    pending: list[tuple[int, datetime, float]] = []
    pending_sign = 0

    for i, (time, ppm) in enumerate(readings):
        if len(window) < baseline_readings:
            window.append(ppm)
            continue
        baseline = statistics.median(window)
        diff = ppm - baseline
        if abs(diff) <= floor_ppm:
            pending, pending_sign = [], 0
            window.append(ppm)
            continue
        sign = 1 if diff > 0 else -1
        if sign != pending_sign:
            pending, pending_sign = [], sign
        pending.append((i, time, ppm))
        if len(pending) >= persist:
            first_i, first_time, _ = pending[0]
            level = statistics.median(p for _, _, p in pending)
            steps.append(TdsStep(first_time, first_i, baseline, level))
            window.clear()
            window.extend(p for _, _, p in pending)
            pending, pending_sign = [], 0
    return steps


def detection_floor(spread_ppm: float, multiplier: float = 3.0) -> float:
    """The T1 detection floor: three times the reading spread."""
    return multiplier * spread_ppm


def predicted_water_change_step(tds_old_ppm: float, fraction: float, tap_tds_ppm: float) -> float:
    """Change in TDS from replacing `fraction` of the water with tap water:
    TDS_new = TDS_old * (1 - f) + TDS_tap * f, returned as TDS_new - TDS_old."""
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    return (tds_old_ppm * (1.0 - fraction) + tap_tds_ppm * fraction) - tds_old_ppm


def predicted_salt_step(salt_grams: float, volume_litres: float) -> float:
    """Rise in TDS (ppm, taken as mg/L) from dissolving salt_grams of salt
    in volume_litres of water. 5 g in 100 L gives 50 ppm."""
    if volume_litres <= 0:
        raise ValueError("volume_litres must be positive")
    return salt_grams * 1000.0 / volume_litres
