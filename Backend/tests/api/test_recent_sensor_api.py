"""GET /v1/ponds/{pond}/sensors/recent: the raw readings behind the app's
"Last hour" trace, over MemoryStorage, and the SupabaseStorage query
against a recording stand-in for the client. No network.

Run from Backend/: python -m pytest -q tests/api/test_recent_sensor_api.py
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, add_upload, api_client, make_settings, make_storage
from koi.api import create_app, schemas
from koi.storage import SupabaseStorage

NOW = datetime(2026, 11, 10, 13, 0, tzinfo=timezone.utc)
URL = f"/v1/ponds/{USER}/sensors/recent"


@pytest.fixture
def setup(monkeypatch):
    storage = make_storage()
    storage._tables["SensorData"].clear()
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    monkeypatch.setattr(schemas, "utc_now", lambda: NOW)
    return storage, api_client(app), app


def _at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_recent_defaults_to_ph_over_the_last_hour_oldest_first(setup):
    storage, client, _ = setup
    for minutes_ago, ph in [(5, 7.4), (90, 6.9), (30, 7.2), (59, 7.0)]:
        add_upload(storage, {"pH": ph, "temp": 28.0}, at=NOW - timedelta(minutes=minutes_ago))
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.get_json()
    assert (body["sensor"], body["minutes"]) == ("pH", 60)
    assert [r["value"] for r in body["readings"]] == [7.0, 7.2, 7.4]
    assert [_at(r["at"]) for r in body["readings"]] == [NOW - timedelta(minutes=m) for m in (59, 30, 5)]
    assert all(r["flags"] is None for r in body["readings"])


def test_recent_window_and_sensor_come_from_the_query(setup):
    storage, client, _ = setup
    add_upload(storage, {"pH": 7.1, "TDS": 210.0}, at=NOW - timedelta(minutes=90))
    add_upload(storage, {"pH": 7.3, "TDS": 215.0}, at=NOW - timedelta(minutes=10))
    body = client.get(URL, query_string={"sensor": "TDS", "minutes": 120}).get_json()
    assert (body["sensor"], body["minutes"]) == ("TDS", 120)
    assert [r["value"] for r in body["readings"]] == [210.0, 215.0]
    assert client.get(URL, query_string={"sensor": "TDS", "minutes": 5}).get_json()["readings"] == []


def test_recent_reading_is_timed_by_sampled_at_and_carries_flags(setup):
    storage, client, _ = setup
    received = NOW - timedelta(minutes=2)
    sampled = received - timedelta(seconds=20)
    storage.add_rows("SensorData", [
        {"userID": USER, "sensor_type": "pH", "data1": 7.42, "created_at": received.isoformat(),
         "sampled_at": sampled.isoformat(), "flags": 4},
        {"userID": USER, "sensor_type": "pH", "data1": None, "created_at": received.isoformat()}])
    [reading] = client.get(URL).get_json()["readings"]
    assert (_at(reading["at"]), reading["value"], reading["flags"]) == (sampled, 7.42, 4)


def test_recent_is_capped_at_the_newest_readings(setup):
    storage, client, _ = setup
    count = schemas.MAX_RECENT_READINGS + 20
    storage.add_rows("SensorData", [
        {"userID": USER, "sensor_type": "pH", "data1": float(i),
         "created_at": (NOW - timedelta(seconds=count - i)).isoformat()}
        for i in range(count)])
    values = [r["value"] for r in client.get(URL, query_string={"minutes": 180}).get_json()["readings"]]
    assert len(values) == schemas.MAX_RECENT_READINGS
    assert values[0] == 20.0 and values[-1] == float(count - 1)


def test_recent_leaves_out_other_ponds(setup):
    storage, client, _ = setup
    add_upload(storage, {"pH": 6.0}, at=NOW - timedelta(minutes=1), user_id=999)
    assert client.get(URL).get_json()["readings"] == []


@pytest.mark.parametrize("query", [{"sensor": "ph"}, {"sensor": "battery_mv"}, {"minutes": 0}, {"minutes": 181},
                                   {"minutes": "soon"}])
def test_recent_rejects_unknown_sensor_and_out_of_range_window(setup, query):
    _, client, _ = setup
    resp = client.get(URL, query_string=query)
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_recent_needs_a_token_and_the_callers_own_pond(setup):
    _, client, app = setup
    assert app.test_client().get(URL).status_code == 401
    assert client.get("/v1/ponds/999/sensors/recent").status_code == 403


def test_recent_storage_failure_is_503(setup):
    storage, client, _ = setup
    storage.failing.add("fetch_recent_sensor_readings")
    assert client.get(URL).status_code == 503


class _Query:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __getattr__(self, method):
        def record(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            return self
        return record

    def execute(self):
        return type("Res", (), {"data": self.rows})()


class _Client:
    def __init__(self, rows):
        self.query = _Query(rows)
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return self.query


def test_supabase_recent_readings_query_and_order():
    since = NOW - timedelta(minutes=60)
    newer, older = NOW - timedelta(minutes=1), NOW - timedelta(minutes=40)
    client = _Client([{"data1": 7.4, "created_at": newer.isoformat(), "sampled_at": None, "flags": 0},
                      {"data1": 7.1, "created_at": older.isoformat(), "sampled_at": None, "flags": None}])
    storage = SupabaseStorage(make_settings(storage="supabase"), client=client)
    rows = storage.fetch_recent_sensor_readings(USER, "pH", since, 500)
    assert rows == [{"at": older.isoformat(), "value": 7.1, "flags": None},
                    {"at": newer.isoformat(), "value": 7.4, "flags": 0}]
    assert client.tables == ["SensorData"]
    calls = client.query.calls
    assert [(m, a) for m, a, _ in calls if m in ("eq", "gte", "limit")] == [
        ("eq", ("userID", USER)), ("eq", ("sensor_type", "pH")), ("gte", ("created_at", since.isoformat())),
        ("limit", (500,))]
    assert ("order", ("created_at",), {"desc": True}) in calls
