"""Three deterministic sensitivity scenarios, preserving the existing central run.

Bands are pointwise envelopes, not probabilities. A missing crossing in any
scenario makes the upper crossing day unknown within the requested horizon.
"""
import copy
import json
from dataclasses import replace
from pathlib import Path

RANGES = json.loads(Path(__file__).with_name("uncertainty.json").read_text(encoding="utf-8"))


def envelope(runs: list[dict]) -> dict:
    central = copy.deepcopy(runs[1])
    if "error" in central:
        return central
    low, high = [], []
    for days in zip(*(r["trajectory"] for r in runs), strict=True):
        low_day = {"days_from_now": days[1]["days_from_now"]}
        high_day = dict(low_day)
        for key, value in days[1].items():
            if key != "days_from_now" and isinstance(value, (int, float)) and not isinstance(value, bool):
                low_day[key] = min(d[key] for d in days)
                high_day[key] = max(d[key] for d in days)
        low.append(low_day)
        high.append(high_day)
    crossings = {}
    for key in central:
        if key.endswith("days_from_now"):
            values = [r[key] for r in runs]
            crossings[key] = crossing_range(values)
    if "scrub_benefit" in central:
        crossings["scrub_benefit_days_bought"] = crossing_range([r["scrub_benefit"]["days_bought"] for r in runs])
    central["uncertainty"] = {
        "low": low, "high": high, "first_crossing_days": crossings,
        "method": "three_scenario_sensitivity",
    }
    return central


def crossing_range(values: list) -> dict:
    crossed = [v for v in values if v is not None]
    return {"low": min(crossed) if crossed else None,
            "high": max(crossed) if len(crossed) == len(values) else None,
            "not_crossed_runs": len(values) - len(crossed)}


def project(engine, domain: str, *, assumed_depth: bool = False, **kwargs) -> dict:
    """Run each scenario once on private state; never alter live constants."""
    return envelope(scenarios(engine, domain, assumed_depth=assumed_depth, **kwargs))


def scenarios(engine, domain: str, *, assumed_depth: bool = False, scenario_environments=None, **kwargs) -> list:
    """Keep paired chemistry inputs available to the coupled algae scenarios."""
    runs = []
    for index, scenario in enumerate(("low", "central", "high")):
        local = copy.deepcopy(engine)
        options = dict(kwargs)
        if scenario_environments is not None:
            options["daily_environment"] = scenario_environments[index]
        if domain == "chemistry":
            for name in ("tan_to_no2_rate", "no2_to_no3_rate"):
                options[name] = RANGES[name][scenario]
        elif domain == "evaporation":
            options["shelter_factor"] = RANGES["shelter_factor"][scenario]
            if assumed_depth:
                local.config = replace(local.config, pond_depth_m=RANGES["depth_m"][scenario])
        elif domain == "algae":
            if local._rate_source == "literature_fallback":
                local._intrinsic_rate = RANGES["fallback_intrinsic_rate"][scenario]
        else:
            raise ValueError(f"Unknown uncertainty domain: {domain}")
        runs.append(local.project_forward(**options))
    return runs
