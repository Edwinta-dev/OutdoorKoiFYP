import csv
from datetime import datetime, timezone
from pathlib import Path

from koi.models import ladder

NOW = datetime(2026, 10, 8, 8, tzinfo=timezone.utc)


def test_react_requires_complete_rain_and_fires_at_threshold():
    incomplete = ladder.react({"status": "partial", "total_mm": 60}, decision_time=NOW)
    none = ladder.react({"status": "complete", "total_mm": 49.9}, decision_time=NOW)
    fired = ladder.react({"status": "complete", "total_mm": 50}, decision_time=NOW)
    assert incomplete["status"] == "insufficient_data"
    assert none["status"] == "assessed" and none["actions"] == []
    assert fired["actions"][0]["rule"] == "REACT"


def test_preempt_requires_both_heat_signals():
    assert ladder.preempt(observed_tmax_c=33, regime="old", outlook_high_c=33,
                          outlook_hot_threshold_c=32)["actions"]
    assert ladder.preempt(observed_tmax_c=30, regime="old", outlook_high_c=33,
                          outlook_hot_threshold_c=32)["actions"] == []
    assert ladder.preempt(observed_tmax_c=33, regime="unknown", outlook_high_c=33,
                          outlook_hot_threshold_c=32)["status"] == "insufficient_data"


def test_window_distinguishes_dry_rank_from_missing():
    assert ladder.window(pooled_rank=None, dry_band_max=.16)["status"] == "insufficient_data"
    assert ladder.window(pooled_rank=.2, dry_band_max=.16)["actions"] == []
    assert ladder.window(pooled_rank=.1, dry_band_max=.16)["actions"]


def test_nowcast_rain_call_and_valid_no_rain():
    assert ladder.nowcast(rain_call=None)["status"] == "insufficient_data"
    assert ladder.nowcast(rain_call="Fair") ["actions"] == []
    assert ladder.nowcast(rain_call="Thundery Showers")["actions"]


def test_composite_actions_exposes_missing_assessments():
    result = ladder.actions(decision_time=NOW)
    assert result["status"] == "insufficient_data"
    assert {r["rule"] for r in result["rules"]} == {"REACT", "PREEMPT", "WINDOW", "NOWCAST"}


def test_held_out_window_precision_from_committed_daily_labels():
    root = Path(__file__).resolve().parents[3]
    labels_path = root / "NEA_Data_Analysis" / "exploration_outputs" / "daily_labels.csv"
    with labels_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    held_out_fires = [r for r in rows if r["date"] > "2024-12-31" and r["f_window"] == "True"
                      and r["rain_mm"]]
    precision = sum(float(r["rain_mm"]) < 1 for r in held_out_fires) / len(held_out_fires)
    assert len(held_out_fires) == 121
    assert abs(precision - 0.859504) <= 0.02


def test_held_out_preempt_precision_matches_committed_notebook_validation():
    root = Path(__file__).resolve().parents[3]
    validation_path = root / "NEA_Data_Analysis" / "exploration_outputs" / "intervention_validation.csv"
    with validation_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    row = next(r for r in rows if r["split"] == "TEST" and "PREEMPT:" in r["rule"])
    assert abs(float(row["precision"]) - 0.885057) <= 0.02
    assert round(float(row["n"]) * float(row["precision"])) == 77
