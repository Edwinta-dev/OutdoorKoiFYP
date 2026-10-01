"""The poller's cycle report (worker_status), which the API's /ready and
/metrics read (issue #10)."""
from conftest import DASHBOARD_PAYLOAD, USER, make_settings, make_storage
from koi.registry import EngineRegistry
from koi.worker import poller


def _worker(storage, holder="w1"):
    return poller.Worker(make_settings(), EngineRegistry(storage), holder=holder)


def _add_pond(storage, user_id):
    storage.add_rows("UserData", [{"userID": user_id, "volume": 800, "biomass": 1.5}])


def test_health_report_records_each_pond_result():
    storage = make_storage()
    payload = {**DASHBOARD_PAYLOAD, "raw_sensor": {**DASHBOARD_PAYLOAD["raw_sensor"],
                                                   "recorded_at": "2026-08-20T00:00:00+00:00"}}
    storage.set_dashboard_payload(USER, payload)
    _add_pond(storage, 500)  # no dashboard payload: skipped
    assert _worker(storage).run_cycle() is True

    status = storage.fetch_worker_status("poller")
    assert status["holder"] == "w1" and status["cycle_duration_sec"] >= 0
    assert status["last_success_at"] == status["cycle_finished_at"]
    assert status["ponds"][str(USER)] == {"result": "ok", "reason": None, "failures_total": 0,
                                          "sensor_recorded_at": "2026-08-20T00:00:00+00:00"}
    assert status["ponds"]["500"]["result"] == "skipped"
    assert status["ponds"]["500"]["reason"] == "no sensor reading in the dashboard payload"


def test_health_report_skips_a_pond_with_an_unusable_reading():
    storage = make_storage()
    storage.set_dashboard_payload(USER, {**DASHBOARD_PAYLOAD, "raw_sensor": {"pH": "n/a", "recorded_at": "t"}})
    _worker(storage).run_cycle()
    pond = storage.fetch_worker_status("poller")["ponds"][str(USER)]
    assert pond["result"] == "skipped" and pond["sensor_recorded_at"] == "t"
    assert pond["reason"].startswith("unusable sensor reading")


def test_health_report_counts_failures_and_keeps_last_success_when_every_pond_fails(monkeypatch):
    storage = make_storage()
    worker = _worker(storage)
    worker.run_cycle()
    first_success = storage.fetch_worker_status("poller")["last_success_at"]
    assert first_success

    def broken(registry, user_id, row):
        raise RuntimeError("broken pond")

    monkeypatch.setattr(poller, "_poll_user", broken)
    worker.run_cycle()
    worker.run_cycle()
    status = storage.fetch_worker_status("poller")
    assert status["last_success_at"] == first_success, "a cycle where every pond failed is not a success"
    assert status["ponds"][str(USER)]["result"] == "failed"
    assert status["ponds"][str(USER)]["failures_total"] == 2
    assert status["ponds"][str(USER)]["reason"] == "RuntimeError: broken pond"


def test_health_report_one_failing_pond_does_not_stop_success(monkeypatch):
    storage = make_storage()
    _add_pond(storage, 500)
    real = poller._poll_user

    def one_broken(registry, user_id, row):
        if user_id == 500:
            raise RuntimeError("broken pond")
        return real(registry, user_id, row)

    monkeypatch.setattr(poller, "_poll_user", one_broken)
    _worker(storage).run_cycle()
    status = storage.fetch_worker_status("poller")
    assert status["last_success_at"] is not None
    assert status["ponds"]["500"]["result"] == "failed" and status["ponds"][str(USER)]["result"] == "ok"


def test_health_report_written_when_the_pond_list_cannot_be_read():
    storage = make_storage()
    _worker(storage, "w0").run_cycle()
    previous = storage.fetch_worker_status("poller")["last_success_at"]
    storage.failing.add("fetch_active_pond_configs")
    storage.release_lease("poller", "w0")
    assert _worker(storage, "w2").run_cycle() is True
    status = storage.fetch_worker_status("poller")
    assert status["holder"] == "w2" and status["ponds"] == {}
    assert status["last_success_at"] == previous, "a new worker keeps the previous worker's last success"


def test_health_report_write_failure_does_not_stop_the_cycle():
    storage = make_storage()
    storage.failing.add("record_worker_status")
    assert _worker(storage).run_cycle() is True
    assert storage.rows("pond_chemistry_evaluations"), "the pond was still polled"
