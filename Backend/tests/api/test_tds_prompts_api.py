"""The prompt API, poller and snapshot adapters, without live services."""
from datetime import datetime, timedelta, timezone

import pytest
from jsonschema import validate
from tests.storage.test_supabase_storage import FakeClient

from conftest import USER, add_upload, api_client, make_settings, make_storage
from koi.api import create_app, schemas
from koi.api.openapi import build_document, schema_for
from koi.api.responses import TdsPromptAnswer, TdsPromptList
from koi.models.pond_twin import PondTwin
from koi.storage import SupabaseStorage
from koi.worker.poller import _poll_user


def setup_prompt(enabled=True, falling=False):
    storage = make_storage()
    app = create_app(make_settings(tds_prompts=enabled), storage=storage)
    registry = app.extensions["koi_registry"]
    start = datetime.now(timezone.utc) - timedelta(hours=5)
    for i, ppm in enumerate([200] * 12 + [150 if falling else 250] * 2):
        add_upload(storage, {"TDS": ppm, "temp": 28.0, "pH": 7.5, "LUX": 1000},
                   start + timedelta(minutes=i * 15))
    _poll_user(registry, USER, storage.fetch_pond_config(USER))
    client = api_client(app)
    return app, storage, client


def test_prompt_poll_api_contract_etag_restart_and_no_repeated_detection():
    app, storage, client = setup_prompt()
    path = f"/v1/ponds/{USER}/prompts"
    first = client.get(path)
    assert first.status_code == 200 and len(first.json["prompts"]) == 1
    validate(first.json, schema_for(TdsPromptList))
    assert client.get(path, headers={"If-None-Match": first.headers["ETag"]}).status_code == 304
    restarted = create_app(make_settings(tds_prompts=True), storage=storage)
    assert api_client(restarted).get(path).json == first.json
    _poll_user(app.extensions["koi_registry"], USER, storage.fetch_pond_config(USER))
    assert client.get(path).json == first.json
    doc = build_document()
    post = doc["paths"]["/v1/ponds/{pond}/prompts/{prompt_id}"]["post"]
    assert {p["name"] for p in post["parameters"]} == {"pond", "prompt_id"}


@pytest.mark.parametrize("answer,amounts", [("salt", {"salt_grams": 5}), ("filter_clean", {}),
                                          ("feeding", {"food_grams": 2, "protein_percent": 35}),
                                          ("water_change", {"volume_percent": 10}),
                                          ("top_up", {"volume_litres": 20}),
                                          ("algal_scrub", {"scrub_type": "water"})])
def test_prompt_event_answers_create_ledger_event_once_and_allow_owner_correction(answer, amounts):
    _, storage, client = setup_prompt()
    prompt = client.get(f"/v1/ponds/{USER}/prompts").json["prompts"][0]
    path = f"/v1/ponds/{USER}/prompts/{prompt['id']}"
    reply = client.post(path, json={"answer": answer, **amounts})
    assert reply.status_code == 200, reply.json
    validate(reply.json, schema_for(TdsPromptAnswer))
    assert reply.json["event_id"] == prompt["id"]
    snapshot = storage.load_engine_snapshot(USER)
    ledger = snapshot["event_ledger"]["entries"]
    assert len(ledger) == 1 and ledger[0]["event"]["kind"] == answer
    assert ledger[0]["event"]["time"] == prompt["event_at"]
    assert client.post(path, json={"answer": answer, **amounts}).json == reply.json
    assert storage.load_engine_snapshot(USER)["event_ledger"] == snapshot["event_ledger"]
    assert client.post(path, json={"answer": "dismiss"}).status_code == 409
    assert client.get(f"/v1/ponds/{USER}/prompts").json["prompts"] == []


@pytest.mark.parametrize("answer,rejected", [("none of these", True), ("dismiss", False)])
def test_prompt_non_event_answers_persist_without_creating_event(answer, rejected):
    _, storage, client = setup_prompt()
    prompt = client.get(f"/v1/ponds/{USER}/prompts").json["prompts"][0]
    path = f"/v1/ponds/{USER}/prompts/{prompt['id']}"
    reply = client.post(path, json={"answer": answer})
    assert reply.status_code == 200 and reply.json["event_id"] is None
    snapshot = storage.load_engine_snapshot(USER)
    assert bool(snapshot["tds_prompts"]["rejected"]) == rejected
    assert snapshot["event_ledger"]["entries"] == []
    assert client.post(path, json={"answer": answer}).json == reply.json


def test_prompt_flag_off_hides_saved_questions_and_rejects_answers_without_writes():
    _, storage, client = setup_prompt(False)
    assert client.get(f"/v1/ponds/{USER}/prompts").json == {"enabled": False, "prompts": []}
    assert storage.load_engine_snapshot(USER)["tds_prompts"]["baseline"] == []
    app, storage, client = setup_prompt()
    prompt = client.get(f"/v1/ponds/{USER}/prompts").json["prompts"][0]
    off = api_client(create_app(make_settings(), storage=storage))
    before = storage.load_engine_snapshot(USER)
    assert off.get(f"/v1/ponds/{USER}/prompts").json["prompts"] == []
    assert off.post(f"/v1/ponds/{USER}/prompts/{prompt['id']}", json={"answer": "dismiss"}).status_code == 404
    assert storage.load_engine_snapshot(USER) == before


@pytest.mark.parametrize("body", [{"answer": "unknown"}, {"answer": "salt"},
                                  {"answer": "salt", "salt_grams": -1}, {"answer": "water_change"},
                                  {"answer": "feeding"}, {"answer": "dismiss", "user_id": 999}])
def test_prompt_invalid_answer_is_atomic(body):
    _, storage, client = setup_prompt()
    prompt = client.get(f"/v1/ponds/{USER}/prompts").json["prompts"][0]
    before = storage.load_engine_snapshot(USER)
    reply = client.post(f"/v1/ponds/{USER}/prompts/{prompt['id']}", json=body)
    assert reply.status_code == 400 and "error" in reply.json
    assert storage.load_engine_snapshot(USER) == before


def test_prompt_auth_pond_isolation_and_unknown_prompt():
    app, storage, client = setup_prompt()
    before = storage.load_engine_snapshot(USER)
    assert app.test_client().get(f"/v1/ponds/{USER}/prompts").status_code == 401
    assert client.get("/v1/ponds/999/prompts").status_code == 403
    assert client.post("/v1/ponds/999/prompts/unknown", json={"answer": "dismiss"}).status_code == 403
    assert client.post(f"/v1/ponds/{USER}/prompts/unknown", json={"answer": "dismiss"}).status_code == 404
    assert storage.load_engine_snapshot(USER) == before


def test_prompt_supabase_adapter_preserves_snapshot_state_using_existing_rpc():
    _, memory, _ = setup_prompt()
    snapshot = memory.load_engine_snapshot(USER)
    client = FakeClient({"rpc:save_pond_snapshot": 2,
                         "pond_chemistry_state": [{"snapshot": snapshot, "snapshot_version": 2}]})
    storage = SupabaseStorage(make_settings(storage="supabase"), client=client)
    assert storage.save_engine_snapshot(USER, snapshot, base_version=1) == 2
    params = client.queries[0].calls[0][1][1]
    assert params["p_snapshot"]["tds_prompts"] == snapshot["tds_prompts"]
    loaded, version = storage.load_engine_state(USER)
    assert version == 2 and PondTwin.from_snapshot(loaded).tds_prompts.to_dict() == snapshot["tds_prompts"]


@pytest.mark.parametrize("answer", ["salt", "none of these", "dismiss"])
def test_prompt_old_question_rejects_event_atomically_but_can_be_closed(monkeypatch, answer):
    _, storage, client = setup_prompt()
    prompt = client.get(f"/v1/ponds/{USER}/prompts").json["prompts"][0]
    before = storage.load_engine_snapshot(USER)
    monkeypatch.setattr(schemas, "utc_now", lambda: datetime.now(timezone.utc) + timedelta(days=31))
    reply = client.post(f"/v1/ponds/{USER}/prompts/{prompt['id']}",
                        json={"answer": answer, **({"salt_grams": 5} if answer == "salt" else {})})
    if answer == "salt":
        assert reply.status_code == 400
        assert reply.json["error"]["details"]["fields"][0]["type"] == "timestamp_too_old"
        assert storage.load_engine_snapshot(USER) == before
    else:
        assert reply.status_code == 200 and reply.json["event_id"] is None
        assert storage.load_engine_snapshot(USER)["event_ledger"]["entries"] == []


def test_prompt_answer_retry_after_replay_window_does_not_create_another_event(monkeypatch):
    _, storage, client = setup_prompt()
    prompt = client.get(f"/v1/ponds/{USER}/prompts").json["prompts"][0]
    path = f"/v1/ponds/{USER}/prompts/{prompt['id']}"
    body = {"answer": "salt", "salt_grams": 5}
    first = client.post(path, json=body)
    assert first.status_code == 200
    before = storage.load_engine_snapshot(USER)["event_ledger"]
    monkeypatch.setattr(schemas, "utc_now", lambda: datetime.now(timezone.utc) + timedelta(days=31))
    retry = client.post(path, json=body)
    assert retry.status_code == 200 and retry.json == first.json
    assert storage.load_engine_snapshot(USER)["event_ledger"] == before
