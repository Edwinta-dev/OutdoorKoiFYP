"""The route table of the digital twin API: every route, its versioned
path, its old path, and the models of its request and responses.

Routes are declared with `route(...)` instead of Flask's `@bp.route`, so
one declaration registers the Flask URL rules and records the
Operation that koi/api/openapi.py turns into docs/api/openapi.yaml. A
route missing from this table cannot reach the OpenAPI document, and
tests/api/test_openapi.py checks that every URL rule of the app is in it.

Versioning. Every route is served under /v1. The paths the app calls
today (/assessment/..., /forecast/..., /events/..., /ratings/...) stay
as deprecated aliases of their /v1 route: same view, same response, plus
the headers below. They are removed by the mobile repositories issue
once the app calls /v1. /health and /ready stay unversioned as well,
without deprecation, because probes and monitoring call them.

    Deprecation: @1790985600            (RFC 9745: deprecated since 2026-10-03)
    Link: </v1/...>; rel="successor-version"   (when the /v1 path can be built)

Caching. Every GET that returns 200 JSON carries a strong ETag over the
response body and Cache-Control: private, no-cache, and answers a
matching If-None-Match with 304 and no body. no-cache makes a client
revalidate on every use; because the ETag is computed from the body the
server would send now (freshness included, see koi/api/dashboard.py), a
304 can never keep showing a reading as fresh after it has gone stale.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Optional

from flask import Blueprint, Response, request, url_for
from pydantic import BaseModel
from werkzeug.routing import BuildError

# RFC 9745 Deprecation: the date the unversioned paths were deprecated.
DEPRECATED_SINCE = datetime(2026, 10, 3, tzinfo=timezone.utc)
DEPRECATION_HEADER = f"@{int(DEPRECATED_SINCE.timestamp())}"
CACHE_CONTROL = "private, no-cache"


@dataclass(frozen=True)
class Operation:
    method: str
    rule: str                          # Flask URL rule
    endpoint: str                      # Flask endpoint name inside the blueprint
    summary: str
    tag: str
    responses: dict[int, Optional[type[BaseModel]]]
    body: Optional[type[BaseModel]] = None
    query: Optional[type[BaseModel]] = None
    auth: bool = True
    deprecated: bool = False
    successor: Optional[str] = None    # the /v1 rule an alias stands for
    description: str = ""
    errors: tuple[int, ...] = field(default=())


OPERATIONS: list[Operation] = []
_BY_ENDPOINT: dict[str, Operation] = {}


def route(bp: Blueprint, method: str, rule: str, *, summary: str, tag: str,
          response: Optional[type[BaseModel]], status: int = 200, body: Optional[type[BaseModel]] = None,
          legacy_body: Optional[type[BaseModel]] = None, query: Optional[type[BaseModel]] = None,
          legacy: Optional[str] = None, alias: Optional[str] = None, auth: bool = True,
          errors: tuple[int, ...] = ()) -> Callable:
    """Registers view at rule (a /v1 path) and records it. legacy is an
    old path served as a deprecated alias (with legacy_body when its
    request body differs); alias is an old path kept without deprecation."""
    def register(view: Callable) -> Callable:
        name = view.__name__
        description = (view.__doc__ or "").strip()
        primary = Operation(method=method, rule=rule, endpoint=name, summary=summary, tag=tag,
                            responses={status: response}, body=body, query=query, auth=auth,
                            description=description, errors=errors)
        ops = [primary]
        if legacy is not None:
            ops.append(replace(primary, rule=legacy, endpoint=f"legacy_{name}", body=legacy_body or body,
                               deprecated=True, successor=rule))
        if alias is not None:
            ops.append(replace(primary, rule=alias, endpoint=f"unversioned_{name}", successor=rule))
        for op in ops:
            bp.add_url_rule(op.rule, endpoint=op.endpoint, view_func=view, methods=[method])
            OPERATIONS.append(op)
            _BY_ENDPOINT[f"{bp.name}.{op.endpoint}"] = op
        return view
    return register


def operation_for(endpoint: Optional[str]) -> Optional[Operation]:
    """The Operation of a Flask endpoint ("twin.get_dashboard")."""
    return _BY_ENDPOINT.get(endpoint or "")


def openapi_path(rule: str) -> str:
    """A Flask rule as an OpenAPI path: <int:user_id> becomes {pond}
    under /v1 and {user_id} on the old paths."""
    name = "pond" if rule.startswith("/v1/") else "user_id"
    path = re.sub(r"<(?:\w+:)?user_id>", "{" + name + "}", rule)
    return re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", path)


def after_request(response: Response) -> Response:
    """Deprecation headers on the old paths; ETag, conditional GET and
    Cache-Control on JSON GETs."""
    op = operation_for(request.endpoint)
    if op is not None and op.deprecated:
        response.headers["Deprecation"] = DEPRECATION_HEADER
        successor = _successor_url(op)
        if successor is not None:
            response.headers["Link"] = f'<{successor}>; rel="successor-version"'
    if request.method == "GET" and response.status_code == 200 and response.mimetype == "application/json":
        response.headers["Cache-Control"] = CACHE_CONTROL
        response.vary.add("Authorization")
        response.add_etag()
        response.make_conditional(request)
    return response


def _successor_url(op: Operation) -> Optional[str]:
    target = next((o for o in OPERATIONS if o.rule == op.successor and o.method == op.method), None)
    if target is None:
        return None
    blueprint = (request.endpoint or "").split(".")[0]
    try:
        return url_for(f"{blueprint}.{target.endpoint}", **(request.view_args or {}))
    except BuildError:  # the /v1 path needs a pond the old path does not carry (events)
        return None


__all__ = ["CACHE_CONTROL", "DEPRECATED_SINCE", "DEPRECATION_HEADER", "OPERATIONS", "Operation", "after_request",
           "openapi_path", "operation_for", "route"]
