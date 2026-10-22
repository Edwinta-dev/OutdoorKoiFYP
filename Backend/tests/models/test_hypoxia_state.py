from datetime import datetime, timedelta, timezone

from koi.models.hypoxia import assess_hypoxia
from koi.models.hypoxia_state import HISTORY_SAMPLE_CAP, HypoxiaState

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_empty_and_warmup_boundaries():
    state = HypoxiaState()
    assert state.history_hours(T0) == 0
    state.add(T0, 7.5, 28)
    assert state.history_hours(T0 + timedelta(hours=23)) == 23
    assert state.history_hours(T0 + timedelta(hours=24)) == 24
    assert state.history_hours(T0 - timedelta(hours=1)) == 0


def test_hour_bin_keeps_latest_reading():
    state = HypoxiaState()
    state.add(T0 + timedelta(minutes=2), 7.1, 27)
    state.add(T0 + timedelta(minutes=45), 7.8, 29)
    assert len(state.samples) == 1
    assert state.samples[0] == (T0 + timedelta(minutes=45), 7.8, 29)


def test_cap_keeps_newest_samples_and_future_does_not_change_age_at_t():
    state = HypoxiaState()
    for hour in range(HISTORY_SAMPLE_CAP + 20):
        state.add(T0 + timedelta(hours=hour), 7.5, 28)
    assert len(state.samples) == HISTORY_SAMPLE_CAP
    assert state.samples[-1][0] == T0 + timedelta(hours=HISTORY_SAMPLE_CAP + 19)
    at_t = T0 + timedelta(hours=100)
    before = state.history_hours(at_t)
    before_outcome = assess_hypoxia(now=at_t, ph=7.5, water_temp_c=28, history_hours=before).to_dict()
    state.add(at_t + timedelta(days=1), 7.6, 28)
    assert state.history_hours(at_t) == before
    assert assess_hypoxia(now=at_t, ph=7.5, water_temp_c=28,
                          history_hours=state.history_hours(at_t)).to_dict() == before_outcome


def test_rows_older_than_retention_are_ignored():
    state = HypoxiaState()
    assert not state.add(T0, 7.5, 28, now=T0 + timedelta(days=15))
    assert state.first_seen_at is None and state.samples == []


def test_snapshot_round_trip():
    state = HypoxiaState()
    state.add(T0, 7.5, 28)
    assert HypoxiaState.from_dict(state.to_dict()).to_dict() == state.to_dict()


def test_poller_logs_one_shadow_event_and_snapshots_latest(caplog):
    from koi.registry import EngineRegistry
    from koi.storage import MemoryStorage
    from koi.worker.poller import _poll_user

    pond = 8126
    storage = MemoryStorage()
    storage.add_rows("UserData", [{"userID": pond, "volume": 5000, "biomass": 8.0}])
    at = T0
    storage.add_rows("SensorData", [
        {"userID": pond, "sensor_type": kind, "data1": value, "created_at": at.isoformat()}
        for kind, value in (("pH", 7.5), ("temp", 29.0), ("TDS", 220.0), ("LUX", 20000.0))
    ])
    registry = EngineRegistry(storage)
    config = next(row for row in storage.fetch_active_pond_configs() if row["user_id"] == pond)
    with caplog.at_level("INFO", logger="koi.worker"):
        _poll_user(registry, pond, config, now=at + timedelta(minutes=1))
    events = [record for record in caplog.records
              if getattr(record, "koi_event", None) == "hypoxia_shadow"]
    assert len(events) == 1
    assert events[0].koi_fields["n"] == 1
    saved = HypoxiaState.from_dict(storage.load_engine_snapshot(pond)["hypoxia_state"])
    assert saved.last_shadow == {"ph_z": None, "ph_below_own_range": None, "n": 1}
