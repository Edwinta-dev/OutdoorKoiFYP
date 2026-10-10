"""Regenerate ladder thresholds from the committed NEA CSV inputs."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "NEA_Data_Analysis"
OUTPUT = ROOT / "Backend" / "koi" / "models" / "ladder_params.json"
LABELS = DATA / "exploration_outputs" / "daily_labels.csv"
VALIDATION = DATA / "exploration_outputs" / "intervention_validation.csv"
TRAIN_END = date(2024, 12, 31)


def quantile(values: list[float], q: float) -> float:
    values = sorted(values)
    position = (len(values) - 1) * q
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def main() -> None:
    with (DATA / "daily_climate.csv").open(encoding="utf-8-sig", newline="") as f:
        climate = list(csv.DictReader(f))
    train = [r for r in climate if date.fromisoformat(r["date"]) <= TRAIN_END]
    old = [float(r["t_max"]) for r in train if "cluster" in r["source"]]
    # The notebook z-matches the instrument regime using that regime's
    # measured distribution, while the hot quantile itself is training-only.
    new = [float(r["t_max"]) for r in climate if "cluster" not in r["source"]]
    hot_old = quantile(old, .70)
    mean_old = sum(old) / len(old)
    std_old = (sum((x - mean_old) ** 2 for x in old) / (len(old) - 1)) ** .5
    mean_new = sum(new) / len(new)
    std_new = (sum((x - mean_new) ** 2 for x in new) / (len(new) - 1)) ** .5
    hot_new = mean_new + (hot_old - mean_old) / std_old * std_new
    with LABELS.open(encoding="utf-8-sig", newline="") as f:
        labels = list(csv.DictReader(f))
    train_labels = [r for r in labels if r.get("pooled_rank") and
                    date.fromisoformat(r["date"]) <= TRAIN_END]
    with VALIDATION.open(encoding="utf-8-sig", newline="") as f:
        validation = list(csv.DictReader(f))
    held_out = [r for r in validation if r["split"] == "TEST"]
    precisions = {
        "preempt_precision": next(float(r["precision"]) for r in held_out if "PREEMPT:" in r["rule"]),
        "window_precision": next(float(r["precision"]) for r in held_out if "dry today" in r["rule"]),
    }
    result = {
        "source": ("NEA_Data_Analysis/daily_climate.csv, exploration_outputs/daily_labels.csv, "
                   "and intervention_validation.csv"),
        "train_end": TRAIN_END.isoformat(), "hot_quantile": .70, "outlook_hot_quantile": .75,
        "window_quantile": .16, "react_rain_mm": 50.0, "dry_day_rain_mm": 1.0,
        "temperature_threshold_c": {"old": round(hot_old, 2), "new": round(hot_new, 2)},
        "outlook_hot_threshold_c": round(quantile([float(r["outlook_temp_ahead"]) for r in train_labels
                                                       if r.get("outlook_temp_ahead")], .75), 3),
        "pooled_dry_rank_max": round(quantile([float(r["pooled_rank"]) for r in train_labels], .16), 3),
        "evidence": {**precisions,
                     "nowcast_warning_minutes_median": 50},
        "limitations": ("PREEMPT's hot_next3 target is absent from the committed daily label export; "
                        "held-out precision comes from the notebook validation export."),
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
