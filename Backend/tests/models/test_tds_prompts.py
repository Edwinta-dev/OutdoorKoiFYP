"""Prompt signals, persistence, cool-down and rejected guesses, all offline."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from conftest import FIXTURES, make_settings
from koi.models.engine import EventKind, PondConfig, PondEvent
from koi.models.event_ledger import EventLedger
from koi.models.pond_twin import PondTwin
from koi.models.sensor_inputs import SensorInput
from koi.models.tds_prompts import PromptConfig, TdsPrompts
from koi.models.tds_steps import detect_steps

AT = datetime(2026, 10, 4, tzinfo=timezone.utc)
ON = PromptConfig(enabled=True)


def samples(values, start=AT):
    return [SensorInput(start + timedelta(minutes=15 * i), "ingestion", {"tds": v}, {"tds": i + 1})
            for i, v in enumerate(values)]


def observe(state, values, start=AT, config=ON, ledger=None):
    state.observe(samples(values, start), ledger or EventLedger(), config)
    return state


@pytest.mark.parametrize("level,kinds", [(260, ["salt", "filter_clean"]),
                                        (160, ["water_change", "top_up", "filter_clean"])])
def test_prompt_step_uses_viability_detector_and_waits_for_persistence(level, kinds):
    state = observe(TdsPrompts(), [200] * 12 + [level])
    assert state.prompts == []
    observe(state, [level] * 20, AT + timedelta(minutes=15 * 13))
    assert len(state.prompts) == 1
    p = state.prompts[0]
    expected = detect_steps([(s.time, s.channels["tds"]) for s in samples([200] * 12 + [level] * 2)], 20)[0]
    assert p["delta_ppm"] == expected.delta_ppm and p["event_at"] == expected.time.isoformat()
    assert p["event_kinds"] == kinds and p["confidence"] == 1


def test_prompt_spike_minimum_size_missing_and_invalid_readings():
    state = observe(TdsPrompts(), [200] * 12 + [280, 200, 215, 215, None, float("nan"), 0, 6000])
    assert state.prompts == [] and state.pending == []
    assert all(5 <= p[1] <= 5000 for p in state.baseline)


def test_prompt_confidence_threshold_suppresses_noisy_step():
    state = observe(TdsPrompts(), [195, 205] * 6 + [250, 250],
                    config=PromptConfig(True, 20, 0.9, 24))
    assert state.prompts == []
    assert len(observe(TdsPrompts(), [195, 205] * 6 + [250, 250]).prompts) == 1


@pytest.mark.parametrize("level,expected", [(239, 0), (240, 0), (241, 1)])
def test_prompt_configured_minimum_step_is_strict(level, expected):
    state = observe(TdsPrompts(), [200] * 12 + [level] * 2,
                    config=PromptConfig(True, 40, 0.8, 24))
    assert len(state.prompts) == expected


def test_prompt_dismissal_allows_same_guess_after_exact_cooldown():
    state = observe(TdsPrompts(), [200] * 12 + [250] * 2)
    state.prompts[0]["answer"] = "dismiss"
    confirmed = datetime.fromisoformat(state.last_prompt_at)
    restored = TdsPrompts.from_dict(json.loads(json.dumps(state.to_dict())))
    observe(restored, [250] * 12, confirmed + timedelta(hours=20))
    observe(restored, [300] * 2, confirmed + timedelta(hours=24, minutes=-15))
    assert len(restored.prompts) == 2
    assert restored.prompts[1]["event_kinds"] == state.prompts[0]["event_kinds"]
    assert restored.rejected == []


@pytest.mark.parametrize("sign", [1, -1])
def test_prompt_slope_reports_measured_rate_and_confidence(sign):
    state = observe(TdsPrompts(), [200] * 12 + [200 + sign * 3 * i for i in range(1, 15)])
    slopes = [p for p in state.prompts if p["detection"] == "slope"]
    assert len(slopes) == 1 and slopes[0]["delta_ppm"] * sign > 20
    assert slopes[0]["slope_ppm_hour"] * sign > 0 and slopes[0]["confidence"] >= 0.8


def test_prompt_cooldown_is_per_pond_and_consumes_suppressed_step():
    state = observe(TdsPrompts(), [200] * 12 + [250] * 12 + [300] * 2)
    assert len(state.prompts) == 1
    observe(state, [300] * 12, AT + timedelta(hours=25))
    assert len(state.prompts) == 1
    observe(state, [350] * 2, AT + timedelta(hours=29))
    assert len(state.prompts) == 2
    assert len(observe(TdsPrompts(), [200] * 12 + [300] * 2).prompts) == 1


def test_prompt_none_of_these_remembers_rejections_across_snapshot():
    state = observe(TdsPrompts(), [200] * 12 + [250] * 2)
    state.reject(state.prompts[0])
    restored = TdsPrompts.from_dict(json.loads(json.dumps(state.to_dict())))
    observe(restored, [250] * 12 + [300] * 2, AT + timedelta(hours=30))
    assert len(restored.prompts) == 1 and restored.rejected == ["step:rise:salt", "step:rise:filter_clean"]
    observe(restored, [300] * 12 + [250] * 2, AT + timedelta(hours=60))
    assert len(restored.prompts) == 2


def test_prompt_flag_off_does_not_collect_or_mutate_state():
    state = TdsPrompts()
    before = state.to_dict()
    observe(state, [200] * 12 + [300] * 2, config=PromptConfig())
    assert state.to_dict() == before and not make_settings().tds_prompts


def test_prompt_logged_event_explains_signal_and_duplicate_inputs_are_ignored():
    ledger = EventLedger()
    ledger.add("known", PondEvent(EventKind.SALT, AT + timedelta(hours=3), salt_grams=5),
               recorded_at=AT, source="api")
    state = observe(TdsPrompts(), [200] * 12 + [300] * 2, ledger=ledger)
    assert state.prompts == []
    before = state.to_dict()
    observe(state, [200] * 12 + [300] * 2)
    assert state.to_dict() == before


def test_prompt_snapshot_roundtrip_and_recorded_old_snapshot_loads():
    twin = PondTwin.create(PondConfig(volume_litres=1000, estimated_biomass_grams=1000))
    observe(twin.tds_prompts, [200] * 12 + [250])
    restored = PondTwin.from_snapshot(json.loads(json.dumps(twin.to_snapshot())))
    observe(restored.tds_prompts, [250], AT + timedelta(minutes=195))
    assert len(restored.tds_prompts.prompts) == 1
    # Recorded fixture predates prompt state, not a freshly manufactured snapshot.
    old = json.loads((FIXTURES / "snapshot_v2_utc_days.json").read_text())
    assert PondTwin.from_snapshot(old).tds_prompts.to_dict() == TdsPrompts().to_dict()


@pytest.mark.parametrize("field,value", [("tds_prompt_min_step_ppm", 0),
                                         ("tds_prompt_confidence_threshold", 1.1),
                                         ("tds_prompt_cooldown_hours", -1),
                                         ("tds_prompt_min_step_ppm", float("inf"))])
def test_prompt_settings_validate_limits(field, value):
    with pytest.raises(ValidationError):
        make_settings(**{field: value})
