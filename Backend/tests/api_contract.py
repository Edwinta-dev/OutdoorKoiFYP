"""Checks every digital twin API response against the OpenAPI document.

tests/conftest.py wraps Flask's test client so that every response the
koi.api app sends in any test is validated here: a JSON body against
the schema documented for its route and status (the ErrorEnvelope for an
error), a 304 is for a GET and has no body, and the status itself is
documented for the route. A response that breaks the contract fails the
test that produced it.
"""
from __future__ import annotations

from functools import cache
from typing import Any, Optional

from jsonschema import Draft202012Validator
from werkzeug.exceptions import HTTPException

from koi.api.openapi import build_document
from koi.api.spec import openapi_path, operation_for


@cache
def document() -> dict:
    return build_document()


@cache
def validator(ref: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": ref, "components": document()["components"]})


def schema_ref(path: str, method: str, status: int) -> Optional[str]:
    """The $ref of the documented JSON body, or None for no body."""
    responses = document()["paths"][path][method.lower()]["responses"]
    assert str(status) in responses, f"{method} {path} does not document status {status}"
    content = responses[str(status)].get("content", {}).get("application/json")
    return content["schema"]["$ref"] if content else None


def check_response(app: Any, response: Any) -> None:
    request = response.request
    method, path = request.method, request.path
    body = response.get_json(silent=True)
    try:
        endpoint, _ = app.url_map.bind("localhost").match(path, method)
    except HTTPException:
        endpoint = None
    op = operation_for(f"{endpoint}" if endpoint else None)
    if op is None:
        # Unrouted paths, /metrics: an error must still be the envelope.
        if response.status_code >= 400 and body is not None:
            _validate("#/components/schemas/ErrorEnvelope", body, method, path, response.status_code)
        return
    documented = openapi_path(op.rule)
    if response.status_code == 304:
        assert method == "GET" and not response.get_data(), f"{method} {path}: 304 with a body"
        schema_ref(documented, method, 304)
        return
    ref = schema_ref(documented, method, response.status_code)
    if ref is not None:
        assert body is not None, f"{method} {path} {response.status_code}: expected a JSON body"
        _validate(ref, body, method, path, response.status_code)


def _validate(ref: str, body: Any, method: str, path: str, status: int) -> None:
    errors = sorted(validator(ref).iter_errors(body), key=lambda e: list(e.absolute_path))
    detail = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '(body)'}: {e.message}" for e in errors[:5])
    assert not errors, f"{method} {path} {status} does not match {ref.rsplit('/', 1)[-1]}: {detail}"


def validate(model_name: str, payload: Any) -> None:
    """Validates a payload built outside the app against a component."""
    _validate(f"#/components/schemas/{model_name}", payload, "-", model_name, 0)
