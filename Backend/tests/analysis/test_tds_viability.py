"""Offline TDS viability analysis, analysis/tds_viability/tds_viability.py
(report Section 4.4.3.1, tests T1 to T4), run on synthetic bench data."""
import importlib.util
import sys
from dataclasses import replace
from datetime import timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "analysis" / "tds_viability" / "tds_viability.py"
_spec = importlib.util.spec_from_file_location("tds_viability", SCRIPT)
assert _spec and _spec.loader
tv = importlib.util.module_from_spec(_spec)
sys.modules["tds_viability"] = tv
_spec.loader.exec_module(tv)


def statuses(params=None, cfg=None):
    data = tv.synthetic_dataset(params or tv.SyntheticParams())
    return {r.name: r.status for r in tv.analyse(data, cfg or tv.Config())}


def test_pass_scenario_passes_every_test():
    assert statuses() == {"T1": "PASS", "T2": "PASS", "T3": "PASS", "T4": "PASS"}


@pytest.mark.parametrize("seed", range(5))
def test_pass_scenario_passes_with_other_noise_draws(seed):
    assert set(statuses(tv.SyntheticParams(seed=seed)).values()) == {"PASS"}


def test_t1_fails_when_noise_hides_the_smallest_step():
    result = statuses(tv.SCENARIOS["fail-t1"])
    assert result["T1"] == "FAIL"


def test_t1_fails_on_drift_larger_than_the_floor():
    result = statuses(tv.SyntheticParams(drift_ppm_per_day=10.0))
    assert result["T1"] == "FAIL"


def test_t2_fails_when_compensation_leaves_a_temperature_residual():
    result = statuses(tv.SCENARIOS["fail-t2"])
    assert result["T1"] == "PASS"
    assert result["T2"] == "FAIL"


def test_t2_is_inconclusive_when_the_swing_is_too_small():
    result = statuses(tv.SyntheticParams(temp_swing_c=2.0))
    assert result["T2"] == "INCONCLUSIVE"


def test_t3_fails_when_measured_steps_miss_the_prediction():
    result = statuses(tv.SCENARIOS["fail-t3"])
    assert result == {"T1": "PASS", "T2": "PASS", "T3": "FAIL", "T4": "PASS"}


def test_t3_passes_inside_the_20_percent_band_and_fails_outside_it():
    assert statuses(tv.SyntheticParams(dose_response=0.9))["T3"] == "PASS"
    assert statuses(tv.SyntheticParams(dose_response=0.7))["T3"] == "FAIL"


def test_t4_fails_on_unexplained_steps():
    result = statuses(tv.SCENARIOS["fail-t4"])
    assert result == {"T1": "PASS", "T2": "PASS", "T3": "PASS", "T4": "FAIL"}


def test_t4_counts_false_flags_and_detections():
    data = tv.synthetic_dataset(tv.SCENARIOS["fail-t4"])
    t4 = tv.analyse(data)[3]
    assert t4.values["detected"] == 3
    # Three 30 ppm pulses, each flagged going up and coming back down.
    assert t4.values["false_flags"] == 6
    assert t4.values["false_flags_per_week"] == pytest.approx(6.0)


def test_t4_allows_one_false_flag_per_week():
    assert statuses(tv.SyntheticParams(unexplained_steps=0))["T4"] == "PASS"
    data = tv.synthetic_dataset(tv.SyntheticParams(unexplained_steps=1))
    # One pulse is two flags in 7 days.
    assert tv.analyse(data)[3].status == "FAIL"
    two_weeks = tv.analyse(data, replace(tv.Config(), max_false_flags_per_week=2.0))[3]
    assert two_weeks.status == "PASS"


def test_t4_does_not_count_a_flag_near_a_logged_event_as_false():
    data = tv.synthetic_dataset(tv.SyntheticParams(unexplained_steps=1))
    pulses = [f.time for f in tv.tds_steps.detect_steps(data.tds, 6.0)]
    start = min(t for t in pulses if t > data.events[-1].time - timedelta(days=5))
    data.events.append(tv.Event(start, "top_up"))
    data.events.append(tv.Event(start + timedelta(hours=6), "top_up"))
    assert tv.analyse(data)[3].values["false_flags"] == 0


def test_tests_are_not_run_without_phase_windows():
    data = tv.synthetic_dataset()
    data.events = [e for e in data.events if e.type not in tv.PHASE_EVENTS]
    result = {r.name: r.status for r in tv.analyse(data)}
    assert result["T1"] == "NOT RUN"
    assert result["T2"] == "NOT RUN"
    assert result["T4"] == "NOT RUN"
    assert "Not every test could be run" in tv.verdict(tv.analyse(data))


def test_verdict_follows_the_report():
    good = tv.analyse(tv.synthetic_dataset())
    assert tv.verdict(good).startswith("All four tests pass")
    bad_t2 = tv.analyse(tv.synthetic_dataset(tv.SCENARIOS["fail-t2"]))
    assert "evaporation cross-check" in tv.verdict(bad_t2)


def test_parse_time_accepts_supabase_export_formats():
    expected = tv.parse_time("2026-10-01T12:00:00+00:00")
    assert tv.parse_time("2026-10-01 12:00:00+00") == expected
    assert tv.parse_time("2026-10-01T12:00:00Z") == expected
    assert tv.parse_time("2026-10-01 12:00:00") == expected
    assert tv.parse_time("2026-10-01 20:00:00.5+08").astimezone(timezone.utc).hour == 12


def test_water_change_without_tap_tds_is_rejected(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text("event_timestamp,event_type,volume_percentage\n2026-10-01T10:00:00Z,water_change,20\n")
    with pytest.raises(ValueError, match="tap_tds_ppm"):
        tv.load_events(path)


def test_csv_round_trip_gives_the_same_results(tmp_path):
    data = tv.synthetic_dataset()
    tv.write_sensors(tmp_path / "sensors.csv", data.tds, data.temp)
    tv.write_events(tmp_path / "events.csv", data.events)
    code = tv.main(["--sensors", str(tmp_path / "sensors.csv"), "--events", str(tmp_path / "events.csv"),
                    "--run", "bench1", "--out", str(tmp_path / "out")])
    assert code == 0
    report = (tmp_path / "out" / "bench1" / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# TDS viability: bench1")
    for name in ("T1 Detection floor", "T2 Temperature residual", "T3 Known-dose events", "T4 False prompts"):
        assert f"| {name} |" in report
    assert "| PASS |" in report and "| FAIL |" not in report


def test_synthetic_cli_writes_report_and_inputs(tmp_path):
    assert tv.main(["--synthetic", "--scenario", "fail-t3", "--out", str(tmp_path)]) == 0
    run_dir = tmp_path / "synthetic-fail-t3"
    assert {p.name for p in run_dir.iterdir()} == {"report.md", "sensors.csv", "events.csv"}
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## T3 Known-dose events: FAIL" in report
