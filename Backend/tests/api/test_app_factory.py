"""koi.api.create_app and the python -m koi.api / koi.worker entry points."""
import pytest

from conftest import make_settings
from koi.api import create_app
from koi.storage import client


def test_create_app_serves_health_and_every_route():
    app = create_app(make_settings())
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert {"/health", "/events/feeding", "/events/water-change", "/events/top-up",
            "/events/algal-scrub", "/events/algae-rating", "/events/algae-rating/undo",
            "/ratings/algae/<int:user_id>", "/assessment/<int:user_id>",
            "/assessment/evaporation/<int:user_id>", "/assessment/algae/<int:user_id>",
            "/assessment/all/<int:user_id>", "/forecast/<int:user_id>",
            "/forecast/evaporation/<int:user_id>", "/forecast/algae/<int:user_id>"} <= rules
    resp = app.test_client().get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["domains"] == ["chemistry", "evaporation", "algae"]


def test_create_app_applies_cors_origins():
    app = create_app(make_settings(cors_origins="https://pond.example"))
    allowed = app.test_client().get("/health", headers={"Origin": "https://pond.example"})
    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://pond.example"
    other = app.test_client().get("/health", headers={"Origin": "https://other.example"})
    assert "Access-Control-Allow-Origin" not in other.headers


def test_storage_client_needs_credentials():
    create_app(make_settings())
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        client.get_client()


@pytest.mark.parametrize("env, starts_poller", [
    ("development", True), ("production", False), ("test", False)])
def test_api_entry_point_runs_poller_only_in_development(monkeypatch, env, starts_poller):
    import koi.api.__main__ as api_main
    from koi.worker import poller

    started = []
    monkeypatch.setattr(poller, "start", lambda settings, blocking=False: started.append(blocking))
    app = api_main.main(make_settings(env=env), run=False)
    assert app.name == "koi.api"
    assert started == ([False] if starts_poller else [])


def test_worker_entry_point_blocks_on_the_configured_interval(monkeypatch):
    import koi.worker.__main__ as worker_main
    from koi.worker import poller

    calls = []
    monkeypatch.setattr(poller, "start", lambda settings, blocking=False:
                        calls.append((settings.poll_interval_minutes, blocking)))
    worker_main.main(make_settings(poll_interval_minutes=5))
    assert calls == [(5, True)]


def test_poller_schedules_at_the_configured_interval(monkeypatch):
    import apscheduler.schedulers.background as background

    from koi.worker import poller

    jobs = []

    class FakeScheduler:
        def add_job(self, fn, trigger, minutes, next_run_time):
            jobs.append((fn, trigger, minutes))

        def start(self):
            jobs.append("started")

    monkeypatch.setattr(background, "BackgroundScheduler", FakeScheduler, raising=False)
    poller.start(make_settings(poll_interval_minutes=3))
    assert jobs == [(poller._poll_once, "interval", 3), "started"]
