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

Auth note: every endpoint below trusts a caller-supplied user_id (in the
body for events, in the path for reads). That is NOT safe for production
- anyone who can reach this service can read or write state for any user.
Before shipping, verify the caller's Supabase JWT (Flutter already holds
one from its own auth session) and derive user_id from the verified token
rather than from the request. See the write-up.
"""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models import forecast_utils
from koi.models.engine import EventKind, PondConfig, PondEvent, WaterChemistryEngine
from koi.registry import registry
from koi.storage import state_store

bp = Blueprint("twin", __name__)


# ===================================================================
# Helpers
# ===================================================================

def _parse_time(body: dict) -> datetime:
    ts = body.get("timestamp")
    if ts:
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            # A malformed client timestamp should not 500 the whole log -
            # fall back to server time and carry on.
            print(f"[app] unparseable timestamp {ts!r}, using server time")
    return datetime.now(timezone.utc)


def _default_config_for(user_id: int, body: dict) -> PondConfig | None:
    """Bootstraps a PondConfig for a user with no persisted snapshot yet.
    volume/biomass come from UserData (state_store.fetch_pond_config);
    fish_type/fish_count aren't stored there, so the caller (Flutter) sends
    them from its own SharedPreferences - see quick_log_modals.dart. Both
    are optional and fall back to PondConfig's own defaults if omitted."""
    row = state_store.fetch_pond_config(user_id)
    if row is None:
        return None
    kwargs = dict(row)
    if body.get("fish_type") is not None:
        kwargs["fish_type"] = body["fish_type"]
    if body.get("fish_count") is not None:
        kwargs["fish_count"] = body["fish_count"]
    return PondConfig(**kwargs)


def _environment_for(user_id: int):
    """Fetches the bundled payload once and derives everything the
    engines need from it. Returns (payload, context) where context holds
    the live conditions, forecast days and rain flags."""
    payload = state_store.fetch_dashboard_payload(user_id) or {}
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

    Called INSIDE the twin lock. The Supabase writes happen here rather
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
    state_store.push_evaluation(user_id, chem.to_dict())

    # --- evaporation ---
    evap_days = None
    try:
        evap_days = twin.evaporation.project_forward(
            daily_environment=_evaporation_env(ctx), horizon_days=21
        ).get("predicted_topup_days_from_now")
    except Exception as exc:  # noqa: BLE001
        print(f"[app] evaporation projection failed for {user_id}: {exc}")
    evap = twin.evaporation.assess(
        current_water_temp_c=float(water_temp), days_to_topup=evap_days
    )
    state_store.push_evaporation_evaluation(user_id, evap.to_dict())

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
            print(f"[app] algae projection failed for {user_id}: {exc}")
        algae = twin.algae.assess(days_to_scrub=algae_days)
        if algae is not None:
            algae_dict = algae.to_dict()
            state_store.push_algae_evaluation(user_id, algae_dict)

    return {
        "chemistry": chem.to_dict(),
        "evaporation": evap.to_dict(),
        "algae": algae_dict,
    }


def _handle_event(user_id: int, event: PondEvent, body: dict):
    """Shared path for every /events/* endpoint: apply across all engines
    under the lock, re-assess, push, return."""
    _, ctx = _environment_for(user_id)

    def mutate(twin):
        twin.apply_event(event)
        return _recompute_and_push(user_id, twin, ctx)

    return registry.with_twin(
        user_id, mutate, default_config=_default_config_for(user_id, body)
    )


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
    body = request.get_json(force=True)
    user_id = body["user_id"]
    try:
        event = PondEvent(
            kind=EventKind.FEEDING,
            time=_parse_time(body),
            food_grams=float(body["food_grams"]),
            protein_percent=float(body["protein_percent"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": f"food_grams and protein_percent required: {exc}"}), 400
    if event.food_grams < 0 or not (0 <= event.protein_percent <= 100):
        return jsonify({"error": "food_grams must be >= 0 and protein_percent 0-100"}), 400

    return jsonify(_handle_event(user_id, event, body)), 200


@bp.route("/events/water-change", methods=["POST"])
def log_water_change():
    body = request.get_json(force=True)
    user_id = body["user_id"]
    event = PondEvent(
        kind=EventKind.WATER_CHANGE,
        time=_parse_time(body),
        volume_percent=body.get("volume_percent"),
        volume_litres=body.get("volume_litres"),
    )
    if event.volume_percent is None and event.volume_litres is None:
        return jsonify({"error": "volume_percent or volume_litres required"}), 400

    return jsonify(_handle_event(user_id, event, body)), 200


@bp.route("/events/top-up", methods=["POST"])
def log_top_up():
    body = request.get_json(force=True)
    user_id = body["user_id"]
    event = PondEvent(
        kind=EventKind.TOP_UP,
        time=_parse_time(body),
        volume_percent=body.get("volume_percent"),
        volume_litres=body.get("volume_litres"),
    )
    if event.volume_percent is None and event.volume_litres is None:
        return jsonify({"error": "volume_percent or volume_litres required"}), 400

    return jsonify(_handle_event(user_id, event, body)), 200


@bp.route("/events/algal-scrub", methods=["POST"])
def log_algal_scrub():
    body = request.get_json(force=True)
    user_id = body["user_id"]
    event = PondEvent(
        kind=EventKind.ALGAL_SCRUB,
        time=_parse_time(body),
        scrub_type=body.get("scrub_type", "unspecified"),
    )
    return jsonify(_handle_event(user_id, event, body)), 200


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
    body = request.get_json(force=True)
    user_id = body["user_id"]
    severity = str(body.get("severity", "")).strip().lower()

    valid = ae.SEVERITY_LEVELS + ["obstruction"]
    if severity not in valid:
        return jsonify({"error": f"severity must be one of {valid}"}), 400

    # Resolve the frame being rated.
    image_id = body.get("image_id")
    image_row = None
    inferred = False
    if image_id is not None:
        image_row = state_store.fetch_image_by_id(user_id, int(image_id))
    if image_row is None:
        rows = state_store.fetch_image_history(user_id, limit=1)
        image_row = rows[0] if rows else None
        inferred = image_id is not None or image_row is not None

    green_at = body.get("green_ratio")
    if green_at is None and image_row is not None:
        green_at = image_row.get("green_ratio")

    rated_at = _parse_time(body)

    # Persist first, so the engine can carry the row id for undo.
    stored = state_store.insert_algae_rating(
        user_id=user_id,
        severity=None if severity == "obstruction" else severity,
        is_obstructed=(severity == "obstruction"),
        image_id=image_row.get("id") if image_row else None,
        image_url=image_row.get("imageURL") if image_row else None,
        green_ratio_at_rating=float(green_at) if green_at is not None else None,
        image_captured_at=str(image_row.get("created_at")) if image_row else None,
        notes=body.get("notes"),
    )
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

    try:
        result = registry.with_twin(
            user_id, mutate, default_config=_default_config_for(user_id, body)
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    result["rating_id"] = rating_id
    result["rated_image_id"] = image_row.get("id") if image_row else None
    result["image_inferred"] = inferred and body.get("image_id") is None
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
    body = request.get_json(force=True)
    user_id = body["user_id"]
    rating_id = body.get("rating_id")

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

    result = registry.with_twin(user_id, mutate)
    if not result.get("undone"):
        return jsonify({
            "undone": False,
            "error": "Nothing to undo, or that rating is no longer the most "
                     "recent one. Older ratings can be removed but their "
                     "level correction has already been overtaken."
        }), 409

    if rating_id is not None:
        state_store.delete_algae_rating(user_id, int(rating_id))
    return jsonify(result), 200


@bp.route("/ratings/algae/<int:user_id>", methods=["GET"])
def get_algae_ratings(user_id):
    """Rating history plus the calibration state it produces. Drives the
    rating card's "last time you called this Moderate" anchor and its
    progress hint."""
    rows = state_store.fetch_algae_ratings(user_id, limit=50)

    def read(twin):
        return {
            "label_counts": twin.algae.label_counts(),
            "labels_needed": twin.algae.labels_needed(),
            "thresholds": twin.algae.thresholds,
            "class_targets": {k: round(v, 5) for k, v in twin.algae._class_targets().items()},
            "camera_drift": twin.algae.detect_camera_drift(),
            "green_ratio": twin.algae.green_ratio,
        }

    try:
        calibration = registry.with_twin(
            user_id, read, default_config=_default_config_for(user_id, {}), persist=False
        )
    except ValueError:
        calibration = None

    latest_image = state_store.fetch_image_history(user_id, limit=1)
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

@bp.route("/assessment/<int:user_id>", methods=["GET"])
def get_latest_assessment(user_id):
    """Most recent chemistry assessment, whether produced by a poll tick
    or by an event endpoint's immediate re-assessment."""
    assessment = state_store.fetch_latest_evaluation(user_id)
    if assessment is None:
        return jsonify({"error": "no assessment yet for this user"}), 404
    return jsonify(assessment), 200


@bp.route("/assessment/evaporation/<int:user_id>", methods=["GET"])
def get_latest_evaporation_assessment(user_id):
    assessment = state_store.fetch_latest_evaporation_evaluation(user_id)
    if assessment is None:
        return jsonify({
            "error": "No evaporation assessment yet. This appears after the first "
                     "poll cycle, or once pond_evaporation_evaluations exists "
                     "(apply supabase/migrations/0001_baseline.sql)."
        }), 404
    return jsonify(assessment), 200


@bp.route("/assessment/algae/<int:user_id>", methods=["GET"])
def get_latest_algae_assessment(user_id):
    assessment = state_store.fetch_latest_algae_evaluation(user_id)
    if assessment is None:
        return jsonify({
            "error": "No algae assessment yet. Needs at least one clean ESP32-CAM "
                     "frame in imageTable, and pond_algae_evaluations to exist "
                     "(apply supabase/migrations/0001_baseline.sql)."
        }), 404
    return jsonify(assessment), 200


@bp.route("/assessment/all/<int:user_id>", methods=["GET"])
def get_all_assessments(user_id):
    """All three cached assessments in one call - lets the dashboard
    populate every outcome card and its alert badge from a single
    request instead of three."""
    return jsonify({
        "chemistry": state_store.fetch_latest_evaluation(user_id),
        "evaporation": state_store.fetch_latest_evaporation_evaluation(user_id),
        "algae": state_store.fetch_latest_algae_evaluation(user_id),
    }), 200


# ===================================================================
# Forecast reads (detail graph screens) - live, read-only
# ===================================================================

@bp.route("/forecast/<int:user_id>", methods=["GET"])
def get_forecast(user_id):
    """Water chemistry lookahead: projects TAN/NO2/NO3 assuming feeding
    continues at this pond's recent average rate and no further
    interventions occur."""
    horizon_days = request.args.get("horizon_days", default=21, type=int)

    feeding_rows = state_store.fetch_recent_feeding_events(user_id, limit=30)
    avg_daily_tan_mg = WaterChemistryEngine.estimate_avg_daily_tan_mg(feeding_rows)
    if avg_daily_tan_mg is None:
        return jsonify({
            "error": "Not enough recent feeding history to estimate an average feed "
                     "rate yet - need at least 2 logged FEEDING events spanning some "
                     "time (not all on the same day)."
        }), 422

    temp_stats = state_store.fetch_daily_sensor_stats(user_id, "temp")
    if temp_stats is None:
        return jsonify({
            "error": "No historical temperature data available for this pond yet - "
                     "need at least a few days of sensor polling before forecasting."
        }), 422
    lux_stats = state_store.fetch_daily_sensor_stats(user_id, "LUX") or \
        state_store.fetch_daily_sensor_stats(user_id, "lux")
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

    result = registry.with_twin(
        user_id, run, default_config=_default_config_for(user_id, {}), persist=False
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
    horizon_days = request.args.get("horizon_days", default=14, type=int)
    depth_m = request.args.get("depth_m", default=ev.DEFAULT_POND_DEPTH_M, type=float)

    config_row = state_store.fetch_pond_config(user_id)
    if config_row is None:
        return jsonify({
            "error": "No pond configuration for this user - complete onboarding "
                     "(volume and biomass) before forecasting."
        }), 422

    payload, ctx = _environment_for(user_id)
    env = _evaporation_env(ctx)

    def run(twin):
        return twin.evaporation.project_forward(
            daily_environment=env, horizon_days=horizon_days
        )

    result = registry.with_twin(
        user_id,
        run,
        default_config=_default_config_for(user_id, {}),
        pond_depth_m=depth_m,
        persist=False,
    )

    # Grounding cross-check against the measured TDS trend - two
    # independent sources, so agreement is real corroboration.
    tds_series = state_store.fetch_daily_sensor_series(user_id, "TDS", days=30) or \
        state_store.fetch_daily_sensor_series(user_id, "tds", days=30)
    observed_slope = state_store.slope_per_day(tds_series)
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
    horizon_days = request.args.get("horizon_days", default=21, type=int)

    payload, ctx = _environment_for(user_id)
    now = ctx["now"]

    lux_stats = state_store.fetch_daily_sensor_stats(user_id, "LUX") or \
        state_store.fetch_daily_sensor_stats(user_id, "lux")
    baseline_lux = float(_first(now.get("lux"), lux_stats["avg"] if lux_stats else None, 10000.0))
    temp_stats = state_store.fetch_daily_sensor_stats(user_id, "temp")
    baseline_temp = float(_first(now.get("water_temp_c"), temp_stats["avg"] if temp_stats else None, 29.0))

    feeding_rows = state_store.fetch_recent_feeding_events(user_id, limit=30)
    avg_tan = WaterChemistryEngine.estimate_avg_daily_tan_mg(feeding_rows)

    def run(twin):
        # Assimilate any frames that arrived since the last poll, so an
        # on-demand chart refresh is never staler than the camera.
        fresh = ae.parse_image_rows(state_store.fetch_image_history(user_id, limit=50))
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
    result, assimilated = registry.with_twin(
        user_id, run, default_config=_default_config_for(user_id, {}), persist=True
    )

    if "error" in result:
        return jsonify(result), 422

    result["assimilated_camera_frames"] = assimilated
    image_rows = state_store.fetch_image_history(user_id, limit=1)
    result["latest_image_url"] = image_rows[0].get("imageURL") if image_rows else None
    return jsonify(result), 200


# ===================================================================

@bp.app_errorhandler(ValueError)
def handle_missing_config(err):
    return jsonify({"error": str(err)}), 404


@bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "domains": ["chemistry", "evaporation", "algae"]}), 200

