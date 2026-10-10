"""Issue #32: the three calibration fits recover known values from
synthetic data, report pending with too little data, and the engines use
a fitted value when one is applied (defaults otherwise)."""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from koi import kit_readings
from koi.models import evaporation_engine as ev
from koi.models.engine import PondConfig, WaterChemistryEngine
from koi.models.pond_twin import PondTwin

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def air_series(hours, start=T0):
    """A daily cycle around 29 C with a slower weather swing, so lags and
    persistence are distinguishable."""
    return {start + i * HOUR: 29.0 + 3.0 * math.sin(2 * math.pi * i / 24) + 1.5 * math.sin(2 * math.pi * i / 97)
            for i in range(hours)}


# ---------------------------------------------------------------------
# Water/air temperature: offset and lag
# ---------------------------------------------------------------------
def test_calibration_temperature_fit_recovers_offset_and_lag_and_beats_persistence():
    air = air_series(24 * 14)
    true_lag, true_offset = 3, -1.7
    water = {h: air[h - true_lag * HOUR] + true_offset + 0.05 * math.sin(i * 1.3)
             for i, h in enumerate(sorted(air)) if h - true_lag * HOUR in air}
    result = ev.fit_water_air_temperature(water, air)
    assert result["status"] == "fitted"
    assert result["lag_hours"] == true_lag
    assert result["value"] == pytest.approx(true_offset, abs=0.02)
    error = result["error"]
    assert error["evaluation_rmse"] < 0.1
    assert error["evaluation_rmse"] < error["persistence_rmse"]
    assert error["evaluation_rmse"] < error["default_rmse"]
    assert result["details"]["beats_persistence"] is True
    # Training and evaluation are separate, evaluation later.
    assert result["training_to"] <= result["evaluation_from"]
    assert result["details"]["training_hours"] + result["details"]["evaluation_hours"] == result["sample_count"]


def test_calibration_temperature_fit_is_pending_without_enough_overlap():
    air = air_series(24 * 3)
    water = {h: v - 1.0 for h, v in list(air.items())[:40]}
    result = ev.fit_water_air_temperature(water, air)
    assert result["status"] == "pending" and result["value"] is None
    assert result["details"]["reason"] == "not_enough_overlap"
    # Hours without air at every lag do not count: 40 water hours, the first 12 lack lagged air.
    assert result["sample_count"] == 28


def test_calibration_temperature_fit_needs_a_persistence_counterpart_to_evaluate():
    # Enough aligned hours, but water only every other day: no evaluation hour
    # has the value 24 hours earlier.
    air = air_series(24 * 12)
    water = {h: v - 1.0 for h, v in air.items() if ((h - T0).days % 2 == 0)}
    result = ev.fit_water_air_temperature(water, air)
    assert result["status"] == "pending"
    assert result["details"]["reason"] == "not_enough_evaluation_hours"


def test_calibration_water_air_offset_daily_fit_is_unchanged():
    assert ev.fit_water_air_offset([28.0, 29.0, 30.0], [30.0, 31.0, 32.0]) == -2.0
    assert ev.fit_water_air_offset([28.0, None], [30.0, 31.0]) is None


# ---------------------------------------------------------------------
# Shelter factor from top-ups
# ---------------------------------------------------------------------
def hours_of(days, *, air=31.0, rh=70.0, wind=4.0, rain=None, area=4.0, missing_every=None):
    out = []
    for i in range(int(days * 24)):
        if missing_every and i % missing_every == 0:
            out.append(None)
            continue
        out.append(ev.HourConditions(air_temp_c=air, relative_humidity_pct=rh, wind_speed_ms=wind,
                                     rain_mm=(rain(i) if rain else 0.0), water_temp_c=air - 1.0,
                                     surface_area_m2=area))
    return out


def sample(days, factor, start=T0, **kwargs):
    hours = hours_of(days, **kwargs)
    s = ev.TopUpSample(start=start, end=start + timedelta(days=days), litres_added=0.0, hours=hours)
    s.litres_added = ev.modelled_topup_litres(s, factor)
    return s


def test_calibration_shelter_fit_recovers_the_true_factor():
    true = 0.35
    samples = [sample(5, true, wind=3.0), sample(8, true, wind=5.0, rh=80.0),
               sample(4, true, wind=6.0, rain=lambda i: 2.0 if i % 30 == 0 else 0.0)]
    result = ev.fit_shelter_factor(samples)
    assert result["status"] == "fitted"
    assert result["value"] == pytest.approx(true, abs=0.005)
    assert result["sample_count"] == 3
    assert result["error"]["training_rmse"] < 0.5
    assert result["error"]["default_rmse"] > result["error"]["training_rmse"]
    assert result["details"]["at_bound"] is False
    assert len(result["details"]["samples"]) == 3


def test_calibration_shelter_fit_with_two_usable_topups_is_pending():
    samples = [sample(5, 0.4), sample(6, 0.4), sample(6, 0.4, missing_every=2)]  # last: 50% coverage
    result = ev.fit_shelter_factor(samples)
    assert result["status"] == "pending" and result["value"] is None
    assert result["details"] == {"reason": "not_enough_topups", "required_samples": 3, "candidates": 3}
    assert result["sample_count"] == 2


def test_calibration_shelter_partial_coverage_scales_up_to_the_interval():
    full = sample(5, 0.6)
    partial = ev.TopUpSample(start=T0, end=T0 + timedelta(days=5), litres_added=1.0,
                             hours=hours_of(5, missing_every=5))
    assert partial.coverage == pytest.approx(0.8)
    assert ev.modelled_topup_litres(partial, 0.6) == pytest.approx(full.litres_added)


# ---------------------------------------------------------------------
# Nitrification rate scale from kit readings
# ---------------------------------------------------------------------
VOLUME = 3000.0


def feedings(days):
    return [kit_readings.NitrogenEvent(time=T0 + timedelta(days=d, hours=h), kind="FEEDING", food_grams=20.0,
                                       protein_percent=38.0) for d in range(days) for h in (8, 17)]


def readings_for(scale, times, events, temp=27.0):
    values = kit_readings.simulate_nitrogen(events, lambda t: temp, lambda t: VOLUME, T0, times, scale)
    return [{"id": i + 1, "taken_at": t.isoformat(), "ammonia_mg_l": tan, "nitrite_mg_l": no2 if i % 2 else None}
            for i, (t, (tan, no2)) in enumerate(zip(times, values, strict=True))]


def test_calibration_nitrification_fit_recovers_the_true_scale():
    events = feedings(30)
    times = [T0 + timedelta(days=d, hours=12) for d in (9, 13, 17, 22, 27)]
    true = 1.8
    result = kit_readings.fit_nitrification_scale(readings_for(true, times, events), events,
                                                  lambda t: 27.0, lambda t: VOLUME, T0)
    assert result["status"] == "fitted"
    # Grid spacing is about 2.5%.
    assert result["value"] == pytest.approx(true, rel=0.03)
    assert result["sample_count"] == 5
    assert result["error"]["training_rmse"] < result["error"]["default_rmse"]
    assert result["training_from"] == times[0] and result["training_to"] == times[-1]


def test_calibration_nitrification_fit_is_pending_with_three_readings_or_none_after_spin_up():
    events = feedings(30)
    times = [T0 + timedelta(days=d) for d in (10, 14, 18)]
    result = kit_readings.fit_nitrification_scale(readings_for(1.0, times, events), events,
                                                  lambda t: 27.0, lambda t: VOLUME, T0)
    assert result["status"] == "pending" and result["details"]["reason"] == "not_enough_kit_readings"
    # Readings in the first week after the simulation starts do not count.
    early = [T0 + timedelta(days=d) for d in (1, 2, 3, 4, 5)]
    result = kit_readings.fit_nitrification_scale(readings_for(1.0, early, events), events,
                                                  lambda t: 27.0, lambda t: VOLUME, T0)
    assert result["status"] == "pending" and result["sample_count"] == 0


def test_calibration_nitrification_fit_ignores_readings_without_ammonia_or_nitrite():
    events = feedings(30)
    times = [T0 + timedelta(days=d) for d in (10, 14, 18, 22)]
    readings = readings_for(1.0, times, events)
    readings[0].update(ammonia_mg_l=None, nitrite_mg_l=None, nitrate_mg_l=12.0)
    result = kit_readings.fit_nitrification_scale(readings, events, lambda t: 27.0, lambda t: VOLUME, T0)
    assert result["status"] == "pending" and result["sample_count"] == 3


def test_calibration_nitrification_fit_without_feeding_is_pending():
    times = [T0 + timedelta(days=d) for d in (10, 14, 18, 22)]
    readings = [{"id": i, "taken_at": t.isoformat(), "ammonia_mg_l": 0.1} for i, t in enumerate(times)]
    result = kit_readings.fit_nitrification_scale(readings, [], lambda t: 27.0, lambda t: VOLUME, T0)
    assert result["status"] == "pending" and result["details"]["reason"] == "no_feeding_logged"


def test_calibration_nitrogen_simulation_matches_the_engine_kinetics():
    """One feeding then 10 hours: the simulation and the engine's own
    pools agree, at scale 1 and at a fitted scale."""
    event = kit_readings.NitrogenEvent(time=T0, kind="FEEDING", food_grams=10.0, protein_percent=40.0)
    for scale in (1.0, 2.5):
        engine = WaterChemistryEngine(PondConfig(volume_litres=VOLUME, estimated_biomass_grams=1000.0))
        engine.nitrification_scale = scale
        engine._tan_mg = kit_readings._apply(event, 0.0, 0.0, VOLUME)[0]
        for _ in range(10):
            engine._advance_pools(1.0, 27.0, None)
        [(tan, no2)] = kit_readings.simulate_nitrogen([event], lambda t: 27.0, lambda t: VOLUME, T0,
                                                      [T0 + 10 * HOUR], scale)
        assert tan == pytest.approx(engine._tan_mg / VOLUME)
        assert no2 == pytest.approx(engine._no2_mg / VOLUME)


def test_calibration_water_change_dilutes_the_simulated_pools():
    events = [kit_readings.NitrogenEvent(time=T0, kind="FEEDING", food_grams=10.0, protein_percent=40.0),
              kit_readings.NitrogenEvent(time=T0 + 2 * HOUR, kind="WATER_CHANGE", volume_percent=50.0)]
    [(diluted, _)] = kit_readings.simulate_nitrogen(events, lambda t: 27.0, lambda t: VOLUME, T0,
                                                    [T0 + 2 * HOUR], 1.0)
    [(whole, _)] = kit_readings.simulate_nitrogen(events[:1], lambda t: 27.0, lambda t: VOLUME, T0,
                                                  [T0 + 2 * HOUR], 1.0)
    assert diluted == pytest.approx(whole / 2)


# ---------------------------------------------------------------------
# The engines use a fitted value when present, the default otherwise
# ---------------------------------------------------------------------
CONFIG = ev.EvaporationConfig(volume_litres=5000.0, estimated_biomass_grams=8000.0)
ENV = ev.DayEnvironment(air_temp_c=31.0, relative_humidity_pct=70.0, wind_speed_ms=4.0)


def loss_over_a_day(engine):
    engine.advance_state(T0, ENV)
    return engine.advance_state(T0 + timedelta(hours=12), ENV)


def test_calibration_shelter_factor_drives_the_loss_integral_and_projection():
    default, fitted = ev.EvaporationFeedEngine(CONFIG), ev.EvaporationFeedEngine(CONFIG)
    assert default.shelter_factor == ev.POND_SHELTER_FACTOR
    fitted.apply_calibration(shelter_factor=0.3, versions={"evaporation_shelter_factor": 7})
    assert fitted.shelter_factor == 0.3
    assert loss_over_a_day(fitted) < loss_over_a_day(default)
    days = [ENV]
    assert fitted.project_forward(daily_environment=days)["wind_shelter_factor"] == 0.3
    assert default.project_forward(daily_environment=days)["wind_shelter_factor"] == ev.POND_SHELTER_FACTOR
    # A scenario factor is read relative to the default.
    assert fitted.project_forward(daily_environment=days, shelter_factor=0.8)["wind_shelter_factor"] == \
        pytest.approx(0.4)
    assert default.project_forward(daily_environment=days, shelter_factor=0.8)["wind_shelter_factor"] == 0.8


def test_calibration_water_air_lag_uses_the_recorded_air_temperature():
    engine = ev.EvaporationFeedEngine(CONFIG)
    engine.apply_calibration(water_air_offset_c=-2.0, water_air_lag_hours=3.0)
    for i in range(5):
        engine.advance_state(T0 + i * HOUR, ev.DayEnvironment(air_temp_c=25.0 + i, relative_humidity_pct=70.0,
                                                              wind_speed_ms=3.0))
    # At T0+4h the water follows the air of T0+1h (26 C) minus 2.
    assert engine._recent_env[-1]["water"] == pytest.approx(24.0)
    # A lag without an offset is not applied.
    engine.apply_calibration(water_air_offset_c=None, water_air_lag_hours=3.0)
    assert engine._water_air_lag_hours is None and engine._water_air_offset_c is None


def test_calibration_evaporation_snapshot_round_trips_and_old_snapshots_load_uncalibrated():
    engine = ev.EvaporationFeedEngine(CONFIG)
    engine.apply_calibration(shelter_factor=0.45, water_air_offset_c=-1.4, water_air_lag_hours=2.0,
                             versions={"evaporation_shelter_factor": 3, "water_air_temperature": 4})
    restored = ev.EvaporationFeedEngine.from_snapshot(json.loads(json.dumps(engine.to_snapshot())))
    assert (restored.shelter_factor, restored._water_air_offset_c, restored._water_air_lag_hours) == (0.45, -1.4, 2.0)
    assert restored.calibration_versions == {"evaporation_shelter_factor": 3, "water_air_temperature": 4}


@pytest.mark.parametrize("fixture", ["snapshot_v2_utc_days.json", "snapshot_v3_sensor_inputs.json"])
def test_calibration_recorded_old_snapshots_load_with_defaults(fixture):
    twin = PondTwin.from_snapshot(json.loads((FIXTURES / fixture).read_text(encoding="utf-8")))
    assert twin.evaporation.shelter_factor == ev.POND_SHELTER_FACTOR
    assert twin.evaporation.calibration_versions == {}
    assert twin.chemistry.nitrification_scale == 1.0
    assert twin.chemistry.nitrification_scale_version is None


def test_calibration_nitrification_scale_speeds_conversion_and_round_trips():
    def engine(scale):
        e = WaterChemistryEngine(PondConfig(volume_litres=VOLUME, estimated_biomass_grams=1000.0))
        e.nitrification_scale = scale
        e._tan_mg = 3000.0
        return e
    slow, fast = engine(1.0), engine(2.0)
    slow._advance_pools(6.0, 27.0, None)
    fast._advance_pools(6.0, 27.0, None)
    assert fast._tan_mg < slow._tan_mg
    proj_slow = engine(1.0).project_forward(avg_daily_tan_mg=0.0, fallback_temp_c=27.0, horizon_days=1)
    proj_fast = engine(2.0).project_forward(avg_daily_tan_mg=0.0, fallback_temp_c=27.0, horizon_days=1)
    assert proj_fast["trajectory"][0]["tan_ppm"] < proj_slow["trajectory"][0]["tan_ppm"]
    stored = fast.to_snapshot()
    fast.nitrification_scale_version = 9
    restored = WaterChemistryEngine.from_snapshot(json.loads(json.dumps(fast.to_snapshot())))
    assert stored["nitrification_scale"] == 2.0
    assert (restored.nitrification_scale, restored.nitrification_scale_version) == (2.0, 9)
