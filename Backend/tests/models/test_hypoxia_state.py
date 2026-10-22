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
