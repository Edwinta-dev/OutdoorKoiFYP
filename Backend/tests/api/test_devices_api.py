"""Issue #23: GET /v1/ponds/{pond}/devices and the data_confidence on
every assessment the API returns, over MemoryStorage. The poller records
the sensor node's contacts as it ingests; the camera's are covered in
tests/camera/test_camera_app.py.

Run from Backend/: python -m pytest -q -k "device or confidence"
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, add_upload, api_client, make_settings, make_storage
from koi.api import create_app, schemas
from koi.worker import poller

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
FULL = {"temp": 29.0, "TDS": 220.0, "pH": 7.6, "LUX": 20000.0}


@pytest.fixture
def setup(monkeypatch):
    storage = make_storage()
    # The fixture pond's one August upload is not part of these cases.
    storage._tables["SensorData"].clear()
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    registry = app.extensions["koi_registry"]
    clock = {"now": NOW}
    monkeypatch.setattr(schemas, "utc_now", lambda: clock["now"])

    def poll(at):
        config = next(c for c in storage.fetch_active_pond_configs() if c["user_id"] == USER)
        return poller._poll_user(registry, USER, config, now=at)

    return storage, api_client(app), poll, clock


def _uploads(storage, start, count, reading=None, step=timedelta(minutes=15)):
    for i in range(count):
        varied = {"temp": 29.0 + i / 10, "TDS": 220.0 + i, "pH": 7.5 + i / 100, "LUX": 20000.0 + 100 * i}
        add_upload(storage, varied if reading is None else reading, at=start + i * step)


def test_devices_for_a_pond_never_heard_from(setup):
    _, client, _, _ = setup
    resp = client.get(f"/v1/ponds/{USER}/devices")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["pond_id"] == USER
    assert [d["kind"] for d in body["devices"]] == ["sensor", "camera"]
    assert all(d["status"] == "never_seen" for d in body["devices"])
    assert body["devices"][0]["channels"]["tds"]["status"] == "never_seen"


def test_devices_other_pond_is_forbidden(setup):
    _, client, _, _ = setup
    assert client.get("/v1/ponds/999/devices").status_code == 403


def test_device_last_seen_is_receipt_time_and_counts_cycles(setup):
    storage, client, poll, _ = setup
    _uploads(storage, NOW - timedelta(hours=2), 8)
    poll(NOW - timedelta(minutes=10))
    [row] = storage.rows("devices")
    assert (row["pond_id"], row["kind"]) == (USER, "sensor")
    newest = NOW - timedelta(hours=2) + 7 * timedelta(minutes=15)
    assert row["last_seen_at"] == newest.isoformat()
    assert len(storage.rows("device_contact")) == 8

    sensor = client.get(f"/v1/ponds/{USER}/devices").get_json()["devices"][0]
    assert sensor["status"] == "ok"
    assert datetime.fromisoformat(sensor["last_seen_at"].replace("Z", "+00:00")) == newest
    assert (sensor["received_24h"], sensor["missed_24h"]) == (8, 0)
    assert (sensor["expected_interval_seconds"], sensor["interval_source"]) == (900, "default")
    assert sensor["channels"]["ph"]["status"] == "fresh"
    assert sensor["clock"]["sample_time_basis"] == "ingestion" and sensor["clock"]["uncertain"]


def test_device_poll_repeated_records_nothing_twice(setup):
    storage, _, poll, _ = setup
    _uploads(storage, NOW - timedelta(hours=1), 3)
    poll(NOW - timedelta(minutes=20))
    poll(NOW - timedelta(minutes=5))
    assert len(storage.rows("device_contact")) == 3


def test_silent_device_lowers_confidence_and_says_why(setup):
    storage, client, poll, clock = setup
    _uploads(storage, NOW - timedelta(hours=1), 4)
    poll(NOW - timedelta(minutes=10))

    chem = client.get(f"/v1/ponds/{USER}/assessments/chemistry").get_json()
    assert chem["data_confidence"]["level"] == "high"
    assert chem["data_confidence"]["reasons"] == []
    assert chem["data_confidence"]["never_seen"] == []

    # Nothing more arrives: three hours later every assessment is low.
    clock["now"] = NOW + timedelta(hours=3)
    every = client.get(f"/v1/ponds/{USER}/assessments").get_json()
    for domain in ("chemistry", "evaporation"):
        confidence = every[domain]["data_confidence"]
        assert confidence["level"] == "low"
        assert confidence["reasons"][0]["code"] == "node_silent"
    devices = client.get(f"/v1/ponds/{USER}/devices").get_json()["devices"]
    assert devices[0]["status"] == "silent"
    assert devices[0]["missed_24h"] == 12
    dashboard = client.get(f"/v1/ponds/{USER}/dashboard").get_json()
    assert dashboard["assessments"]["chemistry"]["data_confidence"]["level"] == "low"


def test_stale_flagged_channel_reduces_confidence(setup):
    storage, client, poll, _ = setup
    # The same pH four times in a row: the sensor gate flags it stale.
    _uploads(storage, NOW - timedelta(hours=1), 5, reading=FULL)
    poll(NOW - timedelta(minutes=5))
    chem = client.get(f"/v1/ponds/{USER}/assessments/chemistry").get_json()["data_confidence"]
    assert chem["level"] == "reduced"
    assert ("channel_stale_flagged", "ph") in {(r["code"], r["channel"]) for r in chem["reasons"]}
    sensor = client.get(f"/v1/ponds/{USER}/devices").get_json()["devices"][0]
    assert sensor["channels"]["ph"]["stale_flagged"]


def test_confidence_without_tds_is_not_reduced(setup):
    storage, client, poll, _ = setup
    _uploads(storage, NOW - timedelta(minutes=50), 3, reading={"temp": 29.0, "pH": 7.6, "LUX": 20000.0})
    add_upload(storage, {"temp": 29.4, "pH": 7.7, "LUX": 21000.0}, at=NOW - timedelta(minutes=5))
    poll(NOW - timedelta(minutes=2))
    chem = client.get(f"/v1/ponds/{USER}/assessments/chemistry").get_json()["data_confidence"]
    assert chem["never_seen"] == ["tds"]
    assert "channel_old" not in {r["code"] for r in chem["reasons"]}
    sensor = client.get(f"/v1/ponds/{USER}/devices").get_json()["devices"][0]
    assert sensor["channels"]["tds"]["status"] == "never_seen"
    assert sensor["channels"]["temp"]["status"] == "fresh"


def test_device_falling_battery_and_reset_reason(setup):
    storage, client, poll, _ = setup
    for i in range(12):
        at = NOW - timedelta(hours=12) + i * timedelta(hours=1)
        add_upload(storage, {**FULL, "pH": 7.0 + i / 10, "battery_mv": 12600.0 - 15 * i}, at=at)
    add_upload(storage, {"reset_reason": 9.0}, at=NOW - timedelta(hours=6))
    poll(NOW - timedelta(minutes=30))
    sensor = client.get(f"/v1/ponds/{USER}/devices").get_json()["devices"][0]
    assert sensor["battery"]["trend"] == "falling"
    assert sensor["battery"]["latest_mv"] == 12435.0
    assert sensor["last_reset"]["reason"] == "brownout"
    # Battery and reset rows are not model inputs.
    ledgered = {r["sensor_type"] for r in storage.rows("sensor_ingest_ledger")}
    assert ledgered == {"temp", "TDS", "pH", "LUX"}


def test_device_configured_interval_is_used_for_confidence(setup):
    storage, client, poll, clock = setup
    _uploads(storage, NOW - timedelta(hours=3), 3, step=timedelta(hours=1))
    storage.add_rows("devices", [{"pond_id": USER, "kind": "sensor", "expected_interval_seconds": 3600}])
    poll(NOW - timedelta(minutes=55))
    # 1 h 20 min after the newest reading: silent at 15 minutes, on time at 1 hour.
    clock["now"] = NOW + timedelta(minutes=20)
    chem = client.get(f"/v1/ponds/{USER}/assessments/chemistry").get_json()["data_confidence"]
    assert chem["expected_interval_seconds"] == 3600
    assert chem["level"] == "high"
    sensor = client.get(f"/v1/ponds/{USER}/devices").get_json()["devices"][0]
    assert (sensor["interval_source"], sensor["missed_24h"]) == ("device", 0)


def test_confidence_on_event_response(setup):
    storage, client, poll, _ = setup
    _uploads(storage, NOW - timedelta(hours=1), 4)
    poll(NOW - timedelta(minutes=10))
    resp = client.post(f"/v1/ponds/{USER}/events/feeding", json={"food_grams": 10, "protein_percent": 35})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["chemistry"]["data_confidence"]["level"] in ("high", "reduced", "low")


def test_device_health_failure_does_not_stop_the_poll(setup):
    storage, client, poll, _ = setup
    storage.failing.add("record_device_contacts")
    _uploads(storage, NOW - timedelta(hours=1), 2)
    assert poll(NOW - timedelta(minutes=10))
    assert storage.rows("devices") == []
    assert client.get(f"/v1/ponds/{USER}/assessments/chemistry").status_code == 200
