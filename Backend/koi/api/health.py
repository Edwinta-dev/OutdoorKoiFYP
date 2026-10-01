"""Readiness and the poller metrics the API serves.

The poller usually runs in its own process (python -m koi.worker), so the
API learns how it is doing from storage: the worker_status row the
worker writes after each cycle (koi/worker/poller.py) and the
worker_lease row.

readiness() backs GET /ready. The API is ready when storage answers and
the poller's last successful cycle finished no more than two poll
intervals ago (the lease length). Ponds that failed or were skipped in
the last cycle and the lease holder are reported but do not make the API
unready on their own.

poller_metrics() is added to GET /metrics at scrape time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from koi.metrics import Metrics
from koi.settings import Settings
from koi.storage import Storage, StorageError
from koi.storage.base import parse_timestamp
from koi.worker.poller import LEASE_NAME, lease_seconds


def _age_sec(now: datetime, value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return (now - parse_timestamp(value)).total_seconds()
    except ValueError:
        return None


def _pond_key(pond_id: str) -> Any:
    return int(pond_id) if pond_id.isdigit() else pond_id


def _ponds_with(ponds: dict, result: str) -> list:
    return sorted((_pond_key(p) for p, r in ponds.items() if (r or {}).get("result") == result),
                  key=lambda p: (isinstance(p, str), p))


def readiness(storage: Storage, settings: Settings, now: Optional[datetime] = None) -> tuple[bool, dict]:
    """(ready, report). report["reasons"] says in plain words why not."""
    now = now or datetime.now(timezone.utc)
    max_age = lease_seconds(settings)
    try:
        status = storage.fetch_worker_status(LEASE_NAME) or {}
        lease = storage.fetch_lease(LEASE_NAME)
    except StorageError as exc:
        return False, {"storage": {"reachable": False, "operation": exc.operation},
                       "reasons": ["The pond database could not be reached."]}

    reasons = []
    last_success = status.get("last_success_at")
    age = _age_sec(now, last_success)
    if age is None:
        reasons.append("The poller has not completed a successful cycle yet.")
    elif age > max_age:
        reasons.append(f"The last successful poll finished {age / 60:.0f} minutes ago; "
                       f"the limit is {max_age / 60:.0f} minutes.")
    ponds = status.get("ponds") or {}
    lease_age = _age_sec(now, (lease or {}).get("expires_at"))
    lease_live = lease is not None and lease_age is not None and lease_age <= 0
    report = {
        "storage": {"reachable": True},
        "poller": {
            "last_success_at": last_success,
            "last_success_age_sec": None if age is None else round(age),
            "max_age_sec": max_age,
            "last_cycle_finished_at": status.get("cycle_finished_at"),
            "last_cycle_duration_sec": status.get("cycle_duration_sec"),
            "failed_ponds": _ponds_with(ponds, "failed"),
            "skipped_ponds": _ponds_with(ponds, "skipped"),
            "lease_holder": lease["holder"] if lease_live and lease else None,
            "lease_expires_at": lease["expires_at"] if lease_live and lease else None,
        },
        "reasons": reasons,
    }
    return not reasons, report


def poller_metrics(storage: Storage, now: Optional[datetime] = None) -> str:
    """Prometheus text for the poller, from storage. A storage failure
    gives koi_storage_up 0 and nothing else."""
    now = now or datetime.now(timezone.utc)
    m = Metrics()
    up = m.gauge("koi_storage_up", "1 when the API could read the poller report from storage, else 0.")
    try:
        status = storage.fetch_worker_status(LEASE_NAME) or {}
        lease = storage.fetch_lease(LEASE_NAME)
        snapshots = storage.fetch_snapshot_times()
    except StorageError:
        up.set(0)
        return m.render()
    up.set(1)

    last_success_age = _age_sec(now, status.get("last_success_at"))
    if last_success_age is not None:
        m.gauge("koi_poll_last_success_age_seconds",
                "Seconds since the poller's last successful cycle finished.").set(last_success_age)
    if status.get("cycle_duration_sec") is not None:
        m.gauge("koi_poll_cycle_duration_seconds",
                "Length of the poller's last cycle in seconds.").set(float(status["cycle_duration_sec"]))

    failed = m.gauge("koi_poll_pond_failed", "1 when polling the pond raised in the last cycle, else 0.",
                     ["pond_id"])
    failures = m.counter("koi_poll_pond_failures_total",
                         "Cycles in which polling the pond raised, since the active worker started.", ["pond_id"])
    sensor_age = m.gauge("koi_sensor_reading_age_seconds",
                         "Seconds since the newest sensor reading the poller used for the pond was recorded.",
                         ["pond_id"])
    for pond_id, result in sorted((status.get("ponds") or {}).items()):
        result = result or {}
        failed.set(1 if result.get("result") == "failed" else 0, pond_id=pond_id)
        failures.inc(float(result.get("failures_total") or 0), pond_id=pond_id)
        age = _age_sec(now, result.get("sensor_recorded_at"))
        if age is not None:
            sensor_age.set(age, pond_id=pond_id)

    snapshot_age = m.gauge("koi_snapshot_age_seconds", "Seconds since the pond's engine snapshot was last saved.",
                           ["pond_id"])
    for pond_id, updated_at in sorted(snapshots.items()):
        age = _age_sec(now, updated_at)
        if age is not None:
            snapshot_age.set(age, pond_id=pond_id)

    lease_age = _age_sec(now, (lease or {}).get("expires_at"))
    held = m.gauge("koi_poller_lease_held", "1 when a worker holds an unexpired poller lease, else 0.")
    held.set(1 if lease_age is not None and lease_age <= 0 else 0)
    return m.render()


__all__ = ["poller_metrics", "readiness"]
