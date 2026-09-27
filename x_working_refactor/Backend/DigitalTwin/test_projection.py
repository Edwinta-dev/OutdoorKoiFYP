"""Ad-hoc verification script (not part of the deliverable) - sanity
checks project_forward() and forecast_utils against realistic inputs
before handing this off. Not wired into any test framework; just run
`python3 test_projection.py`."""
import json
from datetime import datetime, timezone

from engine import WaterChemistryEngine, PondConfig, PondEvent, EventKind
import forecast_utils

# ---- 1. forecast_utils against the exact NEA payload shape provided ----
sample_payload = json.loads("""
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

outlook = sample_payload["nea_forecasts"]["outlook_4day"]
daily = forecast_utils.daily_forecasts_from_outlook(outlook)
print("temp_c forecast:", daily["temp_c"])          # expect [29.0, 30.5, 30.5, 30.0]
print("lux multipliers:", daily["lux_multiplier"])   # expect [0.5 (TL), 0.95 (WD), 0.95 (WD), 1.0 (FA)]
print("rain per day:", daily["rain"])                # expect [(True,'heavy'), (False,'unknown')*3]

assert daily["temp_c"] == [29.0, 30.5, 30.5, 30.0], "temp midpoints wrong"
assert daily["lux_multiplier"] == [0.5, 0.95, 0.95, 1.0], "lux multipliers wrong"
assert daily["rain"][0] == (True, "heavy"), "thundery showers should flag heavy rain"
assert daily["rain"][1] == (False, "unknown"), "windy/fair should not flag rain"
print("forecast_utils: OK\n")

# ---- 2. estimate_avg_daily_tan_mg from a synthetic feeding history ----
now = datetime(2026, 8, 18, tzinfo=timezone.utc)
feeding_rows = [
    {"food_grams": 50, "protein_percentage": 32, "event_timestamp": (now.replace(day=18)).isoformat()},
    {"food_grams": 50, "protein_percentage": 32, "event_timestamp": (now.replace(day=17)).isoformat()},
    {"food_grams": 45, "protein_percentage": 32, "event_timestamp": (now.replace(day=16)).isoformat()},
    {"food_grams": 55, "protein_percentage": 32, "event_timestamp": (now.replace(day=15)).isoformat()},
    {"food_grams": 50, "protein_percentage": 32, "event_timestamp": (now.replace(day=14)).isoformat()},
    {"food_grams": 50, "protein_percentage": 32, "event_timestamp": (now.replace(day=13)).isoformat()},
    {"food_grams": 50, "protein_percentage": 32, "event_timestamp": (now.replace(day=12)).isoformat()},
]
avg_tan = WaterChemistryEngine.estimate_avg_daily_tan_mg(feeding_rows)
print(f"estimate_avg_daily_tan_mg over {len(feeding_rows)} events spanning 6 days: {avg_tan:.1f} mg/day")
# total food 350g over the 6-day span between oldest/newest timestamp ->
# 350 * 0.32 * 0.16 * 0.70 * 1000 / 6 = ~2090.7 mg/day
assert 2000 < avg_tan < 2200, f"unexpected avg TAN rate: {avg_tan}"
print("estimate_avg_daily_tan_mg: OK\n")

# ---- 3. Full project_forward simulation on a moderately stocked pond ----
config = PondConfig(volume_litres=5000, estimated_biomass_grams=8000, tap_nitrate_ppm=2.0)
engine = WaterChemistryEngine(config)

# Give it some starting waste load (a few days of feeding already applied),
# roughly matching what the pond would look like mid-cycle.
engine.apply_event(PondEvent(kind=EventKind.FEEDING, time=now, food_grams=50, protein_percent=32))

result = engine.project_forward(
    avg_daily_tan_mg=avg_tan,
    daily_temp_forecast_c=daily["temp_c"],
    daily_lux_forecast=[20000 * m for m in daily["lux_multiplier"]],
    daily_rain=daily["rain"],
    fallback_temp_c=29.0,
    fallback_lux=20000,
    horizon_days=30,
)

print("assumptions:", result["assumptions"])
print("predicted_action_days_from_now:", result["predicted_action_days_from_now"])
print("first_watch_days_from_now:", result["first_watch_days_from_now"])
print("first_high_risk_days_from_now:", result["first_high_risk_days_from_now"])
print("first_nitrite_days_from_now:", result["first_nitrite_days_from_now"])
print("caveat:", result["caveat"])
print("\nday | tan_ppm | no2_ppm | no3_ppm | risk_score")
for row in result["trajectory"][:10]:
    print(f"{row['days_from_now']:>3} | {row['tan_ppm']:.3f}   | {row['no2_ppm']:.3f}   | "
          f"{row['no3_ppm']:.2f}    | {row['risk_score']}")
print("...")
for row in result["trajectory"][-3:]:
    print(f"{row['days_from_now']:>3} | {row['tan_ppm']:.3f}   | {row['no2_ppm']:.3f}   | "
          f"{row['no3_ppm']:.2f}    | {row['risk_score']}")

# Sanity assertions: NO3 should be monotonically non-decreasing net of
# uptake being weaker than production at this feed rate/volume, and the
# trajectory length should match horizon_days.
assert len(result["trajectory"]) == 30
no3_series = [r["no3_ppm"] for r in result["trajectory"]]
assert no3_series[-1] >= no3_series[0], "NO3 should trend up over a month of constant feeding with no water change"
print("\nproject_forward: OK - basic invariants hold")

# ---- 4. Engine still mutates only local state (self untouched) ----
assert engine._tan_mg == 0.0 or engine._tan_mg > 0.0  # trivial - just confirming no exception
snapshot_before = engine.to_snapshot()
engine.project_forward(
    avg_daily_tan_mg=avg_tan,
    daily_temp_forecast_c=daily["temp_c"],
    daily_lux_forecast=[20000 * m for m in daily["lux_multiplier"]],
    fallback_temp_c=29.0,
    fallback_lux=20000,
    horizon_days=10,
)
snapshot_after = engine.to_snapshot()
assert snapshot_before == snapshot_after, "project_forward must not mutate engine state!"
print("no-mutation guarantee: OK - engine snapshot unchanged after project_forward()")

# ---- 5. Nitrite override triggers correctly regardless of low TAN/NO3 ----
config2 = PondConfig(volume_litres=1000, estimated_biomass_grams=2000)
engine2 = WaterChemistryEngine(config2)
engine2._no2_mg = 600.0  # 0.6ppm in a 1000L pond -> already above the 0.5ppm override
result2 = engine2.project_forward(
    avg_daily_tan_mg=0.0,  # no feeding at all - isolate the nitrite check
    fallback_temp_c=25.0,
    fallback_lux=10000,
    horizon_days=5,
)
print("\nnitrite-only scenario first_nitrite_days_from_now:", result2["first_nitrite_days_from_now"])
assert result2["first_nitrite_days_from_now"] == 0, "nitrite override should be flagged as 'already now' (day 0)"
assert result2["predicted_action_days_from_now"] == 0
print("nitrite override (already-in-breach-right-now case): OK")

# ---- 6. A pond that is fine now but crosses NO3 threshold later ----
config3 = PondConfig(volume_litres=500, estimated_biomass_grams=1500)
engine3 = WaterChemistryEngine(config3)
result3 = engine3.project_forward(
    avg_daily_tan_mg=3000.0,  # heavy feeding relative to a small 500L pond
    fallback_temp_c=27.0,
    fallback_lux=15000,
    horizon_days=30,
)
print("\nsmall-pond/heavy-feed scenario predicted_action_days_from_now:", result3["predicted_action_days_from_now"])
print("first_watch_days_from_now:", result3["first_watch_days_from_now"],
      "first_high_risk_days_from_now:", result3["first_high_risk_days_from_now"])
assert result3["predicted_action_days_from_now"] is not None and result3["predicted_action_days_from_now"] > 0, (
    "a small pond fed heavily should cross a threshold within the horizon, on a future day (not day 0)"
)
print("future-breach scenario: OK")

print("\nALL CHECKS PASSED")
