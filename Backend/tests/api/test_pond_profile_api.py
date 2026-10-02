"""GET/PUT /v1/ponds/{pond}/profile and where the engines get their pond
settings from (issue #16): the stored profile, never a request body.

Run from Backend/: python -m pytest -q -k profile
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app
from koi.worker import poller

OTHER = 456
PROFILE = {"volume_l": 4000.0, "depth_m": 0.8, "biomass_g": 12000.0, "fish_type": "Koi", "fish_count": 7,
           "tap_tds_ppm": 140.0, "tap_nitrate_ppm": 3.0, "aeration": True}


@pytest.fixture
def app():
    storage = make_storage()
    storage.add_rows("UserData", [{"userID": OTHER, "volume": 3000.0, "biomass": 5.0}])
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return api_client(app)


def url(pond=USER):
    return f"/v1/ponds/{pond}/profile"


def twin(app):
    return app.extensions["koi_registry"]._twins[USER].twin


def assert_error(resp, status, code):
    assert resp.status_code == status, resp.get_json()
    assert resp.get_json()["error"]["code"] == code
    return resp.get_json()["error"]


# --- GET / PUT ---------------------------------------------------------------

def test_profile_get_falls_back_to_userdata_for_a_pond_without_profile_rows(client, app):
    userdata = app.extensions["koi_storage"].fetch_pond_config(USER)
    body = client.get(url()).get_json()
    assert body["source"] == "userdata" and body["history"] == [] and body["pond_id"] == USER
    current = body["current"]
    assert current["volume_l"] == userdata["volume_litres"]
    assert current["biomass_g"] == userdata["estimated_biomass_grams"]
    assert current["effective_from"] is None
    assert current["depth_m"] is None and current["fish_type"] is None


def test_profile_put_adds_a_row_effective_now_and_get_returns_it(client):
    resp = client.put(url(), json=PROFILE)
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["source"] == "pond_profile"
    assert {k: body["current"][k] for k in PROFILE} == PROFILE
    assert body["added"]["source"] == "api" and body["added"]["pond_id"] == USER
    got = client.get(url()).get_json()
    assert len(got["history"]) == 1 and got["current"] == body["current"]
    effective = datetime.fromisoformat(got["current"]["effective_from"])
    assert abs(effective - datetime.now(timezone.utc)) < timedelta(minutes=1)


def test_profile_put_keeps_old_rows_and_a_future_row_waits_for_its_time(client):
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert client.put(url(), json={**PROFILE, "effective_from": past}).status_code == 201
    assert client.put(url(), json={"volume_l": 9000.0, "biomass_g": 1.0, "effective_from": future}).status_code == 201
    body = client.get(url()).get_json()
    assert [r["volume_l"] for r in body["history"]] == [4000.0, 9000.0]
    assert body["current"]["volume_l"] == 4000.0


def test_profile_put_without_an_offset_is_utc(client):
    body = client.put(url(), json={**PROFILE, "effective_from": "2026-09-01T06:00:00"}).get_json()
    assert datetime.fromisoformat(body["added"]["effective_from"]) == datetime(2026, 9, 1, 6, tzinfo=timezone.utc)


def test_profile_put_twice_at_the_same_time_is_409(client):
    at = "2026-09-01T06:00:00+00:00"
    assert client.put(url(), json={**PROFILE, "effective_from": at}).status_code == 201
    assert_error(client.put(url(), json={**PROFILE, "effective_from": "2026-09-01T14:00:00+08:00"}),
                 409, "profile_exists")
    assert len(client.get(url()).get_json()["history"]) == 1


@pytest.mark.parametrize("body, field", [
    ({k: v for k, v in PROFILE.items() if k != "volume_l"}, "volume_l"),
    ({k: v for k, v in PROFILE.items() if k != "biomass_g"}, "biomass_g"),
    ({**PROFILE, "volume_l": 0}, "volume_l"),
    ({**PROFILE, "depth_m": 0}, "depth_m"),
    ({**PROFILE, "depth_m": -1.0}, "depth_m"),
    ({**PROFILE, "biomass_g": -5.0}, "biomass_g"),
    ({**PROFILE, "fish_count": True}, "fish_count"),
    ({**PROFILE, "aeration": "yes"}, "aeration"),
    ({**PROFILE, "volume_l": "4000"}, "volume_l"),
    ({**PROFILE, "effective_from": "yesterday"}, "effective_from"),
    ({**PROFILE, "effective_from": 1755680000}, "effective_from"),
])
def test_profile_put_validation_names_the_invalid_field(client, body, field):
    error = assert_error(client.put(url(), json=body), 400, "validation_failed")
    assert field in [f["field"] for f in error["details"]["fields"]]


def test_profile_put_rejects_a_body_that_is_not_an_object(client):
    assert_error(client.put(url(), data=b"[1]", content_type="application/json"), 400, "invalid_body")


def test_profile_of_another_pond_is_403(client, app):
    assert_error(client.get(url(OTHER)), 403, "pond_forbidden")
    assert_error(client.put(url(OTHER), json=PROFILE), 403, "pond_forbidden")
    assert app.extensions["koi_storage"].rows("pond_profile") == []


def test_profile_needs_a_token(app):
    assert_error(app.test_client().get(url()), 401, "auth_required")


def test_profile_of_an_unconfigured_pond_is_404():
    app = create_app(make_settings(env="development", auth="disabled"), storage=make_storage())
    assert_error(app.test_client().get(url(999)), 404, "pond_not_configured")


# --- engines read the stored profile ------------------------------------

FEED = {"user_id": USER, "food_grams": 50.0, "protein_percent": 40.0}


def test_profile_fish_settings_in_an_event_body_are_ignored(client, app):
    client.put(url(), json=PROFILE)
    resp = client.post("/events/feeding", json={**FEED, "fish_type": "Goldfish", "fish_count": 99})
    assert resp.status_code == 200, resp.get_json()
    config = twin(app).chemistry.config
    assert (config.fish_type, config.fish_count) == ("Koi", 7)
    assert config.volume_litres == 4000.0 and config.tap_tds_ppm == 140.0


def test_profile_events_without_profile_rows_use_userdata_and_defaults(client, app):
    client.post("/events/feeding", json={**FEED, "fish_type": "Goldfish", "fish_count": 99})
    config = twin(app).chemistry.config
    userdata = app.extensions["koi_storage"].fetch_pond_config(USER)
    assert config.volume_litres == userdata["volume_litres"]
    assert (config.fish_type, config.fish_count) == ("Unspecified", 0)


def test_profile_change_reaches_an_existing_twin_and_the_evaporation_forecast(client, app):
    client.post("/events/feeding", json=FEED)  # the twin exists, on the UserData profile
    client.put(url(), json=PROFILE)
    forecast = client.get(f"/forecast/evaporation/{USER}").get_json()
    assert forecast["volume_litres"] == 4000.0
    assert forecast["assumed_depth_m"] == 0.8
    assert forecast["surface_area_m2"] == pytest.approx(4.0 / 0.8)


def test_profile_depth_can_be_replaced_for_one_evaporation_forecast(client, app):
    client.put(url(), json=PROFILE)
    forecast = client.get(f"/forecast/evaporation/{USER}?depth_m=2.0").get_json()
    assert forecast["assumed_depth_m"] == 2.0 and forecast["surface_area_m2"] == pytest.approx(2.0)
    assert twin(app).evaporation.config.pond_depth_m == 0.8
    assert client.get(f"/forecast/evaporation/{USER}").get_json()["assumed_depth_m"] == 0.8


def test_profile_is_what_the_poller_runs_the_pond_with(app):
    storage = app.extensions["koi_storage"]
    registry = app.extensions["koi_registry"]
    api_client(app).put(url(), json=PROFILE)
    poller._poll_user(registry, USER, storage.fetch_active_pond_configs()[0])
    t = twin(app)
    assert (t.chemistry.config.fish_type, t.chemistry.config.fish_count) == ("Koi", 7)
    assert t.evaporation.config.pond_depth_m == 0.8 and t.evaporation.config.volume_litres == 4000.0
