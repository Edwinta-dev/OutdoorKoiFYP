"""koi.error_tracking: Sentry for the API, worker and camera service.

Events go to an in-memory transport; nothing is sent over the network.
The DSN below points at a reserved .invalid host.
"""
import logging

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from conftest import USER, api_client, make_settings, make_storage, mint_token
from koi import error_tracking
from koi.error_tracking import FILTERED, MODEL_VERSION, init_error_tracking, scrub, scrub_event, scrub_text
from koi.models.pond_twin import SNAPSHOT_VERSION
from koi.storage import MemoryStorage

TEST_DSN = "https://public@sentry.koi-test.invalid/1"


class RecordingTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)

    def flush(self, timeout, callback=None):
        return None

    def kill(self):
        return None


@pytest.fixture
def transport():
    recorder = RecordingTransport()
    yield recorder
    # Back to a disabled client so later tests send nothing anywhere.
    sentry_sdk.init(dsn=None)
    sentry_sdk.get_global_scope().remove_tag("service")
    sentry_sdk.get_global_scope().remove_tag("model_version")


def _enabled(transport, service, **overrides):
    settings = make_settings(sentry_dsn=TEST_DSN, **overrides)
    assert init_error_tracking(settings, service, transport=transport)
    return settings


def test_off_without_a_dsn(monkeypatch):
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert init_error_tracking(make_settings(), "api") is False
    assert init_error_tracking(make_settings(sentry_dsn="  "), "api") is False
    assert calls == []


def test_settings_default_to_off_and_read_the_sentry_names(monkeypatch):
    from koi.settings import Settings

    for name in ("SENTRY_DSN", "SENTRY_ENVIRONMENT", "SENTRY_RELEASE"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)
    assert (s.sentry_dsn.get_secret_value(), s.sentry_environment, s.sentry_release) == ("", "", "")
    monkeypatch.setenv("SENTRY_DSN", TEST_DSN)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    monkeypatch.setenv("SENTRY_RELEASE", "koi@abc123")
    s = Settings(_env_file=None)
    assert (s.sentry_dsn.get_secret_value(), s.sentry_environment, s.sentry_release) == \
        (TEST_DSN, "staging", "koi@abc123")
    assert TEST_DSN not in repr(s)


def test_environment_release_and_tags(transport):
    _enabled(transport, "worker", env="production")
    sentry_sdk.capture_message("boom")
    [event] = transport.events
    assert event["environment"] == "production"
    assert event["release"] == f"koi@{error_tracking.package_version()}+twin.{SNAPSHOT_VERSION}"
    assert event["tags"]["service"] == "worker"
    assert event["tags"]["model_version"] == MODEL_VERSION


def test_environment_and_release_overrides(transport):
    _enabled(transport, "camera", sentry_environment="staging", sentry_release="koi@abc123")
    sentry_sdk.capture_message("boom")
    [event] = transport.events
    assert (event["environment"], event["release"]) == ("staging", "koi@abc123")


def test_scrub_removes_location_email_and_tokens():
    token = mint_token()
    data = {
        "latitude": 1.35, "longitude": 103.8, "lat": 1.35, "lng": 103.8, "manualpostallocation": "123456",
        "ClosestStations": ["S24"], "location__latitude": 1.3, "location": {"latitude": 1.3},
        "email": "owner@example.com", "Authorization": f"Bearer {token}", "X-Device-Token": "abc",
        "SUPABASE_SERVICEROLE_KEY": "sk", "password": "p", "nested": [{"api_key": "k"}],
        "message": f"failed for owner@example.com with Bearer {token} and {token}",
        "url": "https://user:pass@host.invalid/x?token=abc&volume=1200",
        "headers": [["Authorization", "Bearer x"], ["Accept", "application/json"]],
        "latency_ms": 12, "volume": 1200, "pond_id": "455", "route": "/v1/ponds/<int:user_id>/assessments/chemistry",
    }
    out = scrub(data)
    for key in ("latitude", "longitude", "lat", "lng", "manualpostallocation", "ClosestStations",
                "location__latitude", "location", "email", "Authorization", "X-Device-Token",
                "SUPABASE_SERVICEROLE_KEY", "password"):
        assert out[key] == FILTERED, key
    assert out["nested"] == [{"api_key": FILTERED}]
    assert "owner@example.com" not in out["message"] and token not in out["message"]
    assert out["message"].startswith("failed for " + FILTERED)
    assert "pass" not in out["url"] and "token=abc" not in out["url"]
    assert out["url"].endswith(f"?token={FILTERED}&volume=1200")
    assert out["headers"] == [["Authorization", FILTERED], ["Accept", "application/json"]]
    assert (out["latency_ms"], out["volume"], out["pond_id"], out["route"]) == \
        (12, 1200, "455", "/v1/ponds/<int:user_id>/assessments/chemistry")


def test_scrub_text_leaves_ordinary_messages_alone():
    text = "pond_poll_failed: KeyError 'temp' at 2026-08-20T10:00:00+08:00, volume=1200 L"
    assert scrub_text(text) == text


def test_scrub_event_keeps_only_the_user_id_and_drops_request_body():
    event = {"user": {"id": 455, "email": "owner@example.com", "ip_address": "10.0.0.5"},
             "request": {"data": {"latitude": 1.3}, "cookies": {"sb": "x"}, "env": {"REMOTE_ADDR": "10.0.0.5"},
                         "headers": {"Authorization": "Bearer x", "User-Agent": "Dart/3.11"}}}
    out = scrub_event(event)
    assert out["user"] == {"id": "455"}
    assert "data" not in out["request"] and "cookies" not in out["request"]
    assert out["request"]["env"] == {}
    assert out["request"]["headers"] == {"Authorization": FILTERED, "User-Agent": "Dart/3.11"}


def test_api_unhandled_error_is_reported_without_the_token(transport, monkeypatch):
    from koi.api import create_app

    monkeypatch.setattr(error_tracking.sentry_sdk, "init", _with_transport(transport))
    storage = make_storage()
    app = create_app(make_settings(sentry_dsn=TEST_DSN), storage=storage)

    def explode(user_id):
        raise RuntimeError("engine fell over for owner@example.com")

    monkeypatch.setattr(storage, "fetch_latest_evaluation", explode)
    token = mint_token()
    resp = api_client(app, token).get(f"/v1/ponds/{USER}/assessments/chemistry?lat=1.35&lon=103.8")
    assert resp.status_code == 500
    assert transport.events, "the unhandled error was not reported"
    event = transport.events[-1]
    assert event["tags"]["service"] == "api"
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"
    text = repr(event)
    assert token not in text
    assert "owner@example.com" not in text
    assert "103.8" not in text
    assert event["request"]["headers"]["Authorization"] == FILTERED


def test_camera_service_reports_with_its_tag(transport, monkeypatch):
    from koi.camera import create_app

    monkeypatch.setattr(error_tracking.sentry_sdk, "init", _with_transport(transport))
    app = create_app(make_settings(sentry_dsn=TEST_DSN), storage=MemoryStorage())
    with app.app_context():
        logging.getLogger("koi.camera.camera").error("upload store failed")
    [event] = [e for e in transport.events if e.get("logentry", {}).get("message") == "upload store failed"]
    assert event["tags"]["service"] == "camera"


def test_worker_starts_error_tracking(transport, monkeypatch):
    import koi.worker.__main__ as worker_main
    from koi.worker import poller

    monkeypatch.setattr(error_tracking.sentry_sdk, "init", _with_transport(transport))
    monkeypatch.setattr(poller, "start", lambda settings, registry, blocking=False: None)
    worker_main.main(make_settings(sentry_dsn=TEST_DSN), storage=MemoryStorage())
    logging.getLogger("koi.worker.poller").error("pond_poll_failed")
    [event] = [e for e in transport.events if e.get("logentry", {}).get("message") == "pond_poll_failed"]
    assert event["tags"]["service"] == "worker"


_real_init = sentry_sdk.init


def _with_transport(transport):
    """sentry_sdk.init with the recording transport swapped in, for the
    services that call init_error_tracking themselves."""
    def init(**kwargs):
        kwargs["transport"] = transport
        return _real_init(**kwargs)
    return init
