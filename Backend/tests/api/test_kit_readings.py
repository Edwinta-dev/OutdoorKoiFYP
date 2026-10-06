"""Issue #30: authenticated kit entry, immutable comparisons and validation."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, add_upload, api_client, make_settings, make_storage
from koi.api import create_app, schemas
from koi.kit_readings import ANALYTES
from koi.models.pond_twin import PondTwin
from koi.registry import EngineRegistry
from koi.worker import poller

AT = datetime(2026, 8, 20, tzinfo=timezone.utc)
PATH = f"/v1/ponds/{USER}/kit-readings"


@pytest.fixture
def app():
    app = create_app(make_settings(), storage=make_storage())
    app.config["TESTING"] = True
    return app


def evaluation(storage, at=AT, **values):
    return storage.add_rows("pond_chemistry_evaluations", [{
        "userid": USER, "evaluated_at": at.isoformat(), "tan_ppm": 0.5, "no2_ppm": 0.2,
        "no3_ppm": 10, "ph_reactivity": 0.7, **values}])[0]


def test_kit_records_exact_evaluation_and_does_not_change_model(app):
    storage = app.extensions["koi_storage"]
    row = evaluation(storage)
    before = {table: storage.rows(table) for table in storage._tables}
    client = api_client(app)
    response = client.post(PATH, json={"taken_at": AT.isoformat(), "kit": "TAN liquid kit",
                                      "ammonia_mg_l": 0.3, "nitrite_mg_l": 0, "nitrate_mg_l": 12,
                                      "ph": 7.5, "kh_dkh": 4, "notes": "Before feeding"})
    assert response.status_code == 201
    result = response.get_json()
    assert result["comparison"]["source"] == "evaluation"
    assert result["comparison"]["evaluation_id"] == row["id"]
    assert result["comparison"]["model_version"] is None  # legacy evaluation
    assert result["estimates"] == dict(zip(ANALYTES, (0.5, 0.2, 10, None, None), strict=True))
    assert result["differences"]["ammonia_mg_l"] == pytest.approx(0.2)
    assert result["differences"]["nitrate_mg_l"] == -2
    assert result["differences"]["ph"] is None
    assert client.get(PATH).get_json()["readings"] == [result]
    for table, rows in before.items():
        if table != "kit_readings":
            assert storage.rows(table) == rows
    evaluation(storage, tan_ppm=9)
    assert client.get(PATH).get_json()["readings"][0] == result


def test_kit_validation_statistics_missing_values_and_zero(app):
    storage = app.extensions["koi_storage"]
    client = api_client(app)
    assert all(a["count"] == 0 and a["mean_error"] is None and a["mean_absolute_error"] is None
               and a["pairs"] == [] for a in client.get(f"/v1/ponds/{USER}/validation").get_json()["analytes"].values())
    for index, (model, measured) in enumerate(((0, 1), (3, 0))):
        at = AT + timedelta(minutes=index)
        evaluation(storage, at, tan_ppm=model)
        assert client.post(PATH, json={"taken_at": at.isoformat(), "ammonia_mg_l": measured}).status_code == 201
    assert client.post(PATH, json={"ph": 7.5, "kh_dkh": 4}).status_code == 201
    data = client.get(f"/v1/ponds/{USER}/validation").get_json()["analytes"]
    assert data["ammonia_mg_l"]["count"] == 2
    assert data["ammonia_mg_l"]["mean_error"] == 1
    assert data["ammonia_mg_l"]["mean_absolute_error"] == 2
    assert [p["error"] for p in data["ammonia_mg_l"]["pairs"]] == [-1, 3]
    assert data["nitrite_mg_l"]["count"] == data["ph"]["count"] == data["kh_dkh"]["count"] == 0


def test_kit_nullable_reading_no_time_and_legacy_comparisons(app):
    client = api_client(app)
    result = client.post(PATH, json={}).get_json()
    assert result["taken_at"] is None
    assert result["comparison"]["source"] == "unavailable"
    assert all(v is None for v in result["estimates"].values())
    storage = app.extensions["koi_storage"]
    storage.add_rows("kit_readings", [{"pond": USER, "ph": 7}])
    assert client.get(PATH).get_json()["readings"][-1]["comparison"] is None


@pytest.mark.parametrize("body", [
    {"ammonia_mg_l": -1}, {"nitrite_mg_l": "1"}, {"nitrate_mg_l": True}, {"ph": 15}, {"kh_dkh": -1},
    {"ammonia_mg_l": float("nan")}, {"nitrite_mg_l": float("inf")}, {"ph": float("-inf")},
    {"taken_at": "bad"}, {"taken_at": "2026-08-20T00:00:00"}, {"taken_at": 123},
    {"taken_at": "2999-01-01T00:00:00Z"}, {"kit": 123}, {"notes": "x" * 1001}, {"user_id": USER + 1},
])
def test_kit_invalid_input(app, body):
    assert api_client(app).post(PATH, json=body).status_code == 400
    assert app.extensions["koi_storage"].rows("kit_readings") == []


@pytest.mark.parametrize("method,suffix", [("post", "kit-readings"), ("get", "kit-readings"), ("get", "validation")])
def test_kit_auth_and_pond_isolation(app, method, suffix):
    assert getattr(app.test_client(), method)(f"/v1/ponds/{USER}/{suffix}").status_code == 401
    assert getattr(api_client(app), method)(f"/v1/ponds/{USER + 1}/{suffix}").status_code == 403


@pytest.mark.parametrize("suffix", ["kit-readings", "validation"])
def test_kit_conditional_get(app, suffix):
    client = api_client(app)
    path = f"/v1/ponds/{USER}/{suffix}"
    response = client.get(path)
    assert client.get(path, headers={"If-None-Match": response.headers["ETag"]}).status_code == 304


def test_kit_historical_rebuild_ignores_later_evaluations_and_preserves_state(app, monkeypatch):
    storage = app.extensions["koi_storage"]
    settings = make_settings()
    add_upload(storage, {"pH": 7.4, "temp": 28, "TDS": 180, "LUX": 10000}, AT)
    storage.add_rows("pondInterventions", [{"userID": USER, "event_type": "FEEDING", "food_grams": 30,
                                          "protein_percentage": 40, "event_timestamp": AT.isoformat(),
                                          "created_at": AT.isoformat()}])
    registry = EngineRegistry(storage, settings.hypoxia_thresholds, settings.sensor_ingest)
    poller._poll_user(registry, USER, {**storage.fetch_pond_config(USER), "user_id": USER}, now=AT)
    expected = PondTwin.from_snapshot(storage.load_engine_snapshot(USER)).chemistry.assess(rain_incoming=False).tan_ppm
    # Later evaluations/snapshot must not be used for a backdated kit result.
    evaluation(storage, AT + timedelta(days=1), tan_ppm=999)
    snapshot_before = storage.load_engine_state(USER)
    evaluations_before = storage.rows("pond_chemistry_evaluations")
    # Force rebuild by removing the exact evaluation only.
    storage._tables["pond_chemistry_evaluations"] = [r for r in evaluations_before
                                                    if r["evaluated_at"] != AT.isoformat()]
    remaining = storage.rows("pond_chemistry_evaluations")
    monkeypatch.setattr(schemas, "utc_now", lambda: AT + timedelta(days=2))
    result = api_client(app).post(PATH, json={"taken_at": "2026-08-20T08:00:00+08:00",
                                            "ammonia_mg_l": 0.2}).get_json()
    assert result["comparison"]["source"] == "rebuild"
    assert result["estimates"]["ammonia_mg_l"] == pytest.approx(expected)
    assert result["estimates"]["ammonia_mg_l"] > 0
    assert result["comparison"]["rebuild_weather"]["status"] == "incomplete"
    assert storage.load_engine_state(USER) == snapshot_before
    assert storage.rows("pond_chemistry_evaluations") == remaining


def test_kit_without_replayable_sensors_keeps_unknown_estimates(app):
    evaluation(app.extensions["koi_storage"], AT + timedelta(days=1), tan_ppm=999)
    response = api_client(app).post(PATH, json={"taken_at": AT.isoformat(), "ammonia_mg_l": 1})
    assert response.status_code == 201
    assert response.get_json()["comparison"]["source"] == "unavailable"
    assert response.get_json()["estimates"]["ammonia_mg_l"] is None


@pytest.mark.parametrize("operation", ["insert_kit_reading", "fetch_chemistry_evaluation_at", "fetch_kit_readings"])
def test_kit_storage_errors_are_not_silently_successful(app, operation):
    app.extensions["koi_storage"].failing.add(operation)
    client = api_client(app)
    response = (client.get(PATH) if operation == "fetch_kit_readings"
                else client.post(PATH, json={"taken_at": AT.isoformat()}))
    assert response.status_code == 503
