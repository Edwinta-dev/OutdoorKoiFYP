"""Immutable kit/model pairs. Error is model estimate minus kit measurement.

Ammonia is paired with the model's total ammonia (TAN), not free NH3.
pH reactivity is not a pH estimate; neither pH nor KH has a model value.

fit_nitrification_scale (issue #32) fits a per-pond multiplier on both
nitrification rates from the readings with ammonia or nitrite.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from koi.models.engine import WaterChemistryEngine, _ammonia_mg
from koi.models.evaporation_engine import pending_calibration
from koi.models.pond_twin import PondTwin
from koi.settings import Settings
from koi.storage import Storage
from koi.storage.base import parse_timestamp
from koi.tools.rebuild import rebuild

ANALYTES = ("ammonia_mg_l", "nitrite_mg_l", "nitrate_mg_l", "ph", "kh_dkh")
MODEL_FIELDS = {"ammonia_mg_l": "tan_ppm", "nitrite_mg_l": "no2_ppm", "nitrate_mg_l": "no3_ppm"}

# Nitrification scale fit.
MIN_NITROGEN_READINGS = 4
# Readings this soon after the simulation starts (empty pools) are left
# out: TAN and nitrite settle within days of steady feeding.
SPIN_UP = timedelta(days=7)
SCALE_BOUNDS = (0.1, 5.0)
_SCALE_GRID_POINTS = 161
_BASE_TAN_TO_NO2 = 0.05
_BASE_NO2_TO_NO3 = 0.035


@dataclass
class NitrogenEvent:
    """A logged event as the chemistry engine applies it to TAN and
    nitrite: feeding adds TAN, a water change dilutes both."""
    time: datetime
    kind: str  # "FEEDING" | "WATER_CHANGE"
    food_grams: Optional[float] = None
    protein_percent: Optional[float] = None
    volume_percent: Optional[float] = None
    volume_litres: Optional[float] = None


def nitrogen_events(rows: list[dict]) -> list[NitrogenEvent]:
    """pondInterventions rows (Storage.fetch_interventions) as the events
    the simulation applies; other event types do not move TAN or nitrite."""
    def num(value: object) -> Optional[float]:
        return float(value) if value is not None else None  # type: ignore[arg-type]
    return [NitrogenEvent(time=parse_timestamp(r["event_timestamp"]), kind=r["event_type"],
                          food_grams=num(r.get("food_grams")), protein_percent=num(r.get("protein_percentage")),
                          volume_percent=num(r.get("volume_percentage")), volume_litres=num(r.get("volume_litres")))
            for r in rows if r.get("event_type") in ("FEEDING", "WATER_CHANGE") and r.get("event_timestamp")]


def simulate_nitrogen(events: list[NitrogenEvent], temp_at: Callable[[datetime], float],
                      volume_at: Callable[[datetime], float], start: datetime, times: list[datetime],
                      scale: float, step_hours: float = 1.0) -> list[tuple[float, float]]:
    """(TAN, nitrite) in mg/L at each of times (ascending), from empty
    pools at start, advanced by WaterChemistryEngine._step_pools (the live
    kinetics) with both rates times scale. Events are applied at their
    own times as WaterChemistryEngine.apply_event applies them."""
    tan = no2 = no3 = 0.0
    pending = sorted((e for e in events if e.time >= start), key=lambda e: e.time)
    out: list[tuple[float, float]] = []
    now, index = start, 0
    for target in times:
        while now < target:
            step_end = min(now + timedelta(hours=step_hours), target)
            while index < len(pending) and pending[index].time <= step_end:
                tan, no2 = _apply(pending[index], tan, no2, volume_at(pending[index].time))
                index += 1
            hours = (step_end - now).total_seconds() / 3600.0
            tan, no2, no3, _ = WaterChemistryEngine._step_pools(
                tan, no2, no3, 0, hours, temp_at(now), None,
                _BASE_TAN_TO_NO2 * scale, _BASE_NO2_TO_NO3 * scale)
            now = step_end
        volume = volume_at(target)
        out.append((tan / volume, no2 / volume))
    return out


def _apply(event: NitrogenEvent, tan: float, no2: float, volume: float) -> tuple[float, float]:
    if event.kind == "FEEDING":
        return tan + _ammonia_mg(food_grams=event.food_grams or 0.0, protein_percent=event.protein_percent or 0.0), no2
    if event.volume_percent is not None:
        pct = min(max(event.volume_percent / 100.0, 0.0), 1.0)
    elif event.volume_litres is not None and volume > 0:
        pct = min(max(event.volume_litres / volume, 0.0), 1.0)
    else:
        pct = 0.0
    return tan * (1 - pct), no2 * (1 - pct)


def fit_nitrification_scale(readings: list[dict], events: list[NitrogenEvent],
                            temp_at: Callable[[datetime], float], volume_at: Callable[[datetime], float],
                            start: datetime, *, min_readings: int = MIN_NITROGEN_READINGS) -> dict:
    """Fits the multiplier on both nitrification rates that makes the
    simulated TAN and nitrite best match the kit readings (least squares
    in mg/L over a log-spaced grid inside SCALE_BOUNDS).

    A reading counts when it has a taken_at at least SPIN_UP after start
    and an ammonia or a nitrite value; fewer than min_readings, or no
    feeding logged before the newest of them, gives a pending result. The
    error is the in-sample RMSE beside the RMSE at scale 1.0."""
    usable = sorted(
        (r for r in readings
         if r.get("taken_at") and parse_timestamp(r["taken_at"]) >= start + SPIN_UP
         and (r.get("ammonia_mg_l") is not None or r.get("nitrite_mg_l") is not None)),
        key=lambda r: (parse_timestamp(r["taken_at"]), r.get("id") or 0))
    if len(usable) < min_readings:
        return pending_calibration("not_enough_kit_readings", len(usable), required_readings=min_readings)
    times = [parse_timestamp(r["taken_at"]) for r in usable]
    if not any(e.kind == "FEEDING" and start <= e.time <= times[-1] for e in events):
        return pending_calibration("no_feeding_logged", len(usable))

    def errors(scale: float) -> list[tuple[str, float]]:
        out = []
        for row, (tan, no2) in zip(usable, simulate_nitrogen(events, temp_at, volume_at, start, times, scale),
                                   strict=True):
            if row.get("ammonia_mg_l") is not None:
                out.append(("ammonia_mg_l", tan - float(row["ammonia_mg_l"])))
            if row.get("nitrite_mg_l") is not None:
                out.append(("nitrite_mg_l", no2 - float(row["nitrite_mg_l"])))
        return out

    def rmse(values: list[float]) -> Optional[float]:
        return round(math.sqrt(sum(v * v for v in values) / len(values)), 5) if values else None

    low, high = SCALE_BOUNDS
    ratio = (high / low) ** (1 / (_SCALE_GRID_POINTS - 1))
    grid = [round(low * ratio ** i, 4) for i in range(_SCALE_GRID_POINTS)]
    scored = {scale: sum(e * e for _, e in errors(scale)) for scale in grid}
    scale = min(grid, key=lambda s: (scored[s], abs(math.log(s))))
    fitted, default = errors(scale), errors(1.0)
    return {
        "status": "fitted", "value": scale, "lag_hours": None, "sample_count": len(usable),
        "error": {"unit": "mg/L", "training_rmse": rmse([e for _, e in fitted]),
                  "default_rmse": rmse([e for _, e in default]),
                  "by_analyte": {key: rmse([e for k, e in fitted if k == key])
                                 for key in ("ammonia_mg_l", "nitrite_mg_l")}},
        "training_from": times[0], "training_to": times[-1], "evaluation_from": None, "evaluation_to": None,
        "details": {"at_bound": scale in (grid[0], grid[-1]), "bounds": list(SCALE_BOUNDS),
                    "simulation_start": start.isoformat(), "reading_ids": [r.get("id") for r in usable]},
    }


def record(storage: Storage, settings: Settings, pond: int, reading: dict, at: Optional[datetime]) -> dict:
    estimates = dict.fromkeys(ANALYTES)
    comparison: dict = {"source": "unavailable", "model_version": None, "evaluation_id": None,
                        "estimated_at": None, "rebuild_weather": None}
    if at is not None:
        evaluation = storage.fetch_chemistry_evaluation_at(pond, at)
        if evaluation is not None:
            estimates.update({key: evaluation.get(field) for key, field in MODEL_FIELDS.items()})
            comparison.update(source="evaluation", model_version=evaluation.get("model_version"),
                              evaluation_id=evaluation["id"], estimated_at=at.isoformat())
        else:
            report = rebuild(storage, settings, pond, now=at, dry_run=True)
            comparison["rebuild_weather"] = report.weather
            if report.rebuilt_snapshot is not None:
                assessment = PondTwin.from_snapshot(report.rebuilt_snapshot).chemistry.assess(
                    rain_incoming=False).to_dict()
                estimates.update({key: assessment.get(field) for key, field in MODEL_FIELDS.items()})
                comparison.update(source="rebuild", model_version=report.versions["model"],
                                  estimated_at=at.isoformat())
    differences = {key: estimates[key] - reading[key]
                   if estimates[key] is not None and reading.get(key) is not None else None for key in ANALYTES}
    return storage.insert_kit_reading(pond, {**reading, "estimates": estimates,
                                           "differences": differences, "comparison": comparison})


def validation(readings: list[dict]) -> dict:
    analytes = {}
    for key in ANALYTES:
        pairs = []
        for row in readings:
            measured, estimated = row.get(key), (row.get("estimates") or {}).get(key)
            if measured is not None and estimated is not None:
                pairs.append({"reading_id": row["id"], "taken_at": row.get("taken_at"),
                              "measured": measured, "estimated": estimated, "error": estimated - measured})
        errors = [pair["error"] for pair in pairs]
        analytes[key] = {"count": len(pairs), "mean_error": sum(errors) / len(errors) if errors else None,
                         "mean_absolute_error": sum(abs(e) for e in errors) / len(errors) if errors else None,
                         "pairs": pairs}
    return {"analytes": analytes}
