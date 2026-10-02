"""koi.metrics and GET /metrics on the API (issue #10)."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DASHBOARD_PAYLOAD, POND_FIXTURE, USER, api_client, make_settings, make_storage
from koi.api import create_app
from koi.metrics import CONTENT_TYPE, Metrics
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage
from koi.worker import poller


def _value(text: str, sample: str) -> float:
    """The value of one exposition line, by its name and labels."""
    match = re.search(rf"^{re.escape(sample)} (\S+)$", text, re.M)
    assert match, f"{sample} not in:\n{text}"
    return float(match.group(1))


def test_metrics_counter_gauge_and_histogram_render_in_prometheus_text():
    m = Metrics()
    c = m.counter("koi_things_total", "Things.", ["kind"])
    c.inc(kind="a")
    c.inc(2, kind="a")
    m.gauge("koi_level", "Level.").set(1.5)
    h = m.histogram("koi_wait_seconds", "Wait.", buckets=(0.1, 1.0))
    h.observe(0.05)
    h.observe(0.5)
    h.observe(5)
    assert m.render() == (
        "# HELP koi_level Level.\n# TYPE koi_level gauge\nkoi_level 1.5\n"
        "# HELP koi_things_total Things.\n# TYPE koi_things_total counter\nkoi_things_total{kind=\"a\"} 3\n"
        "# HELP koi_wait_seconds Wait.\n# TYPE koi_wait_seconds histogram\n"
        "koi_wait_seconds_bucket{le=\"0.1\"} 1\nkoi_wait_seconds_bucket{le=\"1\"} 2\n"
        "koi_wait_seconds_bucket{le=\"+Inf\"} 3\nkoi_wait_seconds_sum 5.55\nkoi_wait_seconds_count 3\n")


def test_metrics_label_values_are_escaped_and_label_names_checked():
    m = Metrics()
    c = m.counter("koi_x_total", "X.", ["route"])
    c.inc(route='a"b\\c\nd')
    assert 'koi_x_total{route="a\\"b\\\\c\\nd"} 1' in m.render()
    with pytest.raises(ValueError):
        c.inc(other="x")
    assert m.counter("koi_x_total", "X.", ["route"]) is c, "same family returned"
    with pytest.raises(ValueError):
        m.gauge("koi_x_total", "X.")


def test_metrics_endpoint_counts_requests_by_route_pattern():
    client = api_client(create_app(make_settings(), storage=make_storage()))
    client.get("/health")
    client.get("/health")
    client.get(f"/assessment/{USER}")
    client.get("/no-such-route")
    resp = client.get("/metrics")
    assert resp.status_code == 200 and resp.content_type == CONTENT_TYPE
    text = resp.get_data(as_text=True)
    assert _value(text, 'koi_http_requests_total{service="api",route="/health",method="GET",status="200"}') == 2
    assert 'route="/assessment/<int:user_id>"' in text and f"/assessment/{USER}" not in text
    assert _value(text, 'koi_http_requests_total{service="api",route="unmatched",method="GET",status="404"}') == 1
    assert _value(text, 'koi_http_request_duration_seconds_count{service="api",route="/health",method="GET"}') == 2


def test_metrics_endpoint_reports_the_poller_from_storage():
    storage = MemoryStorage.from_json(POND_FIXTURE)  # real clock
    recorded = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    storage.set_dashboard_payload(USER, {**DASHBOARD_PAYLOAD,
                                         "raw_sensor": {**DASHBOARD_PAYLOAD["raw_sensor"], "recorded_at": recorded}})
    assert poller.Worker(make_settings(), EngineRegistry(storage), holder="w1").run_cycle() is True

    text = create_app(make_settings(), storage=storage).test_client().get("/metrics").get_data(as_text=True)
    assert _value(text, "koi_storage_up") == 1
    assert _value(text, "koi_poll_cycle_duration_seconds") >= 0
    assert 0 <= _value(text, "koi_poll_last_success_age_seconds") < 60
    assert _value(text, f'koi_poll_pond_failed{{pond_id="{USER}"}}') == 0
    assert _value(text, f'koi_poll_pond_failures_total{{pond_id="{USER}"}}') == 0
    assert 595 <= _value(text, f'koi_sensor_reading_age_seconds{{pond_id="{USER}"}}') < 660
    assert 0 <= _value(text, f'koi_snapshot_age_seconds{{pond_id="{USER}"}}') < 60
    assert _value(text, "koi_poller_lease_held") == 1
    assert "# TYPE koi_poll_pond_failures_total counter" in text


def test_metrics_endpoint_counts_pond_failures_across_cycles(monkeypatch):
    storage = make_storage()

    def broken(registry, user_id, row):
        raise RuntimeError("broken pond")

    monkeypatch.setattr(poller, "_poll_user", broken)
    worker = poller.Worker(make_settings(), EngineRegistry(storage), holder="w1")
    worker.run_cycle()
    worker.run_cycle()
    text = create_app(make_settings(), storage=storage).test_client().get("/metrics").get_data(as_text=True)
    assert _value(text, f'koi_poll_pond_failed{{pond_id="{USER}"}}') == 1
    assert _value(text, f'koi_poll_pond_failures_total{{pond_id="{USER}"}}') == 2
    assert "koi_poll_last_success_age_seconds" not in text, "no cycle has succeeded"


def test_metrics_endpoint_still_answers_when_storage_is_down():
    storage = make_storage()
    storage.failing.add("fetch_worker_status")
    resp = create_app(make_settings(), storage=storage).test_client().get("/metrics")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert _value(text, "koi_storage_up") == 0
    assert "koi_poll_cycle_duration_seconds" not in text
