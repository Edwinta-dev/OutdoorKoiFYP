"""TDS step detector (report Section 4.4.3.1, test T4) and the step
predictions used by test T3."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from koi.models import tds_steps

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def series(values):
    return [(T0 + timedelta(minutes=15 * i), v) for i, v in enumerate(values)]


def test_flat_noisy_series_has_no_steps():
    rng = random.Random(1)
    values = [250 + rng.gauss(0, 2) for _ in range(500)]
    assert tds_steps.detect_steps(series(values), floor_ppm=6.0) == []


def test_held_rise_is_flagged_once_at_the_first_new_reading():
    values = [250.0] * 20 + [300.0] * 20
    steps = tds_steps.detect_steps(series(values), floor_ppm=6.0)
    assert len(steps) == 1
    assert steps[0].index == 20
    assert steps[0].time == T0 + timedelta(minutes=15 * 20)
    assert steps[0].baseline_ppm == 250.0
    assert steps[0].delta_ppm == 50.0


def test_held_fall_is_flagged_with_a_negative_delta():
    steps = tds_steps.detect_steps(series([250.0] * 20 + [210.0] * 20), floor_ppm=6.0)
    assert [s.delta_ppm for s in steps] == [-40.0]


def test_single_reading_spike_is_not_a_step():
    values = [250.0] * 20 + [320.0] + [250.0] * 20
    assert tds_steps.detect_steps(series(values), floor_ppm=6.0) == []


def test_alternating_deviations_do_not_persist():
    values = [250.0] * 20 + [270.0, 230.0, 270.0, 230.0] + [250.0] * 20
    assert tds_steps.detect_steps(series(values), floor_ppm=6.0) == []


def test_change_within_the_floor_is_not_a_step():
    assert tds_steps.detect_steps(series([250.0] * 20 + [255.0] * 20), floor_ppm=6.0) == []


def test_pulse_up_and_back_gives_two_steps():
    values = [250.0] * 20 + [280.0] * 24 + [250.0] * 20
    steps = tds_steps.detect_steps(series(values), floor_ppm=6.0)
    assert [s.delta_ppm for s in steps] == [30.0, -30.0]


def test_persist_sets_how_many_readings_must_deviate():
    values = [250.0] * 20 + [300.0, 300.0] + [250.0] * 20
    assert len(tds_steps.detect_steps(series(values), floor_ppm=6.0, persist=2)) == 1
    assert tds_steps.detect_steps(series(values), floor_ppm=6.0, persist=3) == []


def test_no_flags_before_the_baseline_is_full():
    values = [250.0] * 5 + [300.0] * 5
    assert tds_steps.detect_steps(series(values), floor_ppm=6.0, baseline_readings=12) == []


def test_invalid_arguments_are_rejected():
    with pytest.raises(ValueError):
        tds_steps.detect_steps(series([250.0]), floor_ppm=0.0)
    with pytest.raises(ValueError):
        tds_steps.detect_steps(series([250.0]), floor_ppm=6.0, persist=0)
    with pytest.raises(ValueError):
        tds_steps.predicted_water_change_step(250.0, 1.5, 50.0)
    with pytest.raises(ValueError):
        tds_steps.predicted_salt_step(5.0, 0.0)


def test_report_worked_examples():
    # Section 4.4.3.1: 20 % change with 50 ppm tap water at 250 ppm lowers
    # TDS by 40 ppm; 5 g of salt in 100 L raises it by about 50 ppm.
    assert tds_steps.predicted_water_change_step(250.0, 0.20, 50.0) == pytest.approx(-40.0)
    assert tds_steps.predicted_salt_step(5.0, 100.0) == pytest.approx(50.0)
    # Rain simulated as a water change with tap TDS of zero.
    assert tds_steps.predicted_water_change_step(250.0, 0.10, 0.0) == pytest.approx(-25.0)
    assert tds_steps.detection_floor(2.0) == pytest.approx(6.0)
