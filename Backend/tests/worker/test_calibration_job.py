"""Issue #32: the worker's calibration job over a synthetic pond history
in MemoryStorage, where the true shelter factor, water/air offset and
lag, and nitrification scale are known.

The history is 30 days: hourly station air temperature, wind and
rainfall, the 24-hour forecast's humidity, the pond's water temperature
following the air two hours late, daily feeding, a water change and four
top-ups whose litres are the loss at the true shelter factor, and kit
readings simulated at the true nitrification scale.

Run from Backend/: python -m pytest -q -k calibration
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DASHBOARD_PAYLOAD, make_settings
from koi import kit_readings
from koi.models import evaporation_engine as ev
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage
from koi.tools import rebuild as rb
from koi.worker import poller

POND = 7
T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=30)
HOUR = timedelta(hours=1)
STATIONS = {"air-temperature": "S43", "rainfall": "S43", "wind-speed": "S43"}
VOLUME = 5000.0
AREA = VOLUME / 1000.0 / ev.DEFAULT_POND_DEPTH_M
WIND_KNOTS = 8.0
HUMIDITY = 70.0  # midpoint of the forecast's 60 to 80
TRUE_SHELTER, TRUE_LAG, TRUE_OFFSET, TRUE_SCALE = 0.3, 2, -1.5, 0.6
HOURS = [T0 + i * HOUR for i in range(30 * 24)]


def air(h: datetime) -> float:
    i = (h - T0) / HOUR
    return 30.0 + 2.0 * math.sin(2 * math.pi * i / 24) + math.sin(2 * math.pi * i / 101)


WATER = {h: air(h - TRUE_LAG * HOUR) + TRUE_OFFSET for h in HOURS[TRUE_LAG:]}


def true_loss(start: datetime, end: datetime) -> float:
    """Litres evaporated over [start, end) at the true factor, from the
    Penman term hour by hour (no rain falls)."""
    total, h = 0.0, start
    while h < end:
        total += ev.evaporation_mm_per_day(WATER[h], air(h), HUMIDITY, WIND_KNOTS * 0.514444,
                                           shelter_factor=TRUE_SHELTER) / 24.0 * AREA
        h += HOUR
    return total


def temp_at(t: datetime) -> float:
    h = t.replace(minute=0, second=0, microsecond=0)
    return WATER.get(h, air(h) if h >= T0 else air(T0))


FEEDINGS = [T0 + timedelta(days=d, hours=9) for d in range(30)]
READING_DAYS = (10, 14, 18, 22, 26)
TOPUP_DAYS = (7, 12, 18, 25)
WATER_CHANGE_DAY = 2


def seed(storage: MemoryStorage, pond: int = POND, stations: dict = STATIONS) -> None:
    storage.add_rows("UserData", [{"userID": pond, "volume": VOLUME, "biomass": 3.0, "ClosestStations": stations}])
    if pond != POND:
        return
    obs = []
    for h in HOURS:
        at = h + timedelta(minutes=5)
        common = {"source": "nea_v2", "series": "station", "station_id": "S43", "fetched_at": at.isoformat()}
        obs.append({**common, "metric": "air_temperature", "value": air(h), "unit": "degC",
                    "semantics": "instantaneous", "observed_from": at.isoformat(), "observed_to": at.isoformat()})
        obs.append({**common, "metric": "wind_speed", "value": WIND_KNOTS, "unit": "knot",
                    "semantics": "instantaneous", "observed_from": at.isoformat(), "observed_to": at.isoformat()})
        obs.append({**common, "metric": "rainfall", "value": 0.0, "unit": "mm", "semantics": "interval_total",
                    "observed_from": h.isoformat(), "observed_to": (h + HOUR).isoformat()})
    storage.add_rows("weather_observation", obs)
    storage.add_rows("weather_forecast_issuance", [
        {"source": "nea_v2", "product": "24hr", "slot_id": "GENERAL", "issued_at": t.isoformat(),
         "source_updated_at": t.isoformat(), "valid_from": t.isoformat(),
         "valid_to": (t + timedelta(days=1)).isoformat(),
         "payload": {"relativeHumidity": {"low": 60, "high": 80}}, "fetched_at": t.isoformat(),
         "available_at": t.isoformat()}
        for t in (T0 - timedelta(hours=1) + timedelta(hours=6 * i) for i in range(30 * 4))])
    sensor = storage.add_rows("SensorData", [
        {"userID": pond, "sensor_type": "temp", "data1": value, "created_at": (h + timedelta(minutes=10)).isoformat()}
        for h, value in WATER.items()])
    storage.add_rows("sensor_ingest_ledger", [
        {"pond_id": pond, "sensor_row_id": r["id"], "sensor_type": "temp", "effective_sample_time": r["created_at"],
         "time_basis": "ingestion", "disposition": "applied", "snapshot_version": 1, "ingested_at": r["created_at"]}
        for r in sensor])
    rows = [{"userID": pond, "event_type": "FEEDING", "event_timestamp": t.isoformat(), "food_grams": 25.0,
             "protein_percentage": 38.0} for t in FEEDINGS]
    boundary = T0 + timedelta(days=WATER_CHANGE_DAY)
    rows.append({"userID": pond, "event_type": "WATER_CHANGE", "event_timestamp": boundary.isoformat(),
                 "volume_percentage": 10.0})
    for day in TOPUP_DAYS:
        end = T0 + timedelta(days=day)
        rows.append({"userID": pond, "event_type": "WATER_TOPUP", "event_timestamp": end.isoformat(),
                     "volume_litres": round(true_loss(boundary, end), 3)})
        boundary = end
    storage.add_rows("pondInterventions", rows)
    events = kit_readings.nitrogen_events(storage.fetch_interventions(pond, None))
    times = [T0 + timedelta(days=d, hours=15) for d in READING_DAYS]
    values = kit_readings.simulate_nitrogen(events, temp_at, lambda t: VOLUME, NOW - poller.CALIBRATION_WINDOW,
                                            times, TRUE_SCALE)
    storage.add_rows("kit_readings", [{"pond": pond, "taken_at": t.isoformat(), "kit": "liquid",
                                       "ammonia_mg_l": round(tan, 5), "nitrite_mg_l": round(no2, 5)}
                                      for t, (tan, no2) in zip(times, values, strict=True)])


@pytest.fixture
def setup():
    storage = MemoryStorage()
    seed(storage)
    settings = make_settings()
    return storage, EngineRegistry(storage, settings.hypoxia_thresholds, settings.sensor_ingest)


def test_calibration_job_recovers_the_true_constants_and_stores_them(setup):
    storage, registry = setup
    report = poller.calibrate_pond(registry, POND, NOW)
    assert {p: r["status"] for p, r in report.items()} == dict.fromkeys(poller.CALIBRATION_PARAMETERS, "fitted")
    assert all(r["stored"] for r in report.values())
    rows = {r["parameter"]: r for r in storage.rows("pond_calibration")}
    assert set(rows) == set(poller.CALIBRATION_PARAMETERS)

    shelter = rows[poller.SHELTER]
    assert shelter["value"] == pytest.approx(TRUE_SHELTER, abs=0.005)
    assert shelter["sample_count"] == len(TOPUP_DAYS)
    assert shelter["error"]["training_rmse"] < shelter["error"]["default_rmse"]

    temperature = rows[poller.TEMPERATURE]
    assert temperature["lag_hours"] == TRUE_LAG
    assert temperature["value"] == pytest.approx(TRUE_OFFSET, abs=0.01)
    assert temperature["error"]["evaluation_rmse"] < temperature["error"]["persistence_rmse"]
    assert temperature["training_to"] <= temperature["evaluation_from"]

    scale = rows[poller.NITRIFICATION]
    assert scale["value"] == pytest.approx(TRUE_SCALE, rel=0.03)
    assert scale["sample_count"] == len(READING_DAYS)

    for row in rows.values():
        assert row["pond_id"] == POND and row["status"] == "fitted" and row["method_version"] == 1
        assert row["fitted_at"] == row["effective_from"] == NOW.isoformat()
        assert row["details"]["default"] == poller.CALIBRATION_DEFAULTS[row["parameter"]]


def test_calibration_job_does_not_store_an_unchanged_fit_again(setup):
    storage, registry = setup
    poller.calibrate_pond(registry, POND, NOW)
    again = poller.calibrate_pond(registry, POND, NOW + timedelta(hours=1))
    assert not any(r["stored"] for r in again.values())
    assert len(storage.rows("pond_calibration")) == 3


def test_calibration_job_without_data_stores_pending_once_and_defaults_stay(setup):
    storage, registry = setup
    seed(storage, pond=8)
    report = poller.calibrate_pond(registry, 8, NOW)
    assert {p: (r["status"], r["value"]) for p, r in report.items()} == dict.fromkeys(
        poller.CALIBRATION_PARAMETERS, ("pending", None))
    assert report[poller.SHELTER]["reason"] == "not_enough_topups"
    assert report[poller.TEMPERATURE]["reason"] == "not_enough_overlap"
    # The station has air history (shared with pond 7), but pond 8 has no kit readings.
    assert report[poller.NITRIFICATION]["reason"] == "not_enough_kit_readings"
    assert len(storage.fetch_calibrations(8)) == 3
    assert not any(r["stored"] for r in poller.calibrate_pond(registry, 8, NOW + HOUR).values())
    assert poller.calibration_in_force(storage.fetch_calibrations(8), NOW + HOUR) == {}


def test_calibration_pending_never_replaces_a_fit():
    fit = {"id": 1, "parameter": poller.SHELTER, "status": "fitted", "value": 0.4, "lag_hours": None,
           "sample_count": 3, "training_to": NOW.isoformat(), "details": {}}
    pending = poller._calibration_row(poller.SHELTER, ev.pending_calibration("not_enough_topups", 1), NOW)
    assert poller._changed(pending, [fit]) is False
    assert poller._changed(pending, []) is True
    refit = poller._calibration_row(poller.SHELTER, {**ev.pending_calibration("x", 0), "status": "fitted",
                                                     "value": 0.41, "sample_count": 3}, NOW)
    assert poller._changed(refit, [fit]) is True


def test_calibration_in_force_is_the_newest_fit_at_the_time():
    rows = [{"id": 1, "parameter": poller.SHELTER, "status": "fitted", "value": 0.4,
             "effective_from": T0.isoformat()},
            {"id": 2, "parameter": poller.SHELTER, "status": "fitted", "value": 0.5,
             "effective_from": (T0 + timedelta(days=5)).isoformat()},
            {"id": 3, "parameter": poller.SHELTER, "status": "pending", "value": None,
             "effective_from": (T0 + timedelta(days=6)).isoformat()}]
    assert poller.calibration_in_force(rows, T0 - HOUR) == {}
    assert poller.calibration_in_force(rows, T0 + timedelta(days=1))[poller.SHELTER]["id"] == 1
    assert poller.calibration_in_force(rows, T0 + timedelta(days=9))[poller.SHELTER]["id"] == 2


def poll(storage, registry, at):
    storage.set_dashboard_payload(POND, DASHBOARD_PAYLOAD)
    storage.add_rows("SensorData", [{"userID": POND, "sensor_type": t, "data1": v, "created_at": at.isoformat()}
                                    for t, v in {"pH": 7.5, "TDS": 200.0, "temp": 28.0, "LUX": 9000.0}.items()])
    [config] = [c for c in storage.fetch_active_pond_configs() if c["user_id"] == POND]
    poller._poll_user(registry, POND, config, now=at + timedelta(minutes=1))
    return storage.load_engine_snapshot(POND)


def test_calibration_poll_applies_the_versions_in_force_at_its_time(setup):
    storage, registry = setup
    before = poll(storage, registry, NOW - timedelta(hours=2))
    assert before["evaporation"]["shelter_factor"] is None
    assert before["chemistry"]["nitrification_scale"] == 1.0
    poller.calibrate_pond(registry, POND, NOW)
    ids = {r["parameter"]: r["id"] for r in storage.rows("pond_calibration")}
    after = poll(storage, registry, NOW + timedelta(hours=1))
    assert after["evaporation"]["shelter_factor"] == pytest.approx(TRUE_SHELTER, abs=0.005)
    assert after["evaporation"]["water_air_offset_c"] == pytest.approx(TRUE_OFFSET, abs=0.01)
    assert after["evaporation"]["water_air_lag_hours"] == TRUE_LAG
    assert after["evaporation"]["calibration_versions"] == {poller.SHELTER: ids[poller.SHELTER],
                                                            poller.TEMPERATURE: ids[poller.TEMPERATURE]}
    assert after["chemistry"]["nitrification_scale"] == pytest.approx(TRUE_SCALE, rel=0.03)
    assert after["chemistry"]["nitrification_scale_version"] == ids[poller.NITRIFICATION]


def test_calibration_unreadable_versions_leave_the_twin_as_it_was(setup):
    storage, registry = setup
    poller.calibrate_pond(registry, POND, NOW)
    poll(storage, registry, NOW + timedelta(hours=1))
    storage.failing.add("fetch_calibrations")
    kept = poll(storage, registry, NOW + timedelta(hours=2))
    assert kept["evaporation"]["shelter_factor"] == pytest.approx(TRUE_SHELTER, abs=0.005)


def test_calibration_rebuild_uses_the_version_in_force_at_the_replayed_time(setup):
    storage, registry = setup
    poller.calibrate_pond(registry, POND, NOW)
    settings = make_settings()
    early = rb.rebuild(storage, settings, POND, since=NOW - timedelta(hours=4), now=NOW - timedelta(hours=1))
    assert early.rebuilt_snapshot["evaporation"]["shelter_factor"] is None
    assert early.rebuilt_snapshot["chemistry"]["nitrification_scale"] == 1.0
    storage.add_rows("SensorData", [{"userID": POND, "sensor_type": "temp", "data1": 28.0,
                                     "created_at": (NOW + HOUR).isoformat()}])
    storage.add_rows("sensor_ingest_ledger", [{
        "pond_id": POND, "sensor_row_id": storage.rows("SensorData")[-1]["id"], "sensor_type": "temp",
        "effective_sample_time": (NOW + HOUR).isoformat(), "time_basis": "ingestion", "disposition": "applied",
        "snapshot_version": 2, "ingested_at": (NOW + HOUR).isoformat()}])
    late = rb.rebuild(storage, settings, POND, since=NOW - timedelta(hours=4), now=NOW + 2 * HOUR)
    assert late.rebuilt_snapshot["evaporation"]["shelter_factor"] == pytest.approx(TRUE_SHELTER, abs=0.005)


def test_calibration_job_takes_the_lease_and_survives_a_failing_pond(setup, monkeypatch):
    storage, registry = setup
    seed(storage, pond=8)
    real = poller.fit_pond_calibration

    def fit(storage_, profiles, pond, now, in_force=None):
        if pond == 8:
            raise RuntimeError("broken history")
        return real(storage_, profiles, pond, now, in_force)
    monkeypatch.setattr(poller, "fit_pond_calibration", fit)
    assert storage.take_lease(poller.CALIBRATION_LEASE, "other", 600)
    job = poller.CalibrationJob(registry, "me")
    assert job.run(NOW) is None  # standby while another worker holds it
    storage.release_lease(poller.CALIBRATION_LEASE, "other")
    report = job.run(NOW)
    assert report["8"] == {"failed": "RuntimeError: broken history"}
    assert report[str(POND)][poller.SHELTER]["status"] == "fitted"
    assert storage.take_lease(poller.CALIBRATION_LEASE, "other", 600)  # released after the run


def test_calibration_without_a_station_or_water_record_is_pending(setup):
    storage, registry = setup
    seed(storage, pond=9, stations={})
    report = poller.calibrate_pond(registry, 9, NOW)
    assert report[poller.NITRIFICATION]["reason"] == "no_temperature_history"
    [temperature] = [r for r in storage.fetch_calibrations(9) if r["parameter"] == poller.TEMPERATURE]
    assert temperature["details"]["station"] == "no air-temperature station assigned"
