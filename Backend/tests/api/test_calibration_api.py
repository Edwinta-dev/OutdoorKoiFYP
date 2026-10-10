"""Issue #32: GET /v1/ponds/{pond}/calibration shows the fit in force for
each parameter (fit date, sample count, error) or the default while
pending, plus every stored version. Responses are validated against the
OpenAPI document by conftest."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app
from koi.models import evaporation_engine as ev
from koi.worker import poller

PATH = f"/v1/ponds/{USER}/calibration"
NOW = datetime.now(timezone.utc)


@pytest.fixture
def app():
    app = create_app(make_settings(), storage=make_storage())
    app.config["TESTING"] = True
    return app


def version(parameter, status="fitted", value=0.4, at=NOW - timedelta(days=1), lag=None, **values):
    return {"parameter": parameter, "status": status, "value": value, "lag_hours": lag, "fitted_at": at.isoformat(),
            "effective_from": at.isoformat(), "sample_count": 3, "error": {"unit": "L", "training_rmse": 2.0},
            "training_from": (at - timedelta(days=20)).isoformat(), "training_to": at.isoformat(),
            "evaluation_from": None, "evaluation_to": None, "details": {"default": {}}, "method_version": 1,
            **values}


def test_calibration_api_without_rows_shows_every_default_as_pending(app):
    body = api_client(app).get(PATH).get_json()
    assert body["pond_id"] == USER and body["history"] == []
    params = body["parameters"]
    assert params[poller.SHELTER]["status"] == "pending"
    assert params[poller.SHELTER]["value_in_use"] == ev.POND_SHELTER_FACTOR
    assert (params[poller.TEMPERATURE]["value_in_use"], params[poller.TEMPERATURE]["lag_hours_in_use"]) == (-1.0, 0.0)
    assert params[poller.TEMPERATURE]["unit"] == "degC"
    assert params[poller.NITRIFICATION]["value_in_use"] == 1.0
    assert all(p["in_force"] is None and p["latest"] is None for p in params.values())


def test_calibration_api_shows_the_fit_in_force_and_its_successor(app):
    storage = app.extensions["koi_storage"]
    old = storage.insert_calibration(USER, version(poller.SHELTER, value=0.4))
    future = storage.insert_calibration(USER, version(poller.SHELTER, value=0.5, at=NOW + timedelta(days=1)))
    temp = storage.insert_calibration(USER, version(poller.TEMPERATURE, value=-1.6, lag=3.0, evaluation_from=(
        NOW - timedelta(days=1)).isoformat(), evaluation_to=NOW.isoformat()))
    pending = storage.insert_calibration(USER, version(poller.NITRIFICATION, status="pending", value=None,
                                                       details={"reason": "not_enough_kit_readings"}))
    body = api_client(app).get(PATH).get_json()
    shelter = body["parameters"][poller.SHELTER]
    assert shelter["status"] == "fitted" and shelter["value_in_use"] == 0.4
    assert shelter["in_force"]["id"] == old["id"] and shelter["in_force"]["sample_count"] == 3
    assert shelter["in_force"]["error"]["training_rmse"] == 2.0
    assert shelter["latest"]["id"] == future["id"]
    assert datetime.fromisoformat(shelter["effective_until"]) == datetime.fromisoformat(future["effective_from"])
    temperature = body["parameters"][poller.TEMPERATURE]
    assert (temperature["value_in_use"], temperature["lag_hours_in_use"]) == (-1.6, 3.0)
    assert temperature["in_force"]["id"] == temp["id"] and temperature["effective_until"] is None
    scale = body["parameters"][poller.NITRIFICATION]
    assert scale["status"] == "pending" and scale["value_in_use"] == 1.0
    assert scale["latest"]["id"] == pending["id"]
    assert scale["latest"]["details"]["reason"] == "not_enough_kit_readings"
    assert [r["id"] for r in body["history"]] == [old["id"], temp["id"], pending["id"], future["id"]]


def test_calibration_api_is_the_callers_pond_only(app):
    response = api_client(app).get("/v1/ponds/999/calibration")
    assert response.status_code == 403


def test_calibration_api_storage_failure_is_503(app):
    app.extensions["koi_storage"].failing.add("fetch_calibrations")
    response = api_client(app).get(PATH)
    assert response.status_code == 503
