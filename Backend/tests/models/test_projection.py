"""Checks WaterChemistryEngine.project_forward() and forecast_utils
against realistic inputs: the NEA 4-day outlook shape, a week of logged
feeding, and three pond scenarios (a moderately stocked 5000 L pond, a
1000 L pond already over the nitrite limit, and a heavily fed 500 L pond).

The scenario numbers below pin the model's current output. They are model
estimates of TAN/NO2/NO3, not measurements.
Run from Backend/: python -m pytest tests/models/test_projection.py
"""
import json
from datetime import datetime, timezone

from koi.models import forecast_utils
from koi.models.engine import EventKind, PondConfig, PondEvent, WaterChemistryEngine

OUTLOOK_PAYLOAD = json.loads("""
{
  "nea_forecasts": {
    "forecast_2hr": {"forecast": "Cloudy"},
    "outlook_4day": [
      {"data": {"day": "Wednesday", "forecast": {"code": "TL", "text": "Thundery Showers"},
                "temperature": {"low": 25, "high": 33, "unit": "Degrees Celsius"}}, "slot_id": "WED"},
      {"data": {"day": "Thursday", "forecast": {"code": "WD", "text": "Windy"},
                "temperature": {"low": 27, "high": 34, "unit": "Degrees Celsius"}}, "slot_id": "THU"},
      {"data": {"day": "Friday", "forecast": {"code": "WD", "text": "Windy"},
                "temperature": {"low": 27, "high": 34, "unit": "Degrees Celsius"}}, "slot_id": "FRI"},
      {"data": {"day": "Saturday", "forecast": {"code": "FA", "text": "Fair (Day)"},
                "temperature": {"low": 26, "high": 34, "unit": "Degrees Celsius"}}, "slot_id": "SAT"}
    ]
  }
}
""")
DAILY = forecast_utils.daily_forecasts_from_outlook(
    OUTLOOK_PAYLOAD["nea_forecasts"]["outlook_4day"])

NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)
# Seven feeds of 45-55 g at 32% protein, one per day from Aug 12 to Aug 18.
FEEDING_ROWS = [
    {"food_grams": grams, "protein_percentage": 32,
     "event_timestamp": NOW.replace(day=day).isoformat()}
    for day, grams in [(18, 50), (17, 50), (16, 45), (15, 55), (14, 50), (13, 50), (12, 50)]
]

NITRITE_OVERRIDE_PPM = WaterChemistryEngine.NITRITE_OVERRIDE_PPM


def avg_tan():
    return WaterChemistryEngine.estimate_avg_daily_tan_mg(FEEDING_ROWS)


def stocked_engine():
    """5000 L pond, 8 kg of fish, one 50 g feed already applied."""
    config = PondConfig(volume_litres=5000, estimated_biomass_grams=8000, tap_nitrate_ppm=2.0)
    engine = WaterChemistryEngine(config)
    engine.apply_event(PondEvent(kind=EventKind.FEEDING, time=NOW, food_grams=50, protein_percent=32))
    return engine


def stocked_projection(engine, horizon_days=30, with_rain=True):
    kwargs = {"daily_rain": DAILY["rain"]} if with_rain else {}
    return engine.project_forward(
        avg_daily_tan_mg=avg_tan(),
        daily_temp_forecast_c=DAILY["temp_c"],
        daily_lux_forecast=[20000 * m for m in DAILY["lux_multiplier"]],
        fallback_temp_c=29.0,
        fallback_lux=20000,
        horizon_days=horizon_days,
        **kwargs,
    )


def test_forecast_utils_reads_the_nea_outlook():
    assert DAILY["temp_c"] == [29.0, 30.5, 30.5, 30.0], f"temp midpoints wrong: {DAILY['temp_c']}"
    # TL -> 0.5, WD -> 0.95, FA -> 1.0
    assert DAILY["lux_multiplier"] == [0.5, 0.95, 0.95, 1.0], \
        f"lux multipliers wrong: {DAILY['lux_multiplier']}"
    assert DAILY["rain"][0] == (True, "heavy"), "thundery showers should flag heavy rain"
    assert DAILY["rain"][1] == (False, "unknown"), "windy/fair should not flag rain"
    assert all(r == (False, "unknown") for r in DAILY["rain"][1:]), \
        f"only the thundery day flags rain: {DAILY['rain']}"


def test_estimate_avg_daily_tan_from_feeding_history():
    # 350 g of food over the 6-day span between the oldest and newest feed:
    # 350 * 0.32 * 0.16 * 0.70 * 1000 / 6 = 2090.67 mg/day
    tan = avg_tan()
    assert 2000 < tan < 2200, f"unexpected avg TAN rate: {tan}"
    assert abs(tan - 350 * 0.32 * 0.16 * 0.70 * 1000 / 6) < 1e-6, \
        f"avg TAN rate does not match the textbook formula: {tan}"


def test_stocked_pond_stays_clear_for_30_days():
    result = stocked_projection(stocked_engine())
    trajectory = result["trajectory"]
    assert len(trajectory) == 30, f"trajectory length should match horizon: {len(trajectory)}"
    assert [r["days_from_now"] for r in trajectory] == list(range(1, 31)), \
        "trajectory days should run 1..30"
    assert result["assumptions"]["horizon_days"] == 30, "assumptions should echo the horizon"
    assert abs(result["assumptions"]["avg_daily_tan_mg"] - avg_tan()) < 1e-9, \
        "assumptions should echo the TAN rate used"
    assert "buffering" in result["caveat"], \
        "caveat should say buffering/reactivity is excluded from the projection"

    no3 = [r["no3_ppm"] for r in trajectory]
    assert no3[-1] >= no3[0], \
        "NO3 should trend up over a month of constant feeding with no water change"

    # At ~2.1 g TAN/day into 5000 L the waste load settles well below every
    # threshold: no watch, high-risk, nitrite or action day within 30 days.
    assert result["predicted_action_days_from_now"] is None, \
        f"5000 L pond should need no action within 30 days: {result['predicted_action_days_from_now']}"
    assert result["first_watch_days_from_now"] is None, "5000 L pond should not reach watch"
    assert result["first_high_risk_days_from_now"] is None, "5000 L pond should not reach high risk"
    assert result["first_nitrite_days_from_now"] is None, "5000 L pond should not trip nitrite"
    assert all(r["no2_ppm"] < NITRITE_OVERRIDE_PPM for r in trajectory), \
        f"NO2 should stay below {NITRITE_OVERRIDE_PPM} ppm: max {max(r['no2_ppm'] for r in trajectory):.3f}"
    assert all(r["tan_ppm"] < 0.5 for r in trajectory), \
        f"TAN should stay below 0.5 ppm: max {max(r['tan_ppm'] for r in trajectory):.3f}"
    assert no3[-1] < 5.0, f"NO3 should plateau under 5 ppm after 30 days: {no3[-1]:.2f}"


def test_projection_does_not_mutate_engine_state():
    engine = stocked_engine()
    snapshot_before = engine.to_snapshot()
    stocked_projection(engine, horizon_days=10, with_rain=False)
    snapshot_after = engine.to_snapshot()
    assert snapshot_before == snapshot_after, "project_forward must not mutate engine state"


def test_nitrite_already_over_limit_flags_day_zero():
    engine = WaterChemistryEngine(PondConfig(volume_litres=1000, estimated_biomass_grams=2000))
    engine._no2_mg = 600.0  # 0.6 ppm in a 1000 L pond, already above the 0.5 ppm override
    result = engine.project_forward(
        avg_daily_tan_mg=0.0,  # no feeding at all - isolate the nitrite check
        fallback_temp_c=25.0,
        fallback_lux=10000,
        horizon_days=5,
    )
    assert result["first_nitrite_days_from_now"] == 0, \
        "nitrite override should be flagged as 'already now' (day 0)"
    assert result["predicted_action_days_from_now"] == 0, \
        f"action should be due now: {result['predicted_action_days_from_now']}"
    assert len(result["trajectory"]) == 5, "trajectory length should match horizon"


def test_small_pond_heavy_feed_predicts_action_within_two_days():
    engine = WaterChemistryEngine(PondConfig(volume_litres=500, estimated_biomass_grams=1500))
    result = engine.project_forward(
        avg_daily_tan_mg=3000.0,  # heavy feeding relative to a small 500 L pond
        fallback_temp_c=27.0,
        fallback_lux=15000,
        horizon_days=30,
    )
    action_day = result["predicted_action_days_from_now"]
    assert action_day is not None and action_day > 0, \
        "a small pond fed heavily should cross a threshold within the horizon, " \
        "on a future day (not day 0)"
    assert action_day <= 2, f"heavy feed into 500 L should need action within 2 days: {action_day}"
    # Nitrite is what trips first: 3 g TAN/day into 500 L converts to NO2
    # faster than it clears.
    assert result["first_nitrite_days_from_now"] == action_day, \
        f"the nitrite override should drive the action day: " \
        f"nitrite={result['first_nitrite_days_from_now']} action={action_day}"
    day = result["trajectory"][action_day - 1]
    assert day["nitrite_override"] is True, "trajectory should mark the nitrite override"
    assert day["no2_ppm"] > NITRITE_OVERRIDE_PPM, \
        f"NO2 on the action day should exceed {NITRITE_OVERRIDE_PPM} ppm: {day['no2_ppm']:.3f}"
