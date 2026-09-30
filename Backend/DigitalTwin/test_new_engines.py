"""Verification for evaporation_engine and algae_engine, run against the
REAL sample rows and the REAL NEA payload shape supplied by the user.
Run from Backend/: python -m pytest DigitalTwin/test_new_engines.py"""
import json
from datetime import datetime, timedelta, timezone

import algae_engine
import evaporation_engine as ev
import forecast_utils

PAYLOAD = json.loads("""
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

DAYS = forecast_utils.daily_environment_from_outlook(PAYLOAD["nea_forecasts"]["outlook_4day"])
CONFIG = ev.EvaporationConfig(volume_litres=5000, estimated_biomass_grams=8000)
ENV = [
    ev.DayEnvironment(
        air_temp_c=d["air_temp_c"],
        relative_humidity_pct=d["humidity_pct"],
        wind_speed_ms=d["wind_ms"],
        rain_category=d["rain_category"],
    )
    for d in DAYS
]
AENV = [
    algae_engine.AlgaeDayEnvironment(
        lux=20000 * d["lux_multiplier"],
        temp_c=d["air_temp_c"],
        no3_ppm=10.0,
    )
    for d in DAYS
]

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
# True doubling every ~3.5 days -> mu = ln(2)/3.5 = 0.198/day
TRUE_MU = 0.198
BLOOM = [
    {"created_at": (T0 + timedelta(days=i)).isoformat(),
     "green_ratio": round(0.02 * (2.718281828 ** (TRUE_MU * i)), 5),
     "current_state": ["base", round(0.02 * (2.718281828 ** (TRUE_MU * i)), 5)]}
    for i in range(10)
]


def evaporation_engine():
    engine = ev.EvaporationFeedEngine(CONFIG)
    # The water/air offset is now engine STATE, fitted from history by
    # refit_water_air_offset, rather than a per-call argument.
    engine._water_air_offset_c = -1.5
    return engine


def bloom_samples():
    return algae_engine.parse_image_rows(BLOOM)


def primed(samples, lux=18000.0, temp=29.0, no3=10.0):
    """Builds an algae engine in the state the poller would have left it:
    camera frames assimilated, growth rate refitted against the conditions
    that prevailed. Replaces the old stateless call signature where
    samples/recent_* were passed to project_forward directly."""
    e = algae_engine.AlgaeGrowthEngine()
    e.ingest_camera_samples(samples)
    e.refit_growth_rate(lux, temp, no3)
    return e


def test_penman_evaporation_physical_plausibility():
    # Typical Singapore: water 29C, air 30C, RH 75%, wind 3 m/s (at 2m)
    e = ev.evaporation_mm_per_day(29.0, 30.0, 75.0, 3.0, wind_measured_at_10m=False, shelter_factor=1.0)
    assert 3.0 <= e <= 6.0, f"tropical evaporation in accepted 3-6 mm/day band: {e:.2f}"

    # Saturated air must produce near-zero evaporation.
    e_sat = ev.evaporation_mm_per_day(29.0, 29.0, 100.0, 3.0, wind_measured_at_10m=False, shelter_factor=1.0)
    assert e_sat < 0.05, f"100% RH at equal temps -> ~0 mm/day: {e_sat:.4f}"

    # Higher wind must increase evaporation, monotonically.
    e_low = ev.evaporation_mm_per_day(29.0, 30.0, 75.0, 1.0, wind_measured_at_10m=False, shelter_factor=1.0)
    e_high = ev.evaporation_mm_per_day(29.0, 30.0, 75.0, 6.0, wind_measured_at_10m=False, shelter_factor=1.0)
    assert e_low < e < e_high, \
        f"evaporation increases with wind: {e_low:.2f} < {e:.2f} < {e_high:.2f}"

    # Never negative even when air is hotter and wetter than the water.
    e_neg = ev.evaporation_mm_per_day(20.0, 35.0, 100.0, 5.0, wind_measured_at_10m=False, shelter_factor=1.0)
    assert e_neg == 0.0, f"condensation case floored at 0: {e_neg}"


def test_nea_payload_environment_extraction():
    days = DAYS
    assert len(days) == 4, f"4 forecast days extracted: {len(days)}"
    assert days[0]["air_temp_c"] == 29.0, "Wednesday temp midpoint = 29.0"
    assert days[0]["humidity_pct"] == 77.5, "Wednesday humidity midpoint = 77.5"
    # 15 knots midpoint (10-20) = 15 * 0.514444 = 7.717 m/s
    assert abs(days[0]["wind_ms"] - 15 * 0.514444) < 1e-6, \
        f"wind converted knots->m/s: {days[0]['wind_ms']:.3f}"
    assert days[0]["rain_category"] == "heavy", "thundery showers -> heavy rain category"
    assert days[3]["rain_category"] == "unknown", "Fair (Day) -> no rain"

    now = forecast_utils.current_conditions(PAYLOAD)
    assert now["air_temp_c"] == 29.8, "current air temp read from telemetry"
    assert now["humidity_pct"] == 77.5, "current humidity from 24hr general"
    assert abs(now["wind_ms"] - 6.9 * 0.514444) < 1e-6, "current wind converted to m/s"
    assert now["water_temp_c"] == 27.65, "water temp read from raw_sensor"


def test_evaporation_projection_5000l_pond():
    assert 3.0 < CONFIG.surface_area_m2 < 5.0, \
        f"surface area sane for 5000L: {CONFIG.surface_area_m2:.2f}"

    res = evaporation_engine().project_forward(daily_environment=ENV, horizon_days=21)
    assert len(res["trajectory"]) == 21, "trajectory length matches horizon"
    assert res["predicted_topup_days_from_now"] is not None, "a top-up is predicted within 21 days"
    # The action threshold may legitimately not be reached inside the horizon
    # (a sheltered 5000L pond losing ~0.43%/day takes ~23 days to shed 10%),
    # so None here means "not within the window asked for", not a failure.
    assert (res["first_action_days_from_now"] is None
            or res["first_watch_days_from_now"] <= res["first_action_days_from_now"]), \
        f"watch fires no later than action: watch={res['first_watch_days_from_now']} " \
        f"action={res['first_action_days_from_now']}"
    assert all(res["trajectory"][i]["cumulative_loss_litres"]
               <= res["trajectory"][i + 1]["cumulative_loss_litres"] + 1e-9
               for i in range(len(res["trajectory"]) - 1)), \
        "cumulative loss is monotonic non-decreasing on dry days"

    # Day 1 is thundery (12mm credited) - net loss should be negative that day.
    assert res["trajectory"][0]["net_loss_litres"] < 0, \
        f"heavy rain day offsets evaporation: {res['trajectory'][0]['net_loss_litres']:.2f} L"


def test_feed_cap_table():
    assert ev.feed_cap_grams_per_day(8000, 8)[0] == 0.0, "no feeding below 10C"
    peak = ev.feed_cap_grams_per_day(8000, 28)[0]
    hot = ev.feed_cap_grams_per_day(8000, 32)[0]
    assert hot < peak, f"ration pulled back above 30C (oxygen limit): {hot} < {peak}"


def test_day0_already_past_threshold_guard():
    # 12% already lost -> action threshold (10%) must report day 0, not a future day.
    # Accrued loss is now real engine state built up by advance_state, so the
    # day-0 guard is exercised by setting that state rather than passing a
    # litres_already_lost argument.
    engine = evaporation_engine()
    engine._cumulative_loss_litres = 600.0
    res0 = engine.project_forward(daily_environment=ENV, horizon_days=10)
    assert res0["predicted_topup_days_from_now"] == 0, \
        f"already-overdue top-up reports day 0: {res0['predicted_topup_days_from_now']}"


def test_tds_grounding_cross_check():
    # 5000L pond losing 15 L/day at 220 ppm -> predicted slope = 220*15/5000 = 0.66 ppm/day
    cc = ev.cross_check_against_tds(
        predicted_daily_loss_litres=15.0, volume_litres=5000,
        observed_tds_slope_ppm_per_day=0.7, current_tds_ppm=220,
    )
    assert cc["verdict"] == "corroborated", "matching TDS slope -> corroborated"

    cc2 = ev.cross_check_against_tds(
        predicted_daily_loss_litres=15.0, volume_litres=5000,
        observed_tds_slope_ppm_per_day=9.0, current_tds_ppm=220,
    )
    assert cc2["verdict"] == "under_predicted", \
        f"TDS rising far faster -> under_predicted: {cc2['verdict']}"

    cc3 = ev.cross_check_against_tds(
        predicted_daily_loss_litres=15.0, volume_litres=5000,
        observed_tds_slope_ppm_per_day=-8.0, current_tds_ppm=220,
    )
    assert cc3["verdict"] == "over_predicted", f"TDS falling -> over_predicted: {cc3['verdict']}"

    cc4 = ev.cross_check_against_tds(
        predicted_daily_loss_litres=15.0, volume_litres=5000,
        observed_tds_slope_ppm_per_day=None, current_tds_ppm=220,
    )
    assert cc4["verdict"] == "insufficient_data", "missing slope -> insufficient_data"


def test_image_table_parsing_real_rows():
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
    assert len(samples) == 4, "all 4 real rows parsed"
    assert samples[0].state == "base" and samples[0].smoothed_green == 0.1235, \
        "current_state list decoded to (state, smoothed)"
    assert samples[0].time < samples[-1].time, "samples sorted oldest first"

    # JSON-string form of current_state must parse identically.
    str_rows = [dict(r, current_state=json.dumps(r["current_state"])) for r in real_rows]
    assert algae_engine.parse_image_rows(str_rows)[0].smoothed_green == 0.1235, \
        "current_state as JSON string also parses"

    # The real series spans only 12 minutes -> a daily rate is not fittable.
    mu_real = algae_engine.fit_specific_growth_rate(samples)
    assert mu_real is None, f"12-minute span refuses to yield a daily growth rate: {mu_real}"


def test_algae_growth_fit_on_synthetic_bloom():
    bsamples = bloom_samples()
    mu_fit = algae_engine.fit_specific_growth_rate(bsamples)
    assert abs(mu_fit - TRUE_MU) / TRUE_MU < 0.02, \
        f"growth rate recovered within 2%: {mu_fit:.4f} vs {TRUE_MU}"

    th = algae_engine.resolve_thresholds(bsamples)
    assert th["mode"] == "baseline_relative", \
        "thresholds fall back to baseline-relative for a low-reading pond"
    assert th["watch"] < th["action"], "watch threshold below action threshold"


def test_algae_projection_end_to_end():
    bsamples = bloom_samples()
    mu_fit = algae_engine.fit_specific_growth_rate(bsamples)
    ares = primed(bsamples).project_forward(daily_environment=AENV, horizon_days=21)

    assert ares["rate_source"] == "fitted_from_camera", "rate fitted from camera, not fallback"
    assert ares["intrinsic_rate_per_day"] > mu_fit, \
        f"intrinsic rate exceeds the fitted realised rate (favourability divided out): " \
        f"intrinsic={ares['intrinsic_rate_per_day']} fitted={mu_fit:.4f}"
    assert ares["predicted_scrub_days_from_now"] is not None, \
        "a scrub is predicted within the horizon"
    assert all(ares["trajectory"][i]["green_ratio"] <= ares["trajectory"][i + 1]["green_ratio"] + 1e-9
               for i in range(len(ares["trajectory"]) - 1)), \
        "green ratio monotonically increasing under growth"
    assert all(t["green_ratio"] <= ares["carrying_capacity"] + 1e-9 for t in ares["trajectory"]), \
        "green ratio never exceeds carrying capacity"


def test_nutrient_coupling_changes_the_answer():
    bsamples = bloom_samples()
    lean = [algae_engine.AlgaeDayEnvironment(lux=e_.lux, temp_c=e_.temp_c, no3_ppm=0.5) for e_ in AENV]
    rich = [algae_engine.AlgaeDayEnvironment(lux=e_.lux, temp_c=e_.temp_c, no3_ppm=60.0) for e_ in AENV]
    r_lean = primed(bsamples).project_forward(daily_environment=lean, horizon_days=21)
    r_rich = primed(bsamples).project_forward(daily_environment=rich, horizon_days=21)
    # Compare on the ACTION threshold, not predicted_scrub_days_from_now: this
    # synthetic pond already sits above the watch threshold at day 0, so the
    # headline number is 0 in both cases and would hide the difference.
    assert r_rich["first_action_days_from_now"] < r_lean["first_action_days_from_now"], \
        f"nitrate-rich pond hits the action threshold sooner: " \
        f"rich={r_rich['first_action_days_from_now']} lean={r_lean['first_action_days_from_now']}"
    assert r_rich["trajectory"][-1]["green_ratio"] > r_lean["trajectory"][-1]["green_ratio"], \
        "nitrate-rich pond is greener at the end of the horizon"


def test_obstructed_frames_excluded_from_the_fit():
    mu_fit = algae_engine.fit_specific_growth_rate(bloom_samples())
    obstructed = list(BLOOM) + [
        {"created_at": (T0 + timedelta(days=10)).isoformat(), "green_ratio": 0.95,
         "current_state": ["obstruction", 0.95]}
    ]
    osamples = algae_engine.parse_image_rows(obstructed)
    mu_obs = algae_engine.fit_specific_growth_rate(osamples)
    assert abs(mu_obs - mu_fit) < 1e-6, "obstructed frame does not corrupt the growth fit"

    ores = primed(osamples).project_forward(daily_environment=AENV, horizon_days=10)
    assert ores["current_green_ratio"] < 0.5, \
        f"obstructed frame not used as the current level: {ores['current_green_ratio']}"
    assert ores["obstructed_sample_count"] == 1, "obstructed frames counted and reported"


def test_scrub_benefit():
    bsamples = bloom_samples()
    ares = primed(bsamples).project_forward(daily_environment=AENV, horizon_days=21)
    sb = primed(bsamples).project_scrub_benefit(daily_environment=AENV, horizon_days=40)
    assert abs(sb["post_scrub_green_ratio"] - ares["current_green_ratio"] * 0.3) < 1e-4, \
        "post-scrub level is 30% of current"
    assert sb["days_bought"] > (ares["predicted_scrub_days_from_now"] or 0), \
        "scrubbing delays the next scrub"


def test_declining_pond_is_not_projected_to_vanish():
    decline = [
        {"created_at": (T0 + timedelta(days=i)).isoformat(),
         "green_ratio": round(0.30 * (0.85 ** i), 5),
         "current_state": ["base", round(0.30 * (0.85 ** i), 5)]}
        for i in range(8)
    ]
    dsamples = algae_engine.parse_image_rows(decline)
    dres = primed(dsamples).project_forward(daily_environment=AENV, horizon_days=14)
    assert dres["rate_source"] == "measured_declining", \
        "declining pond flagged as measured_declining"
    assert dres["trajectory"][-1]["green_ratio"] > 0.0, \
        "declining pond holds level rather than decaying to zero"
