"""
app.py

Exposes one endpoint per intervention event type. Flutter calls these when
the user logs a feeding session, water change, top-up, or algal scrub.

Each endpoint:
  1. parses + validates the request
  2. mutates that user's engine under its lock (via registry.with_engine)
  3. immediately re-assesses and pushes a fresh evaluation row to Supabase,
     so the UI reflects the just-logged event without waiting for the next
     poll cycle
  4. returns the assessment to the caller too, so Flutter can update
     optimistically without a second round-trip

Auth note: every endpoint below trusts a `user_id` field in the request
body. That is NOT safe for production - anyone who can hit this endpoint
can write chemistry state for any user_id. Before shipping, verify the
caller's Supabase JWT (Flutter already has one from its own Supabase auth
session) and derive user_id from the verified token server-side rather
than trusting the request body. See the write-up for how to wire that in
with minimal change to these handlers.
"""
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_cors import CORS

from engine import PondEvent, EventKind, RawSample, PondConfig
from registry import registry
import state_store

app = Flask(__name__)
CORS(app)


def _parse_time(body: dict) -> datetime:
    ts = body.get("timestamp")
    if ts:
        return datetime.fromisoformat(ts)
    return datetime.now(timezone.utc)


def _default_config_for(user_id: int, body: dict) -> PondConfig | None:
    """Bootstraps a PondConfig for a user with no persisted engine snapshot
    yet. volume/biomass come from UserData (state_store.fetch_pond_config);
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


def _assess_and_push(user_id: int, engine) -> dict:
    assessment = engine.assess(rain_incoming=False)  # event endpoints don't have forecast context handy; poller supplies rain context on its own cycle
    state_store.push_evaluation(user_id, assessment.to_dict())
    return assessment.to_dict()


@app.route("/events/feeding", methods=["POST"])
def log_feeding():
    body = request.get_json(force=True)
    user_id = body["user_id"]
    event = PondEvent(
        kind=EventKind.FEEDING,
        time=_parse_time(body),
        food_grams=float(body["food_grams"]),
        protein_percent=float(body["protein_percent"]),
    )

    def mutate(engine):
        engine.apply_event(event)
        return _assess_and_push(user_id, engine)

    result = registry.with_engine(user_id, mutate, default_config=_default_config_for(user_id, body))
    return jsonify(result), 200


@app.route("/events/water-change", methods=["POST"])
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

    def mutate(engine):
        engine.apply_event(event)
        return _assess_and_push(user_id, engine)

    result = registry.with_engine(user_id, mutate, default_config=_default_config_for(user_id, body))
    return jsonify(result), 200


@app.route("/events/top-up", methods=["POST"])
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

    def mutate(engine):
        engine.apply_event(event)
        return _assess_and_push(user_id, engine)

    result = registry.with_engine(user_id, mutate, default_config=_default_config_for(user_id, body))
    return jsonify(result), 200


@app.route("/events/algal-scrub", methods=["POST"])
def log_algal_scrub():
    body = request.get_json(force=True)
    user_id = body["user_id"]
    event = PondEvent(
        kind=EventKind.ALGAL_SCRUB,
        time=_parse_time(body),
        scrub_type=body.get("scrub_type", "unspecified"),
    )

    def mutate(engine):
        engine.apply_event(event)
        return _assess_and_push(user_id, engine)

    result = registry.with_engine(user_id, mutate, default_config=_default_config_for(user_id, body))
    return jsonify(result), 200


@app.route("/assessment/<int:user_id>", methods=["GET"])
def get_latest_assessment(user_id):
    """Read-only endpoint for the Flutter detail graph screen - returns the
    most recently computed WaterChemistryAssessment for this pond, whether
    it was produced by the poller's last sensor-driven tick or by an event
    endpoint's immediate re-assessment."""
    assessment = state_store.fetch_latest_evaluation(user_id)
    if assessment is None:
        return jsonify({"error": "no assessment yet for this user"}), 404
    return jsonify(assessment), 200


@app.errorhandler(ValueError)
def handle_missing_config(err):
    return jsonify({"error": str(err)}), 404


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    # Single-threaded, single-process for now - see registry.py docstring
    # for why multi-worker deployment needs a different locking strategy
    # before this is safe to scale.
    import poller
    poller.start()
    app.run(host="0.0.0.0", port=8080, threaded=True)
