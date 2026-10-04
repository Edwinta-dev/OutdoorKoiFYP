"""The committed OpenAPI document (docs/api/openapi.yaml) is the one the
code generates, and it describes every route the app serves.

If test_openapi_committed_document_is_current fails, regenerate it from
Backend/ with `python -m koi.api.openapi` and commit the result.
"""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from conftest import make_settings, make_storage
from koi.api import create_app
from koi.api.openapi import DOCUMENT, build_document, load, main
from koi.api.spec import OPERATIONS, openapi_path


@pytest.fixture(scope="module")
def document():
    return build_document()


@pytest.fixture(scope="module")
def app():
    return create_app(make_settings(), storage=make_storage())


def test_openapi_committed_document_is_current(document):
    assert DOCUMENT.exists(), f"{DOCUMENT} is missing; run python -m koi.api.openapi"
    assert load() == document, f"{DOCUMENT} is out of date; run python -m koi.api.openapi from Backend/"
    assert main(["--check"]) == 0


def test_openapi_documents_every_route_the_app_serves(app, document):
    served = {(openapi_path(rule.rule), method.lower())
              for rule in app.url_map.iter_rules() if rule.endpoint != "static"
              for method in rule.methods - {"HEAD", "OPTIONS"}}
    documented = {(path, method) for path, item in document["paths"].items() for method in item}
    assert served == documented


def test_openapi_every_route_is_under_v1_or_an_alias_of_one(document):
    unversioned = {"/health", "/ready", "/metrics"}
    for path, item in document["paths"].items():
        if path.startswith("/v1/") or path in unversioned:
            continue
        for method, op in item.items():
            assert op.get("deprecated") is True, f"{method} {path} is not under /v1 and not deprecated"
            successor = op["description"].split("Deprecated alias of ")[1].split(". ")[0].rstrip(".")
            assert successor.startswith("/v1/") and method in document["paths"][successor], (path, successor)
    for alias in ("/health", "/ready"):
        assert "deprecated" not in document["paths"][alias]["get"]
        assert f"/v1{alias}" in document["paths"]


def test_openapi_old_paths_cover_every_route_the_app_called(document):
    # The paths the app and the README used before /v1.
    for path, method in [("/assessment/{user_id}", "get"), ("/assessment/evaporation/{user_id}", "get"),
                         ("/assessment/algae/{user_id}", "get"), ("/assessment/all/{user_id}", "get"),
                         ("/forecast/{user_id}", "get"), ("/forecast/evaporation/{user_id}", "get"),
                         ("/forecast/algae/{user_id}", "get"), ("/ratings/algae/{user_id}", "get"),
                         ("/events/feeding", "post"), ("/events/water-change", "post"), ("/events/top-up", "post"),
                         ("/events/algal-scrub", "post"), ("/events/algae-rating", "post"),
                         ("/events/algae-rating/undo", "post")]:
        assert document["paths"][path][method]["deprecated"] is True, path


def test_openapi_gets_document_etag_and_304(document):
    for path, item in document["paths"].items():
        op = item.get("get")
        if op is None or path == "/metrics":
            continue
        assert "304" in op["responses"], path
        assert "ETag" in op["responses"]["200"]["headers"], path
        assert any(p["name"] == "If-None-Match" for p in op["parameters"]), path


def test_openapi_component_schemas_are_valid_json_schema(document):
    for name, schema in document["components"]["schemas"].items():
        Draft202012Validator.check_schema(schema)
        assert name


def test_openapi_operations_have_unique_ids(document):
    ids = [op["operationId"] for item in document["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))
    assert len(OPERATIONS) + 1 == len(ids)  # + /metrics


def test_openapi_dashboard_is_documented(document):
    op = document["paths"]["/v1/ponds/{pond}/dashboard"]["get"]
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/Dashboard")
    dashboard = document["components"]["schemas"]["Dashboard"]
    assert set(dashboard["required"]) >= {"readings", "assessments", "next_actions", "weather", "forecast"}
    assert dashboard["additionalProperties"] is False
