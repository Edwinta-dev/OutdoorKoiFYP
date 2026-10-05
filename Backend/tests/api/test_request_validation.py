"""Request validation for every digital twin endpoint: for each one, a
missing field, a wrong type, an out-of-range value and an unknown pond,
each returned in the koi.errors envelope.

Run with the error-format tests: python -m pytest -q -k "validation or errors"
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app

UNKNOWN = 999  # no UserData row and no snapshot in the fixture storage


@pytest.fixture
def client():
    app = create_app(make_settings(), storage=make_storage())
    app.config["TESTING"] = True
    return api_client(app)


@pytest.fixture
def open_client():
    """Auth off (development only). With auth on, a pond the account is
    not linked to is 403 before the pond is looked up (tests/api/test_auth.py),
    so the unknown-pond 404 is only reachable this way."""
    app = create_app(make_settings(env="development", auth="disabled"), storage=make_storage())
    app.config["TESTING"] = True
    return app.test_client()


def assert_envelope(resp, status, code):
    assert resp.status_code == status, resp.get_json()
    body = resp.get_json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message", "details"}, body
    assert body["error"]["code"] == code, body
    assert body["error"]["message"], body
    return body["error"]


def invalid_fields(resp):
    error = assert_envelope(resp, 400, "validation_failed")
    return [f["field"] for f in error["details"]["fields"]]


FEED = {"user_id": USER, "food_grams": 100.0, "protein_percent": 40.0}
VOLUME = {"user_id": USER, "volume_percent": 20.0}
SCRUB = {"user_id": USER, "scrub_type": "brush"}
RATING = {"user_id": USER, "severity": "minor"}
UNDO = {"user_id": USER}


def without(body, key):
    return {k: v for k, v in body.items() if k != key}


# (endpoint, body, field reported invalid; None for a rule across fields)
BODY_CASES = [
    # missing field
    ("/events/feeding", without(FEED, "food_grams"), "food_grams"),
    ("/events/feeding", without(FEED, "user_id"), "user_id"),
    ("/events/water-change", {"user_id": USER}, None),
    ("/events/top-up", {"user_id": USER}, None),
    ("/events/algal-scrub", without(SCRUB, "user_id"), "user_id"),
    ("/events/algae-rating", without(RATING, "severity"), "severity"),
    ("/events/algae-rating/undo", {}, "user_id"),
    # wrong type
    ("/events/feeding", {**FEED, "food_grams": "lots"}, "food_grams"),
    ("/events/feeding", {**FEED, "user_id": "455"}, "user_id"),
    ("/events/water-change", {**VOLUME, "volume_percent": "half"}, "volume_percent"),
    ("/events/top-up", {**VOLUME, "volume_litres": [5]}, "volume_litres"),
    ("/events/algal-scrub", {**SCRUB, "scrub_type": 5}, "scrub_type"),
    ("/events/algal-scrub", {**SCRUB, "timestamp": 1755680000}, "timestamp"),
    ("/events/algae-rating", {**RATING, "image_id": "latest"}, "image_id"),
    ("/events/algae-rating/undo", {**UNDO, "rating_id": "x"}, "rating_id"),
    # out of range
    ("/events/feeding", {**FEED, "protein_percent": 400.0}, "protein_percent"),
    ("/events/feeding", {**FEED, "food_grams": -1.0}, "food_grams"),
    ("/events/feeding", {**FEED, "user_id": 0}, "user_id"),
    ("/events/water-change", {**VOLUME, "volume_percent": 150.0}, "volume_percent"),
    ("/events/top-up", {**VOLUME, "volume_percent": 0.0}, "volume_percent"),
    ("/events/algal-scrub", {**SCRUB, "scrub_type": ""}, "scrub_type"),
    ("/events/algae-rating", {**RATING, "severity": "apocalyptic"}, "severity"),
    ("/events/algae-rating", {**RATING, "green_ratio": 1.5}, "green_ratio"),
    ("/events/algae-rating/undo", {**UNDO, "rating_id": 0}, "rating_id"),
]


@pytest.mark.parametrize("path, body, field", BODY_CASES)
def test_body_validation_names_the_invalid_field(client, path, body, field):
    assert field in invalid_fields(client.post(path, json=body))


@pytest.mark.parametrize("path, body", [
    ("/events/feeding", FEED), ("/events/water-change", VOLUME), ("/events/top-up", VOLUME),
    ("/events/algal-scrub", SCRUB), ("/events/algae-rating", RATING), ("/events/algae-rating/undo", UNDO),
])
def test_body_validation_unknown_pond_is_404(open_client, path, body):
    error = assert_envelope(open_client.post(path, json={**body, "user_id": UNKNOWN}), 404, "pond_not_configured")
    assert error["details"] == {"user_id": UNKNOWN}


def test_body_validation_unknown_pond_stores_no_rating(open_client):
    storage = open_client.application.extensions["koi_storage"]
    open_client.post("/events/algae-rating", json={**RATING, "user_id": UNKNOWN})
    assert storage.rows("algae_severity_ratings") == []


@pytest.mark.parametrize("path", ["/events/feeding", "/events/algae-rating"])
@pytest.mark.parametrize("data", [b"not json", b"[1, 2]", b""])
def test_body_validation_rejects_a_body_that_is_not_a_json_object(client, path, data):
    assert_envelope(client.post(path, data=data, content_type="application/json"), 400, "invalid_body")


def test_body_validation_reports_every_invalid_field_at_once(client):
    resp = client.post("/events/feeding", json={"user_id": USER, "food_grams": "lots"})
    assert sorted(invalid_fields(resp)) == ["food_grams", "protein_percent"]
    assert resp.get_json()["error"]["message"] == "The request has 2 invalid fields."


def test_body_validation_ignores_unknown_keys(client):
    resp = client.post("/events/top-up", json={**VOLUME, "app_version": "1.4.0"})
    assert resp.status_code == 200


# --- timestamps -------------------------------------------------------

def iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


@pytest.mark.parametrize("timestamp, message", [
    ("yesterday", "not an ISO 8601"),
    ("2026-13-45T00:00:00Z", "not an ISO 8601"),
    (iso(-timedelta(days=30, minutes=1)), "more than 30 days in the past"),
    (iso(timedelta(minutes=6)), "more than 5 minutes in the future"),
])
def test_timestamp_validation_rejects_instead_of_falling_back(client, timestamp, message):
    resp = client.post("/events/feeding", json={**FEED, "timestamp": timestamp})
    [field] = resp.get_json()["error"]["details"]["fields"]
    assert (resp.status_code, field["field"]) == (400, "timestamp")
    assert message in field["message"]


@pytest.mark.parametrize("timestamp", [
    iso(-timedelta(days=29, hours=23)),
    iso(timedelta(minutes=4)),
    (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None).isoformat(),  # no zone: UTC
])
def test_timestamp_validation_accepts_the_window(client, timestamp):
    assert client.post("/events/algal-scrub", json={**SCRUB, "timestamp": timestamp}).status_code == 200


def test_timestamp_validation_uses_the_given_time():
    from koi.api import schemas

    ts = datetime.now(timezone.utc) - timedelta(days=2)
    body = schemas.FeedingEvent.model_validate({**FEED, "timestamp": ts.replace(tzinfo=None).isoformat()})
    assert body.event_time() == ts
    before = datetime.now(timezone.utc)
    assert schemas.FeedingEvent.model_validate(FEED).event_time() >= before


# --- read endpoints: path and query -----------------------------------

READS = ["/ratings/algae/{}", "/assessment/{}", "/assessment/evaporation/{}", "/assessment/algae/{}",
         "/assessment/all/{}", "/forecast/{}", "/forecast/evaporation/{}", "/forecast/algae/{}"]


@pytest.mark.parametrize("path", READS)
def test_read_validation_unknown_pond_is_404(open_client, path):
    assert_envelope(open_client.get(path.format(UNKNOWN)), 404, "pond_not_configured")


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("user_id", ["abc", "-1", ""])
def test_read_validation_bad_or_missing_pond_id_is_404(client, path, user_id):
    assert_envelope(client.get(path.format(user_id)), 404, "not_found")


@pytest.mark.parametrize("path", ["/forecast/{}", "/forecast/evaporation/{}", "/forecast/algae/{}"])
@pytest.mark.parametrize("query, field", [
    ("horizon_days=abc", "horizon_days"),   # wrong type
    ("horizon_days=0", "horizon_days"),     # out of range
    ("horizon_days=61", "horizon_days"),
    ("horizon_days=", "horizon_days"),      # present but empty
])
def test_query_validation_names_the_invalid_parameter(client, path, query, field):
    assert invalid_fields(client.get(path.format(USER) + "?" + query)) == [field]


@pytest.mark.parametrize("query", ["depth_m=deep", "depth_m=0", "depth_m=-1"])
def test_query_validation_checks_pond_depth(client, query):
    assert invalid_fields(client.get(f"/forecast/evaporation/{USER}?{query}")) == ["depth_m"]


def test_query_validation_applies_defaults(client):
    resp = client.get(f"/forecast/evaporation/{USER}")
    assert resp.status_code == 200
    assert len(resp.get_json()["trajectory"]) == 14


def test_http_not_found_uses_the_error_envelope(client):
    assert_envelope(client.get("/route-that-does-not-exist"), 404, "not_found")


def test_unexpected_value_error_is_a_500_envelope(client):
    def broken_route():
        raise ValueError("internal invariant failed")

    client.application.add_url_rule("/test/value-error", view_func=broken_route)
    assert_envelope(client.get("/test/value-error"), 500, "internal_error")
