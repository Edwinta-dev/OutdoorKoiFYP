"""TDS viability analysis for the bench tests T1 to T4 (report Section 4.4.3.1).

Reads a SensorData export and an events file, runs the four tests and writes
<out>/<run>/report.md with each test's measured value against its pass
condition.

Usage, from the repository root:
    python analysis/tds_viability/tds_viability.py --sensors sensors.csv --events events.csv
    python analysis/tds_viability/tds_viability.py --synthetic [--scenario fail-t2] --out build/tds_demo

Sensor CSV: the SensorData table export, one row per reading with columns
created_at, sensor_type and data1. Rows with sensor_type TDS (ppm) and temp
(deg C) are used; other rows are ignored.

Events CSV, one row per event, columns:
    event_timestamp  ISO 8601 time (UTC if no offset is given)
    event_type       phase_start | phase_end | water_change | salt_dose,
                     or any other logged event (feeding, top_up, ...)
    phase            T1, T2, T3 or T4, for phase_start and phase_end rows
    volume_percentage  water_change: percentage of the water replaced
    tap_tds_ppm      water_change: measured TDS of the replacement water
                     (0 simulates rain)
    salt_grams       salt_dose: grams of salt dissolved
    pond_litres      salt_dose: volume of water in the bucket
    note             free text, copied into the report

T1 and T2 need their phase window. T3 and T4 use their window if one is
given; T4 falls back to the T3 window, and both fall back to the whole log.
Events other than phase markers explain any step flagged near them in T4;
only water_change and salt_dose have a predicted step for T3.

The step detector is koi.models.tds_steps, shared with the server.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Backend"))

from koi.models import tds_steps  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "out"

PASS, FAIL, INCONCLUSIVE, NOT_RUN = "PASS", "FAIL", "INCONCLUSIVE", "NOT RUN"
DOSE_EVENTS = ("water_change", "salt_dose")
PHASE_EVENTS = ("phase_start", "phase_end")

Series = list[tuple[datetime, float]]


@dataclass(frozen=True)
class Config:
    floor_multiplier: float = 3.0          # T1: floor = 3 x reading spread
    fallback_step_ppm: float = 40.0        # T1: smallest step to resolve when no T3 events are logged
    min_swing_c: float = 4.0               # T2: the swing the test must cover
    temp_pair_minutes: float = 10.0        # T2: TDS and temp readings closer than this are one cycle
    step_tolerance: float = 0.20           # T3: +-20 % of the prediction
    pre_hours: float = 2.0                 # T3: baseline window before an event
    settle_hours: float = 0.5              # T3: mixing time skipped after an event
    post_hours: float = 2.0                # T3: level window after the settle time
    match_before_hours: float = 0.25       # T4: a flag this long before an event is explained by it
    max_false_flags_per_week: float = 1.0  # T4
    persist: int = tds_steps.DEFAULT_PERSIST_READINGS
    baseline_readings: int = tds_steps.DEFAULT_BASELINE_READINGS

    @property
    def match_after_hours(self) -> float:
        return self.settle_hours + self.post_hours


@dataclass(frozen=True)
class Event:
    time: datetime
    type: str
    phase: str = ""
    volume_percentage: Optional[float] = None
    tap_tds_ppm: Optional[float] = None
    salt_grams: Optional[float] = None
    pond_litres: Optional[float] = None
    note: str = ""

    def label(self) -> str:
        if self.type == "water_change":
            return f"water change {self.volume_percentage:g} % (tap {self.tap_tds_ppm:g} ppm)"
        if self.type == "salt_dose":
            return f"salt {self.salt_grams:g} g in {self.pond_litres:g} L"
        return self.type.replace("_", " ")


@dataclass
class Dataset:
    tds: Series
    temp: Series
    events: list[Event]

    def phase(self, name: str) -> Optional[tuple[datetime, datetime]]:
        start = next((e.time for e in self.events if e.type == "phase_start" and e.phase == name), None)
        end = next((e.time for e in self.events if e.type == "phase_end" and e.phase == name), None)
        if start is None or end is None or end <= start:
            return None
        return start, end


@dataclass
class TestResult:
    name: str
    title: str
    status: str
    measured: str
    condition: str
    details: list[str] = field(default_factory=list)
    values: dict = field(default_factory=dict)


# ---------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------
def parse_time(text: str) -> datetime:
    s = text.strip().replace(" ", "T", 1)
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    # Postgres exports write the offset as +00 rather than +00:00.
    if len(s) >= 3 and s[-3] in "+-" and s[-2:].isdigit() and "T" in s and s.rfind(":") < len(s) - 3:
        s += ":00"
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _float(row: dict, key: str) -> Optional[float]:
    value = (row.get(key) or "").strip()
    return float(value) if value else None


def load_sensors(path: Path) -> tuple[Series, Series]:
    tds: Series = []
    temp: Series = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            kind = (row.get("sensor_type") or "").strip().lower()
            value = _float(row, "data1")
            if value is None or kind not in ("tds", "temp"):
                continue
            (tds if kind == "tds" else temp).append((parse_time(row["created_at"]), value))
    tds.sort(key=lambda r: r[0])
    temp.sort(key=lambda r: r[0])
    return tds, temp


def load_events(path: Path) -> list[Event]:
    events = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            event = Event(
                time=parse_time(row["event_timestamp"]),
                type=row["event_type"].strip().lower(),
                phase=(row.get("phase") or "").strip().upper(),
                volume_percentage=_float(row, "volume_percentage"),
                tap_tds_ppm=_float(row, "tap_tds_ppm"),
                salt_grams=_float(row, "salt_grams"),
                pond_litres=_float(row, "pond_litres"),
                note=(row.get("note") or "").strip(),
            )
            _check_event(event)
            events.append(event)
    events.sort(key=lambda e: e.time)
    return events


def _check_event(e: Event) -> None:
    when = e.time.isoformat()
    if e.type in PHASE_EVENTS and e.phase not in ("T1", "T2", "T3", "T4"):
        raise ValueError(f"{e.type} at {when} needs phase T1, T2, T3 or T4")
    if e.type == "water_change" and (e.volume_percentage is None or e.tap_tds_ppm is None):
        raise ValueError(f"water_change at {when} needs volume_percentage and tap_tds_ppm")
    if e.type == "salt_dose" and (e.salt_grams is None or not e.pond_litres):
        raise ValueError(f"salt_dose at {when} needs salt_grams and pond_litres")


def write_sensors(path: Path, tds: Series, temp: Series) -> None:
    rows = [(t, "TDS", v) for t, v in tds] + [(t, "temp", v) for t, v in temp]
    rows.sort(key=lambda r: (r[0], r[1]))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["created_at", "sensor_type", "data1"])
        for t, kind, v in rows:
            w.writerow([t.isoformat(), kind, f"{v:.2f}"])


EVENT_COLUMNS = ["event_timestamp", "event_type", "phase", "volume_percentage",
                 "tap_tds_ppm", "salt_grams", "pond_litres", "note"]


def write_events(path: Path, events: list[Event]) -> None:
    def cell(v: Optional[float]) -> str:
        return "" if v is None else f"{v:g}"

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(EVENT_COLUMNS)
        for e in events:
            w.writerow([e.time.isoformat(), e.type, e.phase, cell(e.volume_percentage),
                        cell(e.tap_tds_ppm), cell(e.salt_grams), cell(e.pond_litres), e.note])


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def window(series: Series, start: datetime, end: datetime) -> Series:
    return [(t, v) for t, v in series if start <= t < end]


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares slope and intercept."""
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    return slope, my - slope * mx


def days_since(t: datetime, t0: datetime) -> float:
    return (t - t0).total_seconds() / 86400.0


def pair_with_temperature(tds: Series, temp: Series, tolerance: timedelta) -> list[tuple[float, float]]:
    """(temp C, TDS ppm) for each TDS reading with a temperature reading
    within the tolerance."""
    pairs = []
    j = 0
    for t, ppm in tds:
        while j + 1 < len(temp) and abs(temp[j + 1][0] - t) <= abs(temp[j][0] - t):
            j += 1
        if temp and abs(temp[j][0] - t) <= tolerance:
            pairs.append((temp[j][1], ppm))
    return pairs


def predicted_step(event: Event, tds_before: float) -> float:
    if event.type == "water_change":
        assert event.volume_percentage is not None and event.tap_tds_ppm is not None
        return tds_steps.predicted_water_change_step(tds_before, event.volume_percentage / 100.0,
                                                     event.tap_tds_ppm)
    assert event.salt_grams is not None and event.pond_litres is not None
    return tds_steps.predicted_salt_step(event.salt_grams, event.pond_litres)


def _range_or_all(data: Dataset, name: str) -> tuple[Optional[tuple[datetime, datetime]], str]:
    span = data.phase(name)
    if span:
        return span, f"{name} window"
    if not data.tds:
        return None, "no data"
    return (data.tds[0][0], data.tds[-1][0] + timedelta(seconds=1)), "whole log"


# ---------------------------------------------------------------------
# The four tests
# ---------------------------------------------------------------------
def event_steps(data: Dataset, cfg: Config) -> list[dict]:
    """Measured and predicted step for each water change and salt dose in
    the T3 window."""
    span, _ = _range_or_all(data, "T3")
    if span is None:
        return []
    out = []
    for e in data.events:
        if e.type not in DOSE_EVENTS or not span[0] <= e.time < span[1]:
            continue
        before = [v for _, v in window(data.tds, e.time - timedelta(hours=cfg.pre_hours), e.time)]
        after_start = e.time + timedelta(hours=cfg.settle_hours)
        after = [v for _, v in window(data.tds, after_start, after_start + timedelta(hours=cfg.post_hours))]
        row: dict = {"event": e, "before": None, "after": None, "predicted": None, "measured": None}
        if len(before) >= 2 and len(after) >= 2:
            row["before"] = statistics.median(before)
            row["after"] = statistics.median(after)
            row["predicted"] = predicted_step(e, row["before"])
            row["measured"] = row["after"] - row["before"]
        out.append(row)
    return out


def run_t1(data: Dataset, steps: list[dict], cfg: Config) -> TestResult:
    condition = (f"floor = {cfg.floor_multiplier:g} x spread; floor below the smallest predicted "
                 "event step and drift within the floor per day")
    r = TestResult("T1", "Detection floor", NOT_RUN, "-", condition)
    span = data.phase("T1")
    if span is None:
        r.details.append("No T1 phase_start/phase_end pair in the events file.")
        return r
    series = window(data.tds, *span)
    if len(series) < 3:
        r.details.append(f"{len(series)} TDS readings in the T1 window; at least 3 are needed.")
        return r

    t0 = series[0][0]
    xs = [days_since(t, t0) for t, _ in series]
    ys = [v for _, v in series]
    drift, intercept = linear_fit(xs, ys)
    residuals = [y - (drift * x + intercept) for x, y in zip(xs, ys, strict=True)]
    spread = statistics.stdev(residuals)
    diffs = [b - a for a, b in zip(ys, ys[1:], strict=False)]
    repeat = statistics.stdev(diffs) / math.sqrt(2) if len(diffs) >= 2 else float("nan")
    floor = tds_steps.detection_floor(spread, cfg.floor_multiplier)

    predicted = [abs(s["predicted"]) for s in steps if s["predicted"] is not None]
    if predicted:
        smallest = min(predicted)
        smallest_source = "smallest predicted T3 step"
    else:
        smallest = cfg.fallback_step_ppm
        smallest_source = "no T3 events; default 20 % water change step"

    resolves = floor < smallest
    drift_ok = abs(drift) <= floor
    r.status = PASS if resolves and drift_ok else FAIL
    r.measured = f"floor {floor:.1f} ppm (spread {spread:.2f} ppm), drift {drift:+.2f} ppm/day"
    r.values = {"floor_ppm": floor, "spread_ppm": spread, "drift_ppm_per_day": drift,
                "repeatability_ppm": repeat, "smallest_step_ppm": smallest}
    hours = (series[-1][0] - t0).total_seconds() / 3600
    r.details += [
        f"{len(series)} readings over {hours:.1f} h, mean {statistics.fmean(ys):.1f} ppm.",
        f"Spread (standard deviation about the linear drift): {spread:.2f} ppm.",
        f"Repeatability between consecutive power cycles: {repeat:.2f} ppm.",
        f"Drift: {drift:+.2f} ppm/day ({'within' if drift_ok else 'larger than'} the floor).",
        f"Detection floor: {floor:.1f} ppm against {smallest:.1f} ppm ({smallest_source}): "
        f"{'resolved' if resolves else 'not resolved'}.",
    ]
    return r


def run_t2(data: Dataset, floor: Optional[float], cfg: Config) -> TestResult:
    condition = (f"temperature-driven movement within the T1 floor, over a swing of at least "
                 f"{cfg.min_swing_c:g} deg C")
    r = TestResult("T2", "Temperature residual", NOT_RUN, "-", condition)
    span = data.phase("T2")
    if span is None:
        r.details.append("No T2 phase_start/phase_end pair in the events file.")
        return r
    pairs = pair_with_temperature(window(data.tds, *span), window(data.temp, *span),
                                  timedelta(minutes=cfg.temp_pair_minutes))
    if len(pairs) < 3:
        r.details.append(f"{len(pairs)} TDS readings with a matching temperature; at least 3 are needed.")
        return r

    temps = [p[0] for p in pairs]
    ppms = [p[1] for p in pairs]
    swing = max(temps) - min(temps)
    slope, _ = linear_fit(temps, ppms)
    movement = abs(slope) * swing
    r.values = {"swing_c": swing, "slope_ppm_per_c": slope, "movement_ppm": movement}
    r.measured = f"{movement:.1f} ppm over a {swing:.1f} deg C swing ({slope:+.2f} ppm/deg C)"
    r.details += [
        f"{len(pairs)} readings paired with water temperature, {min(temps):.1f} to {max(temps):.1f} deg C.",
        f"Compensated TDS against temperature: {slope:+.2f} ppm per deg C.",
        f"Movement across the swing: {movement:.1f} ppm.",
    ]
    if floor is None:
        r.status = INCONCLUSIVE
        r.details.append("No T1 floor to compare against.")
    elif swing < cfg.min_swing_c:
        r.status = INCONCLUSIVE
        r.details.append(f"Swing of {swing:.1f} deg C is below the {cfg.min_swing_c:g} deg C the test needs.")
    else:
        r.status = PASS if movement <= floor else FAIL
        r.details.append(f"T1 floor: {floor:.1f} ppm.")
    return r


def run_t3(steps: list[dict], floor: Optional[float], cfg: Config) -> TestResult:
    condition = f"each measured step within +-{cfg.step_tolerance:.0%} of prediction, or within the T1 floor"
    r = TestResult("T3", "Known-dose events", NOT_RUN, "-", condition)
    if not steps:
        r.details.append("No water_change or salt_dose events in the T3 window.")
        return r
    passed = 0
    rows = []
    for s in steps:
        e = s["event"]
        if s["measured"] is None:
            s["ok"] = False
            rows.append(f"| {e.time:%Y-%m-%d %H:%M} | {e.label()} | - | - | - | - | no readings around the event |")
            continue
        tolerance = max(cfg.step_tolerance * abs(s["predicted"]), floor or 0.0)
        error = s["measured"] - s["predicted"]
        s["ok"] = abs(error) <= tolerance
        passed += s["ok"]
        rows.append(f"| {e.time:%Y-%m-%d %H:%M} | {e.label()} | {s['before']:.1f} | {s['predicted']:+.1f} | "
                    f"{s['measured']:+.1f} | {error:+.1f} (allowed +-{tolerance:.1f}) | "
                    f"{'pass' if s['ok'] else 'fail'} |")
    r.status = PASS if passed == len(steps) else FAIL
    r.measured = f"{passed} of {len(steps)} events within tolerance"
    r.values = {"events": len(steps), "within_tolerance": passed}
    r.details += [
        "Times are UTC. Before is the median TDS over the "
        f"{cfg.pre_hours:g} h before the event; the measured step is the median over the "
        f"{cfg.post_hours:g} h after a {cfg.settle_hours:g} h mixing time, minus Before. All values in ppm.",
        "",
        "| Event time | Event | Before | Predicted | Measured | Error | Result |",
        "|---|---|---|---|---|---|---|",
        *rows,
    ]
    if floor is None:
        r.details.append("")
        r.details.append("No T1 floor: tolerance is the percentage band alone.")
    return r


def run_t4(data: Dataset, steps: list[dict], floor: Optional[float], cfg: Config) -> TestResult:
    condition = (f"all T3 events detected and at most {cfg.max_false_flags_per_week:g} false flag per week")
    r = TestResult("T4", "False prompts", NOT_RUN, "-", condition)
    if floor is None:
        r.details.append("No T1 floor, so the step rule has no threshold.")
        return r
    span = data.phase("T4")
    source = "T4 window"
    if span is None:
        span, source = _range_or_all(data, "T3")
        source = source if source != "T3 window" else "T3 window (no T4 window given)"
    if span is None:
        r.details.append("No TDS readings.")
        return r
    series = window(data.tds, *span)
    if len(series) <= cfg.baseline_readings:
        r.details.append(f"{len(series)} readings; the rolling baseline alone needs {cfg.baseline_readings}.")
        return r

    flags = tds_steps.detect_steps(series, floor, cfg.persist, cfg.baseline_readings)
    before = timedelta(hours=cfg.match_before_hours)
    after = timedelta(hours=cfg.match_after_hours)
    logged = [e for e in data.events if e.type not in PHASE_EVENTS and span[0] <= e.time < span[1]]

    def near(flag: tds_steps.TdsStep, e: Event) -> bool:
        return e.time - before <= flag.time <= e.time + after

    detected = 0
    for s in steps:
        if s["predicted"] is None:
            s["detected"] = False
            continue
        sign = 1 if s["predicted"] > 0 else -1
        s["detected"] = any(near(f, s["event"]) and f.delta_ppm * sign > 0 for f in flags)
        detected += s["detected"]
    false_flags = [f for f in flags if not any(near(f, e) for e in logged)]

    # The last reading covers one sampling interval too.
    spacing = statistics.median((b[0] - a[0]).total_seconds() for a, b in zip(series, series[1:], strict=False))
    duration_days = ((series[-1][0] - series[0][0]).total_seconds() + spacing) / 86400
    rate = len(false_flags) / duration_days * 7 if duration_days > 0 else float("inf")
    event_days = {e.time.date() for e in logged}
    days = sorted({t.date() for t, _ in series})
    quiet_days = [d for d in days if d not in event_days]
    quiet_flags = [f for f in false_flags if f.time.date() not in event_days]

    all_detected = detected == len(steps)
    r.status = PASS if all_detected and rate <= cfg.max_false_flags_per_week else FAIL
    r.measured = f"{detected} of {len(steps)} events detected, {rate:.1f} false flags per week"
    r.values = {"detected": detected, "events": len(steps), "flags": len(flags),
                "false_flags": len(false_flags), "false_flags_per_week": rate}
    r.details += [
        f"Series: {source}, {len(series)} readings over {duration_days:.1f} days.",
        f"Rule: a change above the T1 floor ({floor:.1f} ppm) against the median of the last "
        f"{cfg.baseline_readings} readings, held for {cfg.persist} readings in a row.",
        f"Steps flagged: {len(flags)}. A flag from {cfg.match_before_hours:g} h before to "
        f"{cfg.match_after_hours:g} h after a logged event is explained by it.",
        f"T3 events detected: {detected} of {len(steps)}.",
        f"False flags: {len(false_flags)} ({len(quiet_flags)} on the {len(quiet_days)} days with no "
        f"logged event), {rate:.1f} per week.",
    ]
    if 0 < duration_days < 7:
        r.details.append(f"The weekly rate is extrapolated from {duration_days:.1f} days.")
    if false_flags:
        r.details += ["", "| False flag time (UTC) | Baseline | New level | Step |", "|---|---|---|---|"]
        r.details += [f"| {f.time:%Y-%m-%d %H:%M} | {f.baseline_ppm:.1f} | {f.level_ppm:.1f} | "
                      f"{f.delta_ppm:+.1f} |" for f in false_flags]
    missed = [s["event"] for s in steps if not s["detected"]]
    if missed:
        r.details.append("")
        r.details += [f"Missed: {e.label()} at {e.time:%Y-%m-%d %H:%M}." for e in missed]
    return r


def analyse(data: Dataset, cfg: Optional[Config] = None) -> list[TestResult]:
    cfg = cfg or Config()
    steps = event_steps(data, cfg)
    t1 = run_t1(data, steps, cfg)
    floor = t1.values.get("floor_ppm")
    return [t1, run_t2(data, floor, cfg), run_t3(steps, floor, cfg), run_t4(data, steps, floor, cfg)]


def verdict(results: list[TestResult]) -> str:
    status = {r.name: r.status for r in results}
    if all(s == PASS for s in status.values()):
        return ("All four tests pass. The TDS prompting design in Section 3.9.4 has bench evidence "
                "behind it.")
    if FAIL in (status["T1"], status["T2"]):
        return ("T1 or T2 fails. TDS stays a logged trend and an evaporation cross-check; "
                "the prompting design is not built.")
    if FAIL in status.values():
        failed = ", ".join(n for n, s in status.items() if s == FAIL)
        return f"{failed} failed. The prompting design is not supported by this run."
    return "Not every test could be run. No conclusion about the prompting design yet."


def render_report(run: str, results: list[TestResult], sources: list[str]) -> str:
    lines = [f"# TDS viability: {run}", ""]
    lines += [f"Source: {', '.join(sources)}.", "",
              "Bench tests from report Section 4.4.3.1, Table 4.2.", "",
              "| Test | Measured | Pass condition | Result |", "|---|---|---|---|"]
    lines += [f"| {r.name} {r.title} | {r.measured} | {r.condition} | {r.status} |" for r in results]
    lines += ["", f"Verdict: {verdict(results)}", ""]
    for r in results:
        lines += [f"## {r.name} {r.title}: {r.status}", "", *r.details, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class SyntheticParams:
    """A bench run with known properties: T1 (2 days, sealed standard at a
    steady temperature), T2 (2 days, daily temperature swing) and a 7-day
    T3/T4 bucket run with a water change, a salt dose and simulated rain."""
    seed: int = 34
    interval_minutes: int = 15
    start: datetime = datetime(2026, 10, 1, tzinfo=timezone.utc)
    base_ppm: float = 250.0
    noise_ppm: float = 2.0                 # reading noise (standard deviation)
    drift_ppm_per_day: float = 0.0
    temp_residual_ppm_per_c: float = 0.2   # left over after temperature compensation
    temp_mean_c: float = 29.0
    temp_swing_c: float = 5.0              # peak to peak
    dose_response: float = 1.0             # true step / predicted step
    unexplained_steps: int = 0             # 30 ppm pulses on quiet days in the bucket run
    tap_tds_ppm: float = 50.0
    bucket_litres: float = 100.0


SCENARIOS = {
    "pass": SyntheticParams(),
    "fail-t1": SyntheticParams(noise_ppm=15.0),
    "fail-t2": SyntheticParams(temp_residual_ppm_per_c=3.0),
    "fail-t3": SyntheticParams(dose_response=0.5),
    "fail-t4": SyntheticParams(unexplained_steps=3),
}


def synthetic_dataset(p: Optional[SyntheticParams] = None) -> Dataset:
    p = p or SyntheticParams()
    rng = random.Random(p.seed)
    step = timedelta(minutes=p.interval_minutes)
    day = timedelta(days=1)
    t1 = (p.start, p.start + 2 * day)
    t2 = (t1[1], t1[1] + 2 * day)
    t3 = (t2[1], t2[1] + 7 * day)
    hour = timedelta(hours=1)
    doses = [
        Event(t3[0] + 1 * day + 10 * hour, "water_change", volume_percentage=20.0, tap_tds_ppm=p.tap_tds_ppm),
        Event(t3[0] + 3 * day + 10 * hour, "salt_dose", salt_grams=5.0, pond_litres=p.bucket_litres),
        Event(t3[0] + 5 * day + 10 * hour, "water_change", volume_percentage=10.0, tap_tds_ppm=0.0,
              note="simulated rain"),
    ]
    # Pulses up for 6 h and back, on days 2, 4 and 6 of the bucket run (no dose events).
    pulses = [(t3[0] + d * day + 14 * hour, t3[0] + d * day + 20 * hour) for d in (2, 4, 6)]
    pulses = pulses[:p.unexplained_steps]

    def swing_temp(t: datetime) -> float:
        # Coolest near 06:00, warmest near 15:00 (UTC is used as local time here).
        phase = 2 * math.pi * ((t.hour + t.minute / 60) - 9) / 24
        return p.temp_mean_c + p.temp_swing_c / 2 * math.sin(phase)

    tds: Series = []
    temp: Series = []

    def reading(t: datetime, level: float, temp_c: float, drift_from: datetime) -> None:
        value = (level + p.drift_ppm_per_day * days_since(t, drift_from)
                 + p.temp_residual_ppm_per_c * (temp_c - 25.0) + rng.gauss(0, p.noise_ppm))
        tds.append((t, value))
        temp.append((t, temp_c + rng.gauss(0, 0.05)))

    t = t1[0]
    while t < t1[1]:
        reading(t, p.base_ppm, 25.0, t1[0])
        t += step
    while t < t2[1]:
        reading(t, p.base_ppm, swing_temp(t), t2[0])
        t += step
    level = p.base_ppm
    pending = list(doses)
    while t < t3[1]:
        while pending and pending[0].time <= t:
            e = pending.pop(0)
            level += p.dose_response * predicted_step(e, level)
        pulse = 30.0 if any(a <= t < b for a, b in pulses) else 0.0
        reading(t, level + pulse, swing_temp(t), t3[0])
        t += step

    phases = []
    for name, (a, b) in (("T1", t1), ("T2", t2), ("T3", t3)):
        phases += [Event(a, "phase_start", phase=name), Event(b, "phase_end", phase=name)]
    return Dataset(tds, temp, sorted(phases + doses, key=lambda e: e.time))


# ---------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------
def run(data: Dataset, run_name: str, out: Path, sources: list[str],
        cfg: Optional[Config] = None) -> tuple[Path, list[TestResult]]:
    run_dir = out / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    results = analyse(data, cfg)
    report = run_dir / "report.md"
    report.write_text(render_report(run_name, results, sources), encoding="utf-8")
    return report, results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TDS viability analysis for bench tests T1 to T4.")
    parser.add_argument("--sensors", type=Path, help="SensorData export (created_at, sensor_type, data1)")
    parser.add_argument("--events", type=Path, help="events file (see the module docstring)")
    parser.add_argument("--synthetic", action="store_true", help="analyse generated data instead")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="pass",
                        help="synthetic scenario (default: pass)")
    parser.add_argument("--run", help="run name (default: the sensors file name, or synthetic-<scenario>)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output folder; the report goes in <out>/<run>/report.md")
    parser.add_argument("--pre-hours", type=float, default=Config.pre_hours)
    parser.add_argument("--settle-hours", type=float, default=Config.settle_hours)
    parser.add_argument("--post-hours", type=float, default=Config.post_hours)
    args = parser.parse_args(argv)
    cfg = replace(Config(), pre_hours=args.pre_hours, settle_hours=args.settle_hours,
                  post_hours=args.post_hours)

    if args.synthetic:
        run_name = args.run or f"synthetic-{args.scenario}"
        data = synthetic_dataset(SCENARIOS[args.scenario])
        run_dir = args.out / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        write_sensors(run_dir / "sensors.csv", data.tds, data.temp)
        write_events(run_dir / "events.csv", data.events)
        sources = [f"synthetic data, scenario {args.scenario} (sensors.csv and events.csv beside this report)"]
    else:
        if not args.sensors or not args.events:
            parser.error("give --sensors and --events, or --synthetic")
        tds, temp = load_sensors(args.sensors)
        try:
            events = load_events(args.events)
        except ValueError as exc:
            parser.error(str(exc))
        data = Dataset(tds, temp, events)
        run_name = args.run or args.sensors.stem
        sources = [args.sensors.name, args.events.name]

    report, results = run(data, run_name, args.out, sources, cfg)
    for r in results:
        print(f"{r.status:<12} {r.name} {r.title}: {r.measured}")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
