"""Lead-time action rules over retained, as-of weather series."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from koi.models.local_time import local_date

PARAMS = json.loads(Path(__file__).with_name("ladder_params.json").read_text(encoding="utf-8"))


def _action(rule: str, lead: str, text: str, evidence: str) -> dict:
    return {"rule": rule, "lead_time": lead, "action": text, "evidence": evidence}


def react(rain: Optional[dict], *, decision_time: datetime) -> dict:
    """Yesterday's complete Singapore rain window; unknown coverage is not dry."""
    day = local_date(decision_time) - timedelta(days=1)
    if not rain or rain.get("status") != "complete" or rain.get("total_mm") is None:
        return {"rule": "REACT", "status": "insufficient_data",
                "reason": "Yesterday's assigned-station rain window is incomplete.", "actions": []}
    fired = float(rain["total_mm"]) >= PARAMS["react_rain_mm"]
    return {"rule": "REACT", "status": "assessed", "actions": [_action("REACT", "next morning",
            "Check the pond after yesterday's heavy rain; verify water level and fish behaviour.",
            f"{rain['total_mm']:.1f} mm at station {rain.get('station_id')} on {day.isoformat()}.")] if fired else []}


def preempt(*, observed_tmax_c: Optional[float], regime: Optional[str], outlook_high_c: Optional[float],
            outlook_hot_threshold_c: Optional[float], evidence: str = "") -> dict:
    """Fire when today's measured heat and an available hot outlook agree."""
    if (observed_tmax_c is None or regime not in PARAMS["temperature_threshold_c"] or outlook_high_c is None
            or outlook_hot_threshold_c is None):
        return {"rule": "PREEMPT", "status": "insufficient_data",
                "reason": "A measured-temperature window or issued outlook is missing.", "actions": []}
    hot = observed_tmax_c >= PARAMS["temperature_threshold_c"][regime]
    forecast_hot = outlook_high_c >= outlook_hot_threshold_c
    action = _action(
        "PREEMPT", "ahead of the hot stretch",
        "Reduce the feed ration and raise aeration before the hot stretch.",
        evidence or f"Measured Tmax {observed_tmax_c:.1f} °C and outlook high {outlook_high_c:.1f} °C.",
    )
    return {"rule": "PREEMPT", "status": "assessed", "actions": [action] if hot and forecast_hot else []}


def window(*, pooled_rank: Optional[float], dry_band_max: Optional[float], evidence: str = "") -> dict:
    """Use a calibrated pooled forecast rank; absence is not a no-action verdict."""
    if pooled_rank is None or dry_band_max is None:
        return {"rule": "WINDOW", "status": "insufficient_data",
                "reason": "The issued pooled 24-hour forecast band is unavailable.", "actions": []}
    fired = pooled_rank <= dry_band_max
    return {"rule": "WINDOW", "status": "assessed", "actions": [_action("WINDOW", "today",
            "Schedule a water change or scrub during this dry weather window.", evidence or
            f"Pooled forecast rank {pooled_rank:.3f} is in the calibrated driest band.")] if fired else []}


def nowcast(*, rain_call: Optional[str], evidence: str = "") -> dict:
    if rain_call is None:
        return {"rule": "NOWCAST", "status": "insufficient_data",
                "reason": "No issued 2-hour nowcast covers the decision time.", "actions": []}
    is_rain = any(word in rain_call.casefold() for word in ("rain", "shower", "thunder"))
    action = _action("NOWCAST", "within 2 hours",
                     "Rain is forecast soon; secure exposed feed and pause outdoor pond work.", evidence or rain_call)
    return {"rule": "NOWCAST", "status": "assessed", "actions": [action] if is_rain else []}


def actions(*, decision_time: datetime, rain: Optional[dict] = None, observed_tmax_c: Optional[float] = None,
            temperature_regime: Optional[str] = None, outlook_high_c: Optional[float] = None,
            outlook_hot_threshold_c: Optional[float] = None,
            pooled_rank: Optional[float] = None, dry_band_max: Optional[float] = None,
            nowcast_text: Optional[str] = None) -> dict:
    results = [react(rain, decision_time=decision_time),
               preempt(observed_tmax_c=observed_tmax_c, regime=temperature_regime,
                       outlook_high_c=outlook_high_c, outlook_hot_threshold_c=outlook_hot_threshold_c),
               window(pooled_rank=pooled_rank, dry_band_max=dry_band_max),
               nowcast(rain_call=nowcast_text)]
    return {"status": "assessed" if all(r["status"] == "assessed" for r in results) else "insufficient_data",
            "rules": results, "actions": [a for r in results for a in r["actions"]]}


if __name__ == "__main__":
    # Regeneration is deliberately explicit: validation CSV schemas do not
    # contain the notebook's pooled 24-hour forecast-period table.
    raise SystemExit("Regenerate thresholds by running Backend/tools/regenerate_ladder_params.py")
