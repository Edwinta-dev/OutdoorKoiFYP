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

Versions: every route is declared with koi.api.spec.route, which serves
it under /v1 (the pond in the path) and records it for docs/api/openapi.yaml. Response models are in
koi.api.responses. GET /v1/ponds/{pond}/dashboard is the one call the
dashboard screen needs (koi/api/dashboard.py).
"""
import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional, TypeVar

# Flask's Blueprint, under a name that the no-print check (grep for a
# print call in koi/, issue #10) does not mistake for one.
from flask import Blueprint as RouteGroup
from flask import current_app, jsonify, request
from pydantic import BaseModel

from koi import kit_readings
from koi.api import responses as res
from koi.api import schemas
from koi.api.auth import authenticate, require_pond
from koi.api.dashboard import build_dashboard
from koi.api.health import readiness
from koi.api.spec import after_request, route
from koi.errors import ApiError, PondNotConfigured, error_response
from koi.logs import log_event
from koi.models import algae_engine as ae
from koi.models import device_health, forecast_utils, ladder, uncertainty
from koi.models import evaporation_engine as ev
from koi.models.engine import EventKind, PondEvent, WaterChemistryEngine
from koi.models.hypoxia import algae_is_high, assess_hypoxia
from koi.models.local_time import local_date
from koi.models.profile import PROFILE_FIELDS, ProfileHistory
from koi.provenance import ALGAE, CHEMISTRY, EVAPORATION, RunProvenance, forecast_provenance, model_version
from koi.registry import EngineRegistry
from koi.storage import DuplicateProfileError, Storage, fail_soft
from koi.storage.base import parse_timestamp
from koi.weather.history import local_day_window
from koi.worker import poller

bp = RouteGroup("twin", __name__)
bp.before_request(authenticate)
bp.after_request(after_request)

# Error statuses every pond route can return, besides its own.
POND_ERRORS = (401, 403, 404, 503)

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


class LeadTimeActions(BaseModel):
    status: Literal["assessed", "insufficient_data"]
    rules: list[dict[str, Any]]
    actions: list[dict[str, Any]]


def _storage() -> Storage:
    return current_app.extensions["koi_storage"]


def _registry() -> EngineRegistry:
    return current_app.extensions["koi_registry"]


# ===================================================================
# Helpers
# ===================================================================

def _parse_body(model: type[M], pond: Optional[int] = None) -> M:
    """The JSON body validated against model; a ValidationError becomes
    a 400 with field-level detail (koi.errors). Every body names a pond,
    which must be the caller's (403 otherwise). On a /v1 path, pond is
    the path's: the body's user_id may be left out, and a different one
    is a 400."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise ApiError("The request body must be a JSON object.", code="invalid_body")
    parsed: Any = (schemas.on_pond_path(model) if pond is not None else model).model_validate(body)
    if pond is not None:
        if parsed.user_id is None:
            parsed.user_id = pond
        elif parsed.user_id != pond:
            raise ApiError(f"The body's user_id ({parsed.user_id}) is not the pond in the path ({pond}).",
                           code="pond_mismatch", details={"user_id": parsed.user_id, "pond": pond})
    require_pond(parsed.user_id)
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
    the live conditions, forecast days, rain flags and the payload itself
    (for the forecast provenance of evaluation rows)."""
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
        "payload": payload,
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


def _recompute_and_push(user_id: int, twin, ctx, run: str, events: int = 0, ratings: int = 0) -> dict:
    """Re-assesses all three domains after a mutation and pushes a fresh
    evaluation row for each, so the UI reflects a just-logged event
    without waiting for the next poll cycle. Each row, and each assessment
    returned, carries the run's provenance (koi/provenance.py): run names
    the endpoint ("event", "rating", "rating_undo") and events and ratings
    count what it applied.

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
    provenance = RunProvenance(
        run=run, input_cutoff=twin.input_cutoff(),
        forecasts=forecast_provenance(ctx.get("payload"),
                                      fail_soft(lambda: _storage().fetch_dashboard_sources(user_id), None)),
        events=events, ratings=ratings)

    # --- chemistry ---
    assessed_at = schemas.utc_now()
    observed_rain = fail_soft(lambda: forecast_utils.observed_rain_24h(
        _storage(), _storage().fetch_dashboard_sources(user_id), assessed_at), None)
    chem = provenance.stamp(CHEMISTRY, twin.chemistry.assess(
        rain_incoming=ctx["rain_incoming"],
        rain_intensity=ctx["rain_intensity"],
        observed_rain=observed_rain,
        depth_m=twin.depth_m_at(assessed_at),
    ).to_dict())
    _storage().push_evaluation(user_id, chem)

    # --- evaporation ---
    evap_days = None
    try:
        evap_days = twin.evaporation.project_forward(
            daily_environment=_evaporation_env(ctx), horizon_days=21
        ).get("predicted_topup_days_from_now")
    except Exception as exc:  # noqa: BLE001
        log_event(log, "evaporation_projection_failed", level=logging.WARNING, pond_id=user_id, error=str(exc))
    evap = provenance.stamp(EVAPORATION, twin.evaporation.assess(
        current_water_temp_c=float(water_temp), days_to_topup=evap_days
    ).to_dict())
    fail_soft(lambda: _storage().push_evaporation_evaluation(user_id, evap), None)

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
            algae_dict = provenance.stamp(ALGAE, algae.to_dict())
            fail_soft(lambda: _storage().push_algae_evaluation(user_id, algae_dict), None)

    result = {
        "chemistry": chem,
        "evaporation": evap,
        "algae": algae_dict,
    }
    _with_confidence(user_id, result, twin=twin)
    return result


def _handle_event(event: PondEvent, body: schemas.EventBody):
    """Shared path for every /events/* endpoint: record the event in the
    pond's ledger under the lock (applied once per event_id, replayed into
    its place when it is in the past; koi/models/event_ledger.py),
    re-assess, push, return. A repeated event_id changes nothing and
    returns the current assessments."""
    user_id = body.user_id
    _require_pond(user_id)
    _, ctx = _environment_for(user_id)
    registry = _registry()
    history = registry.sensor_history(user_id)

    def mutate(twin):
        outcome = twin.record_event(body.event_id, event, now=datetime.now(timezone.utc), history=history)
        log_event(log, "event_recorded", pond_id=user_id, event_id=outcome["event_id"], status=outcome["status"],
                  replayed=outcome["replayed"], placed_late=outcome["placed_late"])
        entry = twin.ledger.get(outcome["event_id"])
        if entry is not None and entry.event.kind in (EventKind.SALT, EventKind.FILTER_CLEAN):
            outcome.update(salt_grams=entry.event.salt_grams, notes=entry.event.notes)
        applied = 1 if outcome["status"] == "applied" else 0
        return {**_recompute_and_push(user_id, twin, ctx, "event", events=applied), "event": outcome}

    return registry.with_twin(user_id, mutate, profiles=_profiles_for(user_id))


# ===================================================================
# TDS owner questions
# ===================================================================
@route(bp, "GET", "/v1/ponds/<int:user_id>/prompts", summary="List unanswered TDS questions",
       tag="events", response=res.TdsPromptList, errors=POND_ERRORS)
def get_prompts(user_id: int):
    """Off by default. Confidence describes a measured TDS signal, not its cause."""
    _require_pond(user_id)
    if not current_app.config["KOI_SETTINGS"].tds_prompts:
        return jsonify({"enabled": False, "prompts": []})
    return jsonify(_registry().with_twin(
        user_id, lambda twin: {"enabled": True, "prompts": [p for p in twin.tds_prompts.prompts
                                                            if p["answer"] is None]},
        profiles=_profiles_for(user_id), persist=False))


@route(bp, "POST", "/v1/ponds/<int:user_id>/prompts/<prompt_id>", summary="Answer a TDS question",
       tag="events", body=schemas.on_pond_path(schemas.PromptAnswer), response=res.TdsPromptAnswer,
       errors=(400, 409, *POND_ERRORS))
def answer_prompt(user_id: int, prompt_id: str):
    """Event answers need the usual measured amounts and are applied once through the ledger.

    'none of these' remembers rejected guesses for this signal type and direction.
    'dismiss' closes this question without rejecting future explanations. A repeat of
    the same answer is a no-op; changing an answered question returns 409.
    """
    _require_pond(user_id)
    if not current_app.config["KOI_SETTINGS"].tds_prompts:
        raise ApiError("TDS questions are disabled.", code="prompts_disabled", status=404)
    body = _parse_body(schemas.PromptAnswer, user_id)
    _, ctx = _environment_for(user_id)
    registry = _registry()
    history = registry.sensor_history(user_id)

    def mutate(twin):
        prompt = next((p for p in twin.tds_prompts.prompts if p["id"] == prompt_id), None)
        if prompt is None:
            raise ApiError("TDS question not found.", code="prompt_not_found", status=404)
        event_id = None
        if prompt["answer"] is not None:
            if prompt["answer"] != body.answer:
                raise ApiError("This question was already answered.", code="prompt_answered", status=409)
            return {"prompt": prompt, "event_id": prompt.get("event_id")}
        if body.answer == "none of these":
            twin.tds_prompts.reject(prompt)
        elif body.answer != "dismiss":
            fields = {"water_change": ("volume_percent", "volume_litres"),
                      "top_up": ("volume_percent", "volume_litres"), "salt": ("salt_grams",),
                      "feeding": ("food_grams", "protein_percent"), "algal_scrub": ("scrub_type",),
                      "filter_clean": ()}
            event_time = schemas.EventBody.model_validate(
                {"user_id": user_id, "timestamp": prompt["event_at"]}).event_time()
            event = PondEvent(kind=EventKind(body.answer), time=event_time,
                              **body.model_dump(include=set(fields[body.answer]) | {"notes"}))
            outcome = twin.record_event(prompt_id, event, now=datetime.now(timezone.utc), history=history)
            event_id = outcome["event_id"]
            _recompute_and_push(user_id, twin, ctx, "prompt", events=int(outcome["status"] == "applied"))
        prompt["answer"] = body.answer
        prompt["event_id"] = event_id
        return {"prompt": prompt, "event_id": event_id}

    return jsonify(registry.with_twin(user_id, mutate, profiles=_profiles_for(user_id)))


# ===================================================================
# Owner test-kit measurements
# ===================================================================
@route(bp, "POST", "/v1/ponds/<int:user_id>/kit-readings", summary="Record a test-kit measurement",
       tag="validation", body=schemas.on_pond_path(schemas.KitReadingBody), response=res.KitReading,
       status=201, errors=(400, *POND_ERRORS))
def post_kit_reading(user_id: int):
    """Store the kit result and an immutable model comparison at taken_at. Does not calibrate the twin."""
    body = _parse_body(schemas.KitReadingBody, user_id)
    _require_pond(user_id)
    return jsonify(kit_readings.record(_storage(), current_app.config["KOI_SETTINGS"], user_id,
                                      body.model_dump(mode="json", exclude={"user_id"}), body.taken_at)), 201


@route(bp, "GET", "/v1/ponds/<int:user_id>/kit-readings", summary="List test-kit measurements",
       tag="validation", response=res.KitReadings, errors=POND_ERRORS)
def get_kit_readings(user_id: int):
    _require_pond(user_id)
    return jsonify({"readings": _storage().fetch_kit_readings(user_id)})


@route(bp, "GET", "/v1/ponds/<int:user_id>/validation", summary="Compare model estimates with test kits",
       tag="validation", response=res.KitValidation, errors=POND_ERRORS)
def get_kit_validation(user_id: int):
    """Per-analyte paired counts, signed mean error, mean absolute error and measured/estimated pairs.

    Error is model minus measurement. Missing measurements or estimates are excluded;
    an empty analyte has count zero and null means. pH and KH have no model estimate.
    """
    _require_pond(user_id)
    return jsonify(kit_readings.validation(_storage().fetch_kit_readings(user_id)))


# ===================================================================
# Pond calibration (issue #32)
# ===================================================================
_CALIBRATION_UNITS = {poller.SHELTER: None, poller.TEMPERATURE: "degC", poller.NITRIFICATION: None}


@route(bp, "GET", "/v1/ponds/<int:user_id>/calibration", summary="Pond-specific model constants",
       tag="validation", response=res.Calibration, errors=POND_ERRORS)
def get_calibration(user_id: int):
    """The evaporation shelter factor, the water/air temperature offset and lag, and the nitrification rate scale
    the models use for this pond now: the fit in force with its fit date, sample count and error, or the default
    while the fit is pending. history holds every stored version, oldest first."""
    _require_pond(user_id)
    rows = _storage().fetch_calibrations(user_id)
    now = datetime.now(timezone.utc)
    in_force = poller.calibration_in_force(rows, now)
    parameters = {}
    for parameter in poller.CALIBRATION_PARAMETERS:
        default = poller.CALIBRATION_DEFAULTS[parameter]
        row = in_force.get(parameter)
        own = [r for r in rows if r["parameter"] == parameter]
        later = [parse_timestamp(r["effective_from"]) for r in own
                 if r["status"] == "fitted" and row is not None and parse_timestamp(r["effective_from"]) > now]
        parameters[parameter] = {
            "status": "fitted" if row else "pending",
            "value_in_use": row["value"] if row else default["value"],
            "lag_hours_in_use": (row["lag_hours"] if row else default["lag_hours"]),
            "default_value": default["value"], "default_lag_hours": default["lag_hours"],
            "unit": _CALIBRATION_UNITS[parameter], "in_force": row,
            "effective_until": min(later).isoformat() if later else None, "latest": own[-1] if own else None,
        }
    return jsonify({"pond_id": user_id, "as_of": now.isoformat(), "parameters": parameters, "history": rows})


# ===================================================================
# Event endpoints
# ===================================================================
# Each returns ALL THREE domain assessments, not just the one most
# obviously related to the event. That is deliberate: a water change
# moves chemistry, water level and suspended algae at once, and the app
# should be able to refresh every card from a single response rather
# than firing three follow-up reads.

def _event_route(name: str, summary: str, body: type[BaseModel], response: type[BaseModel] = res.EventAssessments,
                 errors: tuple[int, ...] = (400, *POND_ERRORS)):
    return route(bp, "POST", f"/v1/ponds/<int:user_id>/events/{name}", summary=summary, tag="events",
        body=schemas.on_pond_path(body),
                 response=response, errors=errors)


@_event_route("feeding", "Log a feeding", schemas.FeedingEvent)
def log_feeding(user_id: Optional[int] = None):
    """Applies the feeding to every engine, re-assesses and returns all
    three assessments."""
    body = _parse_body(schemas.FeedingEvent, user_id)
    event = PondEvent(
        kind=EventKind.FEEDING,
        time=body.event_time(),
        food_grams=body.food_grams,
        protein_percent=body.protein_percent,
    )
    return jsonify(_handle_event(event, body)), 200


def _log_volume_event(kind: EventKind, pond: Optional[int]):
    body = _parse_body(schemas.VolumeEvent, pond)
    event = PondEvent(
        kind=kind,
        time=body.event_time(),
        volume_percent=body.volume_percent,
        volume_litres=body.volume_litres,
    )
    return jsonify(_handle_event(event, body)), 200


@_event_route("water-change", "Log a water change", schemas.VolumeEvent)
def log_water_change(user_id: Optional[int] = None):
    """Dilutes chemistry, resets the water level and suspended algae,
    re-assesses and returns all three assessments."""
    return _log_volume_event(EventKind.WATER_CHANGE, user_id)


@_event_route("top-up", "Log a top-up", schemas.VolumeEvent)
def log_top_up(user_id: Optional[int] = None):
    """Clears the evaporation engine's accrued loss, re-assesses and
    returns all three assessments."""
    return _log_volume_event(EventKind.TOP_UP, user_id)


@_event_route("salt", "Log salt addition (grams)", schemas.SaltEvent)
def log_salt(user_id: Optional[int] = None):
    body = _parse_body(schemas.SaltEvent, user_id)
    event = PondEvent(kind=EventKind.SALT, time=body.event_time(), salt_grams=body.salt_grams, notes=body.notes)
    return jsonify(_handle_event(event, body)), 200


@_event_route("filter-clean", "Log filter cleaning", schemas.FilterCleanEvent)
def log_filter_clean(user_id: Optional[int] = None):
    body = _parse_body(schemas.FilterCleanEvent, user_id)
    event = PondEvent(kind=EventKind.FILTER_CLEAN, time=body.event_time(), notes=body.notes)
    return jsonify(_handle_event(event, body)), 200


@_event_route("algal-scrub", "Log an algae scrub", schemas.AlgalScrubEvent)
def log_algal_scrub(user_id: Optional[int] = None):
    """Knocks the algae level down, re-assesses and returns all three
    assessments."""
    body = _parse_body(schemas.AlgalScrubEvent, user_id)
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

@_event_route("algae-rating", "Rate the algae in a camera frame", schemas.AlgaeRatingEvent,
              response=res.AlgaeRatingResult)
def log_algae_rating(user_id: Optional[int] = None):
    """Body: {user_id, severity, image_id?, green_ratio?, notes?}

    severity: "none" | "minor" | "moderate" | "severe" | "obstruction"

    image_id identifies the frame being rated. Strongly preferred over
    letting the server guess "the latest frame": if the user rates at 9pm
    and the newest photo is from 6pm, an implicit pairing silently
    corrupts the calibration set. When omitted the server falls back to
    the newest frame and says so in the response.
    """
    body = _parse_body(schemas.AlgaeRatingEvent, user_id)
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
        assessments = _recompute_and_push(user_id, twin, ctx, "rating", ratings=1)
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


@_event_route("algae-rating/undo", "Undo the most recent algae rating", schemas.AlgaeRatingUndo,
              response=res.AlgaeRatingUndoResult, errors=(400, 409, *POND_ERRORS))
def undo_algae_rating(user_id: Optional[int] = None):
    """Reverses the most recent rating. Body: {user_id, rating_id?}

    Only the most recent rating is exactly reversible - that is the
    mis-tap case the undo affordance exists for. The engine restores the
    level, thresholds, growth fit and camera history it had beforehand,
    and the persisted row is deleted so it leaves the calibration set too.
    """
    body = _parse_body(schemas.AlgaeRatingUndo, user_id)
    user_id = body.user_id
    rating_id = body.rating_id
    _require_pond(user_id)

    _, ctx = _environment_for(user_id)

    def mutate(twin):
        ok = twin.undo_severity_rating(rating_id)
        if not ok:
            return {"undone": False}
        assessments = _recompute_and_push(user_id, twin, ctx, "rating_undo")
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


@route(bp, "GET", "/v1/ponds/<int:user_id>/ratings/algae",
    summary="Algae rating history and calibration", tag="ratings", response=res.AlgaeRatingContext,
       errors=POND_ERRORS)
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
# Device health and data confidence (devices, migration 0018)
# ===================================================================
# last_seen_at is when the backend last received anything from a device;
# each channel's last sample time comes from the engine snapshot. The
# confidence added to every assessment returned is worked out at request
# time from the same two, so it drops while a node is silent even though
# the stored assessment does not change (koi/models/device_health.py).

def _with_confidence(user_id: int, assessments: dict[str, Optional[dict]], twin=None,
                     now: Optional[datetime] = None) -> None:
    """Adds data_confidence to each assessment present, from the twin's
    channels when given, else from the stored snapshot. Storage failures
    leave the confidence worked out from what could be read."""
    now = now or schemas.utc_now()
    snapshot: Optional[dict]
    if twin is not None:
        snapshot = {"sensor_inputs": {"channels": twin.sensor_channels},
                    "chemistry": twin.chemistry.to_snapshot()}
    else:
        snapshot = fail_soft(lambda: _storage().load_engine_snapshot(user_id), None)
    activity = fail_soft(lambda: _storage().fetch_device_activity(user_id, now), None) or {}
    interval = device_health.sensor_interval(activity, _registry().sensor_ingest.cadence)
    channels = device_health.snapshot_channels(snapshot)
    stale = device_health.snapshot_stale_flags(snapshot)
    for domain, assessment in assessments.items():
        if isinstance(assessment, dict):
            assessment["data_confidence"] = device_health.assess_confidence(domain, channels, stale, interval, now)


@route(bp, "GET", "/v1/ponds/<int:user_id>/devices", summary="Sensor node and camera health", tag="pond",
       response=res.DevicesResponse, errors=POND_ERRORS)
def get_devices(user_id):
    """For the sensor node and the camera: when each was last seen
    (receipt time), its expected interval, contacts received and missed
    in the last 24 hours, and its status. For the sensor node also the
    battery trend from battery_mv readings, the last reset reason, each
    channel's last usable sample time and how far those times can be
    trusted. Reads only."""
    _require_pond(user_id)
    now = schemas.utc_now()
    activity = _storage().fetch_device_activity(user_id, now - device_health.WINDOW)
    snapshot = fail_soft(lambda: _storage().load_engine_snapshot(user_id), None)
    devices = device_health.summarize_devices(
        activity, device_health.snapshot_channels(snapshot), device_health.snapshot_stale_flags(snapshot),
        _registry().sensor_ingest.cadence, now)
    return jsonify({"pond_id": user_id, "generated_at": now.isoformat(), "devices": devices}), 200


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


@route(bp, "GET", "/v1/ponds/<int:user_id>/assessments/chemistry",
    summary="Latest water chemistry assessment", tag="assessments", response=res.WaterChemistryAssessment,
       errors=POND_ERRORS)
def get_latest_assessment(user_id):
    """Most recent chemistry assessment, whether produced by a poll tick
    or by an event endpoint's immediate re-assessment."""
    assessment = _storage().fetch_latest_evaluation(user_id)
    if assessment is None:
        _no_assessment(user_id, "No water chemistry assessment yet. This appears after the first poll cycle "
                                "or the first logged event.")
    _with_confidence(user_id, {"chemistry": assessment})
    return jsonify(assessment), 200


@route(bp, "GET", "/v1/ponds/<int:user_id>/assessments/evaporation",
       summary="Latest evaporation assessment", tag="assessments",
       response=res.EvaporationAssessment, errors=POND_ERRORS)
def get_latest_evaporation_assessment(user_id):
    """Most recent evaporation and feed assessment."""
    assessment = fail_soft(lambda: _storage().fetch_latest_evaporation_evaluation(user_id), None)
    if assessment is None:
        _no_assessment(user_id, "No evaporation assessment yet. This appears after the first "
                                "poll cycle, or once pond_evaporation_evaluations exists "
                                "(apply supabase/migrations/0001_baseline.sql).")
    _with_confidence(user_id, {"evaporation": assessment})
    return jsonify(assessment), 200


@route(bp, "GET", "/v1/ponds/<int:user_id>/assessments/algae", summary="Latest algae assessment",
    tag="assessments", response=res.AlgaeAssessment, errors=POND_ERRORS)
def get_latest_algae_assessment(user_id):
    """Most recent algae assessment."""
    assessment = fail_soft(lambda: _storage().fetch_latest_algae_evaluation(user_id), None)
    if assessment is None:
        _no_assessment(user_id, "No algae assessment yet. Needs at least one clean ESP32-CAM "
                                "frame in imageTable, and pond_algae_evaluations to exist "
                                "(apply supabase/migrations/0001_baseline.sql).")
    _with_confidence(user_id, {"algae": assessment})
    return jsonify(assessment), 200


@route(bp, "GET", "/v1/ponds/<int:user_id>/assessments",
    summary="All three latest assessments and the hypoxia flag", tag="assessments",
       response=res.AllAssessments, errors=POND_ERRORS)
def get_all_assessments(user_id):
    """All three cached assessments in one call - lets the dashboard
    populate every outcome card and its alert badge from a single
    request instead of three - plus the night-time hypoxia flag."""
    assessments = {
        "chemistry": _storage().fetch_latest_evaluation(user_id),
        "evaporation": fail_soft(lambda: _storage().fetch_latest_evaporation_evaluation(user_id), None),
        "algae": fail_soft(lambda: _storage().fetch_latest_algae_evaluation(user_id), None),
    }
    if all(a is None for a in assessments.values()):
        _require_pond(user_id)
    _with_confidence(user_id, assessments)
    assessments["hypoxia"] = _hypoxia_now(user_id, assessments["algae"])
    return jsonify(assessments), 200


def _hypoxia_now(user_id: int, algae_assessment: Optional[dict]) -> dict:
    """The night-time hypoxia flag (koi/models/hypoxia.py) from the
    latest sensor reading, the profile in force now and the cached algae
    assessment: the inputs the poller uses, read fresh. A missing reading
    gives level "unknown" rather than an error."""
    payload = fail_soft(lambda: _storage().fetch_dashboard_payload(user_id), None) or {}
    raw = payload.get("raw_sensor")
    raw = raw if isinstance(raw, dict) else {}

    def number(key: str) -> Optional[float]:
        try:
            return float(raw[key]) if raw.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    profiles = _profiles_for(user_id)
    return assess_hypoxia(
        lux=number("LUX"),
        water_temp_c=number("temp"),
        aeration=profiles.at(datetime.now(timezone.utc)).aeration if profiles is not None else None,
        algae_high=algae_is_high(algae_assessment),
        thresholds=_registry().hypoxia_thresholds,
    ).to_dict()


# ===================================================================
# Forecast reads (detail graph screens) - live, read-only
# ===================================================================

def _provenance(cutoff: Optional[datetime]) -> dict:
    """model_version and input_cutoff of a live forecast, as on the
    evaluation rows (koi/provenance.py)."""
    return {"model_version": model_version(), "input_cutoff": cutoff.isoformat() if cutoff else None}


@route(bp, "GET", "/v1/ponds/<int:user_id>/forecasts/chemistry", summary="Water chemistry lookahead",
    tag="forecasts", query=schemas.ForecastQuery,
       response=res.WaterChemistryForecast, errors=(400, 422, *POND_ERRORS))
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
        return uncertainty.project(twin.chemistry, "chemistry",
            avg_daily_tan_mg=avg_daily_tan_mg,
            daily_temp_forecast_c=daily["temp_c"],
            daily_lux_forecast=daily_lux_forecast,
            daily_rain=daily["rain"],
            fallback_temp_c=temp_stats["avg"],
            fallback_lux=fallback_lux,
            horizon_days=horizon_days,
        ), twin.input_cutoff()

    result, cutoff = _registry().with_twin(
        user_id, run, profiles=_profiles_for(user_id), persist=False,
        forecast_key=("chemistry", horizon_days)
    )
    result.update(_provenance(cutoff))
    result["avg_daily_tan_mg"] = avg_daily_tan_mg
    result["temp_baseline"] = temp_stats
    result["lux_baseline"] = lux_stats
    return jsonify(result), 200


@route(bp, "GET", "/v1/ponds/<int:user_id>/forecasts/evaporation",
    summary="Evaporation and feed lookahead", tag="forecasts", query=schemas.EvaporationForecastQuery,
       response=res.EvaporationForecast, errors=(400, *POND_ERRORS))
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
            return uncertainty.project(twin.evaporation, "evaporation",
                assumed_depth=query.depth_m is None and (
                    twin.profiles is None or twin.profiles.at(datetime.now(timezone.utc)).depth_m is None),
                daily_environment=env, horizon_days=query.horizon_days
            ), twin.input_cutoff(), list(twin.chemistry._events), twin.chemistry.config.time_zone
        finally:
            twin.evaporation.config = stored

    result, cutoff, events, time_zone = _registry().with_twin(
        user_id,
        run,
        profiles=_profiles_for(user_id),
        persist=False,
        forecast_key=("evaporation", query.horizon_days, query.depth_m),
    )
    result.update(_provenance(cutoff))

    # Grounding cross-check against the measured TDS trend - two
    # independent sources, so agreement is real corroboration.
    tds_series = _storage().fetch_daily_sensor_series(user_id, "TDS", days=30) or \
        _storage().fetch_daily_sensor_series(user_id, "tds", days=30)
    from koi.models.event_ledger import event_from_row

    stored_events = [event_from_row(r) for r in _storage().fetch_interventions(user_id, None)]
    events.extend(e for e in stored_events if e is not None)
    tds_series = forecast_utils.tds_series_after_events(tds_series, events, time_zone)
    observed_slope = forecast_utils.slope_per_day(tds_series)
    current_tds = ctx["now"].get("tds")
    result["tds_cross_check"] = ev.cross_check_against_tds(
        predicted_daily_loss_litres=result["avg_loss_litres_per_day"],
        volume_litres=result["volume_litres"],
        observed_tds_slope_ppm_per_day=observed_slope,
        current_tds_ppm=float(current_tds) if current_tds is not None else None,
    )
    return jsonify(result), 200


@route(bp, "GET", "/v1/ponds/<int:user_id>/forecasts/algae", summary="Algae lookahead", tag="forecasts",
    query=schemas.ForecastQuery, response=res.AlgaeForecast,
       errors=(400, 422, *POND_ERRORS))
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

        no3_scenarios = [[], [], []]
        if avg_tan is not None:
            runs = uncertainty.scenarios(
                twin.chemistry, "chemistry", avg_daily_tan_mg=avg_tan,
                fallback_temp_c=baseline_temp, horizon_days=horizon_days,
            )
            no3_scenarios = [[d["no3_ppm"] for d in r["trajectory"]] for r in runs]
        no3_series = no3_scenarios[1]
        no3_now = no3_series[0] if no3_series else None

        if assimilated:
            # ROUND 3 FIX: reuse the history list ingest_camera_samples()
            # just built rather than having refit_growth_rate re-parse the
            # whole camera history a second time for this same request.
            twin.algae.refit_growth_rate(
                baseline_lux, baseline_temp, no3_now,
                history_samples=twin.algae._last_history_samples,
            )

        environments = [_algae_env(ctx, baseline_lux, baseline_temp, series, series[0] if series else None)
                        for series in no3_scenarios]
        env = environments[1]
        projection = uncertainty.project(twin.algae, "algae",
            include_scrub_benefit=True, scenario_environments=environments,
            daily_environment=env, horizon_days=horizon_days
        )
        return projection, assimilated, twin.input_cutoff()

    # persist=True here: unlike the other forecast endpoints this one can
    # genuinely mutate state, because it assimilates any camera frames
    # that landed since the last poll. Those must be durable.
    result, assimilated, cutoff = _registry().with_twin(
        user_id, run, profiles=_profiles_for(user_id), persist=True,
        forecast_key=("algae", horizon_days)
    )

    if "error" in result:
        raise ApiError(result.get("detail") or result["error"], status=422, code=result["error"])
    result.update(_provenance(cutoff))

    result["assimilated_camera_frames"] = assimilated
    image_rows = _storage().fetch_image_history(user_id, limit=1)
    result["latest_image_url"] = image_rows[0].get("imageURL") if image_rows else None
    return jsonify(result), 200


# ===================================================================
# Dashboard (one call for the dashboard screen)
# ===================================================================

@route(bp, "GET", "/v1/ponds/<int:user_id>/dashboard", summary="Everything the dashboard screen shows",
       tag="pond", response=res.Dashboard, errors=POND_ERRORS)
def get_dashboard(user_id):
    """Latest reading of each channel with its own times, the three
    latest assessments and the hypoxia flag, next actions (empty for
    now), weather now from the pond's assigned stations, and the 2-hour,
    24-hour and 4-day forecasts. Reads only: no engine runs and nothing
    is written. Field rules: docs/api/README.md."""
    sources = _storage().fetch_dashboard_sources(user_id)
    if not sources.get("pond_exists"):
        _require_pond(user_id)
    profiles = _profiles_for(user_id)
    now = schemas.utc_now()
    action_payload = _lead_time_actions(user_id, sources, now)
    assessments = {
        "chemistry": _storage().fetch_latest_evaluation(user_id),
        "evaporation": fail_soft(lambda: _storage().fetch_latest_evaporation_evaluation(user_id), None),
        "algae": fail_soft(lambda: _storage().fetch_latest_algae_evaluation(user_id), None),
    }
    _with_confidence(user_id, assessments, now=now)
    dashboard = build_dashboard(
        pond_id=user_id,
        sources=sources,
        **assessments,
        aeration=profiles.at(now).aeration if profiles is not None else None,
        hypoxia_thresholds=_registry().hypoxia_thresholds,
        now=now,
        next_actions=action_payload["actions"],
    )
    return jsonify(dashboard.model_dump(mode="json")), 200


def _lead_time_actions(user_id: int, sources: dict, now: datetime) -> dict:
    """Evaluate using only retained weather visible at this decision instant."""
    slots = sources.get("stations") or {}
    rainfall_station = slots.get("rainfall") if isinstance(slots, dict) else None
    day = local_date(now) - timedelta(days=1)
    start, end = local_day_window(day)
    rain = (_storage().fetch_rainfall_total(rainfall_station, start, end, as_of=now)
            if rainfall_station else None)
    nowcast_slot = slots.get("two-hr-forecast") if isinstance(slots, dict) else None
    nowcast_row = (_storage().fetch_forecast_as_of("2hr", nowcast_slot, now, valid_at=now)
                   if nowcast_slot else None)
    nowcast_data = (nowcast_row or {}).get("payload") or {}
    nowcast_value = nowcast_data.get("forecast") if isinstance(nowcast_data, dict) else None
    nowcast_text = (nowcast_value.get("text") if isinstance(nowcast_value, dict) else nowcast_value)
    if not isinstance(nowcast_text, str):
        nowcast_text = None
    # The current NEA realtime feed is sub-daily and does not provide the
    # notebook's daily network Tmax or pooled 24-hour period rank. Preserve
    # these as unassessed until their matching retained series is available.
    evaluated = ladder.actions(decision_time=now, rain=rain, nowcast_text=nowcast_text,
                               observed_tmax_c=None, temperature_regime=None, outlook_high_c=None,
                               pooled_rank=None, dry_band_max=None)
    return evaluated


@route(bp, "GET", "/v1/ponds/<int:user_id>/actions", summary="Lead-time weather actions",
       tag="forecasts", response=LeadTimeActions, errors=POND_ERRORS)
def get_actions(user_id: int):
    """Evaluate weather actions from retained observations and forecasts as of now."""
    _require_pond(user_id)
    now = schemas.utc_now()
    return jsonify(_lead_time_actions(user_id, _storage().fetch_dashboard_sources(user_id), now))


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


@route(bp, "GET", "/v1/ponds/<int:user_id>/profile", summary="Pond profile", tag="pond",
       response=res.PondProfileResponse, errors=POND_ERRORS)
def get_pond_profile(user_id):
    """The profile in force now and every stored profile row, oldest
    first. source is "userdata" for a pond with no profile rows yet,
    whose profile is its onboarding volume and biomass."""
    return jsonify(_profile_response(user_id)), 200


@route(bp, "PUT", "/v1/ponds/<int:user_id>/profile", summary="Add a pond profile row", tag="pond",
       body=schemas.ProfileUpdate, response=res.PondProfileResponse, status=201, errors=(400, 409, *POND_ERRORS))
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
# Camera water mask (camera_config, migration 0010)
# ===================================================================
# The camera service computes the green ratio inside this polygon only.
# Each PUT is a new mask_version; earlier versions are kept, so a stored
# frame's mask_version still names the polygon it was analysed over.
# The camera restarts its smoothed baseline on the first frame with the
# new version (koi/camera/camera.py).
#
# Named regions (issue #91, migration 0023) are saved in the same version
# as the mask, so changing a region also restarts the baseline. A PUT that
# leaves regions out keeps the regions in force (so a mask-only editor
# does not clear them); regions: null removes them. polygon: null means no
# mask (the whole frame for the green ratio) and needs regions in the body.

def _camera_mask_response(user_id: int) -> dict:
    config = _storage().fetch_camera_mask(user_id)
    return {
        "pond_id": user_id,
        "mask": config["mask"] if config else None,
        "regions": config.get("regions") if config else None,
        "mask_version": config["mask_version"] if config else None,
        "updated_at": config["updated_at"] if config else None,
        "versions": _storage().fetch_camera_mask_versions(user_id),
    }


@route(bp, "GET", "/v1/ponds/<int:user_id>/camera/mask", summary="Camera water mask", tag="pond",
       response=res.CameraMaskResponse, errors=(401, 403, 503))
def get_camera_mask(user_id):
    """The mask in force and every saved version, oldest first. mask is
    null when none has been saved: the whole frame is analysed."""
    return jsonify(_camera_mask_response(user_id)), 200


@route(bp, "PUT", "/v1/ponds/<int:user_id>/camera/mask", summary="Save a new camera water mask", tag="pond",
       body=schemas.CameraMaskUpdate, response=res.CameraMaskResponse, errors=(400, 401, 403, 503))
def put_camera_mask(user_id):
    """Saves the body's polygon, and its regions or the ones in force,
    as the pond's next mask version."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise ApiError("The request body must be a JSON object.", code="invalid_body")
    update = schemas.CameraMaskUpdate.model_validate(body)
    regions = update.regions
    if "regions" not in update.model_fields_set:
        current = _storage().fetch_camera_mask(user_id)
        regions = current.get("regions") if current else None
    saved = _storage().save_camera_mask(user_id, update.polygon, regions)
    log_event(log, "camera_mask_saved", pond_id=user_id, mask_version=saved["mask_version"],
              points=len(update.polygon) if update.polygon is not None else 0,
              regions=sorted(regions) if regions else [])
    return jsonify(_camera_mask_response(user_id)), 200


# ===================================================================

@route(bp, "GET", "/v1/health", alias="/health", summary="Liveness", tag="service", response=res.Health,
       auth=False)
def health():
    """Liveness: the process is up and answering. Checks nothing else."""
    return jsonify({"status": "ok", "domains": ["chemistry", "evaporation", "algae"]}), 200


@route(bp, "GET", "/v1/ready", alias="/ready", summary="Readiness", tag="service", response=res.Ready,
       auth=False, errors=(503,))
def ready():
    """Readiness: storage answers and the poller succeeded recently
    (koi/api/health.py). 503 with the error envelope when not."""
    is_ready, report = readiness(_storage(), current_app.config["KOI_SETTINGS"])
    if is_ready:
        return jsonify({"status": "ready", **report}), 200
    return error_response(503, "not_ready", "The service is not ready. " + " ".join(report["reasons"]), report)

