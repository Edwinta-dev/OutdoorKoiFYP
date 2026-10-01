"""GET /health (liveness) and GET /ready (storage and poller), issue #10."""
from datetime import datetime, timedelta, timezone

from conftest import POND_FIXTURE, USER, make_settings, make_storage
from koi.api import create_app
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage
from koi.worker import poller


def _client(storage, **settings):
    app = create_app(make_settings(**settings), storage=storage)
    app.config["TESTING"] = True
    return app.test_client()


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _status(last_success_at, ponds=None, holder="w1"):
    return {"holder": holder, "cycle_started_at": _ago(seconds=3), "cycle_finished_at": _ago(seconds=1),
            "cycle_duration_sec": 2.0, "last_success_at": last_success_at, "ponds": ponds or {}}


def test_health_is_liveness_only_and_ignores_storage():
    storage = make_storage()
    storage.failing.update({"fetch_worker_status", "fetch_lease"})
    resp = _client(storage).get("/health")
    assert resp.status_code == 200 and resp.get_json()["status"] == "ok"


def test_ready_is_503_before_the_first_successful_poll():
    resp = _client(make_storage()).get("/ready")
    assert resp.status_code == 503
    error = resp.get_json()["error"]
    assert error["code"] == "not_ready"
    assert "not completed a successful cycle" in error["message"]
    assert error["details"]["storage"] == {"reachable": True}
    assert error["details"]["poller"]["last_success_at"] is None


def test_ready_after_a_worker_cycle_reports_the_poll_and_the_lease_holder():
    storage = MemoryStorage.from_json(POND_FIXTURE)  # real clock, so the lease is current
    worker = poller.Worker(make_settings(), EngineRegistry(storage), holder="worker-a")
    assert worker.run_cycle() is True
    resp = _client(storage).get("/ready")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ready" and body["reasons"] == []
    p = body["poller"]
    assert p["lease_holder"] == "worker-a" and p["lease_expires_at"]
    assert p["last_success_at"] and 0 <= p["last_success_age_sec"] < 60
    assert p["max_age_sec"] == 2 * 15 * 60
    assert p["failed_ponds"] == [] and p["skipped_ponds"] == []
    assert p["last_cycle_duration_sec"] >= 0


def test_ready_is_503_when_the_last_success_is_older_than_two_intervals():
    storage = make_storage()
    storage.record_worker_status("poller", _status(_ago(minutes=31)))
    resp = _client(storage, poll_interval_minutes=15).get("/ready")
    assert resp.status_code == 503
    error = resp.get_json()["error"]
    assert "31 minutes ago" in error["message"] and "30 minutes" in error["message"]
    assert error["details"]["poller"]["last_success_age_sec"] >= 31 * 60

    storage.record_worker_status("poller", _status(_ago(minutes=29)))
    assert _client(storage, poll_interval_minutes=15).get("/ready").status_code == 200


def test_ready_lists_failed_ponds_without_failing_readiness():
    storage = make_storage()
    storage.record_worker_status("poller", _status(_ago(seconds=1), ponds={
        "455": {"result": "ok", "failures_total": 0, "sensor_recorded_at": None, "reason": None},
        "12": {"result": "failed", "failures_total": 3, "sensor_recorded_at": None, "reason": "RuntimeError: x"},
        "9": {"result": "skipped", "failures_total": 0, "sensor_recorded_at": None, "reason": "no reading"},
    }))
    resp = _client(storage).get("/ready")
    assert resp.status_code == 200
    p = resp.get_json()["poller"]
    assert p["failed_ponds"] == [12] and p["skipped_ponds"] == [9]
    assert p["lease_holder"] is None, "no lease row: nobody holds it"


def test_ready_is_503_when_storage_is_unreachable():
    storage = make_storage()
    storage.failing.add("fetch_worker_status")
    resp = _client(storage).get("/ready")
    assert resp.status_code == 503
    error = resp.get_json()["error"]
    assert error["code"] == "not_ready"
    assert error["details"]["storage"] == {"reachable": False, "operation": "fetch_worker_status"}
    assert "could not be reached" in error["message"]


def test_ready_ignores_an_expired_lease():
    storage = make_storage()
    storage.record_worker_status("poller", _status(_ago(seconds=1)))
    storage.add_rows("worker_lease", [{"name": "poller", "holder": "gone", "expires_at": _ago(seconds=5)}])
    p = _client(storage).get("/ready").get_json()["poller"]
    assert p["lease_holder"] is None


def test_health_and_ready_are_in_the_url_map():
    rules = {r.rule for r in create_app(make_settings(), storage=make_storage()).url_map.iter_rules()}
    assert {"/health", "/ready", "/metrics"} <= rules
    assert USER  # the fixture pond is the one polled above
