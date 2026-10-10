"""The chemistry rain term (issue #27): observed rain at the assigned
station over the rolling 24 hours and the pond's depth, scored by the
dilution 1 - exp(-R/D), replacing the forecast-wording points.
Run from Backend/: python -m pytest tests/models/test_rain_dilution.py
"""
from datetime import datetime, timedelta, timezone

import pytest

from koi.models import forecast_utils, ladder
from koi.models.engine import PondConfig, WaterChemistryEngine
from koi.models.pond_twin import PondTwin
from koi.models.profile import PondProfile, ProfileHistory
from koi.storage import MemoryStorage
from koi.weather.history import local_day_window

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 8, 18, tzinfo=SGT)


def complete(total_mm: float) -> dict:
    return {"station_id": "S24", "total_mm": total_mm, "status": "complete", "coverage": 1.0}


def two_point_engine(volume: float = 1000.0) -> WaterChemistryEngine:
    """TAN 0.6 ppm (+1) and NO3 50 ppm (+1): one point short of Watch."""
    engine = WaterChemistryEngine(PondConfig(volume_litres=volume, estimated_biomass_grams=2000))
    engine._tan_mg = 0.6 * volume
    engine._no3_mg = 50.0 * volume
    return engine


def first_point_mm(depth_m: float) -> int:
    return next(mm for mm in range(0, 1000)
                if WaterChemistryEngine.rain_dilution_term(complete(mm), depth_m)["points"] >= 1)


# ---------------------------------------------------------------------
# Dilution and its pinned levels
# ---------------------------------------------------------------------
def test_dilution_converts_millimetres_and_metres():
    assert WaterChemistryEngine.rain_dilution_fraction(30, 1.2) == pytest.approx(0.0247, abs=5e-4)
    assert WaterChemistryEngine.rain_dilution_fraction(100, 1.2) == pytest.approx(0.07996, abs=1e-5)
    assert WaterChemistryEngine.rain_dilution_fraction(0, 1.2) == 0.0
    with pytest.raises(ValueError):
        WaterChemistryEngine.rain_dilution_fraction(30, 0.0)


def test_rain_levels_are_pinned():
    assert WaterChemistryEngine.RAIN_DILUTION_LEVELS == (0.08, 0.16)


def test_shallow_pond_reaches_the_rain_point_at_lower_rainfall():
    deep, shallow = first_point_mm(1.2), first_point_mm(0.4)
    # About 100 mm on a 1.2 m pond; about a third of that on 0.4 m.
    assert deep == 101
    assert shallow == 34
    assert shallow < deep
    assert WaterChemistryEngine.rain_dilution_term(complete(60), 0.4)["points"] == 1
    assert WaterChemistryEngine.rain_dilution_term(complete(60), 1.2)["points"] == 0
    assert WaterChemistryEngine.rain_dilution_term(complete(250), 1.2)["points"] == 2


def test_levels_are_configurable():
    term = WaterChemistryEngine.rain_dilution_term(complete(30), 1.2, levels=(0.02,))
    assert term["points"] == 1 and term["levels"] == [0.02]


# ---------------------------------------------------------------------
# Forecast wording no longer scores
# ---------------------------------------------------------------------
def test_thundery_forecast_alone_no_longer_changes_the_score():
    engine = two_point_engine()
    incoming, intensity = forecast_utils.rain_context_from_text("Thundery Showers")
    assert (incoming, intensity) == (True, "heavy")
    dry = engine.assess(rain_incoming=False)
    thundery = engine.assess(rain_incoming=incoming, rain_intensity=intensity)
    assert dry.risk_score == thundery.risk_score == 2
    assert thundery.status == "Green"


def test_thundery_forecast_days_do_not_change_the_projection():
    engine = two_point_engine()
    kwargs = dict(avg_daily_tan_mg=0.0, fallback_temp_c=29.0, fallback_lux=10000, horizon_days=4)
    plain = engine.project_forward(**kwargs)
    thundery = engine.project_forward(**kwargs, daily_rain=[(True, "heavy")] * 4)
    assert [r["risk_score"] for r in plain["trajectory"]] == [r["risk_score"] for r in thundery["trajectory"]]
    assert plain["first_watch_days_from_now"] == thundery["first_watch_days_from_now"]


def test_observed_rain_adds_points_by_depth():
    engine = two_point_engine()
    shallow = engine.assess(observed_rain=complete(60), depth_m=0.4)
    deep = engine.assess(observed_rain=complete(60), depth_m=1.2)
    assert (shallow.risk_score, shallow.status) == (3, "Amber")
    assert (deep.risk_score, deep.status) == (2, "Green")
    assert shallow.to_dict()["rain_dilution"]["dilution_fraction"] == pytest.approx(0.1393, abs=1e-4)
    assert shallow.rain_dilution["status"] == "assessed" and shallow.rain_dilution["confidence"] == "full"


# ---------------------------------------------------------------------
# Missing or incomplete inputs are not zero dilution
# ---------------------------------------------------------------------
@pytest.mark.parametrize("rain, depth, reason", [
    (None, 1.2, "No rainfall station"),
    ({"station_id": "S24", "total_mm": None, "status": "no_data", "coverage": 0.0}, 1.2, "incomplete"),
    (complete(300), None, "no depth"),
    (complete(300), 0.0, "no depth"),
])
def test_unknown_rain_or_depth_reduces_confidence(rain, depth, reason):
    term = WaterChemistryEngine.rain_dilution_term(rain, depth)
    assert term["status"] == "insufficient_data"
    assert term["confidence"] == "reduced"
    assert term["dilution_fraction"] is None and term["points"] == 0
    assert any(reason in r for r in term["reasons"])


def test_partial_rain_counts_as_a_lower_bound_with_reduced_confidence():
    partial = {"station_id": "S24", "total_mm": 40.0, "status": "partial", "coverage": 0.5}
    term = WaterChemistryEngine.rain_dilution_term(partial, 0.4)
    assert term["points"] == 1
    assert term["status"] == "insufficient_data" and term["confidence"] == "reduced"
    assert "lower bound" in term["reasons"][0]


# ---------------------------------------------------------------------
# Rolling 24 hours (chemistry) versus yesterday's local day (REACT)
# ---------------------------------------------------------------------
def hourly_rain(start: datetime, end: datetime, wet: dict) -> list[dict]:
    rows, t = [], start
    while t < end:
        rows.append({"source": "nea_v2", "series": "station", "station_id": "S24", "metric": "rainfall",
                     "value": wet.get(t, 0.0), "unit": "mm", "semantics": "interval_total",
                     "observed_from": t.isoformat(), "observed_to": (t + timedelta(hours=1)).isoformat(),
                     "fetched_at": (t + timedelta(hours=1)).isoformat(), "regime_id": None, "provenance": {}})
        t += timedelta(hours=1)
    return rows


def test_chemistry_window_is_rolling_24_hours_and_react_is_yesterday():
    yesterday_start, _ = local_day_window((NOW - timedelta(days=1)).date())
    wet = {datetime(2026, 10, 7, 10, tzinfo=SGT): 60.0,   # yesterday morning
           datetime(2026, 10, 8, 9, tzinfo=SGT): 40.0}    # this morning
    storage = MemoryStorage()
    storage.add_rows("weather_observation", hourly_rain(yesterday_start, NOW, wet))
    sources = {"stations": {"rainfall": "S24"}}

    rolling = forecast_utils.observed_rain_24h(storage, sources, NOW)
    assert (rolling["status"], rolling["total_mm"]) == ("complete", 40.0)
    assert WaterChemistryEngine.rain_dilution_window(NOW) == (NOW - timedelta(hours=24), NOW)

    start, end = local_day_window((NOW - timedelta(days=1)).date())
    yesterday = storage.fetch_rainfall_total("S24", start, end, as_of=NOW)
    assert (yesterday["status"], yesterday["total_mm"]) == ("complete", 60.0)
    assert ladder.react(yesterday, decision_time=NOW)["actions"]

    assert forecast_utils.observed_rain_24h(storage, {"stations": {}}, NOW) is None


def test_twin_uses_the_profile_depth_for_the_rain_term():
    twin = PondTwin.create(PondConfig(volume_litres=1000, estimated_biomass_grams=2000))
    twin.profiles = ProfileHistory([PondProfile(volume_l=1000, biomass_g=2000, depth_m=0.4)])
    twin.chemistry._tan_mg, twin.chemistry._no3_mg = 600.0, 50000.0
    chem = twin.ingest_environment(now=NOW, sample=None, evaporation_env=None, algae_env=None,
                                   observed_rain=complete(60))["chemistry"]
    assert chem.rain_dilution["depth_m"] == 0.4 and chem.risk_score == 3

    twin.profiles = ProfileHistory([PondProfile(volume_l=1000, biomass_g=2000)])
    unknown = twin.ingest_environment(now=NOW, sample=None, evaporation_env=None, algae_env=None,
                                      observed_rain=complete(60))["chemistry"]
    assert unknown.rain_dilution["confidence"] == "reduced" and unknown.risk_score == 2
