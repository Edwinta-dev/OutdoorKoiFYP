"""GET/PUT /v1/ponds/{pond}/camera/mask (issue #39): the pond's water
mask for the camera's green-ratio analysis, versioned on every save.

Run from Backend/: python -m pytest -q -k mask
"""
import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app

OTHER = 456
RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
TOP_HALF = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]


@pytest.fixture
def app():
    storage = make_storage()
    storage.add_rows("UserData", [{"userID": OTHER, "volume": 3000.0, "biomass": 5.0}])
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return api_client(app)


def url(pond=USER):
    return f"/v1/ponds/{pond}/camera/mask"


def assert_error(resp, status, code):
    assert resp.status_code == status, resp.get_json()
    assert resp.get_json()["error"]["code"] == code
    return resp.get_json()["error"]


def test_mask_get_without_a_saved_mask_is_the_whole_frame(client):
    body = client.get(url()).get_json()
    assert body == {"pond_id": USER, "mask": None, "mask_version": None, "updated_at": None, "versions": []}


def test_mask_put_saves_version_1_and_get_returns_it(client):
    resp = client.put(url(), json={"polygon": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["mask"] == RIGHT_HALF and body["mask_version"] == 1 and body["updated_at"]
    assert [v["mask_version"] for v in body["versions"]] == [1]
    assert client.get(url()).get_json() == body


def test_mask_each_put_is_a_new_version_and_old_versions_are_kept(client, app):
    client.put(url(), json={"polygon": RIGHT_HALF})
    body = client.put(url(), json={"polygon": TOP_HALF}).get_json()
    assert body["mask"] == TOP_HALF and body["mask_version"] == 2
    assert [(v["mask_version"], v["mask"]) for v in body["versions"]] == [(1, RIGHT_HALF), (2, TOP_HALF)]
    # Saving the same polygon again is still a new version.
    assert client.put(url(), json={"polygon": TOP_HALF}).get_json()["mask_version"] == 3
    assert app.extensions["koi_storage"].fetch_camera_mask(OTHER) is None


@pytest.mark.parametrize("body, field", [
    ({"polygon": [[0, 0], [1, 0]]}, "polygon"),
    ({"polygon": [[0, 0], [1.5, 0], [1, 1]]}, "polygon"),
    ({"polygon": [[0, 0], [0.05, 0], [0.05, 0.05]]}, "polygon"),
    ({"polygon": [[0, 0], [1, 0], ["1", 1]]}, "polygon.2.0"),
    ({"polygon": [[0, 0], [1, 0], [True, 1]]}, "polygon.2.0"),
    ({"polygon": [[i / 70, 0.5] for i in range(65)]}, "polygon"),
    ({}, "polygon"),
])
def test_mask_put_rejects_an_invalid_polygon(client, app, body, field):
    error = assert_error(client.put(url(), json=body), 400, "validation_failed")
    assert field in [d["field"] for d in error["details"]["fields"]], error
    assert app.extensions["koi_storage"].fetch_camera_mask(USER) is None


def test_mask_put_rejects_a_body_that_is_not_an_object(client):
    assert_error(client.put(url(), data="[1, 2]", content_type="application/json"), 400, "invalid_body")


def test_mask_needs_a_token(app):
    plain = app.test_client()
    assert_error(plain.get(url()), 401, "auth_required")
    assert_error(plain.put(url(), json={"polygon": RIGHT_HALF}), 401, "auth_required")


def test_mask_of_another_pond_is_forbidden(client, app):
    assert_error(client.get(url(OTHER)), 403, "pond_forbidden")
    assert_error(client.put(url(OTHER), json={"polygon": RIGHT_HALF}), 403, "pond_forbidden")
    assert app.extensions["koi_storage"].fetch_camera_mask(OTHER) is None


def test_mask_storage_failure_is_a_503(client, app):
    app.extensions["koi_storage"].failing.add("save_camera_mask")
    assert_error(client.put(url(), json={"polygon": RIGHT_HALF}), 503, "storage_unavailable")
