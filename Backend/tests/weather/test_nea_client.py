"""NeaClient: retry and backoff, rate limits, malformed responses and
pagination, against a fake transport (no network)."""
from __future__ import annotations

from datetime import date

import pytest
from nea_fakes import FakeClock, FakeTransport, make_client, response

from koi.weather.nea import NeaFetchError


def test_latest_fetch_returns_the_data_object_and_sends_the_key():
    transport = FakeTransport()
    client = make_client(transport, api_key="test-key")
    pages = client.fetch("rainfall")
    assert pages == [response("rainfall")["data"]]
    url, headers = transport.calls[0]
    assert url == "https://nea.test.invalid/v2/real-time/api/rainfall"
    assert headers["x-api-key"] == "test-key"


def test_no_key_header_without_a_key():
    transport = FakeTransport()
    make_client(transport).fetch("uv")
    assert "x-api-key" not in transport.calls[0][1]


def test_day_fetch_follows_pagination_tokens():
    transport = FakeTransport()
    first, second = response("rainfall"), response("rainfall")
    first["data"]["paginationToken"] = "b2Zmc2V0PTI1"
    transport.queue("rainfall", first, date="2026-09-01")
    transport.queue("rainfall", second, date="2026-09-01", paginationToken="b2Zmc2V0PTI1")
    pages = make_client(transport).fetch("rainfall", day=date(2026, 9, 1))
    assert len(pages) == 2
    assert [c[0].split("?")[1] for c in transport.calls] == [
        "date=2026-09-01", "date=2026-09-01&paginationToken=b2Zmc2V0PTI1"]


def test_runaway_pagination_is_an_error():
    transport = FakeTransport()
    body = response("rainfall")
    body["data"]["paginationToken"] = "again"
    transport.handler = lambda product, params: body
    with pytest.raises(NeaFetchError) as err:
        make_client(transport, max_pages=3).fetch("rainfall")
    assert err.value.kind == "pagination"
    assert len(transport.calls) == 3


def test_server_errors_are_retried_with_exponential_backoff():
    transport = FakeTransport()
    clock = FakeClock()
    transport.queue("rainfall", (503, {}, b"busy"), (502, {}, b""), response("rainfall"))
    client = make_client(transport, clock, backoff_seconds=2.0)
    assert client.fetch("rainfall")
    assert clock.sleeps == [2.0, 4.0]
    assert client.retries == 2 and client.requests == 3


def test_backoff_is_capped():
    transport = FakeTransport()
    clock = FakeClock()
    transport.handler = lambda product, params: (500, {}, b"")
    with pytest.raises(NeaFetchError) as err:
        make_client(transport, clock, max_attempts=4, backoff_seconds=10, max_backoff_seconds=25).fetch("uv")
    assert err.value.kind == "http"
    assert clock.sleeps == [10, 20, 25, 25]


def test_rate_limit_waits_retry_after_then_succeeds():
    transport = FakeTransport()
    clock = FakeClock()
    transport.queue("uv", (429, {"Retry-After": "7"}, b""), (429, {}, b""), response("uv"))
    client = make_client(transport, clock, backoff_seconds=1.5)
    assert client.fetch("uv")
    assert clock.sleeps == [7.0, 3.0]  # Retry-After, then backoff for attempt 2


def test_rate_limit_exhausted_reports_rate_limited():
    transport = FakeTransport()
    transport.handler = lambda product, params: (429, {}, b"")
    with pytest.raises(NeaFetchError) as err:
        make_client(transport, max_attempts=2).fetch("uv")
    assert err.value.kind == "rate_limited"


def test_network_errors_are_retried():
    transport = FakeTransport()
    transport.queue("uv", TimeoutError("timed out"), ConnectionResetError(), response("uv"))
    client = make_client(transport)
    assert client.fetch("uv")
    assert client.requests == 3


def test_body_that_is_not_json_is_retried_then_reported_malformed():
    transport = FakeTransport()
    transport.handler = lambda product, params: (200, {}, b"<html>maintenance</html>")
    with pytest.raises(NeaFetchError) as err:
        make_client(transport, max_attempts=3).fetch("uv")
    assert err.value.kind == "malformed"
    assert len(transport.calls) == 3


@pytest.mark.parametrize("body, kind", [
    ([1, 2], "malformed"),
    ({"code": 0, "data": None}, "malformed"),
    ({"code": 17, "errorMsg": "Invalid date", "data": {}}, "api"),
])
def test_wrong_shaped_or_error_bodies_are_not_retried(body, kind):
    transport = FakeTransport()
    transport.handler = lambda product, params: body
    with pytest.raises(NeaFetchError) as err:
        make_client(transport).fetch("uv")
    assert err.value.kind == kind
    assert len(transport.calls) == 1


def test_client_errors_are_not_retried():
    transport = FakeTransport()
    transport.handler = lambda product, params: (404, {}, b"")
    with pytest.raises(NeaFetchError) as err:
        make_client(transport).fetch("uv")
    assert (err.value.kind, err.value.detail) == ("http", "HTTP 404")
    assert len(transport.calls) == 1


def test_requests_are_spaced_by_the_minimum_interval():
    transport = FakeTransport()
    clock = FakeClock()
    client = make_client(transport, clock, min_interval_seconds=2.5)
    client.fetch("uv")
    client.fetch("rainfall")
    client.fetch("rainfall")
    assert clock.sleeps == [2.5, 2.5]
