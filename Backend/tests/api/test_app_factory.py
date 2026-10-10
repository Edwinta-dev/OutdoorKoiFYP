"""koi.api.create_app and the python -m koi.api / koi.worker entry points."""
import pytest

from conftest import make_settings
from koi.api import create_app
from koi.storage import MemoryStorage, StorageError, SupabaseStorage


def test_create_app_serves_health_and_every_route():
    app = create_app(make_settings())
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert {"/health", "/v1/ponds/<int:user_id>/events/feeding",
            "/v1/ponds/<int:user_id>/events/water-change",
            "/v1/ponds/<int:user_id>/events/top-up",
            "/v1/ponds/<int:user_id>/events/algal-scrub",
            "/v1/ponds/<int:user_id>/events/algae-rating",
            "/v1/ponds/<int:user_id>/events/algae-rating/undo",
            "/v1/ponds/<int:user_id>/ratings/algae",
            "/v1/ponds/<int:user_id>/assessments/chemistry",
            "/v1/ponds/<int:user_id>/assessments/evaporation",
            "/v1/ponds/<int:user_id>/assessments/algae",
            "/v1/ponds/<int:user_id>/assessments",
            "/v1/ponds/<int:user_id>/forecasts/chemistry",
            "/v1/ponds/<int:user_id>/forecasts/evaporation",
            "/v1/ponds/<int:user_id>/forecasts/algae"} <= rules
    resp = app.test_client().get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["domains"] == ["chemistry", "evaporation", "algae"]


def test_create_app_applies_cors_origins():
    app = create_app(make_settings(cors_origins="https://pond.example"))
    allowed = app.test_client().get("/health", headers={"Origin": "https://pond.example"})
    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://pond.example"
    other = app.test_client().get("/health", headers={"Origin": "https://other.example"})
    assert "Access-Control-Allow-Origin" not in other.headers


@pytest.mark.parametrize("choice, kind", [("memory", MemoryStorage), ("supabase", SupabaseStorage)])
def test_settings_storage_selects_the_implementation(choice, kind):
    app = create_app(make_settings(storage=choice))
    assert isinstance(app.extensions["koi_storage"], kind)
    assert app.extensions["koi_registry"].storage is app.extensions["koi_storage"]


def test_supabase_storage_needs_credentials_only_when_used():
    app = create_app(make_settings(storage="supabase"))
    with pytest.raises(StorageError, match="SUPABASE_URL"):
        app.extensions["koi_storage"].fetch_active_pond_configs()


@pytest.mark.parametrize("env, starts_poller", [
    ("development", True), ("production", False), ("test", False)])
def test_api_entry_point_runs_poller_only_in_development(monkeypatch, env, starts_poller):
    import koi.api.__main__ as api_main
    from koi.worker import poller

    started = []
    monkeypatch.setattr(poller, "start", lambda settings, registry, blocking=False:
                        started.append((registry, blocking)))
    app = api_main.main(make_settings(env=env), run=False)
    assert app.name == "koi.api"
    # In development the poller shares the app's registry (and so its locks).
    assert started == ([(app.extensions["koi_registry"], False)] if starts_poller else [])


def test_worker_entry_point_blocks_on_the_configured_interval(monkeypatch):
    import koi.worker.__main__ as worker_main
    from koi.worker import poller

    calls = []
    storage = MemoryStorage()
    monkeypatch.setattr(poller, "start", lambda settings, registry, blocking=False:
                        calls.append((settings.poll_interval_minutes, registry.storage, blocking)))
    worker_main.main(make_settings(poll_interval_minutes=5), storage=storage)
    assert calls == [(5, storage, True)]


def test_poller_schedules_at_the_configured_interval(monkeypatch):
    import apscheduler.schedulers.background as background

    from koi.registry import EngineRegistry
    from koi.worker import poller

    jobs = []

    class FakeScheduler:
        def add_job(self, fn, trigger, **kwargs):
            jobs.append((fn, trigger, kwargs))

        def start(self):
            jobs.append("started")

    monkeypatch.setattr(background, "BackgroundScheduler", FakeScheduler, raising=False)
    registry = EngineRegistry(MemoryStorage())
    poller.start(make_settings(poll_interval_minutes=3, evaluation_retention_days=45), registry)
    [(fn, trigger, kwargs), (daily, daily_trigger, daily_kwargs), (calibration, cal_trigger, cal_kwargs),
     started] = jobs
    # Calibration (issue #32) runs once a day after retention, over the same registry.
    assert cal_trigger == "cron" and (cal_kwargs["hour"], cal_kwargs["minute"]) == (4, 0)
    assert cal_kwargs["timezone"] == "Asia/Singapore" and calibration.__self__.registry is registry
    assert (trigger, kwargs["minutes"], started) == ("interval", 3, "started")
    # The job is a lease-gated worker cycle over the given registry.
    assert fn.__func__ is poller.Worker.run_cycle and fn.__self__.registry is registry
    # Evaluation retention runs once a day, local time, over the same storage.
    assert daily_trigger == "cron"
    assert (daily_kwargs["hour"], daily_kwargs["minute"], daily_kwargs["timezone"]) == (3, 30, "Asia/Singapore")
    assert daily.__self__.storage is registry.storage and daily.__self__.retention_days == 45


def test_blocking_worker_releases_the_lease_when_stopped(monkeypatch):
    import apscheduler.schedulers.blocking as blocking

    from koi.registry import EngineRegistry
    from koi.worker import poller

    storage = MemoryStorage()

    class FakeScheduler:
        def add_job(self, fn, trigger, **kwargs):
            if trigger == "interval":
                self.fn = fn

        def start(self):
            assert self.fn() is True  # one cycle takes the lease
            assert storage.rows("worker_lease") != []
            raise KeyboardInterrupt

    monkeypatch.setattr(blocking, "BlockingScheduler", FakeScheduler, raising=False)
    poller.start(make_settings(), EngineRegistry(storage), blocking=True)
    assert storage.rows("worker_lease") == []


def test_gunicorn_config_serves_the_app_factory_on_two_workers():
    import runpy
    from pathlib import Path

    import koi.api

    config = runpy.run_path(str(Path(__file__).resolve().parents[2] / "gunicorn.conf.py"))
    assert config["workers"] == 2
    module, call = config["wsgi_app"].split(":")
    assert module == "koi.api" and call == "create_app()"
    assert callable(getattr(koi.api, call.removesuffix("()")))
