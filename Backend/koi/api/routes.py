"""
koi.api.routes

HTTP surface of the digital twin service (formerly DigitalTwin/app.py;
the Flask app itself is built by koi.api.create_app). This process now models ALL
THREE pond outcome domains, not just water chemistry:

    chemistry   -> TAN/NO2/NO3 nitrogen cycle + buffering trend
    evaporation -> water loss, top-up timing, temperature-driven feed cap
    algae       -> green coverage, grounded on the ESP32-CAM HSV series

--------------------------------------------------------------------
STATE UPDATE TRIGGERS
--------------------------------------------------------------------
Exactly two things move engine state, and both go through the same
locked, persisted path (registry.with_twin -> PondTwin):

  1. ENVIRONMENTAL POLL (koi.worker.poller, every poll_interval_minutes)
     Pulls sensors + NEA telemetry/forecast + any new camera frames,
     advances all three engines to now, and pushes one evaluation row per
     domain so the UI reads cached status instead of recomputing.

  2. LOGGED INTERVENTION (the /events/* endpoints below)
     Fans the event out to every affected engine - a WATER_TOPUP clears
     the evaporation engine's accrued loss, an ALGAE_SCRUB knocks the
     algae engine's level down, a WATER_CHANGE does both plus chemistry
     dilution - then immediately re-assesses and re-projects, so the app
     reflects the action without waiting for the next poll tick.

--------------------------------------------------------------------
ENDPOINT SHAPES
--------------------------------------------------------------------
  /assessment/*  cheap cached CURRENT status, read straight from the
                 evaluation tables. What the dashboard cards and their
                 alert badges want.
  /forecast/*    full day-by-day trajectory computed live from engine
                 state. What the detail graph screens want. Read-only:
                 projections run on local copies and never mutate state.

Request bodies and query strings are validated by the models in
koi.api.schemas. Every non-2xx response is the koi.errors envelope,
{"error": {"code", "message", "details"}}; routes raise ApiError or
PondNotConfigured rather than building error responses themselves.

Auth: every route except /health and /ready verifies the caller's
Supabase access token, and the user_id in the path or body must be the
pond linked to that account (koi/api/auth.py): 401 without a valid token,
403 for any other pond. Path user_ids are checked before the view runs;
_parse_body checks the body's user_id.
"""
import dataclasses
import logging
from typing import Optional, TypeVar

# Flask's Blueprint, under a name that the no-print check (grep for a
# print call in koi/, issue #10) does not mistake for one.
from flask import Blueprint as RouteGroup
from flask import current_app, jsonify, request
from pydantic import BaseModel

from koi.api import schemas
from koi.api.auth import authenticate, require_pond
from koi.api.health import readiness
from koi.errors import ApiError, PondNotConfigured, error_response
from koi.logs import log_event
from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models import forecast_utils
from koi.models.engine import EventKind, PondEvent, WaterChemistryEngine
from koi.models.profile import PROFILE_FIELDS, ProfileHistory
from koi.registry import EngineRegistry
from koi.storage import DuplicateProfileError, Storage, fail_soft

bp = RouteGroup("twin", __name__)
bp.before_request(authenticate)

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


def _storage() -> Storage:
    return current_app.extensions["koi_storage"]


def _registry() -> EngineRegistry:
    return current_app.extensions["koi_registry"]


# ===================================================================
# Helpers
# ===================================================================

def _parse_body(model: type[M]) -> M:
    """The JSON body validated against model; a ValidationError becomes
    a 400 with field-level detail (koi.errors). Every body names a pond,
    which must be the caller's (403 otherwise)."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise ApiError("The request body must be a JSON object.", code="invalid_body")
    parsed = model.model_validate(body)
    require_pond(getattr(parsed, "user_id"))  # noqa: B009 - every body model has user_id
    return parsed


def _parse_query(model: type[M]) -> M:
    return model.model_validate(request.args.to_dict())


def _require_pond(user_id: int) -> None:
    """Raises PondNotConfigured unless the pond has a stored snapshot or
    a profile (pond_profile rows or a UserData config) to start one from."""
    if _storage().fetch_snapshot_version(user_id) == 0 and _registry().profile_history(user_id) is None:
        raise PondNotConfigured(user_id)


def _profiles_for(user_id: int) -> Optional[ProfileHistory]:
    """The pond's profile history (EngineRegistry.profile_history). Every
    engine call gets it, so volume, biomass, depth, fish and tap water
    always come from the pond's stored profile, never from a request."""
    return _registry().profile_history(user_id)


def _environment_for(user_id: int):
    """Fetches the bundled payload once and derives everything the
    engines need from it. Returns (payload, context) where context holds
    the live conditions, forecast days and rain flags."""
    payload = _storage().fetch_dashboard_payload(user_id) or {}
    now_conditions = forecast_utils.current_conditions(payload)
    rain_incoming, rain_intensity = forecast_utils.today_rain_context(payload)
    outlook = (payload.get("nea_forecasts") or {}).get("outlook_4day", [])
    forecast_days = forecast_utils.daily_environment_from_outlook(outlook)
    return payload, {
        "now": now_conditions,
        "rain_incoming": rain_incoming,
        "rain_intensity": rain_intensity,
        "forecast_days": forecast_days,
    }


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _evaporation_env(ctx) -> list:
    now = ctx["now"]
    days = ctx["forecast_days"]
    air = _first(now.get("air_temp_c"), 30.0)
    hum = _first(now.get("humidity_pct"), 80.0)
    wind = _first(now.get("wind_ms"), 2.0)
    env = [
        ev.DayEnvironment(
            air_temp_c=float(_first(d.get("air_temp_c"), air)),
            relative_humidity_pct=float(_first(d.get("humidity_pct"), hum)),
            wind_speed_ms=float(_first(d.get("wind_ms"), wind)),
            rain_category=d.get("rain_category", "unknown"),
        )
        for d in days
    ]
    if not env:
        env = [
            ev.DayEnvironment(
                air_temp_c=float(air),
                relative_humidity_pct=float(hum),
                wind_speed_ms=float(wind),
            )
        ]
    return env


def _algae_env(ctx, baseline_lux, baseline_temp, no3_series, no3_now) -> list:
    days = ctx["forecast_days"]
    env = [
        ae.AlgaeDayEnvironment(
            lux=baseline_lux * d.get("lux_multiplier", 0.85),
            temp_c=float(_first(d.get("air_temp_c"), baseline_temp)),
            no3_ppm=no3_series[i] if i < len(no3_series) else no3_now,
        )
        for i, d in enumerate(days)
    ]
    if not env:
        env = [
            ae.AlgaeDayEnvironment(
                lux=baseline_lux, temp_c=baseline_temp, no3_ppm=no3_now
            )
        ]
    return env


def _recompute_and_push(user_id: int, twin, ctx) -> dict:
    """Re-assesses all three domains after a mutation and pushes a fresh
    evaluation row for each, so the UI reflects a just-logged event
    without waiting for the next poll cycle.

    Called INSIDE the twin lock. The storage writes happen here rather
    than after the lock releases because the caller needs the resulting
    payload as its HTTP response - the alternative would be threading
    three assessment objects back out just to write them a microsecond
    later. Event endpoints are low-frequency (a handful of user taps a
    day) so holding the lock across those writes costs nothing that
    matters; the poller, which runs far more often, does push its rows
    outside the lock.
    """
    now = ctx["now"]
    water_temp = _first(now.get("water_temp_c"), 28.0)
    lux_now = _first(now.get("lux"), 10000.0)

    # --- chemistry ---
    chem = twin.chemistry.assess(
        rain_incoming=ctx["rain_incoming"],
        rain_intensity=ctx["rain_intensity"],
    )
    _storage().push_evaluation(user_id, chem.to_dict())

    # --- evaporation ---
    evap_days = None
    try:
        evap_days = twin.evaporation.project_forward(
            daily_environment=_evaporation_env(ctx), horizon_days=21
        ).get("predicted_topup_days_from_now")
    except Exception as exc:  # noqa: BLE001
        log_event(log, "evaporation_projection_failed", level=logging.WARNING, pond_id=user_id, error=str(exc))
    evap = twin.evaporation.assess(
        current_water_temp_c=float(water_temp), days_to_topup=evap_days
    )
    fail_soft(lambda: _storage().push_evaporation_evaluation(user_id, evap.to_dict()), None)

    # --- algae ---
    algae_dict = None
    if twin.algae.has_measurement:
        algae_days = None
        try:
            algae_days = twin.algae.project_forward(
                daily_environment=_algae_env(
                    ctx, float(lux_now), float(water_temp), [], None
                ),
                horizon_days=21,
            ).get("predicted_scrub_days_from_now")
        except Exception as exc:  # noqa: BLE001
            log_event(log, "algae_projection_failed", level=logging.WARNING, pond_id=user_id, error=str(exc))
        algae = twin.algae.assess(days_to_scrub=algae_days)
        if algae is not None:
            algae_dict = algae.to_dict()
            fail_soft(lambda: _storage().push_algae_evaluation(user_id, algae_dict), None)

    return {
        "chemistry": chem.to_dict(),
        "evaporation": evap.to_dict(),
        "algae": algae_dict,
    }


def _handle_event(event: PondEvent, body: schemas.EventBody):
    """Shared path for every /events/* endpoint: apply across all engines
    under the lock, re-assess, push, return."""
    user_id = body.user_id
    _require_pond(user_id)
    _, ctx = _environment_for(user_id)

    def mutate(twin):
        twin.apply_event(event)
        return _recompute_and_push(user_id, twin, ctx)

    return _registry().with_twin(user_id, mutate, profiles=_profiles_for(user_id))


# ===================================================================
# Event endpoints
# ===================================================================
# Each returns ALL THREE domain assessments, not just the one most
# obviously related to the event. That is deliberate: a water change
# moves chemistry, water level and suspended algae at once, and the app
# should be able to refresh every card from a single response rather
# than firing three follow-up reads.

@bp.route("/events/feeding", methods=["POST"])
def log_feeding():
    body = _parse_body(schemas.FeedingEvent)
    event = PondEvent(
        kind=EventKind.FEEDING,
        time=body.event_time(),
        food_grams=body.food_grams,
        protein_percent=body.protein_percent,
    )
    return jsonify(_handle_event(event, body)), 200


def _log_volume_event(kind: EventKind):
    body = _parse_body(schemas.VolumeEvent)
    event = PondEvent(
        kind=kind,
        time=body.event_time(),
        volume_percent=body.volume_percent,
        volume_litres=body.volume_litres,
    )
    return jsonify(_handle_event(event, body)), 200


@bp.route("/events/water-change", methods=["POST"])
def log_water_change():
    return _log_volume_event(EventKind.WATER_CHANGE)


@bp.route("/events/top-up", methods=["POST"])
def log_top_up():
    return _log_volume_event(EventKind.TOP_UP)


@bp.route("/events/algal-scrub", methods=["POST"])
def log_algal_scrub():
    body = _parse_body(schemas.AlgalScrubEvent)
    event = PondEvent(
        kind=EventKind.ALGAL_SCRUB,
        time=body.event_time(),
        scrub_type=body.scrub_type,
    )
    return jsonify(_handle_event(event, body)), 200


# ===================================================================
# Human severity ratings (algae)
# ===================================================================
# The third state-update trigger, alongside environmental polls and
# logged interventions. A rating is treated as a genuine observation of
# algae state - see AlgaeGrowthEngine.apply_severity_rating - because a
# person looking at the pond is better evidence than a 640x480 JPEG of
# one corner of it. It corrects the modelled level harder than a camera
# frame does, and simultaneously builds the calibration set that turns
# guessed thresholds into measured ones.

@bp.route("/events/algae-rating", methods=["POST"])
def log_algae_rating():
    """Body: {user_id, severity, image_id?, green_ratio?, notes?}

    severity: "none" | "minor" | "moderate" | "severe" | "obstruction"

    image_id identifies the frame being rated. Strongly preferred over
    letting the server guess "the latest frame": if the user rates at 9pm
    and the newest photo is from 6pm, an implicit pairing silently
    corrupts the calibration set. When omitted the server falls back to
    the newest frame and says so in the response.
    """
    body = _parse_body(schemas.AlgaeRatingEvent)
    user_id = body.user_id
    severity = body.severity
    _require_pond(user_id)

    # Resolve the frame being rated.
    image_id = body.image_id
    image_row = None
    inferred = False
    if image_id is not None:
        image_row = fail_soft(lambda: _storage().fetch_image_by_id(user_id, image_id), None)
    if image_row is None:
        rows = _storage().fetch_image_history(user_id, limit=1)
        image_row = rows[0] if rows else None
        inferred = image_id is not None or image_row is not None

    green_at = body.green_ratio
    if green_at is None and image_row is not None:
        green_at = image_row.get("green_ratio")

    rated_at = body.event_time()

    # Persist first, so the engine can carry the row id for undo.
    stored = fail_soft(lambda: _storage().insert_algae_rating(
        user_id=user_id,
        severity=None if severity == "obstruction" else severity,
        is_obstructed=(severity == "obstruction"),
        image_id=image_row.get("id") if image_row else None,
        image_url=image_row.get("imageURL") if image_row else None,
        green_ratio_at_rating=float(green_at) if green_at is not None else None,
        image_captured_at=str(image_row.get("created_at")) if image_row else None,
        notes=body.notes,
    ), None)
    rating_id = stored.get("id") if stored else None

    _, ctx = _environment_for(user_id)

    def mutate(twin):
        summary = twin.apply_severity_rating(
            time=rated_at,
            severity=severity,
            green_ratio_at_rating=float(green_at) if green_at is not None else None,
            image_id=image_row.get("id") if image_row else None,
            rating_id=rating_id,
        )
        assessments = _recompute_and_push(user_id, twin, ctx)
        summary["assessments"] = assessments
        return summary

    result = _registry().with_twin(user_id, mutate, profiles=_profiles_for(user_id))

    result["rating_id"] = rating_id
    result["rated_image_id"] = image_row.get("id") if image_row else None
    result["image_inferred"] = inferred and body.image_id is None
    if stored is None:
        # The engine still assimilated it, but it will not survive a
        # restart and will not join the calibration set. Say so plainly
        # rather than reporting a clean success.
        result["warning"] = (
            "Rating applied to the live model but NOT persisted - the "
            "algae_severity_ratings table is missing (apply supabase/migrations/0001_baseline.sql)."
        )
    return jsonify(result), 200


@bp.route("/events/algae-rating/undo", methods=["POST"])
def undo_algae_rating():
    """Reverses the most recent rating. Body: {user_id, rating_id?}

    Only the most recent rating is exactly reversible - that is the
    mis-tap case the undo affordance exists for. The engine restores the
    level, thresholds, growth fit and camera history it had beforehand,
    and the persisted row is deleted so it leaves the calibration set too.
    """
    body = _parse_body(schemas.AlgaeRatingUndo)
    user_id = body.user_id
    rating_id = body.rating_id
    _require_pond(user_id)

    _, ctx = _environment_for(user_id)

    def mutate(twin):
        ok = twin.undo_severity_rating(rating_id)
        if not ok:
            return {"undone": False}
        assessments = _recompute_and_push(user_id, twin, ctx)
        return {
            "undone": True,
            "green_ratio": twin.algae.green_ratio,
            "thresholds": twin.algae.thresholds,
            "label_counts": twin.algae.label_counts(),
            "assessments": assessments,
        }

    result = _registry().with_twin(user_id, mutate, profiles=_profiles_for(user_id))
    if not result.get("undone"):
        raise ApiError(
            "Nothing to undo, or that rating is no longer the most recent one. "
            "Older ratings can be removed but their level correction has already been overtaken.",
            status=409, code="nothing_to_undo")

    if rating_id is not None:
        fail_soft(lambda: _storage().delete_algae_rating(user_id, rating_id), None)
    return jsonify(result), 200


@bp.route("/ratings/algae/<int:user_id>", methods=["GET"])
def get_algae_ratings(user_id):
    """Rating history plus the calibration state it produces. Drives the
    rating card's "last time you called this Moderate" anchor and its
    progress hint."""
    _require_pond(user_id)
    rows = fail_soft(lambda: _storage().fetch_algae_ratings(user_id, limit=50), [])

    def read(twin):
        return {
            "label_counts": twin.algae.label_counts(),
            "labels_needed": twin.algae.labels_needed(),
            "thresholds": twin.algae.thresholds,
            "class_targets": {k: round(v, 5) for k, v in twin.algae._class_targets().items()},
            "camera_drift": twin.algae.detect_camera_drift(),
            "green_ratio": twin.algae.green_ratio,
        }

    calibration = _registry().with_twin(
        user_id, read, profiles=_profiles_for(user_id), persist=False
    )

    latest_image = _storage().fetch_image_history(user_id, limit=1)
    return jsonify({
        "ratings": rows[-20:],
        "latest_rating": rows[-1] if rows else None,
        "calibration": calibration,
        "latest_image": latest_image[0] if latest_image else None,
        "severity_levels": ae.SEVERITY_LEVELS + ["obstruction"],
    }), 200


# ===================================================================
# Cached assessment reads (dashboard cards + alert badges)
# ===================================================================

def _no_assessment(user_id: int, message: str):
    """Raises the 404 for a cached assessment that does not exist yet:
    pond_not_configured when the pond itself is unknown, otherwise
    no_assessment_yet. Checked only on a miss, so a normal read stays one
    query."""
    _require_pond(user_id)
    raise ApiError(message, status=404, code="no_assessment_yet")


@bp.route("/assessment/<int:user_id>", methods=["GET"])
def get_latest_assessment(user_id):
    """Most recent chemistry assessment, whether produced by a poll tick
    or by an event endpoint's immediate re-assessment."""
    assessment = _storage().fetch_latest_evaluation(user_id)
    if assessment is None:
        _no_assessment(user_id, "No water chemistry assessment yet. This appears after the first poll cycle "
                                "or the first logged event.")
    return jsonify(assessment), 200


@bp.route("/assessment/evaporation/<int:user_id>", methods=["GET"])
def get_latest_evaporation_assessment(user_id):
    assessment = fail_soft(lambda: _storage().fetch_latest_evaporation_evaluation(user_id), None)
    if assessment is None:
        _no_assessment(user_id, "No evaporation assessment yet. This appears after the first "
                                "poll cycle, or once pond_evaporation_evaluations exists "
                                "(apply supabase/migrations/0001_baseline.sql).")
    return jsonify(assessment), 200


@bp.route("/assessment/algae/<int:user_id>", methods=["GET"])
def get_latest_algae_assessment(user_id):
    assessment = fail_soft(lambda: _storage().fetch_latest_algae_evaluation(user_id), None)
    if assessment is None:
        _no_assessment(user_id, "No algae assessment yet. Needs at least one clean ESP32-CAM "
                                "frame in imageTable, and pond_algae_evaluations to exist "
                                "(apply supabase/migrations/0001_baseline.sql).")
    return jsonify(assessment), 200


@bp.route("/assessment/all/<int:user_id>", methods=["GET"])
def get_all_assessments(user_id):
    """All three cached assessments in one call - lets the dashboard
    populate every outcome card and its alert badge from a single
    request instead of three."""
    assessments = {
        "chemistry": _storage().fetch_latest_evaluation(user_id),
        "evaporation": fail_soft(lambda: _storage().fetch_latest_evaporation_evaluation(user_id), None),
        "algae": fail_soft(lambda: _storage().fetch_latest_algae_evaluation(user_id), None),
    }
    if all(a is None for a in assessments.values()):
        _require_pond(user_id)
    return jsonify(assessments), 200


# ===================================================================
# Forecast reads (detail graph screens) - live, read-only
# ===================================================================

@bp.route("/forecast/<int:user_id>", methods=["GET"])
def get_forecast(user_id):
    """Water chemistry lookahead: projects TAN/NO2/NO3 assuming feeding
    continues at this pond's recent average rate and no further
    interventions occur."""
    horizon_days = _parse_query(schemas.ForecastQuery).horizon_days
    _require_pond(user_id)

    feeding_rows = _storage().fetch_recent_feeding_events(user_id, limit=30)
    avg_daily_tan_mg = WaterChemistryEngine.estimate_avg_daily_tan_mg(feeding_rows)
    if avg_daily_tan_mg is None:
        raise ApiError(
            "Not enough recent feeding history to estimate an average feed "
            "rate yet - need at least 2 logged FEEDING events spanning some "
            "time (not all on the same day).", status=422, code="not_enough_history")

    temp_stats = _storage().fetch_daily_sensor_stats(user_id, "temp")
    if temp_stats is None:
        raise ApiError(
            "No historical temperature data available for this pond yet - "
            "need at least a few days of sensor polling before forecasting.",
            status=422, code="not_enough_history")
    lux_stats = _storage().fetch_daily_sensor_stats(user_id, "LUX") or \
        _storage().fetch_daily_sensor_stats(user_id, "lux")
    fallback_lux = lux_stats["avg"] if lux_stats else None

    payload, ctx = _environment_for(user_id)
    daily = forecast_utils.daily_forecasts_from_outlook(
        (payload.get("nea_forecasts") or {}).get("outlook_4day", [])
    )
    daily_lux_forecast = [
        (fallback_lux * m) if fallback_lux is not None else None
        for m in daily["lux_multiplier"]
    ]

    def run(twin):
        return twin.chemistry.project_forward(
            avg_daily_tan_mg=avg_daily_tan_mg,
            daily_temp_forecast_c=daily["temp_c"],
            daily_lux_forecast=daily_lux_forecast,
            daily_rain=daily["rain"],
            fallback_temp_c=temp_stats["avg"],
            fallback_lux=fallback_lux,
            horizon_days=horizon_days,
        )

    result = _registry().with_twin(
        user_id, run, profiles=_profiles_for(user_id), persist=False
    )
    result["avg_daily_tan_mg"] = avg_daily_tan_mg
    result["temp_baseline"] = temp_stats
    result["lux_baseline"] = lux_stats
    return jsonify(result), 200


@bp.route("/forecast/evaporation/<int:user_id>", methods=["GET"])
def get_evaporation_forecast(user_id):
    """Evaporation + feed lookahead for the Temperature & Feed screen.

    Projects forward from the engine's ACCUMULATED loss state - the
    running integral the poller has been building against real weather -
    rather than re-estimating it from a timestamp at request time.
    """
    query = _parse_query(schemas.EvaporationForecastQuery)
    _require_pond(user_id)

    payload, ctx = _environment_for(user_id)
    env = _evaporation_env(ctx)

    def run(twin):
        stored = twin.evaporation.config
        if query.depth_m is not None:
            twin.evaporation.config = dataclasses.replace(stored, pond_depth_m=query.depth_m)
        try:
            return twin.evaporation.project_forward(
                daily_environment=env, horizon_days=query.horizon_days
            )
        finally:
            twin.evaporation.config = stored

    result = _registry().with_twin(
        user_id,
        run,
        profiles=_profiles_for(user_id),
        persist=False,
    )

    # Grounding cross-check against the measured TDS trend - two
    # independent sources, so agreement is real corroboration.
    tds_series = _storage().fetch_daily_sensor_series(user_id, "TDS", days=30) or \
        _storage().fetch_daily_sensor_series(user_id, "tds", days=30)
    observed_slope = forecast_utils.slope_per_day(tds_series)
    current_tds = ctx["now"].get("tds")
    result["tds_cross_check"] = ev.cross_check_against_tds(
        predicted_daily_loss_litres=result["avg_loss_litres_per_day"],
        volume_litres=result["volume_litres"],
        observed_tds_slope_ppm_per_day=observed_slope,
        current_tds_ppm=float(current_tds) if current_tds is not None else None,
    )
    return jsonify(result), 200


@bp.route("/forecast/algae/<int:user_id>", methods=["GET"])
def get_algae_forecast(user_id):
    """Algae lookahead for the Algal & Solar screen.

    Projects from the engine's current green level - which the poller
    keeps corrected against each new ESP32-CAM frame - using a growth rate
    fitted from that same camera history and modulated by the NEA
    forecast. Nitrate comes from the chemistry engine so the two
    lookaheads stay coupled.
    """
    horizon_days = _parse_query(schemas.ForecastQuery).horizon_days
    _require_pond(user_id)

    payload, ctx = _environment_for(user_id)
    now = ctx["now"]

    lux_stats = _storage().fetch_daily_sensor_stats(user_id, "LUX") or \
        _storage().fetch_daily_sensor_stats(user_id, "lux")
    baseline_lux = float(_first(now.get("lux"), lux_stats["avg"] if lux_stats else None, 10000.0))
    temp_stats = _storage().fetch_daily_sensor_stats(user_id, "temp")
    baseline_temp = float(_first(now.get("water_temp_c"), temp_stats["avg"] if temp_stats else None, 29.0))

    feeding_rows = _storage().fetch_recent_feeding_events(user_id, limit=30)
    avg_tan = WaterChemistryEngine.estimate_avg_daily_tan_mg(feeding_rows)

    def run(twin):
        # Assimilate any frames that arrived since the last poll, so an
        # on-demand chart refresh is never staler than the camera.
        fresh = ae.parse_image_rows(_storage().fetch_image_history(user_id, limit=50))
        assimilated = twin.algae.ingest_camera_samples(fresh)

        no3_series = []
        if avg_tan is not None:
            no3_series = twin.no3_projection(
                avg_daily_tan_mg=avg_tan,
                fallback_temp_c=baseline_temp,
                horizon_days=horizon_days,
            )
        no3_now = no3_series[0] if no3_series else None

        if assimilated:
            # ROUND 3 FIX: reuse the history list ingest_camera_samples()
            # just built rather than having refit_growth_rate re-parse the
            # whole camera history a second time for this same request.
            twin.algae.refit_growth_rate(
                baseline_lux, baseline_temp, no3_now,
                history_samples=twin.algae._last_history_samples,
            )

        env = _algae_env(ctx, baseline_lux, baseline_temp, no3_series, no3_now)
        projection = twin.algae.project_forward(
            daily_environment=env, horizon_days=horizon_days
        )
        if "error" not in projection:
            projection["scrub_benefit"] = twin.algae.project_scrub_benefit(
                daily_environment=env, horizon_days=horizon_days
            )
        return projection, assimilated

    # persist=True here: unlike the other forecast endpoints this one can
    # genuinely mutate state, because it assimilates any camera frames
    # that landed since the last poll. Those must be durable.
    result, assimilated = _registry().with_twin(
        user_id, run, profiles=_profiles_for(user_id), persist=True
    )

    if "error" in result:
        raise ApiError(result.get("detail") or result["error"], status=422, code=result["error"])

    result["assimilated_camera_frames"] = assimilated
    image_rows = _storage().fetch_image_history(user_id, limit=1)
    result["latest_image_url"] = image_rows[0].get("imageURL") if image_rows else None
    return jsonify(result), 200


# ===================================================================
# Pond profile (pond_profile, migration 0008)
# ===================================================================
# Effective-dated: each PUT adds a row in force from its effective_from
# until the next row's, and old rows stay, so a change applies from its
# own time onward (koi/models/profile.py). A past effective_from is
# recorded and used from the next engine step on; steps the engines have
# already taken are not recomputed.

def _profile_response(user_id: int) -> dict:
    rows = _storage().fetch_pond_profiles(user_id)
    history = _registry().profile_history(user_id)
    if history is None:
        raise PondNotConfigured(user_id)
    return {
        "pond_id": user_id,
        "source": "pond_profile" if rows else "userdata",
        "current": history.at(schemas.utc_now()).to_dict(),
        "history": rows,
    }


@bp.route("/v1/ponds/<int:user_id>/profile", methods=["GET"])
def get_pond_profile(user_id):
    """The profile in force now and every stored profile row, oldest
    first. source is "userdata" for a pond with no profile rows yet,
    whose profile is its onboarding volume and biomass."""
    return jsonify(_profile_response(user_id)), 200


@bp.route("/v1/ponds/<int:user_id>/profile", methods=["PUT"])
def put_pond_profile(user_id):
    """Adds a profile row effective now, or at the body's effective_from.
    409 when the pond already has a profile at that exact time."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise ApiError("The request body must be a JSON object.", code="invalid_body")
    update = schemas.ProfileUpdate.model_validate(body)
    row = {name: getattr(update, name) for name in PROFILE_FIELDS}
    row["effective_from"] = update.effective_time().isoformat()
    try:
        stored = _storage().insert_pond_profile(user_id, row)
    except DuplicateProfileError as exc:
        raise ApiError(f"Pond {user_id} already has a profile starting at {exc.effective_from}. "
                       "Send a different effective_from.", status=409, code="profile_exists") from exc
    log_event(log, "pond_profile_added", pond_id=user_id, effective_from=row["effective_from"])
    return jsonify({**_profile_response(user_id), "added": stored}), 201


# ===================================================================

@bp.route("/health", methods=["GET"])
def health():
    """Liveness: the process is up and answering. Checks nothing else."""
    return jsonify({"status": "ok", "domains": ["chemistry", "evaporation", "algae"]}), 200


@bp.route("/ready", methods=["GET"])
def ready():
    """Readiness: storage answers and the poller succeeded recently
    (koi/api/health.py). 503 with the error envelope when not."""
    is_ready, report = readiness(_storage(), current_app.config["KOI_SETTINGS"])
    if is_ready:
        return jsonify({"status": "ready", **report}), 200
    return error_response(503, "not_ready", "The service is not ready. " + " ".join(report["reasons"]), report)

