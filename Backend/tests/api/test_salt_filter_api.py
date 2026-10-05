"""New event routes are validated through the shared OpenAPI contract client."""
import uuid

import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app


@pytest.fixture
def app():
    app = create_app(make_settings(), storage=make_storage())
    app.config["TESTING"] = True
    return app


@pytest.mark.parametrize("name,fields", [("salt", {"salt_grams": 1.0, "notes": "Added"}),
                                         ("filter-clean", {"notes": "Rinsed"}), ("filter-clean", {})])
@pytest.mark.parametrize("include_body_pond", [True, False])
def test_salt_filter_contract_and_duplicate(app, name, fields, include_body_pond):
    path = f"/v1/ponds/{USER}/events/{name}"
    body = {**fields, "event_id": str(uuid.uuid4()), **({"user_id": USER} if include_body_pond else {})}
    client = api_client(app)
    first = client.post(path, json=body)
    assert first.status_code == 200
    assert first.get_json()["event"]["event_id"] == body["event_id"]
    assert first.get_json()["event"]["status"] == "applied"
    assert first.get_json()["event"]["salt_grams"] == fields.get("salt_grams")
    assert first.get_json()["event"]["notes"] == fields.get("notes")
    assert client.post(path, json=body).get_json()["event"]["status"] == "duplicate"


@pytest.mark.parametrize("fields", [{}, {"salt_grams": 0}, {"salt_grams": -1}, {"salt_grams": "1"},
                                    {"salt_grams": True}, {"salt_grams": float("inf")}])
def test_salt_invalid_request(app, fields):
    assert api_client(app).post(f"/v1/ponds/{USER}/events/salt", json=fields).status_code == 400


@pytest.mark.parametrize("notes", [42, "x" * 1001])
def test_filter_invalid_notes(app, notes):
    assert api_client(app).post(f"/v1/ponds/{USER}/events/filter-clean", json={"notes": notes}).status_code == 400


@pytest.mark.parametrize("name,original,retry", [
    ("salt", {"salt_grams": 1, "notes": "Original"}, {"salt_grams": 2, "notes": "Retry"}),
    ("filter-clean", {"notes": "Original"}, {"notes": "Retry"}),
])
def test_salt_filter_duplicate_returns_original_logged_fields(app, name, original, retry):
    client = api_client(app)
    path = f"/v1/ponds/{USER}/events/{name}"
    event_id = str(uuid.uuid4())
    assert client.post(path, json={**original, "event_id": event_id}).status_code == 200
    result = client.post(path, json={**retry, "event_id": event_id}).get_json()["event"]
    assert result["status"] == "duplicate"
    assert result["salt_grams"] == original.get("salt_grams")
    assert result["notes"] == original["notes"]


@pytest.mark.parametrize("source", ["api", "history"])
def test_salt_forecast_cross_check_uses_event_before_worker_reconciles(app, source):
    from datetime import datetime, timezone

    client = api_client(app)
    path = f"/v1/ponds/{USER}/forecasts/evaporation"
    first = client.get(path)
    assert first.status_code == 200
    assert first.get_json()["tds_cross_check"]["observed_tds_slope_ppm_per_day"] is not None
    if source == "api":
        assert client.post(f"/v1/ponds/{USER}/events/salt", json={"salt_grams": 1}).status_code == 200
    else:
        app.extensions["koi_storage"].add_rows("pondInterventions", [{
            "userID": USER, "event_type": "SALT", "salt_grams": 1,
            "event_timestamp": datetime.now(timezone.utc).isoformat()}])
    after = client.get(path)
    assert after.status_code == 200
    assert after.get_json()["tds_cross_check"]["observed_tds_slope_ppm_per_day"] is None
