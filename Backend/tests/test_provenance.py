"""Model provenance on every evaluation row (issue #21, migration 0016,
koi/provenance.py): the model version, the input cutoff, the forecast
issue time and the inputs used, on every push from the poller and the
event endpoints, in the API responses, and null (unknown) on rows
written before the migration.

Run from Backend/: python -m pytest -q -k provenance
"""
from __future__ import annotations

import copy
import re
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import koi
from conftest import (
    ALGAE_ASSESSMENT,
    DASHBOARD_PAYLOAD,
    PROVENANCE,
    USER,
    api_client,
    make_settings,
    make_storage,
)
from koi import provenance
from koi.api import create_app
from koi.models.engine import EventKind, PondConfig, PondEvent
from koi.models.pond_twin import PondTwin
from koi.models.sensor_inputs import SensorInput
from koi.provenance import RunProvenance, forecast_provenance, model_version
from koi.storage import StorageError
from koi.storage.base import PROVENANCE_COLUMNS
from koi.worker import poller

SENSOR_AT = "2026-08-19T23:59:00+00:00"  # the fixture pond's newest upload
POLL_AT = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
ISSUED_2HR = "2026-08-20T00:30:00+00:00"
ISSUED_24HR = "2026-08-19T22:00:00+00:00"
ISSUED_4DAY = "2026-08-19T20:00:00+00:00"
CACHED_AT = "2026-08-20T00:45:00+00:00"
OUTLOOK_SLOTS = ("2026-08-20", "2026-08-21")
TABLES = ("pond_chemistry_evaluations", "pond_evaporation_evaluations", "pond_algae_evaluations")


def _t(value):
    return datetime.fromisoformat(value) if value is not None else None


def forecast_rows(payload):
    """weather_forecasts cache rows holding exactly the payload's data,
    each with its provider issue time."""
    nea = payload["nea_forecasts"]
    rows = [
        {"forecast_type": "2hr", "slot_id": "Bishan", "data": nea["forecast_2hr"], "valid_period": {},
         "updated_at": CACHED_AT, "source_issued_at": ISSUED_2HR},
        {"forecast_type": "24hr", "slot_id": "GENERAL", "data": nea["forecast_24hr"]["general"], "valid_period": {},
         "updated_at": CACHED_AT, "source_issued_at": ISSUED_24HR},
    ]
    rows += [{"forecast_type": "4day", "slot_id": e["slot_id"], "data": e["data"], "valid_period": {},
              "updated_at": CACHED_AT, "source_issued_at": ISSUED_4DAY} for e in nea["outlook_4day"]]
    return rows


def forecast_storage():
    """The fixture pond, its 2-hour area assigned, its payload's outlook
    days keyed by slot, and forecast cache rows matching the payload."""
    storage = make_storage()
    storage._tables["UserData"][0]["ClosestStations"] = {"two-hr-forecast": "Bishan"}
    payload = copy.deepcopy(DASHBOARD_PAYLOAD)
    for entry, slot in zip(payload["nea_forecasts"]["outlook_4day"], OUTLOOK_SLOTS, strict=True):
        entry["slot_id"] = slot
    storage.set_dashboard_payload(USER, payload)
    storage.add_rows("weather_forecasts", forecast_rows(payload))
    return storage


def add_frame(storage, at="2026-08-19T12:00:00+00:00"):
    storage.add_rows("imageTable", [{"user_ID": USER, "created_at": at, "green_ratio": 0.04,
                                     "current_state": ["base", 0.04], "imageURL": "https://example/1.jpg"}])


def poll(storage, now=POLL_AT):
    app = create_app(make_settings(), storage=storage)
    registry = app.extensions["koi_registry"]
    poller._poll_user(registry, USER, storage.fetch_active_pond_configs()[0], now=now)
    return app


# ---------------------------------------------------------------------
# Model version
# ---------------------------------------------------------------------
def test_provenance_package_version_matches_pyproject():
    pyproject = tomllib.loads((Path(koi.__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8"))
    assert koi.__version__ == pyproject["project"]["version"]


def test_provenance_model_version_is_package_version_plus_commit():
    version = model_version()
    assert version == koi.__version__ or re.fullmatch(
        re.escape(koi.__version__) + r"\+g[0-9a-f]{7,40}(\.dirty)?", version), version
    commit = provenance.git_commit()
    assert version == (f"{koi.__version__}+g{commit}" if commit else koi.__version__)


def test_provenance_model_version_without_git_is_package_version(monkeypatch):
    monkeypatch.setattr(provenance, "_git", lambda *args: None)
    assert provenance.git_commit() is None


def test_provenance_git_commit_marks_uncommitted_changes(monkeypatch):
    answers = {"ls-files": "__init__.py\n", "rev-parse": "1a2b3c4d5e6f\n", "status": " M koi/api/routes.py\n"}
    monkeypatch.setattr(provenance, "_git", lambda *args: answers[args[0]])
    assert provenance.git_commit() == "1a2b3c4d5e6f.dirty"
    answers["status"] = ""
    assert provenance.git_commit() == "1a2b3c4d5e6f"
    answers["rev-parse"] = "not a sha\n"
    assert provenance.git_commit() is None


def test_provenance_git_commit_needs_the_package_tracked(monkeypatch):
    """An installed copy (site-packages inside some other checkout) does
    not report that checkout's commit."""
    monkeypatch.setattr(provenance, "_git", lambda *args: None if args[0] == "ls-files" else "1a2b3c4d5e6f\n")
    assert provenance.git_commit() is None


# ---------------------------------------------------------------------
# Forecast records
# ---------------------------------------------------------------------
def test_provenance_forecast_issue_times_come_from_matching_cache_rows():
    storage = forecast_storage()
    found = forecast_provenance(storage.fetch_dashboard_payload(USER), storage.fetch_dashboard_sources(USER))
    assert found["forecast_2hr"] == {"available": True, "issued_at": ISSUED_2HR, "records": [
        {"forecast_type": "2hr", "slot_id": "Bishan", "issued_at": ISSUED_2HR, "cache_updated_at": CACHED_AT}]}
    assert found["forecast_24hr_general"]["issued_at"] == ISSUED_24HR
    assert found["outlook_4day"]["issued_at"] == ISSUED_4DAY
    assert [r["slot_id"] for r in found["outlook_4day"]["records"]] == list(OUTLOOK_SLOTS)


def test_provenance_rewritten_cache_row_gives_unknown_issue_time():
    """A cache row whose data is no longer what the run read says nothing
    about the run's forecast: its issue time is unknown."""
    storage = forecast_storage()
    payload = storage.fetch_dashboard_payload(USER)
    row = next(r for r in storage._tables["weather_forecasts"] if r["slot_id"] == OUTLOOK_SLOTS[1])
    row["data"] = {"day": "Thursday", "forecast": {"code": "TL", "text": "Thundery Showers"}}
    row["source_issued_at"] = "2026-08-20T06:00:00+00:00"
    outlook = forecast_provenance(payload, storage.fetch_dashboard_sources(USER))["outlook_4day"]
    assert [r["issued_at"] for r in outlook["records"]] == [ISSUED_4DAY, None]
    assert outlook["issued_at"] is None


def test_provenance_unreadable_sources_and_missing_products():
    found = forecast_provenance(DASHBOARD_PAYLOAD, None)
    assert found["forecast_2hr"]["available"] is True and found["forecast_2hr"]["issued_at"] is None
    assert all(r["issued_at"] is None for r in found["outlook_4day"]["records"])
    empty = forecast_provenance({}, None)
    assert empty == {p: {"available": False, "records": [], "issued_at": None}
                     for p in ("forecast_2hr", "forecast_24hr_general", "outlook_4day")}


def test_provenance_mixed_products_are_not_collapsed_into_one_time():
    storage = forecast_storage()
    forecasts = forecast_provenance(storage.fetch_dashboard_payload(USER), storage.fetch_dashboard_sources(USER))
    run = RunProvenance(run="poll", input_cutoff=_t(SENSOR_AT), forecasts=forecasts, sensor_groups=1)
    chem = run.values("chemistry")
    assert chem["forecast_issued_at"] is None
    assert {p: f["issued_at"] for p, f in chem["inputs"]["forecasts"].items()} == {
        "forecast_2hr": ISSUED_2HR, "outlook_4day": ISSUED_4DAY}
    assert run.values("evaporation")["forecast_issued_at"] is None
    assert set(run.values("evaporation")["inputs"]["forecasts"]) == {"forecast_24hr_general", "outlook_4day"}
    assert run.values("algae")["forecast_issued_at"] == ISSUED_4DAY
    assert chem["input_cutoff"] == SENSOR_AT and chem["model_version"] == model_version()
    assert {k: chem["inputs"][k] for k in ("run", "sensor_groups", "events", "camera_frames", "ratings")} == {
        "run": "poll", "sensor_groups": 1, "events": 0, "camera_frames": 0, "ratings": 0}


# ---------------------------------------------------------------------
# The twin's input cutoff
# ---------------------------------------------------------------------
def test_provenance_twin_input_cutoff_is_the_newest_input_consumed():
    twin = PondTwin.create(PondConfig(volume_litres=5000.0, estimated_biomass_grams=8000.0))
    assert twin.input_cutoff() is None
    sample = datetime(2026, 8, 19, 23, 0, tzinfo=timezone.utc)
    twin.ingest_sensor_inputs([SensorInput(time=sample, time_basis="ingestion",
                                           channels={"temp": 29.0, "ph": 7.4, "tds": 220.0, "lux": 0.0},
                                           row_ids={"temp": 1})], now=sample)
    assert twin.input_cutoff() == sample
    fed = sample + timedelta(hours=1)
    twin.record_event(str(uuid.uuid4()), PondEvent(EventKind.FEEDING, fed, food_grams=50.0,
                                                   protein_percent=40.0), now=fed)
    assert twin.input_cutoff() == fed
    later = fed + timedelta(hours=1)
    twin.algae._last_camera_time = later
    assert twin.input_cutoff() == later


def test_provenance_twin_loaded_from_an_old_snapshot_has_a_cutoff():
    """A twin from a v1 (bare chemistry) snapshot reports the ingest time
    it carried."""
    twin = PondTwin.create(PondConfig(volume_litres=5000.0, estimated_biomass_grams=8000.0))
    twin.chemistry._last_ingest_time = _t(SENSOR_AT)
    old = PondTwin.from_snapshot(twin.chemistry.to_snapshot())
    assert old.input_cutoff() == _t(SENSOR_AT)


# ---------------------------------------------------------------------
# The poller
# ---------------------------------------------------------------------
def test_provenance_poll_stamps_every_evaluation_row():
    storage = forecast_storage()
    add_frame(storage)
    poll(storage)
    chem, evap, algae = (storage.rows(t) for t in TABLES)
    assert len(chem) == len(evap) == len(algae) == 1
    for row in (chem[0], evap[0], algae[0]):
        assert row["model_version"] == model_version()
        assert _t(row["input_cutoff"]) == _t(SENSOR_AT), "sample time, not the poll time"
        assert _t(row["input_cutoff"]) != POLL_AT
        assert {k: row["inputs"][k] for k in ("run", "sensor_groups", "events", "camera_frames", "ratings")} == {
            "run": "poll", "sensor_groups": 1, "events": 0, "camera_frames": 1, "ratings": 0}
    assert chem[0]["forecast_issued_at"] is None and evap[0]["forecast_issued_at"] is None
    assert chem[0]["inputs"]["forecasts"]["forecast_2hr"]["issued_at"] == ISSUED_2HR
    assert evap[0]["inputs"]["forecasts"]["forecast_24hr_general"]["issued_at"] == ISSUED_24HR
    assert algae[0]["forecast_issued_at"] == ISSUED_4DAY
    assert set(algae[0]["inputs"]["forecasts"]) == {"outlook_4day"}


def test_provenance_poll_counts_only_the_inputs_it_applied():
    storage = forecast_storage()
    app = poll(storage)
    poller._poll_user(app.extensions["koi_registry"], USER, storage.fetch_active_pond_configs()[0],
                      now=POLL_AT + timedelta(minutes=15))
    second = storage.rows("pond_chemistry_evaluations")[-1]
    assert second["inputs"]["sensor_groups"] == 0
    assert _t(second["input_cutoff"]) == _t(SENSOR_AT)


def test_provenance_poll_without_forecast_sources_still_stamps_rows():
    storage = forecast_storage()
    storage.failing.add("fetch_dashboard_sources")
    poll(storage)
    [row] = storage.rows("pond_chemistry_evaluations")
    assert row["model_version"] == model_version() and row["input_cutoff"] is not None
    assert row["forecast_issued_at"] is None
    assert all(r["issued_at"] is None for f in row["inputs"]["forecasts"].values() for r in f["records"])


# ---------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------
@pytest.fixture
def app():
    app = create_app(make_settings(), storage=forecast_storage())
    app.config["TESTING"] = True
    return app


def test_provenance_event_response_and_rows_carry_it(app):
    storage = app.extensions["koi_storage"]
    when = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    body = api_client(app).post(f"/v1/ponds/{USER}/events/feeding", json={
        "food_grams": 60.0, "protein_percent": 40.0, "timestamp": when.isoformat(),
        "event_id": str(uuid.uuid4())}).get_json()
    for name in ("chemistry", "evaporation"):
        assert body[name]["model_version"] == model_version()
        assert _t(body[name]["input_cutoff"]) == when
        assert body[name]["inputs"]["run"] == "event" and body[name]["inputs"]["events"] == 1
    [chem] = storage.rows("pond_chemistry_evaluations")
    assert {c: chem[c] for c in PROVENANCE_COLUMNS} == {c: body["chemistry"][c] for c in PROVENANCE_COLUMNS}
    assert chem["inputs"]["forecasts"]["forecast_2hr"]["issued_at"] == ISSUED_2HR


def test_provenance_duplicate_event_counts_no_event(app):
    event = {"food_grams": 60.0, "protein_percent": 40.0, "event_id": str(uuid.uuid4())}
    client = api_client(app)
    client.post(f"/v1/ponds/{USER}/events/feeding", json=event)
    again = client.post(f"/v1/ponds/{USER}/events/feeding", json=event).get_json()
    assert again["event"]["status"] == "duplicate"
    assert again["chemistry"]["inputs"]["events"] == 0


def test_provenance_rating_rows_carry_it(app):
    storage = app.extensions["koi_storage"]
    add_frame(storage)
    body = api_client(app).post(f"/v1/ponds/{USER}/events/algae-rating", json={"severity": "minor"}).get_json()
    assert body["assessments"]["chemistry"]["inputs"]["run"] == "rating"
    assert body["assessments"]["chemistry"]["inputs"]["ratings"] == 1
    assert all(r["model_version"] == model_version() for t in TABLES for r in storage.rows(t))


def test_provenance_assessment_reads_return_it(app):
    storage = app.extensions["koi_storage"]
    poll(storage)
    client = api_client(app)
    chem = client.get(f"/v1/ponds/{USER}/assessments/chemistry").get_json()
    assert chem["model_version"] == model_version() and _t(chem["input_cutoff"]) == _t(SENSOR_AT)
    every = client.get(f"/v1/ponds/{USER}/assessments").get_json()
    assert every["evaporation"]["model_version"] == model_version()
    dashboard = client.get(f"/v1/ponds/{USER}/dashboard").get_json()
    assert dashboard["assessments"]["chemistry"]["input_cutoff"] == chem["input_cutoff"]


def test_provenance_legacy_rows_read_as_unknown(app):
    """Rows written before migration 0016 have no provenance: they still
    load, with null in each column, never labelled with today's version."""
    storage = app.extensions["koi_storage"]
    storage.add_rows("pond_algae_evaluations", [
        {**{k: v for k, v in ALGAE_ASSESSMENT.items() if k != "label_count"}, "userid": USER}])
    body = api_client(app).get(f"/v1/ponds/{USER}/assessments/algae").get_json()
    assert body["green_ratio"] == 0.03
    assert {c: body[c] for c in PROVENANCE_COLUMNS} == dict.fromkeys(PROVENANCE_COLUMNS)


def test_provenance_forecast_responses_carry_version_and_cutoff(app):
    storage = app.extensions["koi_storage"]
    poll(storage)
    body = api_client(app).get(f"/v1/ponds/{USER}/forecasts/evaporation").get_json()
    assert body["model_version"] == model_version()
    assert _t(body["input_cutoff"]) == _t(SENSOR_AT)


# ---------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------
def test_provenance_is_required_on_every_push():
    storage = make_storage()
    with pytest.raises(StorageError, match="model_version"):
        storage.push_algae_evaluation(USER, ALGAE_ASSESSMENT)
    storage.push_algae_evaluation(USER, {**ALGAE_ASSESSMENT, **PROVENANCE})
    row = storage.fetch_latest_algae_evaluation(USER)
    assert {c: row[c] for c in PROVENANCE_COLUMNS} == PROVENANCE
