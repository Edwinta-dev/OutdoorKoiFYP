"""Immutable kit/model pairs. Error is model estimate minus kit measurement.

Ammonia is paired with the model's total ammonia (TAN), not free NH3.
pH reactivity is not a pH estimate; neither pH nor KH has a model value.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from koi.models.pond_twin import PondTwin
from koi.settings import Settings
from koi.storage import Storage
from koi.tools.rebuild import rebuild

ANALYTES = ("ammonia_mg_l", "nitrite_mg_l", "nitrate_mg_l", "ph", "kh_dkh")
MODEL_FIELDS = {"ammonia_mg_l": "tan_ppm", "nitrite_mg_l": "no2_ppm", "nitrate_mg_l": "no3_ppm"}


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
