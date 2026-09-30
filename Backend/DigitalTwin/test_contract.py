"""Contract test: every JSON key the Dart client reads must actually be
emitted by the Python engines.

Dart cannot be compiled in this environment, so this closes the highest-
risk gap that a compile WOULDN'T catch anyway: json['typo_key'] is
perfectly valid Dart that silently yields null at runtime. This extracts
every j['...'] / json['...'] literal from the Dart models and checks it
against the real engine output.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

import algae_engine
import evaporation_engine as ev
import forecast_utils

# Resolved relative to this file so the test works from any working
# directory.
API = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "MobileUI", "mobile_app", "lib", "utils", "digital_twin_api.dart",
)

OUTLOOK = [
    {"data": {"day": "Wednesday", "wind": {"speed": {"low": 10, "high": 20}},
              "forecast": {"code": "TL", "text": "Thundery Showers"},
              "temperature": {"low": 25, "high": 33},
              "relativeHumidity": {"low": 60, "high": 95}}},
    {"data": {"day": "Thursday", "wind": {"speed": {"low": 15, "high": 25}},
              "forecast": {"code": "WD", "text": "Windy"},
              "temperature": {"low": 27, "high": 34},
              "relativeHumidity": {"low": 60, "high": 90}}},
]
T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def dart_keys(path: str, class_name: str) -> set:
    """Pulls j['key'] / json['key'] literals out of one Dart class body."""
    with open(path) as f:
        src = f.read()
    start = src.find(f"class {class_name}")
    if start == -1:
        return set()
    # Walk braces to find the class body end.
    depth, i = 0, src.find("{", start)
    body_start = i
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = src[body_start:i]
    return set(re.findall(r"(?:j|json)\['([^']+)'\]", body))


def bloom_samples():
    rows = [{"created_at": (T0 + timedelta(days=i)).isoformat(),
             "green_ratio": round(0.02 * (2.718281828 ** (0.198 * i)), 5),
             "current_state": ["base", round(0.02 * (2.718281828 ** (0.198 * i)), 5)]}
            for i in range(10)]
    return algae_engine.parse_image_rows(rows)


def assert_json_serialisable(name, payload):
    error = None
    try:
        json.dumps(payload)
    except TypeError as exc:
        error = exc
    assert error is None, f"{name} payload is JSON-serialisable: {error}"


def assert_dart_keys_present(class_name, payload, optional=frozenset()):
    keys = dart_keys(API, class_name)
    missing = sorted(k for k in keys if k not in payload and k not in optional)
    detail = "class not parsed" if not keys else f"missing: {missing}"
    assert keys and not missing, \
        f"{class_name}: all {len(keys)} Dart keys exist in engine output: {detail}"


# ---------------------------------------------------------------------
# The REAL payloads the endpoints would return.
# ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def evap_payload():
    fdays = forecast_utils.daily_environment_from_outlook(OUTLOOK)
    config = ev.EvaporationConfig(volume_litres=5000, estimated_biomass_grams=8000)
    engine = ev.EvaporationFeedEngine(config)
    env = [ev.DayEnvironment(d["air_temp_c"], d["humidity_pct"], d["wind_ms"],
                             d["rain_category"]) for d in fdays]
    # Give the engine some accumulated state, as a real poll cycle would, so
    # the payload exercises the same fields the endpoint actually returns.
    engine._cumulative_loss_litres = 120.0
    engine._last_topup_time = datetime.now(timezone.utc) - timedelta(days=6)
    payload = engine.project_forward(daily_environment=env, horizon_days=14)
    payload["tds_cross_check"] = ev.cross_check_against_tds(
        predicted_daily_loss_litres=payload["avg_loss_litres_per_day"],
        volume_litres=5000, observed_tds_slope_ppm_per_day=0.7, current_tds_ppm=220,
    )
    return payload


@pytest.fixture(scope="module")
def algae_payload():
    fdays = forecast_utils.daily_environment_from_outlook(OUTLOOK)
    # Prime the engine the way a poll cycle would: assimilate camera frames,
    # then refit the growth rate against prevailing conditions.
    aengine = algae_engine.AlgaeGrowthEngine()
    aengine.ingest_camera_samples(bloom_samples())
    aengine.refit_growth_rate(18000.0, 29.0, 10.0)
    aenv = [algae_engine.AlgaeDayEnvironment(lux=20000 * d["lux_multiplier"],
                                             temp_c=d["air_temp_c"], no3_ppm=10.0)
            for d in fdays]
    payload = aengine.project_forward(daily_environment=aenv, horizon_days=21)
    payload["scrub_benefit"] = aengine.project_scrub_benefit(
        daily_environment=aenv, horizon_days=21)
    payload["latest_image_url"] = "https://example/photo.jpg"
    return payload


@pytest.fixture(scope="module")
def rating_payloads():
    """Severity rating payloads. The endpoints assemble these in app.py
    rather than in an engine method, so they are built the same way here."""
    rating_engine = algae_engine.AlgaeGrowthEngine()
    rating_engine.ingest_camera_samples(bloom_samples())
    rating_engine.refit_growth_rate(18000.0, 29.0, 10.0)
    for i, (sev, g) in enumerate([("none", 0.01), ("none", 0.011),
                                  ("minor", 0.04), ("minor", 0.043),
                                  ("moderate", 0.10), ("moderate", 0.105),
                                  ("severe", 0.26), ("severe", 0.28)]):
        rating_engine.apply_severity_rating(
            time=T0 + timedelta(hours=i), severity=sev, green_ratio_at_rating=g,
            rating_id=i + 1)

    rating_summary = rating_engine.apply_severity_rating(
        time=T0 + timedelta(hours=20), severity="minor",
        green_ratio_at_rating=0.041, rating_id=99)
    rating_summary["rating_id"] = 99
    rating_summary["rated_image_id"] = 7
    rating_summary["image_inferred"] = False

    calibration = {
        "label_counts": rating_engine.label_counts(),
        "labels_needed": rating_engine.labels_needed(),
        "thresholds": rating_engine.thresholds,
        "class_targets": {k: round(v, 5) for k, v in rating_engine._class_targets().items()},
        "camera_drift": rating_engine.detect_camera_drift(),
        "green_ratio": rating_engine.green_ratio,
    }
    rating_row = {
        "id": 1, "userid": 455, "image_id": 7, "image_url": "https://example/1.jpg",
        "severity": "minor", "is_obstructed": False, "green_ratio_at_rating": 0.04,
        "image_captured_at": T0.isoformat(), "rated_at": T0.isoformat(), "notes": None,
    }
    context = {
        "ratings": [rating_row],
        "latest_rating": rating_row,
        "calibration": calibration,
        "latest_image": {"id": 7, "created_at": T0.isoformat(),
                         "green_ratio": 0.04, "imageURL": "https://example/1.jpg"},
        "severity_levels": algae_engine.SEVERITY_LEVELS + ["obstruction"],
    }
    return {"summary": rating_summary, "calibration": calibration,
            "row": rating_row, "context": context}


def test_engine_payloads_are_json_serialisable(evap_payload, algae_payload):
    # Everything must survive a JSON round-trip (no stray datetimes/tuples).
    for name, p in [("evaporation", evap_payload), ("algae", algae_payload)]:
        assert_json_serialisable(name, p)


def test_forecast_models_read_only_emitted_keys(evap_payload, algae_payload):
    cases = [
        ("EvaporationDay", evap_payload["trajectory"][0]),
        ("TdsCrossCheck", evap_payload["tds_cross_check"]),
        ("EvaporationForecast", evap_payload),
        ("AlgaeDay", algae_payload["trajectory"][0]),
        ("AlgaeThresholds", algae_payload["thresholds"]),
        ("AlgaeForecast", algae_payload),
    ]
    for class_name, payload in cases:
        assert_dart_keys_present(class_name, payload)

    # AlgaeForecast reads scrub_benefit's inner keys via a separate map.
    scrub_keys = {"days_bought", "post_scrub_green_ratio"}
    missing_scrub = sorted(k for k in scrub_keys if k not in algae_payload["scrub_benefit"])
    assert not missing_scrub, \
        f"AlgaeForecast: scrub_benefit inner keys exist: missing: {missing_scrub}"


def test_field_types_match_dart_casts(evap_payload, algae_payload):
    # Dart casts these with `as num`.
    int_fields_evap = ["predicted_topup_days_from_now", "first_watch_days_from_now",
                       "first_action_days_from_now", "days_using_real_forecast"]
    bad = [f for f in int_fields_evap
           if evap_payload.get(f) is not None and not isinstance(evap_payload[f], int)]
    assert not bad, f"evaporation int fields are int or null: {bad}"

    int_fields_algae = ["predicted_scrub_days_from_now", "first_watch_days_from_now",
                        "first_action_days_from_now", "sample_count",
                        "obstructed_sample_count", "days_using_real_forecast"]
    bad = [f for f in int_fields_algae
           if algae_payload.get(f) is not None and not isinstance(algae_payload[f], int)]
    assert not bad, f"algae int fields are int or null: {bad}"

    assert isinstance(algae_payload["thresholds"]["mode"], str), "thresholds.mode is a string"
    assert algae_payload["rate_source"] in \
        {"fitted_from_camera", "measured_declining", "literature_fallback"}, \
        f"rate_source is one of the three Dart branches: {algae_payload['rate_source']}"
    assert evap_payload["tds_cross_check"]["verdict"] in \
        {"corroborated", "under_predicted", "over_predicted", "insufficient_data"}, \
        "tds verdict is one of the four Dart branches"

    # Dart divides by thresholds.action for the progress meter.
    assert algae_payload["thresholds"]["action"] > 0, \
        "thresholds.action is non-zero (Dart divides by it)"


def test_rating_models_read_only_emitted_keys(rating_payloads):
    summary = rating_payloads["summary"]
    calibration = rating_payloads["calibration"]
    context = rating_payloads["context"]

    for name, payload in [("rating summary", summary), ("rating context", context)]:
        assert_json_serialisable(name, payload)

    # "warning" is only emitted when the ratings table is missing, so it is
    # legitimately absent from a healthy payload. The Dart reads it as
    # nullable and the UI hides the banner when null.
    optional = {"warning"}
    for class_name, payload in [
        ("AlgaeRating", rating_payloads["row"]),
        ("CameraDriftVerdict", calibration["camera_drift"]),
        ("AlgaeCalibration", calibration),
        ("AlgaeRatingContext", context),
        ("AlgaeRatingResult", summary),
    ]:
        assert_dart_keys_present(class_name, payload, optional)

    # The Dart enum must cover exactly the wire values the server accepts.
    with open(API) as f:
        dart_src = f.read()
    wire_values = set(re.findall(r"AlgaeSeverity\.\w+ => '(\w+)'", dart_src))
    server_values = set(algae_engine.SEVERITY_LEVELS + ["obstruction"])
    assert wire_values == server_values, \
        f"Dart enum wire values match the server's severity list: " \
        f"dart={sorted(wire_values)} server={sorted(server_values)}"

    # thresholds.mode strings the Dart branches on.
    assert calibration["thresholds"]["mode"] in \
        {"absolute", "baseline_relative", "label_calibrated", "partially_calibrated"}, \
        f"threshold mode is one of the Dart branches: {calibration['thresholds']['mode']}"
    assert calibration["camera_drift"]["verdict"] in \
        {"stable", "drift_possible", "drift_suspected", "insufficient_data"}, \
        "drift verdict is one of the Dart branches"
