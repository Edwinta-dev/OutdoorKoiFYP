"""GET /v1/ponds/{pond}/dashboard, conditional GETs, and the deprecated
unversioned paths.

The ponds are the synthetic ones in tests/dashboard_scenarios.py, in
MemoryStorage; tests/sql/test_sql_dashboard.py runs the same ponds
through the local database and checks the responses are identical.

The responses are also written to the Dart fixture
MobileUI/mobile_app/test/fixtures/v1_dashboard.json, which the app's
parser test reads. test_dashboard_dart_fixture_is_current fails when it
is out of date; regenerate it from Backend/ with
    KOI_REGENERATE_FIXTURES=1 python -m pytest -q tests/api/test_dashboard.py
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import api_contract
import pytest
from dashboard_scenarios import NOW, SCENARIOS, dashboard_for, memory_storage

from conftest import (
    ALGAE_ASSESSMENT,
    PROVENANCE,
    USER,
    USER_AUTH_UID,
    api_client,
    link_account,
    make_settings,
    make_storage,
    mint_token,
)
from koi.api import create_app, schemas

DART_FIXTURE = Path(__file__).resolve().parents[3] / "MobileUI" / "mobile_app" / "test" / "fixtures" / \
    "v1_dashboard.json"
SCENARIO = {name: (pond, now) for name, pond, now in SCENARIOS}
SESSION = "0b8d3f2a-1c7e-4e55-8a61-910100000001"

CHEMISTRY = {"status": "Amber", "category": "Watch", "tan_ppm": 0.31, "no2_ppm": 0.12, "no3_ppm": 18.0,
             "ph_reactivity": None, "reactivity_trend": None, "tds_trend": 0.8, "sensor_warnings": [],
             "advisory": "Ammonia is rising. Feed lightly for two days.", "add_hardener_now": False}
EVAPORATION = {"status": "Green", "category": "Normal", "loss_litres": 42.0, "loss_pct": 1.7,
               "evaporation_mm_per_day": 4.2, "loss_litres_per_day": 10.5, "water_temp_c": 29.5,
               "feed_cap_grams": 60.0, "feed_note": "Normal ration.", "days_to_topup": 6,
               "advisory": "No top-up needed this week.", "topup_now": False}


def _fixture_cases() -> dict:
    storage = memory_storage()
    cases = {name: dashboard_for(storage, pond, now) for name, pond, now in SCENARIOS}
    cases["complete_with_assessments"] = dashboard_for(
        storage, 9101, NOW, chemistry=CHEMISTRY, evaporation=EVAPORATION, algae=ALGAE_ASSESSMENT, aeration=True)
    return cases


@pytest.fixture
def clock(monkeypatch):
    """Sets the API's clock; returns a setter."""
    def set_to(when):
        monkeypatch.setattr(schemas, "utc_now", lambda: when)
    set_to(NOW)
    return set_to


@pytest.fixture
def scenario_app():
    storage = memory_storage()
    link_account(storage, 9101, USER_AUTH_UID, SESSION)
    app = create_app(make_settings(), storage=storage)
    return app, storage, api_client(app, mint_token(session_id=SESSION))


# ---------------------------------------------------------------------
# The response, per scenario
# ---------------------------------------------------------------------
@pytest.mark.parametrize("name", list(SCENARIO))
def test_dashboard_scenarios_match_the_schema(name):
    pond, now = SCENARIO[name]
    api_contract.validate("Dashboard", dashboard_for(memory_storage(), pond, now))


def test_dashboard_and_actions_route_keep_missing_weather_unassessed(scenario_app, clock):
    app, _, client = scenario_app
    clock(NOW)
    dashboard = client.get("/v1/ponds/9101/dashboard")
    response = client.get("/v1/ponds/9101/actions")
    assert dashboard.status_code == 200
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "insufficient_data"
    assert {r["rule"] for r in body["rules"]} == {"REACT", "PREEMPT", "WINDOW", "NOWCAST"}


def test_dashboard_serves_fired_actions_in_the_documented_shape(scenario_app, clock, monkeypatch):
    """The app reads next_actions entries as LeadTimeAction (issue #55)."""
    from koi.models import ladder
    fired = ladder.nowcast(rain_call="Thundery Showers")
    assert fired["actions"], "a rain nowcast fires"
    monkeypatch.setattr(ladder, "actions", lambda **_: {"status": "insufficient_data", "rules": [fired],
                                                        "actions": fired["actions"]})
    _, _, client = scenario_app
    clock(NOW)
    body = client.get("/v1/ponds/9101/dashboard").get_json()
    assert body["next_actions"] == [{"rule": "NOWCAST", "lead_time": "within 2 hours",
                                     "action": fired["actions"][0]["action"], "evidence": "Thundery Showers"}]
    api_contract.validate("Dashboard", body)


def test_dashboard_complete_pond(scenario_app, clock):
    _, _, client = scenario_app
    r = client.get("/v1/ponds/9101/dashboard")
    assert r.status_code == 200
    body = r.get_json()
    assert body == dashboard_for(memory_storage(), 9101, NOW)
    readings = body["readings"]
    assert {c: readings[c]["status"] for c in ("ph", "tds", "water_temp", "lux")} == dict.fromkeys(
        ("ph", "tds", "water_temp", "lux"), "ok")
    assert readings["ph"]["value"] == 7.5 and readings["ph"]["source_row_id"] == 910111
    assert readings["ph"]["ingested_at"] == "2026-10-03T04:25:00Z"
    assert readings["ph"]["sample_time"] is None and readings["ph"]["sample_time_basis"] == "unknown"
    assert readings["ph"]["fresh_until"] == "2026-10-03T04:55:00Z"
    assert body["next_actions"] == []
    assert body["assessments"]["kind"] == "model_estimate" and body["readings"]["kind"] == "observed"
    assert body["forecast"]["kind"] == "projection"
    weather = body["weather"]
    assert (weather["air_temperature"]["value"], weather["air_temperature"]["station_id"]) == (30.7, "S43")
    assert weather["air_temperature"]["observed_at"] == "2026-10-03T04:25:00Z"
    assert weather["air_temperature"]["ingested_at"] == "2026-10-03T04:26:00Z"
    assert weather["uv_index"] == {"status": "ok", "value": 7.0, "unit": "UV index",
                                   "hour_start": "2026-10-03T04:00:00Z", "daylight": True,
                                   "ingested_at": "2026-10-03T04:10:00Z", "note": None}
    two_hour = body["forecast"]["two_hour"]
    assert two_hour["text"] == "Partly Cloudy (Day)" and two_hour["status"] == "ok"
    assert two_hour["issued_at"] == "2026-10-03T03:40:00Z"
    assert two_hour["ingested_at"] == "2026-10-03T04:05:00Z"
    assert (two_hour["valid_from"], two_hour["valid_to"]) == ("2026-10-03T04:00:00Z",
                                                              "2026-10-03T06:00:00Z")
    general = body["forecast"]["twenty_four_hour_general"]
    assert (general["temperature_low_c"], general["temperature_high_c"], general["wind_high_kmh"]) == (25, 35, 15)
    assert [d["date"] for d in body["forecast"]["outlook"]] == ["2026-10-04", "2026-10-05", "2026-10-06",
                                                                "2026-10-07"]


def test_dashboard_no_tds_is_missing_not_substituted():
    pond, now = SCENARIO["no_tds"]
    tds = dashboard_for(memory_storage(), pond, now)["readings"]["tds"]
    assert (tds["status"], tds["value"], tds["ingested_at"], tds["source_row_id"]) == ("missing", None, None, None)


def test_dashboard_stale_channel_keeps_its_own_time():
    pond, now = SCENARIO["stale_channel"]
    readings = dashboard_for(memory_storage(), pond, now)["readings"]
    assert readings["ph"]["status"] == "stale" and readings["ph"]["value"] == 7.75
    assert readings["ph"]["ingested_at"] == "2026-10-03T02:30:00Z"
    assert readings["tds"]["status"] == "ok" and readings["tds"]["ingested_at"] == "2026-10-03T04:27:00Z"
    assert readings["lux"]["status"] == "invalid" and readings["lux"]["value"] is None
    assert "-1" in readings["lux"]["note"]


def test_dashboard_hypoxia_uses_fresh_readings_only():
    pond, now = SCENARIO["stale_channel"]
    hypoxia = dashboard_for(memory_storage(), pond, now)["assessments"]["hypoxia"]
    assert hypoxia["lux"] is None and hypoxia["level"] == "unknown"


def test_dashboard_missing_station_is_not_borrowed():
    pond, now = SCENARIO["missing_station"]
    body = dashboard_for(memory_storage(), pond, now)
    rainfall = body["weather"]["rainfall"]
    # S43 has rainfall and S50 has a non-cache row with rainfall 9.9: neither is used.
    assert (rainfall["status"], rainfall["value"], rainfall["station_id"]) == ("missing", None, None)
    assert body["weather"]["air_temperature"]["station_id"] == "S50"
    assert body["forecast"]["two_hour"]["status"] == "missing"
    assert body["forecast"]["twenty_four_hour_regional"]["status"] == "missing"
    assert body["stations"]["rainfall"] is None


def test_dashboard_old_rows():
    pond, now = SCENARIO["old_rows"]
    body = dashboard_for(memory_storage(), pond, now)
    assert {body["readings"][c]["status"] for c in ("ph", "tds", "water_temp", "lux")} == {"stale"}
    air = body["weather"]["air_temperature"]
    assert air["status"] == "stale" and air["observed_at"] is None and air["ingested_at"] == "2026-10-03T01:30:00Z"
    two_hour = body["forecast"]["two_hour"]
    assert two_hour["status"] == "expired" and two_hour["issued_at"] is None
    assert "2026-10-02" not in [d["date"] for d in body["forecast"]["outlook"]]


def test_dashboard_two_ponds_do_not_mix():
    storage = memory_storage()
    first = dashboard_for(storage, 9101, NOW)
    second = dashboard_for(storage, 9106, NOW)
    assert second["readings"]["ph"]["value"] == 6.5 and second["readings"]["ph"]["source_row_id"] == 910602
    assert first["readings"]["ph"]["value"] == 7.5
    assert second["weather"]["rainfall"]["station_id"] == "S24" and second["weather"]["rainfall"]["value"] == 1.4
    assert second["forecast"]["two_hour"]["text"] == "Showers"
    assert first["forecast"]["two_hour"]["text"] == "Partly Cloudy (Day)"


def test_dashboard_daylight_missing_uv_is_missing():
    pond, now = SCENARIO["daylight_missing_uv"]
    uv = dashboard_for(memory_storage(), pond, now)["weather"]["uv_index"]
    # Yesterday's 13:00 report sits in the 13:00 slot; it is not this hour's.
    assert (uv["status"], uv["value"], uv["daylight"]) == ("missing", None, True)


def test_dashboard_night_uv_is_derived_not_measured():
    pond, now = SCENARIO["night"]
    uv = dashboard_for(memory_storage(), pond, now)["weather"]["uv_index"]
    assert (uv["status"], uv["value"], uv["daylight"]) == ("night_derived", 0.0, False)
    assert "not measured" in uv["note"]


def test_dashboard_assessments_are_the_stored_evaluations(scenario_app, clock):
    _, storage, client = scenario_app
    storage.push_evaluation(9101, {**CHEMISTRY, **PROVENANCE})
    storage.push_algae_evaluation(9101, {**ALGAE_ASSESSMENT, **PROVENANCE})
    body = client.get("/v1/ponds/9101/dashboard").get_json()
    assert body["assessments"]["chemistry"]["category"] == "Watch"
    assert body["assessments"]["algae"]["green_ratio"] == 0.03
    assert body["assessments"]["evaporation"] is None


def test_dashboard_read_has_no_side_effects(scenario_app, clock):
    app, storage, client = scenario_app
    before = {table: storage.rows(table) for table in storage._tables}
    assert client.get("/v1/ponds/9101/dashboard").status_code == 200
    assert {table: storage.rows(table) for table in storage._tables} == before
    assert storage.fetch_snapshot_version(9101) == 0


def test_dashboard_other_pond_is_forbidden(scenario_app, clock):
    _, _, client = scenario_app
    assert client.get("/v1/ponds/9106/dashboard").status_code == 403


def test_dashboard_needs_a_token(scenario_app, clock):
    app, _, _ = scenario_app
    assert app.test_client().get("/v1/ponds/9101/dashboard").status_code == 401


# ---------------------------------------------------------------------
# ETag and If-None-Match
# ---------------------------------------------------------------------
def _get(client, etag=None):
    headers = {"If-None-Match": etag} if etag else {}
    return client.get("/v1/ponds/9101/dashboard", headers=headers)


def test_dashboard_etag_and_not_modified(scenario_app, clock):
    _, _, client = scenario_app
    first = _get(client)
    etag = first.headers["ETag"]
    assert first.headers["Cache-Control"] == "private, no-cache"
    assert "Authorization" in first.headers["Vary"]
    again = _get(client, etag)
    assert again.status_code == 304 and again.get_data() == b"" and again.headers["ETag"] == etag
    assert _get(client, '"something-else"').status_code == 200


def test_dashboard_etag_changes_when_a_reading_goes_stale(scenario_app, clock):
    _, _, client = scenario_app
    etag = _get(client).headers["ETag"]
    clock(NOW + timedelta(minutes=10))
    assert _get(client, etag).status_code == 304  # still fresh: same body
    clock(NOW + timedelta(minutes=31))
    later = _get(client, etag)
    assert later.status_code == 200 and later.headers["ETag"] != etag
    assert later.get_json()["readings"]["ph"]["status"] == "stale"


@pytest.mark.parametrize("change", ["reading", "evaluation", "forecast", "station", "profile"])
def test_dashboard_etag_changes_with_each_source(scenario_app, clock, change):
    _, storage, client = scenario_app
    etag = _get(client).headers["ETag"]
    if change == "reading":
        storage.add_rows("SensorData", [{"userID": 9101, "sensor_type": "pH", "data1": 7.0,
                                         "created_at": (NOW - timedelta(minutes=1)).isoformat()}])
    elif change == "evaluation":
        storage.push_evaluation(9101, {**CHEMISTRY, **PROVENANCE})
    elif change == "forecast":
        row = next(r for r in storage._tables["weather_forecasts"] if r["slot_id"] == "Serangoon")
        row.update(data={"forecast": "Showers"}, source_issued_at="2026-10-03T12:10:00+08:00")
    elif change == "station":
        row = next(r for r in storage._tables["UserData"] if r["userID"] == 9101)
        row["ClosestStations"] = {**row["ClosestStations"], "rainfall": "S24"}
    elif change == "profile":
        storage.insert_pond_profile(9101, {"effective_from": (NOW - timedelta(hours=1)).isoformat(),
                                           "volume_l": 2500.0, "depth_m": None, "biomass_g": 55000.0,
                                           "fish_type": None, "fish_count": None, "tap_tds_ppm": None,
                                           "tap_nitrate_ppm": None, "aeration": True})
    assert _get(client, etag).status_code == 200


def test_other_gets_are_conditional_too(scenario_app, clock):
    _, _, client = scenario_app
    first = client.get("/v1/ponds/9101/profile")
    assert first.status_code == 200
    assert client.get("/v1/ponds/9101/profile", headers={"If-None-Match": first.headers["ETag"]}).status_code == 304


def test_posts_carry_no_etag(storage):
    app = create_app(make_settings(), storage=storage)
    r = api_client(app).post(f"/v1/ponds/{USER}/events/top-up", json={"volume_percent": 5.0})
    assert r.status_code == 200 and "ETag" not in r.headers


# ---------------------------------------------------------------------
# /v1 routes and the deprecated unversioned paths
# ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def pond_client():
    app = create_app(make_settings(), storage=make_storage())
    client = api_client(app)
    client.post(f"/v1/ponds/{USER}/events/top-up", json={"user_id": USER, "volume_percent": 5.0})
    return client


@pytest.mark.parametrize("old, new", [
    (f"/assessment/{USER}", f"/v1/ponds/{USER}/assessments/chemistry"),
    (f"/assessment/evaporation/{USER}", f"/v1/ponds/{USER}/assessments/evaporation"),
    (f"/assessment/all/{USER}", f"/v1/ponds/{USER}/assessments"),
    (f"/forecast/evaporation/{USER}", f"/v1/ponds/{USER}/forecasts/evaporation"),
    (f"/ratings/algae/{USER}", f"/v1/ponds/{USER}/ratings/algae"),
])
def test_old_paths_are_removed(pond_client, old, new):
    old_r, new_r = pond_client.get(old), pond_client.get(new)
    assert old_r.status_code == 404
    assert new_r.status_code == 200
    assert "Deprecation" not in new_r.headers


def test_removed_path_does_not_resolve_a_pond(pond_client):
    r = pond_client.get("/assessment/456")
    assert r.status_code == 404 and "Deprecation" not in r.headers


def test_old_event_path_is_removed(pond_client):
    r = pond_client.post("/events/feeding", json={"user_id": USER, "food_grams": 20, "protein_percent": 35})
    assert r.status_code == 404 and "Deprecation" not in r.headers
    assert "Link" not in r.headers  # the pond is in the body, so no single successor URL


def test_v1_event_takes_the_pond_from_the_path(pond_client):
    r = pond_client.post(f"/v1/ponds/{USER}/events/feeding", json={"food_grams": 20, "protein_percent": 35})
    assert r.status_code == 200 and "Deprecation" not in r.headers
    assert set(r.get_json()) >= {"chemistry", "evaporation", "algae"}
    same = pond_client.post(f"/v1/ponds/{USER}/events/feeding",
                            json={"user_id": USER, "food_grams": 20, "protein_percent": 35})
    assert same.status_code == 200


def test_v1_event_refuses_a_different_body_pond(pond_client):
    r = pond_client.post(f"/v1/ponds/{USER}/events/top-up", json={"user_id": 456, "volume_percent": 5.0})
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "pond_mismatch"
    other = pond_client.post("/v1/ponds/456/events/top-up", json={"volume_percent": 5.0})
    assert other.status_code == 403


def test_v1_event_still_validates_the_body(pond_client):
    r = pond_client.post(f"/v1/ponds/{USER}/events/top-up", json={})
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "validation_failed"


def test_health_is_served_under_v1_and_unversioned():
    app = create_app(make_settings(), storage=make_storage())
    client = app.test_client()
    for path in ("/health", "/v1/health"):
        r = client.get(path)
        assert r.status_code == 200 and "Deprecation" not in r.headers


# ---------------------------------------------------------------------
# The Dart fixture
# ---------------------------------------------------------------------
def test_dashboard_dart_fixture_is_current():
    cases = _fixture_cases()
    for body in cases.values():
        api_contract.validate("Dashboard", body)
    if os.environ.get("KOI_REGENERATE_FIXTURES") == "1":
        DART_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        DART_FIXTURE.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    assert DART_FIXTURE.exists(), "run KOI_REGENERATE_FIXTURES=1 python -m pytest tests/api/test_dashboard.py"
    assert json.loads(DART_FIXTURE.read_text(encoding="utf-8")) == cases, \
        "v1_dashboard.json is out of date: KOI_REGENERATE_FIXTURES=1 python -m pytest tests/api/test_dashboard.py"

