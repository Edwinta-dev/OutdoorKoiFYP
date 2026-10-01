"""Request logging, request IDs and GET /metrics for both Flask services.

instrument_app(app, service) does four things for every request:

- takes the request id from the X-Request-ID header (when it is a short
  token of letters, digits, '.', '_' or '-') or generates one, puts it on
  every log line written while handling the request, and returns it in
  the response's X-Request-ID header;
- stamps pond_id on those lines: the route's user_id, or whatever
  pond_id_from(request) returns (the camera's X-User-ID header);
- counts the request and times it, by route pattern, method and status;
- logs one "request" line (DEBUG for /metrics and the health checks, so
  scrapes do not flood the log).

It also serves GET /metrics from app.extensions["koi_metrics"], plus
whatever extra_metrics() returns at scrape time (the API's poller
gauges, read from storage).
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Callable, Optional

from flask import Flask, Request, Response, g, request

from koi.logs import log_event, reset_pond_id, reset_request_id, set_pond_id, set_request_id
from koi.metrics import CONTENT_TYPE, Metrics

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_ROUTES = {"/metrics", "/health", "/ready", "/"}

log = logging.getLogger(__name__)


def request_id_from(header: Optional[str]) -> str:
    """The caller's request id if it is safe to log, otherwise a new one."""
    if header and _VALID_REQUEST_ID.match(header):
        return header
    return uuid.uuid4().hex


def _route_user_id(req: Request) -> Optional[object]:
    return (req.view_args or {}).get("user_id")


def instrument_app(app: Flask, service: str, extra_metrics: Optional[Callable[[], str]] = None,
                   pond_id_from: Callable[[Request], Optional[object]] = _route_user_id) -> Metrics:
    metrics: Metrics = app.extensions.setdefault("koi_metrics", Metrics())
    requests_total = metrics.counter(
        "koi_http_requests_total", "HTTP requests handled, by route pattern, method and status.",
        ["service", "route", "method", "status"])
    latency = metrics.histogram(
        "koi_http_request_duration_seconds", "Time to handle an HTTP request, by route pattern and method.",
        ["service", "route", "method"])

    @app.before_request
    def _start() -> None:
        g.koi_started = time.perf_counter()
        g.koi_request_id = request_id_from(request.headers.get(REQUEST_ID_HEADER))
        g.koi_request_token = set_request_id(g.koi_request_id)
        pond_id = pond_id_from(request)
        g.koi_pond_token = set_pond_id(pond_id) if pond_id is not None else None

    @app.after_request
    def _finish(response: Response) -> Response:
        started = g.get("koi_started")
        if started is None:  # a failure before _start ran
            return response
        elapsed = time.perf_counter() - started
        route = request.url_rule.rule if request.url_rule is not None else "unmatched"
        requests_total.inc(service=service, route=route, method=request.method, status=response.status_code)
        latency.observe(elapsed, service=service, route=route, method=request.method)
        response.headers[REQUEST_ID_HEADER] = g.koi_request_id
        log_event(log, "request", level=logging.DEBUG if route in _QUIET_ROUTES else logging.INFO,
                  method=request.method, route=route, status=response.status_code,
                  duration_ms=round(elapsed * 1000, 1))
        return response

    @app.teardown_request
    def _clear(_exc: Optional[BaseException]) -> None:
        if g.get("koi_pond_token") is not None:
            reset_pond_id(g.koi_pond_token)
        if g.get("koi_request_token") is not None:
            reset_request_id(g.koi_request_token)

    def metrics_view() -> Response:
        body = metrics.render() + (extra_metrics() if extra_metrics else "")
        return Response(body, mimetype=None, content_type=CONTENT_TYPE)

    app.add_url_rule("/metrics", "metrics", metrics_view, methods=["GET"])
    return metrics


__all__ = ["REQUEST_ID_HEADER", "instrument_app", "request_id_from"]
