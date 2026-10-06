"""Issue #19: the event endpoints apply each event_id once, take events
back to the ledger's 30-day window, and keep one pond's events away from
another's (koi/api/routes.py _handle_event, koi/models/event_ledger.py).

Run from Backend/: python -m pytest -q -k "ledger or replay or reconcile"
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, api_client, link_account, make_settings, make_storage, mint_token
from koi.api import create_app

OTHER = 456
OTHER_AUTH_UID = "a3e2b7c1-9f40-4d1e-8c55-456000000456"
OTHER_SESSION_ID = "d4c1e8f0-2b6a-4f73-9e10-456000000001"
FEED = {"food_grams": 60.0, "protein_percent": 40.0}


@pytest.fixture
def storage():
    s = make_storage()
    s.add_rows("UserData", [{"userID": OTHER, "volume": 3000.0, "biomass": 5.0}])
    link_account(s, OTHER, OTHER_AUTH_UID, OTHER_SESSION_ID)
    return s


@pytest.fixture
def app(storage):
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    return app


def other(app):
    return api_client(app, mint_token(sub=OTHER_AUTH_UID, session_id=OTHER_SESSION_ID))


def entries(storage, pond):
    snap = storage.load_engine_snapshot(pond)
    return [] if snap is None else snap["event_ledger"]["entries"]


def test_ledger_api_duplicate_post_returns_the_current_assessment_once(app, storage):
    client = api_client(app)
    event_id = str(uuid.uuid4())
    first = client.post(f"/v1/ponds/{USER}/events/feeding", json={**FEED, "event_id": event_id})
    again = client.post(f"/v1/ponds/{USER}/events/feeding", json={**FEED, "event_id": event_id.upper()})
    assert first.status_code == again.status_code == 200
    assert first.get_json()["event"]["status"] == "applied"
    assert again.get_json()["event"] == {**first.get_json()["event"], "status": "duplicate"}
    assert again.get_json()["chemistry"]["tan_ppm"] == first.get_json()["chemistry"]["tan_ppm"]
    assert [e["event_id"] for e in entries(storage, USER)] == [event_id]


def test_ledger_api_post_without_event_id_is_a_legacy_entry(app, storage):
    resp = api_client(app).post(f"/v1/ponds/{USER}/events/feeding", json={**FEED, "user_id": USER})
    assert resp.status_code == 200
    assert resp.get_json()["event"]["event_id"].startswith("legacy:")
    assert entries(storage, USER)[0]["legacy"] is True


@pytest.mark.parametrize("bad", ["not-a-uuid", 12345, ""])
def test_ledger_api_rejects_an_event_id_that_is_not_a_uuid(app, storage, bad):
    resp = api_client(app).post(f"/v1/ponds/{USER}/events/feeding", json={**FEED, "event_id": bad})
    assert resp.status_code == 400
    [field] = resp.get_json()["error"]["details"]["fields"]
    assert field["field"] == "event_id"
    assert entries(storage, USER) == []


def test_ledger_api_accepts_a_backdated_event_inside_the_window(app, storage):
    when = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    resp = api_client(app).post(f"/v1/ponds/{USER}/events/water-change",
                                json={"volume_percent": 20.0, "timestamp": when, "event_id": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert resp.get_json()["event"]["status"] == "applied"


def test_ledger_api_identical_event_id_across_ponds_never_reaches_the_other_pond(app, storage):
    event_id = str(uuid.uuid4())
    mine = api_client(app).post(f"/v1/ponds/{USER}/events/feeding", json={**FEED, "event_id": event_id})
    assert mine.status_code == 200
    before = storage.load_engine_snapshot(USER)

    # Another account cannot post to this pond, with or without the id.
    for path in (f"/v1/ponds/{USER}/events/feeding", f"/v1/ponds/{USER}/events/feeding"):
        resp = other(app).post(path, json={**FEED, "user_id": USER, "event_id": event_id, "food_grams": 999.0})
        assert resp.status_code == 403
    # Posting the same id to its own pond is its own event: applied there,
    # answered with its own pond's assessment, this pond untouched.
    theirs = other(app).post(f"/v1/ponds/{OTHER}/events/feeding",
                             json={**FEED, "event_id": event_id, "food_grams": 999.0})
    assert theirs.status_code == 200 and theirs.get_json()["event"]["status"] == "applied"
    assert theirs.get_json()["chemistry"]["tan_ppm"] != mine.get_json()["chemistry"]["tan_ppm"]
    assert storage.load_engine_snapshot(USER) == before
    assert [(e["event_id"], e["event"]["food_grams"]) for e in entries(storage, OTHER)] == [(event_id, 999.0)]
    # And this pond's retry is still a duplicate of its own event.
    again = api_client(app).post(f"/v1/ponds/{USER}/events/feeding", json={**FEED, "event_id": event_id})
    assert again.get_json()["event"]["status"] == "duplicate"
