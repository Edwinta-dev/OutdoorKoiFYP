"""Issue #33: the backtest harness (koi/tools/backtest.py) and its
baseline (Backend/backtest_baseline.json).

Run from Backend/: python -m pytest -q -k backtest
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from koi.dev import seed as seed_data
from koi.models import evaporation_engine as ev
from koi.tools import backtest as bt

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
HOURS = [T0 + i * HOUR for i in range(10 * 24)]


def air(h: datetime) -> float:
    i = (h - T0) / HOUR
    return 30.0 + 2.0 * math.sin(2 * math.pi * i / 24) + math.sin(2 * math.pi * i / 101)


# ---------------------------------------------------------------------
# Water temperature
# ---------------------------------------------------------------------
def test_water_temperature_scores_a_perfect_model_against_persistence():
    airs = {h: air(h) for h in HOURS}
    water = {h: air(h - 2 * HOUR) - 1.5 for h in HOURS[2:]}
    metrics, details = bt.water_temperature_metrics(water, airs)
    assert details["fit"]["lag_hours"] == 2.0
    assert details["fit"]["offset_c"] == pytest.approx(-1.5)
    for h in bt.HOURS_AHEAD:
        assert metrics[f"water_temp_rmse_c_{h}h"] == pytest.approx(0.0, abs=1e-4)
        assert metrics[f"water_temp_persistence_rmse_c_{h}h"] > 0
        assert metrics[f"water_temp_skill_{h}h"] == pytest.approx(1.0, abs=1e-3)
    # Persistence gets worse with the horizon on a daily cycle.
    p = [metrics[f"water_temp_persistence_rmse_c_{h}h"] for h in bt.HOURS_AHEAD]
    assert p == sorted(p)
    assert [r["hours_ahead"] for r in details["horizons"]] == list(bt.HOURS_AHEAD)
    assert all(r["beats_persistence"] for r in details["horizons"])


def test_water_temperature_persistence_error_is_the_change_over_the_horizon():
    """Water that rises 0.1 C an hour: persistence is off by 0.1 C per hour
    ahead, whatever the model does."""
    airs = {h: 25.0 for h in HOURS}
    water = {h: 20.0 + 0.1 * i for i, h in enumerate(HOURS)}
    metrics, _ = bt.water_temperature_metrics(water, airs)
    for h in bt.HOURS_AHEAD:
        assert metrics[f"water_temp_persistence_rmse_c_{h}h"] == pytest.approx(0.1 * h, abs=1e-4)


def test_water_temperature_without_enough_hours_gives_no_metrics():
    few = HOURS[:20]
    metrics, details = bt.water_temperature_metrics({h: 28.0 for h in few}, {h: 29.0 for h in few})
    assert details["fit_status"] == "pending"
    assert metrics["water_temp_rmse_c_1h"] is None
    assert set(metrics) == {f"water_temp_{k}_{h}h" for h in bt.HOURS_AHEAD
                            for k in ("rmse_c", "persistence_rmse_c", "skill")}


# ---------------------------------------------------------------------
# Evaporation
# ---------------------------------------------------------------------
def _sample(litres: float, hours: int = 48) -> ev.TopUpSample:
    conditions = ev.HourConditions(air_temp_c=30.0, relative_humidity_pct=70.0, wind_speed_ms=3.0, rain_mm=0.0,
                                   water_temp_c=29.0, surface_area_m2=4.0)
    return ev.TopUpSample(start=T0, end=T0 + hours * HOUR, litres_added=litres, hours=[conditions] * hours)


def test_evaporation_scores_each_topup_against_the_modelled_loss():
    exact = ev.modelled_topup_litres(_sample(1.0), ev.POND_SHELTER_FACTOR)
    metrics, details = bt.evaporation_metrics([_sample(exact), _sample(exact / 2)], [{"reason": "no_volume_logged"}])
    assert metrics["evaporation_topups_scored"] == 2
    # Second top-up: the model gives twice the litres logged.
    assert metrics["evaporation_mean_error_l"] == pytest.approx(exact / 4, abs=0.01)
    assert metrics["evaporation_mean_abs_pct_error"] == pytest.approx(50.0, abs=0.01)
    assert [t["error_litres"] for t in details["topups"]] == [0.0, pytest.approx(exact / 2, abs=0.01)]
    assert details["skipped_topups"] == [{"reason": "no_volume_logged"}]
    assert details["shelter_factor"] == ev.POND_SHELTER_FACTOR


def test_evaporation_with_no_topups_has_no_error_metrics():
    metrics, _ = bt.evaporation_metrics([], [])
    assert metrics["evaporation_topups_scored"] == 0
    assert metrics["evaporation_mean_abs_error_l"] is None


# ---------------------------------------------------------------------
# Ladder
# ---------------------------------------------------------------------
def test_ladder_precision_base_rate_lift_and_years():
    rows = [
        {"date": "2020-01-01", "year": "2020", "clear_day": "1", "DRY": "1", "heat_holds": "0", "HARD": "0"},
        {"date": "2020-01-02", "year": "2020", "clear_day": "1", "DRY": "0", "heat_holds": "1", "HARD": "1"},
        {"date": "2021-01-01", "year": "2021", "clear_day": "1", "DRY": "1", "heat_holds": "1", "HARD": "0"},
        {"date": "2021-01-02", "year": "2021", "clear_day": "0", "DRY": "0", "heat_holds": "0", "HARD": "1"},
    ]
    metrics, details = bt.ladder_metrics(rows)
    assert metrics["ladder_days"] == 4
    assert metrics["ladder_clear_day_precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert metrics["ladder_clear_day_lowest_year_precision"] == 0.5
    assert details["clear_day"]["base_rate"] == 0.5
    assert details["clear_day"]["lift"] == pytest.approx((2 / 3) / 0.5, abs=1e-3)
    assert details["clear_day"]["precision_by_year"] == {"2020": 0.5, "2021": 1.0}
    assert metrics["ladder_heat_holds_precision"] == 0.5
    assert metrics["ladder_heat_holds_fires"] == 2


def test_ladder_rule_that_never_fires_has_no_precision():
    rows = [{"date": "2020-01-01", "clear_day": "0", "DRY": "1", "heat_holds": "False", "HARD": "1"}]
    metrics, _ = bt.ladder_metrics(rows)
    assert metrics["ladder_clear_day_precision"] is None
    assert metrics["ladder_heat_holds_lowest_year_precision"] is None


def test_nea_ladder_reproduces_the_validated_precisions():
    """The README's figures: clear day 0.861 on 15.6% of days, heat holds
    0.842 on 11.0%, over 2,302 days."""
    result = bt.nea_ladder()
    m, rules = result["metrics"], result["details"]["rules"]
    assert m["ladder_days"] == 2302
    assert m["ladder_clear_day_precision"] == pytest.approx(0.861, abs=5e-4)
    assert m["ladder_heat_holds_precision"] == pytest.approx(0.842, abs=5e-4)
    assert rules["clear_day"]["fire_rate"] == pytest.approx(0.156, abs=5e-4)
    assert rules["heat_holds"]["fire_rate"] == pytest.approx(0.110, abs=5e-4)


# ---------------------------------------------------------------------
# Demo pond
# ---------------------------------------------------------------------
def test_demo_pond_replays_the_seed(tmp_path):
    assert bt.main(["--scenario", "demo_pond", "--out", str(tmp_path)]) == 0
    result = json.loads((tmp_path / "demo_pond.json").read_text(encoding="utf-8"))
    assert result["scenario"] == "demo_pond"
    m, d = result["metrics"], result["details"]
    assert d["water_temperature"]["fit_status"] == "fitted"
    # The seed's air peaks an hour before its water.
    assert d["water_temperature"]["fit"]["lag_hours"] == 1.0
    for h in bt.HOURS_AHEAD:
        assert m[f"water_temp_rmse_c_{h}h"] > 0
        assert m[f"water_temp_persistence_rmse_c_{h}h"] > 0
    # One top-up after the water change, with every hour's weather known.
    assert m["evaporation_topups_scored"] == 1
    (topup,) = d["evaporation"]["topups"]
    assert topup["logged_litres"] == 100.0
    assert topup["hour_coverage"] == 1.0
    assert result["inputs"]["air_hours"] == result["inputs"]["rain_hours"]


def test_backtest_weather_stays_out_of_the_development_stack():
    seed = seed_data.load()
    block = seed["backtest"]
    hours = len(block["air_temperature_c"])
    assert hours == len(block["wind_speed_knots"]) == len(block["rainfall_mm"]) == 14 * 24 + 1
    assert "weather_observation" not in seed["tables"]
    shifted = seed_data.shift(seed, datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert "weather_observation" not in shifted["tables"]
    observations, issuances = bt.weather_history_rows(block)
    assert len(observations) == 3 * hours
    assert all(i["payload"]["relativeHumidity"]["low"] < i["payload"]["relativeHumidity"]["high"]
               for i in issuances)


# ---------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------
def spec(value, better, tolerance):
    return {"value": value, "better": better, "tolerance": tolerance}


@pytest.mark.parametrize("value, better, expected", [
    (1.0, "lower", "ok"),
    (1.05, "lower", "ok"),       # inside the tolerance
    (1.11, "lower", "worse"),
    (0.8, "lower", "better"),
    (0.95, "higher", "ok"),
    (0.89, "higher", "worse"),
    (1.2, "higher", "better"),
    (None, "lower", "worse"),
])
def test_compare_applies_direction_and_tolerance(value, better, expected):
    (finding,) = bt.compare("s", {"m": value}, {"m": spec(1.0, better, 0.1)})
    assert finding.status == expected


def test_compare_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        bt.compare("s", {"m": 1.0}, {"m": spec(1.0, "smaller", 0)})


def _baseline(tmp_path, version=1, value=1.0):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"version": version, "scenarios": {
        "fake": {"metrics": {"error": spec(value, "lower", 0.1)}}}}), encoding="utf-8")
    return path


def _doc(tmp_path, text="Backtest baseline version 1 (test): first."):
    path = tmp_path / "models.md"
    path.write_text(text, encoding="utf-8")
    return path


def _scenarios(error):
    return {"fake": lambda: {"scenario": "fake", "metrics": {"error": error}}}


def test_check_passes_within_tolerance_and_writes_results(tmp_path):
    ok, lines = bt.check(_baseline(tmp_path), tmp_path / "out", _doc(tmp_path), _scenarios(1.05))
    assert ok
    assert lines[-1] == "1 of 1 metrics within baseline version 1"
    assert (tmp_path / "out" / "fake.json").exists()


def test_check_fails_when_a_metric_gets_worse(tmp_path):
    ok, lines = bt.check(_baseline(tmp_path), tmp_path / "out", _doc(tmp_path), _scenarios(1.5))
    assert not ok
    assert any(line.startswith("WORSE  fake.error: 1.5") for line in lines)
    assert lines[-1] == "0 of 1 metrics within baseline version 1, 1 worse"


def test_check_reports_an_improvement_without_failing(tmp_path):
    ok, lines = bt.check(_baseline(tmp_path), tmp_path / "out", _doc(tmp_path), _scenarios(0.5))
    assert ok
    assert "1 better" in lines[-1]


def test_check_fails_without_a_reason_for_the_baseline_version(tmp_path):
    doc = _doc(tmp_path, "Backtest baseline version 1 (test): first.")
    ok, lines = bt.check(_baseline(tmp_path, version=2), tmp_path / "out", doc, _scenarios(1.0))
    assert not ok
    assert "Backtest baseline version 2" in lines[0]
    # Version 12 is not version 1.
    assert not bt.documented(1, _doc(tmp_path, "Backtest baseline version 12"))


def test_check_fails_on_an_unknown_scenario(tmp_path):
    ok, lines = bt.check(_baseline(tmp_path), tmp_path / "out", _doc(tmp_path), {})
    assert not ok
    assert "'fake' does not exist" in lines[0]


def test_update_baseline_records_values_and_raises_the_version(tmp_path):
    path = _baseline(tmp_path)
    assert bt.update_baseline(path, tmp_path / "out", _scenarios(1.5)) == 2
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert saved["scenarios"]["fake"]["metrics"]["error"] == spec(1.5, "lower", 0.1)
    # Until docs/models.md gives the reason, the check fails.
    assert not bt.check(path, tmp_path / "out", _doc(tmp_path), _scenarios(1.5))[0]
    assert bt.check(path, tmp_path / "out", _doc(tmp_path, "Backtest baseline version 2: why."),
                    _scenarios(1.5))[0]


def test_committed_baseline_passes_and_is_documented(tmp_path):
    baseline = bt.load_baseline()
    assert set(baseline["scenarios"]) == set(bt.SCENARIOS)
    assert bt.documented(baseline["version"])
    ok, lines = bt.check(bt.BASELINE_PATH, tmp_path, bt.MODELS_DOC)
    assert ok, lines
