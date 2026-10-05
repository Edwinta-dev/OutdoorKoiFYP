"""Sensitivity envelopes preserve pinned central projections and live state."""
import copy

import pytest

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models import uncertainty as u
from koi.models.engine import PondConfig, WaterChemistryEngine


def engine_and_options(domain):
    if domain == "chemistry":
        engine = WaterChemistryEngine(PondConfig(volume_litres=1000, estimated_biomass_grams=3000))
        engine._tan_mg, engine._no2_mg, engine._no3_mg = 300, 100, 10000
        return engine, {"avg_daily_tan_mg": 1500, "fallback_temp_c": 29, "fallback_lux": 18000}
    if domain == "evaporation":
        engine = ev.EvaporationFeedEngine(ev.EvaporationConfig(1000, 3000))
        engine._cumulative_loss_litres = 25
        return engine, {"daily_environment": [ev.DayEnvironment(30, 70, 2, "none")]}
    engine = ae.AlgaeGrowthEngine()
    engine._green_ratio = 0.03
    return engine, {"daily_environment": [ae.AlgaeDayEnvironment(18000, 29, 10)]}


def assert_contains(result):
    bands = result["uncertainty"]
    for central, low, high in zip(result["trajectory"], bands["low"], bands["high"], strict=True):
        assert central["days_from_now"] == low["days_from_now"] == high["days_from_now"]
        for key in low:
            assert low[key] <= central[key] <= high[key]
    for key, interval in bands["first_crossing_days"].items():
        if key in result and result[key] is not None:
            assert interval["low"] <= result[key]
            assert interval["high"] is None or result[key] <= interval["high"]


@pytest.mark.parametrize("domain", ["chemistry", "evaporation", "algae"])
def test_uncertainty_preserves_central_and_state(domain):
    engine, options = engine_and_options(domain)
    before = copy.deepcopy(engine.to_snapshot())
    original = engine.project_forward(horizon_days=21, **options)
    result = u.project(engine, domain, horizon_days=21, **options)
    assert {k: v for k, v in result.items() if k != "uncertainty"} == original
    assert engine.to_snapshot() == before
    assert_contains(result)
    assert result["uncertainty"]["low"] != result["uncertainty"]["high"]


@pytest.mark.parametrize("domain", ["chemistry", "evaporation", "algae"])
def test_uncertainty_day_zero(domain):
    engine, options = engine_and_options(domain)
    if domain == "chemistry":
        engine._no2_mg = 10000
    elif domain == "evaporation":
        engine._cumulative_loss_litres = 200
    else:
        engine._green_ratio = 0.8
    result = u.project(engine, domain, horizon_days=1, **options)
    key = "first_nitrite_days_from_now" if domain == "chemistry" else "first_watch_days_from_now"
    assert result["uncertainty"]["first_crossing_days"][key] == {
        "low": 0, "high": 0, "not_crossed_runs": 0}


@pytest.mark.parametrize("values,expected", [
    ([None, None, None], {"low": None, "high": None, "not_crossed_runs": 3}),
    ([2, 4, None], {"low": 2, "high": None, "not_crossed_runs": 1}),
    ([8, 3, 5], {"low": 3, "high": 8, "not_crossed_runs": 0}),
])
def test_uncertainty_crossing_range(values, expected):
    assert u.crossing_range(values) == expected


def test_uncertainty_assumed_depth_only():
    engine, options = engine_and_options("evaporation")
    measured = u.scenarios(engine, "evaporation", **options)
    assumed = u.scenarios(engine, "evaporation", assumed_depth=True, **options)
    assert [r["assumed_depth_m"] for r in measured] == [1.2, 1.2, 1.2]
    assert [r["assumed_depth_m"] for r in assumed] == [1.0, 1.2, 1.5]
    assert measured[1] == assumed[1]


@pytest.mark.parametrize("source", ["fitted_from_camera", "measured_declining"])
def test_uncertainty_preserves_fitted_algae_rate(source):
    engine, options = engine_and_options("algae")
    engine._rate_source, engine._intrinsic_rate = source, 0.12
    runs = u.scenarios(engine, "algae", **options)
    assert runs[0] == runs[1] == runs[2]


@pytest.mark.parametrize("green", [0.03, 0.8])
def test_uncertainty_scrub_uses_same_three_runs(green, monkeypatch):
    engine, options = engine_and_options("algae")
    engine._green_ratio = green
    original = engine.project_scrub_benefit(horizon_days=21, **options)
    call = ae.AlgaeGrowthEngine.project_forward
    calls = []

    def counted(self, **kwargs):
        calls.append(kwargs)
        return call(self, **kwargs)

    monkeypatch.setattr(ae.AlgaeGrowthEngine, "project_forward", counted)
    result = u.project(engine, "algae", include_scrub_benefit=True, horizon_days=21, **options)
    assert len(calls) == 3
    assert result["scrub_benefit"] == original
    assert "scrub_benefit_days_bought" in result["uncertainty"]["first_crossing_days"]


def test_uncertainty_no_camera_keeps_error():
    engine, options = engine_and_options("algae")
    engine._green_ratio = None
    assert u.project(engine, "algae", **options) == engine.project_forward(**options)


def test_uncertainty_sources_and_central_constants_are_pinned():
    assert {k: v["central"] for k, v in u.RANGES.items()} == {
        "tan_to_no2_rate": 0.05, "no2_to_no3_rate": 0.035,
        "shelter_factor": ev.POND_SHELTER_FACTOR, "depth_m": ev.DEFAULT_POND_DEPTH_M,
        "fallback_intrinsic_rate": ae.FALLBACK_INTRINSIC_RATE}
    assert all(v["low"] < v["central"] < v["high"] and v["source"] for v in u.RANGES.values())


def test_uncertainty_pairs_chemistry_and_algae():
    chemistry, options = engine_and_options("chemistry")
    runs = u.scenarios(chemistry, "chemistry", horizon_days=21, **options)
    environments = [[ae.AlgaeDayEnvironment(18000, 29, d["no3_ppm"]) for d in r["trajectory"]] for r in runs]
    algae, _ = engine_and_options("algae")
    result = u.project(algae, "algae", daily_environment=environments[1],
                       scenario_environments=environments, horizon_days=21)
    assert result["trajectory"] == algae.project_forward(daily_environment=environments[1])["trajectory"]
    assert_contains(result)
