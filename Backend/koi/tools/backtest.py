"""python -m koi.tools.backtest --scenario NAME [--out DIR]
python -m koi.tools.backtest --check
python -m koi.tools.backtest --update-baseline

Replays a recorded fixture through the twin's models and scores them
against what was measured (issue #33). Each run writes
build/backtest/<scenario>.json with the metrics and the samples behind
them. --check runs every scenario in Backend/backtest_baseline.json and
fails when a metric is worse than its baseline value by more than the
baseline's tolerance for it (tools/check.py backend runs it).

--------------------------------------------------------------------
SCENARIOS
--------------------------------------------------------------------
demo_pond   The demo pond of Backend/fixtures/demo_pond/seed.json: its
            hourly water temperature, logged top-ups and water changes,
            pond profile, and the station weather in seed["backtest"]
            (loaded as weather history into MemoryStorage, so it is read
            through the same as-of queries and hourly binning as the
            worker's calibration job, koi.worker.poller).

  water temperature  The twin predicts water temperature as station air
            temperature `lag` hours earlier plus an offset, both fitted
            by the calibration job's fit (ev.fit_water_air_temperature)
            on the first 70% of the hours. Each hour of the fit's
            evaluation interval is an issue time t; for h in HORIZONS the
            prediction for t + h is scored against the measured water
            temperature then, beside persistence (the water temperature
            at t). The air temperature at t + h - lag stands in for the
            forecast: where h > lag that is a perfect air forecast, so the
            model's error is a lower bound on what a live forecast gives.

  evaporation  Each logged top-up against the loss the evaporation model
            gives over the hours since the previous top-up or water change
            (ev.modelled_topup_litres, the calibration job's top-up
            samples), at the shelter factor the twin uses without a fit
            (ev.POND_SHELTER_FACTOR). Assumes each top-up refilled the pond
            to the same level.

nea_ladder  The NEA validation table NEA_Data_Analysis/hot_day_validation.csv
            (2,302 days, read only). The action ladder's two same-day
            rules are not in the backend yet, so the backtest scores the
            rule firings recorded in the table against the measured
            outcomes recorded beside them: precision (share of firing days
            the outcome held), base rate, lift, and the lowest precision in
            any one year.

--------------------------------------------------------------------
THE BASELINE
--------------------------------------------------------------------
Backend/backtest_baseline.json holds, per scenario, the metrics that are
checked: each with its value, whether lower or higher is better, and the
tolerance (in the metric's unit) a worse value may differ by. A metric
that is now missing (None) counts as worse. Better values pass and are
reported, so the baseline can be moved up.

--update-baseline rewrites the values from a fresh run and raises the
baseline's version. docs/models.md must then carry a line
"Backtest baseline version N" (N the new version) with the reason;
--check fails until it does.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from koi.dev import seed as seed_data
from koi.models import evaporation_engine as ev
from koi.models.profile import ProfileHistory, profile_from_row, profile_from_userdata_config
from koi.storage import MemoryStorage
from koi.storage.base import parse_timestamp
from koi.worker import poller

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "Backend"
OUT_DIR = ROOT / "build" / "backtest"
BASELINE_PATH = BACKEND / "backtest_baseline.json"
MODELS_DOC = ROOT / "docs" / "models.md"
NEA_TABLE = ROOT / "NEA_Data_Analysis" / "hot_day_validation.csv"

DEMO_POND = 1
HOURS_AHEAD = (1, 3, 6)
HOUR = timedelta(hours=1)

# (rule column, outcome column, message) of hot_day_validation.csv.
LADDER_RULES = (
    ("clear_day", "DRY", "Clear day: good for maintenance"),
    ("heat_holds", "HARD", "Heat is holding: cut the ration"),
)
_TRUE = {"1", "true", "True", "TRUE"}


def _rmse(errors: list[float]) -> Optional[float]:
    return math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else None


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    return round(value, places) if value is not None else None


# ---------------------------------------------------------------------
# Water temperature
# ---------------------------------------------------------------------
def water_temperature_metrics(water: dict[datetime, float], air: dict[datetime, float],
                              hours_ahead: tuple[int, ...] = HOURS_AHEAD) -> tuple[dict, dict]:
    """(metrics, details): the twin's water temperature prediction at each
    horizon against persistence, over the evaluation interval of the
    calibration fit (module docstring). Metrics are None when the fit is
    pending."""
    fit = ev.fit_water_air_temperature(water, air)
    details: dict[str, Any] = {"fit_status": fit["status"], "fit": {
        "offset_c": fit["value"], "lag_hours": fit["lag_hours"], "sample_count": fit["sample_count"],
        "reason": (fit.get("details") or {}).get("reason")}}
    metrics: dict[str, Optional[float]] = {}
    if fit["status"] != "fitted":
        for h in hours_ahead:
            for key in ("rmse_c", "persistence_rmse_c", "skill"):
                metrics[f"water_temp_{key}_{h}h"] = None
        return metrics, details
    lag, offset = int(fit["lag_hours"]), float(fit["value"])
    start, end = fit["evaluation_from"], fit["evaluation_to"]
    details["evaluation_from"], details["evaluation_to"] = start.isoformat(), end.isoformat()
    issues = sorted(t for t in water if start <= t < end)
    horizons = []
    for h in hours_ahead:
        model: list[float] = []
        persistence: list[float] = []
        for t in issues:
            target = t + h * HOUR
            actual, driver = water.get(target), air.get(target - lag * HOUR)
            if actual is None or driver is None:
                continue
            model.append(driver + offset - actual)
            persistence.append(water[t] - actual)
        model_rmse, persistence_rmse = _rmse(model), _rmse(persistence)
        skill = (1.0 - model_rmse / persistence_rmse
                 if model_rmse is not None and persistence_rmse else None)
        metrics[f"water_temp_rmse_c_{h}h"] = _round(model_rmse)
        metrics[f"water_temp_persistence_rmse_c_{h}h"] = _round(persistence_rmse)
        metrics[f"water_temp_skill_{h}h"] = _round(skill)
        horizons.append({"hours_ahead": h, "issue_times": len(model),
                         "model_rmse_c": _round(model_rmse),
                         "model_mean_abs_error_c": _round(_mean([abs(e) for e in model])),
                         "model_mean_error_c": _round(_mean(model)),
                         "persistence_rmse_c": _round(persistence_rmse),
                         "persistence_mean_abs_error_c": _round(_mean([abs(e) for e in persistence])),
                         "beats_persistence": (model_rmse < persistence_rmse
                                               if model_rmse is not None and persistence_rmse is not None
                                               else None)})
    metrics["water_temp_issue_times"] = float(len(issues))
    details["horizons"] = horizons
    return metrics, details


# ---------------------------------------------------------------------
# Evaporation
# ---------------------------------------------------------------------
def evaporation_metrics(samples: list[ev.TopUpSample], skipped: list[dict],
                        shelter_factor: float = ev.POND_SHELTER_FACTOR) -> tuple[dict, dict]:
    """(metrics, details): each logged top-up's litres against the
    modelled loss since the previous top-up or water change."""
    rows = []
    errors: list[float] = []
    percent: list[float] = []
    for s in samples:
        modelled = ev.modelled_topup_litres(s, shelter_factor)
        error = modelled - s.litres_added
        errors.append(error)
        if s.litres_added > 0:
            percent.append(abs(error) / s.litres_added * 100.0)
        rows.append({"start": s.start.isoformat(), "end": s.end.isoformat(),
                     "logged_litres": round(s.litres_added, 2), "modelled_litres": round(modelled, 2),
                     "error_litres": round(error, 2), "hour_coverage": round(s.coverage, 3)})
    metrics = {
        "evaporation_topups_scored": float(len(samples)),
        "evaporation_mean_error_l": _round(_mean(errors), 2),
        "evaporation_mean_abs_error_l": _round(_mean([abs(e) for e in errors]), 2),
        "evaporation_mean_abs_pct_error": _round(_mean(percent), 2),
    }
    return metrics, {"shelter_factor": shelter_factor, "topups": rows, "skipped_topups": skipped}


# ---------------------------------------------------------------------
# Scenario: demo pond
# ---------------------------------------------------------------------
def weather_history_rows(block: dict) -> tuple[list[dict], list[dict]]:
    """seed["backtest"] as weather_observation and
    weather_forecast_issuance rows (migration 0009 shapes): air
    temperature and wind observed five minutes into each hour, rainfall
    as the hour's total, the 24-hour general forecast's humidity issued
    every few hours and valid for a day."""
    station, first = block["station_id"], parse_timestamp(block["first_hour"])
    common = {"source": "backtest", "series": "station", "station_id": station}
    observations: list[dict] = []
    series = (("air_temperature", "degC", block["air_temperature_c"]),
              ("wind_speed", "knot", block["wind_speed_knots"]))
    for metric, unit, values in series:
        for i, value in enumerate(values):
            at = (first + i * HOUR + timedelta(minutes=5)).isoformat()
            observations.append({**common, "metric": metric, "value": value, "unit": unit,
                                 "semantics": "instantaneous", "observed_from": at, "observed_to": at,
                                 "fetched_at": at})
    for i, value in enumerate(block["rainfall_mm"]):
        start, end = first + i * HOUR, first + (i + 1) * HOUR
        observations.append({**common, "metric": "rainfall", "value": value, "unit": "mm",
                             "semantics": "interval_total", "observed_from": start.isoformat(),
                             "observed_to": end.isoformat(), "fetched_at": end.isoformat()})
    issuances = []
    for row in block["humidity_forecast"]:
        issued = parse_timestamp(row["issued_at"])
        issuances.append({"source": "backtest", "product": "24hr", "slot_id": "GENERAL",
                          "issued_at": issued.isoformat(), "source_updated_at": issued.isoformat(),
                          "valid_from": issued.isoformat(), "valid_to": (issued + timedelta(days=1)).isoformat(),
                          "payload": {"relativeHumidity": {"low": row["low"], "high": row["high"]}},
                          "fetched_at": issued.isoformat(), "available_at": issued.isoformat()})
    return observations, issuances


def _pond_rows(tables: dict[str, list[dict]], table: str, column: str, pond: int) -> list[dict]:
    return [r for r in tables.get(table, []) if str(r.get(column)) == str(pond)]


def demo_pond(seed_path: Path = seed_data.SEED_PATH) -> dict:
    seed = seed_data.load(seed_path)
    tables, block = seed["tables"], seed["backtest"]
    pond = DEMO_POND
    storage = MemoryStorage(seed={"tables": {
        "UserData": _pond_rows(tables, "UserData", "userID", pond),
        "pond_profile": _pond_rows(tables, "pond_profile", "pond_id", pond),
        "pondInterventions": _pond_rows(tables, "pondInterventions", "userID", pond),
    }})
    observations, issuances = weather_history_rows(block)
    storage.add_rows("weather_observation", observations)
    storage.add_rows("weather_forecast_issuance", issuances)

    sensor = _pond_rows(tables, "SensorData", "userID", pond)
    times = [parse_timestamp(r["created_at"]) for r in sensor]
    # Whole hours, as the calibration job's window (it runs on the hour):
    # its day-long reads would otherwise split an hour's rainfall total.
    start, end = poller._hour(min(times)) - HOUR, poller._hour(max(times)) + 2 * HOUR
    stations = storage.fetch_dashboard_sources(pond).get("stations") or {}

    # The calibration job's hourly inputs (poller.fit_pond_calibration).
    def hourly(slot: str, metric: str) -> dict[datetime, float]:
        return poller._hourly_mean([(parse_timestamp(o["observed_from"]), o["value"])
                                    for o in poller._observations(storage, stations.get(slot), metric, start, end)])
    air = hourly("air-temperature", "air_temperature")
    wind = {h: v * poller._KNOTS_TO_MS for h, v in hourly("wind-speed", "wind_speed").items()}
    rain = poller._hourly_rain(poller._observations(storage, stations.get("rainfall"), "rainfall", start, end))
    water = poller._hourly_mean([(parse_timestamp(r["created_at"]), r["data1"]) for r in sensor
                                 if r["sensor_type"] == "temp" and r.get("data1") is not None])

    temperature, temperature_details = water_temperature_metrics(water, air)

    profile_rows = storage.fetch_pond_profiles(pond)
    config = storage.fetch_pond_config(pond)
    if profile_rows:
        profiles = ProfileHistory([profile_from_row(r) for r in profile_rows])
    elif config is not None:
        profiles = ProfileHistory([profile_from_userdata_config(config)])
    else:
        raise LookupError(f"pond {pond} has no profile and no UserData volume and biomass")
    interventions = storage.fetch_interventions(pond, None)
    wanted = {h for r in interventions if r.get("event_type") == "WATER_TOPUP"
              for h in poller._interval_hours(start, parse_timestamp(r["event_timestamp"]))}
    humidity = poller._hourly_humidity(storage, wanted) if wanted else {}
    offset = temperature_details["fit"]["offset_c"]
    samples, skipped = poller._topup_samples(interventions, profiles,
                                             {"air": air, "wind": wind, "rain": rain, "humidity": humidity},
                                             water, offset)
    evaporation, evaporation_details = evaporation_metrics(samples, skipped)

    return {
        "scenario": "demo_pond",
        "source": _relative(seed_path),
        "inputs": {"pond": pond, "first_reading": min(times).isoformat(), "last_reading": max(times).isoformat(),
                   "water_hours": len(water), "air_hours": len(air), "wind_hours": len(wind),
                   "rain_hours": len(rain), "humidity_hours": len(humidity),
                   "interventions": len(interventions), "stations": stations},
        "metrics": {**temperature, **evaporation},
        "details": {"water_temperature": temperature_details, "evaporation": evaporation_details},
    }


# ---------------------------------------------------------------------
# Scenario: NEA action ladder
# ---------------------------------------------------------------------
def ladder_metrics(rows: list[dict]) -> tuple[dict, dict]:
    """(metrics, details): each ladder rule's precision against its
    outcome over the table's days, with base rate, lift and per-year
    precision."""
    metrics: dict[str, Optional[float]] = {"ladder_days": float(len(rows))}
    details: dict[str, Any] = {}
    for rule, outcome, message in LADDER_RULES:
        fired = [r for r in rows if r.get(rule) in _TRUE]
        hits = sum(1 for r in fired if r.get(outcome) in _TRUE)
        base = sum(1 for r in rows if r.get(outcome) in _TRUE) / len(rows) if rows else None
        precision = hits / len(fired) if fired else None
        years: dict[str, list[int]] = {}
        for r in fired:
            years.setdefault(str(r.get("year") or r["date"][:4]), []).append(1 if r.get(outcome) in _TRUE else 0)
        by_year = {y: round(sum(v) / len(v), 4) for y, v in sorted(years.items())}
        metrics[f"ladder_{rule}_precision"] = _round(precision)
        metrics[f"ladder_{rule}_fires"] = float(len(fired))
        metrics[f"ladder_{rule}_lowest_year_precision"] = min(by_year.values()) if by_year else None
        details[rule] = {"message": message, "outcome": outcome, "days": len(rows), "fires": len(fired),
                         "hits": hits, "fire_rate": _round(len(fired) / len(rows) if rows else None),
                         "precision": _round(precision), "base_rate": _round(base),
                         "lift": _round(precision / base if precision is not None and base else None),
                         "precision_by_year": by_year}
    return metrics, details


def nea_ladder(table: Path = NEA_TABLE) -> dict:
    with table.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    metrics, details = ladder_metrics(rows)
    return {
        "scenario": "nea_ladder",
        "source": _relative(table),
        "inputs": {"days": len(rows), "first_day": rows[0]["date"] if rows else None,
                   "last_day": rows[-1]["date"] if rows else None},
        "metrics": metrics,
        "details": {"rules": details,
                    "note": "Rule firings are the ones recorded in the table; the ladder is not in the backend yet."},
    }


SCENARIOS: dict[str, Callable[[], dict]] = {"demo_pond": demo_pond, "nea_ladder": nea_ladder}


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------
@dataclass
class Finding:
    scenario: str
    metric: str
    status: str  # "ok", "better" or "worse"
    baseline: Optional[float]
    value: Optional[float]
    better: str
    tolerance: float

    def line(self) -> str:
        return (f"{self.status.upper():6} {self.scenario}.{self.metric}: {self.value} "
                f"(baseline {self.baseline}, {self.better} is better, tolerance {self.tolerance})")


def compare(scenario: str, metrics: dict, expected: dict) -> list[Finding]:
    """One finding per baseline metric of the scenario: worse when the
    value is missing, or past the baseline by more than the tolerance in
    the wrong direction; better when past it the right way."""
    findings = []
    for name, spec in expected.items():
        base, better, tolerance = spec["value"], spec["better"], float(spec["tolerance"])
        if better not in ("lower", "higher"):
            raise ValueError(f"{scenario}.{name}: 'better' must be 'lower' or 'higher', not {better!r}")
        value = metrics.get(name)
        if value is None or base is None:
            status = "ok" if value is None and base is None else ("worse" if value is None else "better")
        else:
            change = (value - base) if better == "higher" else (base - value)
            status = "worse" if change < -tolerance - 1e-12 else ("better" if change > 1e-12 else "ok")
        findings.append(Finding(scenario, name, status, base, value, better, tolerance))
    return findings


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def documented(version: int, doc: Path = MODELS_DOC) -> bool:
    """Whether docs/models.md has the reason line for this baseline version."""
    text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    return re.search(rf"Backtest baseline version {int(version)}\b", text) is not None


def write_result(result: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result['scenario']}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def check(baseline_path: Path, out_dir: Path, doc: Path,
          scenarios: Optional[dict[str, Callable[[], dict]]] = None) -> tuple[bool, list[str]]:
    """Runs every baseline scenario and compares; (passed, report lines)."""
    scenarios = scenarios or SCENARIOS
    baseline = load_baseline(baseline_path)
    lines: list[str] = []
    ok = True
    version = int(baseline["version"])
    if not documented(version, doc):
        ok = False
        lines.append(f"FAIL docs/models.md has no 'Backtest baseline version {version}' entry giving the "
                     "reason for this baseline")
    findings: list[Finding] = []
    for name, expected in baseline["scenarios"].items():
        if name not in scenarios:
            ok = False
            lines.append(f"FAIL baseline scenario {name!r} does not exist")
            continue
        result = scenarios[name]()
        write_result(result, out_dir)
        findings.extend(compare(name, result["metrics"], expected["metrics"]))
    for f in findings:
        if f.status != "ok":
            lines.append(f.line())
    worse = [f for f in findings if f.status == "worse"]
    better = [f for f in findings if f.status == "better"]
    if worse:
        ok = False
    lines.append(f"{len(findings) - len(worse)} of {len(findings)} metrics within baseline version {version}"
                 + (f", {len(worse)} worse" if worse else "")
                 + (f", {len(better)} better (python -m koi.tools.backtest --update-baseline to record them)"
                    if better else ""))
    return ok, lines


def update_baseline(baseline_path: Path, out_dir: Path,
                    scenarios: Optional[dict[str, Callable[[], dict]]] = None) -> int:
    """Rewrites every baseline value from a fresh run (keeping each
    metric's direction and tolerance) and raises the version."""
    scenarios = scenarios or SCENARIOS
    baseline = load_baseline(baseline_path)
    for name, expected in baseline["scenarios"].items():
        result = scenarios[name]()
        write_result(result, out_dir)
        for metric, spec in expected["metrics"].items():
            spec["value"] = result["metrics"].get(metric)
    baseline["version"] = int(baseline["version"]) + 1
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    return int(baseline["version"])


# ---------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------
def summary(result: dict) -> list[str]:
    return [f"  {k} = {v}" for k, v in sorted(result["metrics"].items())]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m koi.tools.backtest",
                                     description="Replay a recorded fixture through the twin and score it.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scenario", choices=sorted(SCENARIOS), help="run one scenario")
    mode.add_argument("--check", action="store_true",
                      help="run every baseline scenario; exit 1 if a metric is worse than the baseline")
    mode.add_argument("--update-baseline", action="store_true",
                      help="record the current metrics as the baseline and raise its version")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="output folder (default: build/backtest)")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--models-doc", type=Path, default=MODELS_DOC, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.scenario:
        result = SCENARIOS[args.scenario]()
        path = write_result(result, args.out)
        sys.stdout.write("\n".join([f"Backtest {args.scenario}: wrote {path}", *summary(result)]) + "\n")
        return 0
    if args.update_baseline:
        version = update_baseline(args.baseline, args.out)
        sys.stdout.write(f"Wrote baseline version {version} to {args.baseline}. Add a line "
                         f"'Backtest baseline version {version}' with the reason to docs/models.md.\n")
        return 0
    ok, lines = check(args.baseline, args.out, args.models_doc)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
