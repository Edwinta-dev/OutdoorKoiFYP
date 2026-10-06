"""Forecast contracts and per-pond cache lifecycle, entirely offline."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER, api_client, make_settings, make_storage
from koi.api import create_app
from koi.models import uncertainty
from koi.models.algae_engine import AlgaeGrowthEngine
from koi.models.engine import PondConfig, WaterChemistryEngine
from koi.models.evaporation_engine import DayEnvironment, EvaporationFeedEngine
from koi.registry import EngineRegistry
from koi.storage import StaleSnapshotError
from koi.worker import poller


@pytest.fixture
def forecast_app():
    storage = make_storage()
    base = datetime(2026, 8, 11, tzinfo=timezone.utc)
    storage.add_rows("pondInterventions", [
        {"userID": USER, "event_type": "FEEDING", "food_grams": 150, "protein_percentage": 40,
         "event_timestamp": (base + timedelta(days=i)).isoformat()} for i in range(8)])
    storage.add_rows("imageTable", [{
        "user_ID": USER, "created_at": base.isoformat(), "green_ratio": 0.03,
        "current_state": ["base", 0.03], "imageURL": "https://example/frame.jpg"}])
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    return app


def count_runs(monkeypatch):
    counts = {"chemistry": 0, "evaporation": 0, "algae": 0}
    for domain, cls in [("chemistry", WaterChemistryEngine), ("evaporation", EvaporationFeedEngine),
                        ("algae", AlgaeGrowthEngine)]:
        original = cls.project_forward

        def counted(self, _domain=domain, _original=original, **kwargs):
            counts[_domain] += 1
            return _original(self, **kwargs)

        monkeypatch.setattr(cls, "project_forward", counted)
    return counts


@pytest.mark.parametrize("domain", ["chemistry", "evaporation", "algae"])
def test_uncertainty_api_contract_and_cache_until_poll(forecast_app, monkeypatch, domain):
    client = api_client(forecast_app)
    counts = count_runs(monkeypatch)
    path = f"/v1/ponds/{USER}/forecasts/{domain}?horizon_days=21"
    first = client.get(path)
    assert first.status_code == 200
    body = first.get_json()
    assert body["uncertainty"]["method"] == "three_scenario_sensitivity"
    assert counts[domain] == 3
    assert max(counts.values()) <= 3
    for central, low, high in zip(body["trajectory"], body["uncertainty"]["low"],
                                  body["uncertainty"]["high"], strict=True):
        for key in low:
            assert low[key] <= central[key] <= high[key]
    previous = dict(counts)
    assert client.get(path).get_json() == body
    conditional = client.get(path, headers={"If-None-Match": first.headers["ETag"]})
    assert conditional.status_code == 304
    assert counts == previous
    registry = forecast_app.extensions["koi_registry"]
    storage = forecast_app.extensions["koi_storage"]
    poller._poll_user(registry, USER, storage.fetch_pond_config(USER))
    after_poll = dict(counts)
    assert client.get(path).status_code == 200
    assert counts[domain] == after_poll[domain] + 3


def test_uncertainty_cache_separates_queries_and_returns_private_copies(forecast_app, monkeypatch):
    client = api_client(forecast_app)
    counts = count_runs(monkeypatch)
    path = f"/v1/ponds/{USER}/forecasts/evaporation"
    first = client.get(path + "?horizon_days=5&depth_m=2").get_json()
    second = client.get(path + "?horizon_days=6&depth_m=1").get_json()
    assert len(first["trajectory"]) == 5 and first["assumed_depth_m"] == 2
    assert len(second["trajectory"]) == 6 and second["assumed_depth_m"] == 1
    assert counts["evaporation"] == 6
    assert client.get(path + "?horizon_days=5&depth_m=2").get_json() == first
    assert counts["evaporation"] == 6


def test_uncertainty_cache_reload_after_other_process_poll(forecast_app, monkeypatch):
    client = api_client(forecast_app)
    counts = count_runs(monkeypatch)
    path = f"/v1/ponds/{USER}/forecasts/evaporation"
    assert client.get(path).status_code == 200
    storage = forecast_app.extensions["koi_storage"]
    other = EngineRegistry(storage)
    poller._poll_user(other, USER, storage.fetch_pond_config(USER))
    previous = counts["evaporation"]
    assert client.get(path).status_code == 200
    assert counts["evaporation"] == previous + 3


def test_uncertainty_cache_isolates_ponds_and_invalidates_mutations(forecast_app):
    registry = forecast_app.extensions["koi_registry"]
    config = PondConfig(volume_litres=1000, estimated_biomass_grams=1000)
    calls = []

    def run(twin):
        calls.append(twin)
        return {"values": [len(calls)]}

    def read(pond):
        return registry.with_twin(pond, run, default_config=config, persist=False, forecast_key=("test", 21))

    first = read(1)
    first["values"].append(99)
    assert read(1) == {"values": [1]}
    assert read(2) == {"values": [2]}
    registry.with_twin(1, lambda twin: None, default_config=config)
    assert read(1) == {"values": [3]}
    assert read(2) == {"values": [2]}


def test_uncertainty_save_race_does_not_exceed_run_cap(forecast_app, monkeypatch):
    registry = forecast_app.extensions["koi_registry"]
    storage = forecast_app.extensions["koi_storage"]
    counts = count_runs(monkeypatch)
    def reject(*args, **kwargs):
        raise StaleSnapshotError(USER, 0)

    monkeypatch.setattr(storage, "save_engine_snapshot", reject)
    with pytest.raises(StaleSnapshotError):
        registry.with_twin(USER, lambda twin: uncertainty.project(
            twin.evaporation, "evaporation", daily_environment=[
                DayEnvironment(30, 70, 2)
            ]), profiles=registry.profile_history(USER), forecast_key=("evaporation", 14))
    assert counts["evaporation"] == 3
