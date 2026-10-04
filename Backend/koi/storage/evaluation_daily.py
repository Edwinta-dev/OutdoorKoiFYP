"""Daily summaries of the evaluation rows (evaluation_daily, migration 0017).

summarize_evaluation_days in the database folds every detailed evaluation
row older than the retention period into one evaluation_daily row per
pond, domain and local day, and deletes the rows it folded in. This
module is the same computation over plain rows, for MemoryStorage; the
migration's header is the reference for the rules, and both stores are
tested against the same expectations.

evaluation_daily holds model output (estimates and Green / Amber / Red
outcomes). Observed sensor values are summarised separately, in
daily_sensor_averages.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional

from koi.models.local_time import local_date
from koi.storage.base import parse_timestamp

# Detailed table and headline columns of each domain.
DOMAIN_TABLES = {
    "chemistry": "pond_chemistry_evaluations",
    "evaporation": "pond_evaporation_evaluations",
    "algae": "pond_algae_evaluations",
}
HEADLINE_METRICS = {
    "chemistry": ("tan_ppm", "no2_ppm", "no3_ppm", "ph_reactivity"),
    "evaporation": ("loss_litres", "loss_pct", "evaporation_mm_per_day", "loss_litres_per_day", "water_temp_c",
                    "days_to_topup"),
    "algae": ("green_ratio", "growth_rate_per_day", "days_to_scrub"),
}
EVALUATION_DAILY_COLUMNS = ("pond_id", "domain", "local_date", "time_zone", "row_count", "first_evaluated_at",
                            "last_evaluated_at", "status_counts", "metrics", "model_versions", "summarized_at",
                            "updated_at")


def _iso(t: datetime) -> str:
    return t.isoformat()


def _key(row: dict) -> tuple[datetime, int]:
    return parse_timestamp(row["evaluated_at"]), int(row["id"])


def summarize_group(domain: str, rows: Iterable[dict]) -> dict:
    """The summary fields of one pond's rows of one domain on one local
    day: row_count, first/last_evaluated_at, status_counts, metrics and
    model_versions, as the migration describes. A null value is left out
    of every statistic; a metric with no value all day has n 0 and null
    first, last, min and max."""
    ordered = sorted(rows, key=_key)
    if not ordered:
        raise ValueError("summarize_group needs at least one row")
    times = [parse_timestamp(r["evaluated_at"]) for r in ordered]

    status_counts: dict[str, int] = {}
    for r in ordered:
        status = r.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    metrics: dict[str, dict] = {}
    for metric in HEADLINE_METRICS[domain]:
        present = [(t, r[metric]) for t, r in zip(times, ordered, strict=True) if r.get(metric) is not None]
        values = [v for _, v in present]
        metrics[metric] = {
            "n": len(present),
            "first": present[0][1] if present else None,
            "first_at": _iso(present[0][0]) if present else None,
            "last": present[-1][1] if present else None,
            "last_at": _iso(present[-1][0]) if present else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        }

    versions: dict[Optional[str], dict] = {}
    for t, r in zip(times, ordered, strict=True):
        version = r.get("model_version")
        entry = versions.setdefault(version, {"model_version": version, "rows": 0, "first_evaluated_at": t,
                                              "last_evaluated_at": t})
        entry["rows"] += 1
        entry["last_evaluated_at"] = t

    return {
        "row_count": len(ordered),
        "first_evaluated_at": _iso(times[0]),
        "last_evaluated_at": _iso(times[-1]),
        "status_counts": status_counts,
        "metrics": metrics,
        "model_versions": _versions_list(versions.values()),
    }


def _versions_list(entries: Iterable[dict]) -> list[dict]:
    def order(e: dict) -> tuple[datetime, bool, str]:
        return (parse_timestamp(e["first_evaluated_at"]), e["model_version"] is not None, e["model_version"] or "")
    return [{"model_version": e["model_version"], "rows": e["rows"],
             "first_evaluated_at": _iso(parse_timestamp(e["first_evaluated_at"])),
             "last_evaluated_at": _iso(parse_timestamp(e["last_evaluated_at"]))}
            for e in sorted(entries, key=order)]


def _earlier(a: Optional[str], b: Optional[str]) -> bool:
    """True when time b is set and before a (or a is unset)."""
    return b is not None and (a is None or parse_timestamp(b) < parse_timestamp(a))


def _later(a: Optional[str], b: Optional[str]) -> bool:
    return b is not None and (a is None or parse_timestamp(b) > parse_timestamp(a))


def _bound(fn: Any, a: Any, b: Any) -> Any:
    present = [v for v in (a, b) if v is not None]
    return fn(present) if present else None


def merge_summary(old: dict, new: dict) -> dict:
    """old with the rows summarised in new folded in: what the migration's
    on-conflict update does when a detailed row arrives for a day that is
    already summarised. Counts add, first/last go by time (old wins a
    tie), min/max widen, model versions combine."""
    merged = dict(old)
    merged["row_count"] = old["row_count"] + new["row_count"]
    merged["first_evaluated_at"] = _iso(min(parse_timestamp(old["first_evaluated_at"]),
                                            parse_timestamp(new["first_evaluated_at"])))
    merged["last_evaluated_at"] = _iso(max(parse_timestamp(old["last_evaluated_at"]),
                                           parse_timestamp(new["last_evaluated_at"])))
    counts = dict(old["status_counts"])
    for status, n in new["status_counts"].items():
        counts[status] = counts.get(status, 0) + n
    merged["status_counts"] = counts

    metrics = {}
    for metric in sorted(set(old["metrics"]) | set(new["metrics"])):
        a = old["metrics"].get(metric) or {}
        b = new["metrics"].get(metric) or {}
        first_b, last_b = _earlier(a.get("first_at"), b.get("first_at")), _later(a.get("last_at"), b.get("last_at"))
        metrics[metric] = {
            "n": (a.get("n") or 0) + (b.get("n") or 0),
            "first": b.get("first") if first_b else a.get("first"),
            "first_at": b.get("first_at") if first_b else a.get("first_at"),
            "last": b.get("last") if last_b else a.get("last"),
            "last_at": b.get("last_at") if last_b else a.get("last_at"),
            "min": _bound(min, a.get("min"), b.get("min")),
            "max": _bound(max, a.get("max"), b.get("max")),
        }
    merged["metrics"] = metrics

    versions: dict[Optional[str], dict] = {}
    for e in [*old["model_versions"], *new["model_versions"]]:
        current = versions.get(e["model_version"])
        if current is None:
            versions[e["model_version"]] = dict(e)
            continue
        current["rows"] += e["rows"]
        if parse_timestamp(e["first_evaluated_at"]) < parse_timestamp(current["first_evaluated_at"]):
            current["first_evaluated_at"] = e["first_evaluated_at"]
        if parse_timestamp(e["last_evaluated_at"]) > parse_timestamp(current["last_evaluated_at"]):
            current["last_evaluated_at"] = e["last_evaluated_at"]
    merged["model_versions"] = _versions_list(versions.values())
    return merged


def select_batch(tables: dict[str, list[dict]], before: date, time_zone: str,
                 max_days: int) -> dict[tuple[int, str, date], list[dict]]:
    """The detailed rows one summarize_evaluation_days call folds in,
    grouped by (pond, domain, local date): rows on a local date before
    `before`, not on their pond and domain's newest local date, and on one
    of the oldest max_days such dates. tables maps each domain to its
    detailed rows."""
    candidates: list[tuple[str, date, dict]] = []
    for domain, rows in tables.items():
        newest: dict[str, date] = {}
        for r in rows:
            d = local_date(parse_timestamp(r["evaluated_at"]), time_zone)
            pond = str(r["userid"])
            if pond not in newest or d > newest[pond]:
                newest[pond] = d
        for r in rows:
            d = local_date(parse_timestamp(r["evaluated_at"]), time_zone)
            if d < before and d < newest[str(r["userid"])]:
                candidates.append((domain, d, r))
    days = sorted({d for _, d, _ in candidates})[:max_days]
    groups: dict[tuple[int, str, date], list[dict]] = {}
    for domain, d, r in candidates:
        if d in days:
            groups.setdefault((int(r["userid"]), domain, d), []).append(r)
    return groups
