from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from koi.models.hypoxia import assess_hypoxia, shadow_ph_relative
from koi.models.hypoxia_state import HypoxiaState


def assess(now, *, ph=7.5, temp=28.0, hours=24):
    return assess_hypoxia(now=now, ph=ph, water_temp_c=temp, history_hours=hours)


@pytest.mark.parametrize(("utc", "level"), [
    ("2026-01-01T13:59:59+00:00", "none"), ("2026-01-01T14:00:00+00:00", "watch"),
    ("2026-01-01T15:59:00+00:00", "watch"), ("2026-01-01T16:00:00+00:00", "watch"),
    ("2026-01-01T21:59:59+00:00", "watch"), ("2026-01-01T22:00:00+00:00", "none"),
])
def test_hypoxia_night_window_boundaries(utc, level):
    assert assess(datetime.fromisoformat(utc)).level == level


def test_hypoxia_non_utc_aware_input():
    assert assess(datetime(2026, 1, 1, 23, 0, tzinfo=ZoneInfo("Asia/Tokyo"))).level == "watch"


@pytest.mark.parametrize("hours", [0, 23.99])
def test_hypoxia_history_warmup_is_unknown(hours):
    result = assess(datetime.now(timezone.utc), hours=hours)
    assert result.level == "unknown" and not result.flagged
    assert "history" in result.explanation.lower()


def test_hypoxia_known_after_warmup_and_never_flagged():
    result = assess(datetime(2026, 1, 1, 14, tzinfo=timezone.utc), hours=24)
    assert result.level == "watch" and result.flagged is False
    assert result.explanation == "DO is not measured; low oxygen is common before dawn; aerate overnight."
    assert result.advice


@pytest.mark.parametrize("hour", [2, 14])
@pytest.mark.parametrize(("ph", "temp", "condition"), [(None, 28, "pH"), (7.5, None, "temperature")])
def test_hypoxia_missing_or_stale_inputs_unknown(hour, ph, temp, condition):
    result = assess(datetime(2026, 1, 1, hour, tzinfo=timezone.utc), ph=ph, temp=temp)
    assert result.level == "unknown" and result.flagged is False
    assert condition in result.explanation


def test_hypoxia_rejects_naive_and_do_inputs():
    with pytest.raises(ValueError):
        assess(datetime(2026, 1, 1))
    with pytest.raises(TypeError):
        assess_hypoxia(now=datetime.now(timezone.utc), ph=7.5, water_temp_c=28, history_hours=24, do=8)
    with pytest.raises(TypeError):
        assess_hypoxia(now=datetime.now(timezone.utc), ph=7.5, water_temp_c=28, history_hours=24, dissolved_oxygen=8)


def test_legacy_temperature_settings_warn_and_remain_readable(monkeypatch):
    import koi.settings as settings_module
    from conftest import make_settings

    monkeypatch.setattr(settings_module, "_HYPOXIA_SETTINGS_WARNING_EMITTED", False)
    with pytest.warns(DeprecationWarning, match="deprecated and unused"):
        settings = make_settings(hypoxia_watch_temp_c=29.0, hypoxia_high_temp_c=31.0)
    assert settings.hypoxia_watch_temp_c == 29.0
    assert settings.hypoxia_high_temp_c == 31.0

    # These retired values no longer have an ordering constraint because they
    # do not participate in the model.
    settings = make_settings(hypoxia_watch_temp_c=33.0, hypoxia_high_temp_c=30.0)
    assert settings.hypoxia_watch_temp_c == 33.0
    assert settings.hypoxia_high_temp_c == 30.0


def test_hypoxia_output_contract_has_legacy_nulls_without_do():
    body = assess(datetime(2026, 1, 1, 14, tzinfo=timezone.utc)).to_dict()
    assert {"level", "flagged", "explanation", "advice", "model_id", "horizon", "support", "issue_time"} <= body.keys()
    assert not {"do", "dissolved_oxygen"} & body.keys()
    assert body["dark"] is None and body["lux"] is None and body["aeration"] is None
    assert body["water_temp_c"] is None and body["algae_high"] is None
    assert body["watch_temp_c"] is None and body["high_temp_c"] is None
    assert body["night_lux_threshold"] is None and body["raised_by"] == []


def test_shadow_ph_relative_hand_computed_mad():
    from datetime import timedelta

    state = HypoxiaState()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, ph in enumerate([7.0] * 24 + [8.0] * 23 + [6.0]):
        state.add(start + timedelta(hours=i), ph, 28)
    result = shadow_ph_relative(state, start + timedelta(hours=47))
    assert result["n"] == 48
    assert result["ph_z"] == pytest.approx(-1.3489795003921634)
    assert result["ph_below_own_range"] is False


def test_shadow_ph_relative_requires_minimum_and_ignores_future_samples():
    from datetime import timedelta

    state = HypoxiaState()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(47):
        state.add(start + timedelta(hours=i), 7.5, 28)
    state.add(start + timedelta(hours=48), 1.0, 28)
    result = shadow_ph_relative(state, start + timedelta(hours=46))
    assert result == {"ph_z": None, "ph_below_own_range": None, "n": 47}


def test_shadow_calculation_does_not_change_advisory():
    import random
    from datetime import timedelta

    rng = random.Random(126)
    now = datetime(2026, 1, 1, 14, tzinfo=timezone.utc)
    baseline = assess(now).to_dict()
    for _ in range(30):
        state = HypoxiaState()
        for i in range(60):
            state.add(now - timedelta(hours=60 - i), rng.uniform(5.0, 10.0), 28)
        shadow_ph_relative(state, now)
        assert assess(now).to_dict() == baseline
