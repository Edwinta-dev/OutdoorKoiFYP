"""Verification for evaporation_engine and algae_engine, run against the
REAL sample rows and the REAL NEA payload shape supplied by the user.
Run with `python3 test_new_engines.py`."""
import json
from datetime import datetime, timedelta, timezone

import algae_engine
import evaporation_engine as ev
import forecast_utils

FAIL = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


# =====================================================================
print("\n=== 1. Penman evaporation physical plausibility ===")
# Typical Singapore: water 29C, air 30C, RH 75%, wind 3 m/s (at 2m)
e = ev.evaporation_mm_per_day(29.0, 30.0, 75.0, 3.0, wind_measured_at_10m=False, shelter_factor=1.0)
print(f"  Singapore typical: {e:.2f} mm/day")
check("tropical evaporation in accepted 3-6 mm/day band", 3.0 <= e <= 6.0, f"{e:.2f}")

# Saturated air must produce near-zero evaporation.
e_sat = ev.evaporation_mm_per_day(29.0, 29.0, 100.0, 3.0, wind_measured_at_10m=False, shelter_factor=1.0)
check("100% RH at equal temps -> ~0 mm/day", e_sat < 0.05, f"{e_sat:.4f}")

# Higher wind must increase evaporation, monotonically.
e_low = ev.evaporation_mm_per_day(29.0, 30.0, 75.0, 1.0, wind_measured_at_10m=False, shelter_factor=1.0)
e_high = ev.evaporation_mm_per_day(29.0, 30.0, 75.0, 6.0, wind_measured_at_10m=False, shelter_factor=1.0)
check("evaporation increases with wind", e_low < e < e_high, f"{e_low:.2f} < {e:.2f} < {e_high:.2f}")

# Never negative even when air is hotter and wetter than the water.
e_neg = ev.evaporation_mm_per_day(20.0, 35.0, 100.0, 5.0, wind_measured_at_10m=False, shelter_factor=1.0)
check("condensation case floored at 0", e_neg == 0.0, f"{e_neg}")

# =====================================================================
print("\n=== 2. NEA payload -> environment extraction (real shape) ===")
payload = json.loads("""
{
  "nea_forecasts": {
    "uv_index": {"data": {"unit": "UV Index", "value": 0, "status": "Nighttime (Inactive)"}},
    "forecast_2hr": {"forecast": "Cloudy"},
    "outlook_4day": [
      {"data": {"day": "Wednesday",
                "wind": {"speed": {"low": 10, "high": 20}, "direction": "SSE"},
                "forecast": {"code": "TL", "text": "Thundery Showers"},
                "temperature": {"low": 25, "high": 33},
                "relativeHumidity": {"low": 60, "high": 95}}},
      {"data": {"day": "Thursday",
                "wind": {"speed": {"low": 15, "high": 25}, "direction": "SSE"},
                "forecast": {"code": "WD", "text": "Windy"},
                "temperature": {"low": 27, "high": 34},
                "relativeHumidity": {"low": 60, "high": 90}}},
      {"data": {"day": "Friday",
                "wind": {"speed": {"low": 15, "high": 25}, "direction": "SSE"},
                "forecast": {"code": "WD", "text": "Windy"},
                "temperature": {"low": 27, "high": 34},
                "relativeHumidity": {"low": 55, "high": 85}}},
      {"data": {"day": "Saturday",
                "wind": {"speed": {"low": 10, "high": 20}, "direction": "SSE"},
                "forecast": {"code": "FA", "text": "Fair (Day)"},
                "temperature": {"low": 26, "high": 34},
                "relativeHumidity": {"low": 55, "high": 85}}}
    ],
    "forecast_24hr": {
      "general": {"relativeHumidity": {"low": 60, "high": 95},
                  "temperature": {"low": 25, "high": 33}},
      "regional": {"code": "CL", "text": "Cloudy"}
    }
  },
  "nea_telemetry": {"air_temp": {"value": 29.8}, "rainfall": {"value": 0}, "wind_speed": {"value": 6.9}},
  "raw_sensor": {"pH": 7.64, "TDS": 220, "temp": 27.65, "LUX": 22755}
}
""")

days = forecast_utils.daily_environment_from_outlook(payload["nea_forecasts"]["outlook_4day"])
for d in days:
    print(f"  {d['day_label']:<10} air={d['air_temp_c']}C  RH={d['humidity_pct']}%  "
          f"wind={d['wind_ms']:.2f}m/s  rain={d['rain_category']:<8} luxmult={d['lux_multiplier']}")

check("4 forecast days extracted", len(days) == 4, str(len(days)))
check("Wednesday temp midpoint = 29.0", days[0]["air_temp_c"] == 29.0)
check("Wednesday humidity midpoint = 77.5", days[0]["humidity_pct"] == 77.5)
# 15 knots midpoint (10-20) = 15 * 0.514444 = 7.717 m/s
check("wind converted knots->m/s", abs(days[0]["wind_ms"] - 15 * 0.514444) < 1e-6,
      f"{days[0]['wind_ms']:.3f}")
check("thundery showers -> heavy rain category", days[0]["rain_category"] == "heavy")
check("Fair (Day) -> no rain", days[3]["rain_category"] == "unknown")

now = forecast_utils.current_conditions(payload)
print(f"  current: {now}")
check("current air temp read from telemetry", now["air_temp_c"] == 29.8)
check("current humidity from 24hr general", now["humidity_pct"] == 77.5)
check("current wind converted to m/s", abs(now["wind_ms"] - 6.9 * 0.514444) < 1e-6)
check("water temp read from raw_sensor", now["water_temp_c"] == 27.65)

# =====================================================================
print("\n=== 3. Evaporation projection (5000L pond) ===")
config = ev.EvaporationConfig(volume_litres=5000, estimated_biomass_grams=8000)
print(f"  surface area @1.2m depth: {config.surface_area_m2:.2f} m2")
check("surface area sane for 5000L", 3.0 < config.surface_area_m2 < 5.0,
      f"{config.surface_area_m2:.2f}")

engine = ev.EvaporationFeedEngine(config)
env = [
    ev.DayEnvironment(
        air_temp_c=d["air_temp_c"],
        relative_humidity_pct=d["humidity_pct"],
        wind_speed_ms=d["wind_ms"],
        rain_category=d["rain_category"],
    )
    for d in days
]
# The water/air offset is now engine STATE, fitted from history by
# refit_water_air_offset, rather than a per-call argument.
engine._water_air_offset_c = -1.5
res = engine.project_forward(daily_environment=env, horizon_days=21)
print(f"  avg evaporation: {res['avg_evaporation_mm_per_day']} mm/day "
      f"({res['avg_loss_litres_per_day']} L/day)")
print(f"  predicted top-up in: {res['predicted_topup_days_from_now']} days "
      f"(watch {res['first_watch_days_from_now']}, action {res['first_action_days_from_now']})")
print("  day | evap_mm | rain_mm | cum_loss_L | cum_% | feed_cap_g")
for t in res["trajectory"][:8]:
    print(f"  {t['days_from_now']:>3} | {t['evaporation_mm']:>7.2f} | {t['rain_offset_mm']:>7.1f} | "
          f"{t['cumulative_loss_litres']:>10.1f} | {t['cumulative_loss_pct']:>5.2f} | {t['feed_cap_grams']:>6.1f}")

check("trajectory length matches horizon", len(res["trajectory"]) == 21)
check("a top-up is predicted within 21 days", res["predicted_topup_days_from_now"] is not None)
# The action threshold may legitimately not be reached inside the horizon
# (a sheltered 5000L pond losing ~0.43%/day takes ~23 days to shed 10%),
# so None here means "not within the window asked for", not a failure.
check("watch fires no later than action",
      res["first_action_days_from_now"] is None
      or res["first_watch_days_from_now"] <= res["first_action_days_from_now"],
      f"watch={res['first_watch_days_from_now']} action={res['first_action_days_from_now']}")
check("cumulative loss is monotonic non-decreasing on dry days",
      all(res["trajectory"][i]["cumulative_loss_litres"] <= res["trajectory"][i + 1]["cumulative_loss_litres"] + 1e-9
          for i in range(len(res["trajectory"]) - 1)))

# Day 1 is thundery (12mm credited) - net loss should be negative that day.
check("heavy rain day offsets evaporation", res["trajectory"][0]["net_loss_litres"] < 0,
      f"{res['trajectory'][0]['net_loss_litres']:.2f} L")

# =====================================================================
print("\n=== 4. Feed cap table ===")
for t in [8, 13, 18, 24, 28, 31, 35]:
    g, note = ev.feed_cap_grams_per_day(8000, t)
    print(f"  {t}C -> {g:6.1f} g/day  ({note[:50]})")
check("no feeding below 10C", ev.feed_cap_grams_per_day(8000, 8)[0] == 0.0)
peak = ev.feed_cap_grams_per_day(8000, 28)[0]
hot = ev.feed_cap_grams_per_day(8000, 32)[0]
check("ration pulled back above 30C (oxygen limit)", hot < peak, f"{hot} < {peak}")

# =====================================================================
print("\n=== 5. Day-0 already-past-threshold guard ===")
# 12% already lost -> action threshold (10%) must report day 0, not a future day.
# Accrued loss is now real engine state built up by advance_state, so the
# day-0 guard is exercised by setting that state rather than passing a
# litres_already_lost argument.
engine._cumulative_loss_litres = 600.0
res0 = engine.project_forward(daily_environment=env, horizon_days=10)
engine._cumulative_loss_litres = 0.0
check("already-overdue top-up reports day 0", res0["predicted_topup_days_from_now"] == 0,
      str(res0["predicted_topup_days_from_now"]))

# =====================================================================
print("\n=== 6. TDS grounding cross-check ===")
# 5000L pond losing 15 L/day at 220 ppm -> predicted slope = 220*15/5000 = 0.66 ppm/day
cc = ev.cross_check_against_tds(
    predicted_daily_loss_litres=15.0, volume_litres=5000,
    observed_tds_slope_ppm_per_day=0.7, current_tds_ppm=220,
)
print(f"  {cc}")
check("matching TDS slope -> corroborated", cc["verdict"] == "corroborated")

cc2 = ev.cross_check_against_tds(
    predicted_daily_loss_litres=15.0, volume_litres=5000,
    observed_tds_slope_ppm_per_day=9.0, current_tds_ppm=220,
)
check("TDS rising far faster -> under_predicted", cc2["verdict"] == "under_predicted", cc2["verdict"])

cc3 = ev.cross_check_against_tds(
    predicted_daily_loss_litres=15.0, volume_litres=5000,
    observed_tds_slope_ppm_per_day=-8.0, current_tds_ppm=220,
)
check("TDS falling -> over_predicted", cc3["verdict"] == "over_predicted", cc3["verdict"])

cc4 = ev.cross_check_against_tds(
    predicted_daily_loss_litres=15.0, volume_litres=5000,
    observed_tds_slope_ppm_per_day=None, current_tds_ppm=220,
)
check("missing slope -> insufficient_data", cc4["verdict"] == "insufficient_data")

# =====================================================================
print("\n=== 7. imageTable parsing (REAL rows from user 15) ===")
real_rows = [
    {"id": 1, "created_at": "2026-07-27 08:43:45.976023+00", "green_ratio": 0.0174,
     "current_state": ["base", 0.1235], "user_ID": 15},
    {"id": 2, "created_at": "2026-07-27 08:49:46.029308+00", "green_ratio": 0.011,
     "current_state": ["base", 0.0161], "user_ID": 15},
    {"id": 3, "created_at": "2026-07-27 08:53:21.032296+00", "green_ratio": 0.0002,
     "current_state": ["base", 0.0088], "user_ID": 15},
    {"id": 4, "created_at": "2026-07-27 08:55:04.664514+00", "green_ratio": 0,
     "current_state": ["base", 0.0002], "user_ID": 15},
]
samples = algae_engine.parse_image_rows(real_rows)
for s in samples:
    print(f"  {s.time}  raw={s.green_ratio}  smoothed={s.smoothed_green}  state={s.state}")
check("all 4 real rows parsed", len(samples) == 4)
check("current_state list decoded to (state, smoothed)",
      samples[0].state == "base" and samples[0].smoothed_green == 0.1235)
check("samples sorted oldest first", samples[0].time < samples[-1].time)

# JSON-string form of current_state must parse identically.
str_rows = [dict(r, current_state=json.dumps(r["current_state"])) for r in real_rows]
check("current_state as JSON string also parses",
      algae_engine.parse_image_rows(str_rows)[0].smoothed_green == 0.1235)

# The real series spans only 12 minutes -> a daily rate is not fittable.
mu_real = algae_engine.fit_specific_growth_rate(samples)
check("12-minute span refuses to yield a daily growth rate", mu_real is None, str(mu_real))

# =====================================================================
print("\n=== 8. Algae growth fit on a synthetic multi-day bloom ===")
t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
# True doubling every ~3.5 days -> mu = ln(2)/3.5 = 0.198/day
true_mu = 0.198
bloom = [
    {"created_at": (t0 + timedelta(days=i)).isoformat(),
     "green_ratio": round(0.02 * (2.718281828 ** (true_mu * i)), 5),
     "current_state": ["base", round(0.02 * (2.718281828 ** (true_mu * i)), 5)]}
    for i in range(10)
]
bsamples = algae_engine.parse_image_rows(bloom)
mu_fit = algae_engine.fit_specific_growth_rate(bsamples)
print(f"  true mu={true_mu}/day   fitted mu={mu_fit:.4f}/day")
check("growth rate recovered within 2%", abs(mu_fit - true_mu) / true_mu < 0.02,
      f"{mu_fit:.4f} vs {true_mu}")

th = algae_engine.resolve_thresholds(bsamples)
print(f"  thresholds: {th}")
check("thresholds fall back to baseline-relative for a low-reading pond",
      th["mode"] == "baseline_relative")
check("watch threshold below action threshold", th["watch"] < th["action"])

# =====================================================================
print("\n=== 9. Algae projection end-to-end ===")
def primed(samples, lux=18000.0, temp=29.0, no3=10.0):
    """Builds an algae engine in the state the poller would have left it:
    camera frames assimilated, growth rate refitted against the conditions
    that prevailed. Replaces the old stateless call signature where
    samples/recent_* were passed to project_forward directly."""
    e = algae_engine.AlgaeGrowthEngine()
    e.ingest_camera_samples(samples)
    e.refit_growth_rate(lux, temp, no3)
    return e

aengine = primed(bsamples)
aenv = [
    algae_engine.AlgaeDayEnvironment(
        lux=20000 * d["lux_multiplier"],
        temp_c=d["air_temp_c"],
        no3_ppm=10.0,
    )
    for d in days
]
ares = aengine.project_forward(daily_environment=aenv, horizon_days=21)
print(f"  current green: {ares['current_green_ratio']}  rate_source={ares['rate_source']} "
      f"confidence={ares['confidence']}")
print(f"  fitted mu={mu_fit:.4f}  intrinsic={ares['intrinsic_rate_per_day']}")
print(f"  predicted scrub in {ares['predicted_scrub_days_from_now']} days "
      f"(watch {ares['first_watch_days_from_now']}, action {ares['first_action_days_from_now']})")
print("  day | green | mu | fav")
for t in ares["trajectory"][:8]:
    print(f"  {t['days_from_now']:>3} | {t['green_ratio']:.5f} | {t['growth_rate_per_day']:.4f} | {t['favourability']:.3f}")

check("rate fitted from camera, not fallback", ares["rate_source"] == "fitted_from_camera")
check("intrinsic rate exceeds the fitted realised rate (favourability divided out)",
      ares["intrinsic_rate_per_day"] > mu_fit,
      f"intrinsic={ares['intrinsic_rate_per_day']} fitted={mu_fit:.4f}")
check("a scrub is predicted within the horizon", ares["predicted_scrub_days_from_now"] is not None)
check("green ratio monotonically increasing under growth",
      all(ares["trajectory"][i]["green_ratio"] <= ares["trajectory"][i + 1]["green_ratio"] + 1e-9
          for i in range(len(ares["trajectory"]) - 1)))
check("green ratio never exceeds carrying capacity",
      all(t["green_ratio"] <= ares["carrying_capacity"] + 1e-9 for t in ares["trajectory"]))

# =====================================================================
print("\n=== 10. Nutrient coupling actually changes the answer ===")
lean = [algae_engine.AlgaeDayEnvironment(lux=e_.lux, temp_c=e_.temp_c, no3_ppm=0.5) for e_ in aenv]
rich = [algae_engine.AlgaeDayEnvironment(lux=e_.lux, temp_c=e_.temp_c, no3_ppm=60.0) for e_ in aenv]
r_lean = primed(bsamples).project_forward(daily_environment=lean, horizon_days=21)
r_rich = primed(bsamples).project_forward(daily_environment=rich, horizon_days=21)
# Compare on the ACTION threshold, not predicted_scrub_days_from_now: this
# synthetic pond already sits above the watch threshold at day 0, so the
# headline number is 0 in both cases and would hide the difference.
print(f"  NO3=0.5ppm  -> action in {r_lean['first_action_days_from_now']} days, "
      f"day-21 green {r_lean['trajectory'][-1]['green_ratio']}")
print(f"  NO3=60ppm   -> action in {r_rich['first_action_days_from_now']} days, "
      f"day-21 green {r_rich['trajectory'][-1]['green_ratio']}")
check("nitrate-rich pond hits the action threshold sooner",
      r_rich["first_action_days_from_now"] < r_lean["first_action_days_from_now"],
      f"rich={r_rich['first_action_days_from_now']} lean={r_lean['first_action_days_from_now']}")
check("nitrate-rich pond is greener at the end of the horizon",
      r_rich["trajectory"][-1]["green_ratio"] > r_lean["trajectory"][-1]["green_ratio"])

# =====================================================================
print("\n=== 11. Obstructed frames excluded from the fit ===")
obstructed = list(bloom) + [
    {"created_at": (t0 + timedelta(days=10)).isoformat(), "green_ratio": 0.95,
     "current_state": ["obstruction", 0.95]}
]
osamples = algae_engine.parse_image_rows(obstructed)
mu_obs = algae_engine.fit_specific_growth_rate(osamples)
print(f"  fitted mu with a 0.95 obstructed frame appended: {mu_obs:.4f} (clean fit was {mu_fit:.4f})")
check("obstructed frame does not corrupt the growth fit", abs(mu_obs - mu_fit) < 1e-6)

ores = primed(osamples).project_forward(daily_environment=aenv, horizon_days=10)
check("obstructed frame not used as the current level",
      ores["current_green_ratio"] < 0.5, str(ores["current_green_ratio"]))
check("obstructed frames counted and reported", ores["obstructed_sample_count"] == 1)

# =====================================================================
print("\n=== 12. Scrub benefit ===")
sb = primed(bsamples).project_scrub_benefit(daily_environment=aenv, horizon_days=40)
print(f"  current {ares['current_green_ratio']} -> post-scrub {sb['post_scrub_green_ratio']}")
print(f"  scrubbing today buys {sb['days_bought']} days (vs {ares['predicted_scrub_days_from_now']} now)")
check("post-scrub level is 30% of current",
      abs(sb["post_scrub_green_ratio"] - ares["current_green_ratio"] * 0.3) < 1e-4)
check("scrubbing delays the next scrub",
      sb["days_bought"] > (ares["predicted_scrub_days_from_now"] or 0))

# =====================================================================
print("\n=== 13. Declining pond is not projected to vanish ===")
decline = [
    {"created_at": (t0 + timedelta(days=i)).isoformat(),
     "green_ratio": round(0.30 * (0.85 ** i), 5),
     "current_state": ["base", round(0.30 * (0.85 ** i), 5)]}
    for i in range(8)
]
dsamples = algae_engine.parse_image_rows(decline)
dres = primed(dsamples).project_forward(daily_environment=aenv, horizon_days=14)
print(f"  rate_source={dres['rate_source']}  final green={dres['trajectory'][-1]['green_ratio']}")
check("declining pond flagged as measured_declining", dres["rate_source"] == "measured_declining")
check("declining pond holds level rather than decaying to zero",
      dres["trajectory"][-1]["green_ratio"] > 0.0)

# =====================================================================
print("\n" + "=" * 60)
if FAIL:
    print(f"{len(FAIL)} CHECK(S) FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
