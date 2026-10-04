"""
koi.worker.retention

The worker's daily evaluation retention job (issue #22, migration 0017).

Each poll writes one row per pond to each of the three evaluation tables,
about 290 a day per pond. Once a day the worker folds every local day
older than Settings.evaluation_retention_days (default 30) into one
evaluation_daily row per pond, domain and day, and deletes the detailed
rows it folded in (Storage.summarize_evaluation_days). The day is the
pond's local calendar day (koi/models/local_time.py).

Safety:
  * The summaries and the deletes are one transaction; the database
    checks that the rows deleted equal the rows summarised before it
    commits, so a failure leaves everything as it was and the next run
    does the same work again.
  * A summarised day has no detailed rows left, so running again changes
    nothing (idempotent). A row that arrives later for a summarised day
    is merged into its summary on the next run.
  * Each pond and domain keeps its newest local day as detailed rows, so
    the latest evaluation the API serves is never removed.
  * Only evaluation rows (model output) are removed. The inputs a rebuild
    or replay reads (sensor rows and their ingest ledger, interventions,
    camera frames, ratings, weather history, snapshots) are not touched.
  * Days are taken a batch at a time (MAX_DAYS_PER_CALL), oldest first,
    so the first run over months of history is a series of small
    transactions rather than one large one.

The job runs under its own lease ("evaluation_retention"), so only one
worker runs it at a time; the database function also serialises calls.
evaluation_daily holds estimates and status outcomes; observed sensor
values are summarised separately in daily_sensor_averages.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from koi.logs import log_event
from koi.models.local_time import DEFAULT_POND_TIME_ZONE, local_date
from koi.storage import Storage, StorageError, fail_soft

LEASE_NAME = "evaluation_retention"
LEASE_SECONDS = 3600
# Local dates folded per database call, and the most calls in one run.
MAX_DAYS_PER_CALL = 7
MAX_CALLS = 200
# When the scheduled run starts, local time.
RUN_HOUR, RUN_MINUTE = 3, 30

log = logging.getLogger(__name__)


def retention_cutoff(now: datetime, retention_days: int, time_zone: str = DEFAULT_POND_TIME_ZONE) -> date:
    """The first local date kept as detailed rows: days before it are
    summarised. With 30 days on 4 October, 3 September and earlier go."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    return local_date(now, time_zone) - timedelta(days=retention_days)


def run_retention(storage: Storage, retention_days: int, now: Optional[datetime] = None,
                  time_zone: str = DEFAULT_POND_TIME_ZONE, max_days_per_call: int = MAX_DAYS_PER_CALL,
                  max_calls: int = MAX_CALLS) -> dict:
    """Summarises and deletes every evaluation day before the cutoff, a
    batch of local dates per call, until nothing is left (or max_calls).
    Returns {"before", "days", "rows", "summaries", "calls", "complete"}.
    A StorageError propagates; what earlier calls committed stays."""
    now = now or datetime.now(timezone.utc)
    before = retention_cutoff(now, retention_days, time_zone)
    report: dict = {"before": before.isoformat(), "days": [], "rows": 0, "summaries": 0, "calls": 0,
                    "complete": False}
    while report["calls"] < max_calls:
        result = storage.summarize_evaluation_days(before, time_zone, max_days_per_call)
        report["calls"] += 1
        if not result.get("days"):
            report["complete"] = True
            break
        report["days"].extend(result["days"])
        report["rows"] += int(result.get("rows") or 0)
        report["summaries"] += int(result.get("summaries") or 0)
    return report


class RetentionJob:
    """The scheduled run: takes the retention lease, summarises, logs."""

    def __init__(self, storage: Storage, retention_days: int, holder: str,
                 time_zone: str = DEFAULT_POND_TIME_ZONE):
        self.storage = storage
        self.retention_days = retention_days
        self.holder = holder
        self.time_zone = time_zone

    def run(self, now: Optional[datetime] = None) -> Optional[dict]:
        """The run's report, or None when another worker holds the lease or
        the run failed (logged; the next day's run starts again)."""
        try:
            held = self.storage.take_lease(LEASE_NAME, self.holder, LEASE_SECONDS)
        except StorageError as exc:
            log_event(log, "evaluation_retention_lease_unavailable", level=logging.WARNING, error=str(exc))
            return None
        if not held:
            log_event(log, "evaluation_retention_standby", holder=self.holder)
            return None
        try:
            report = run_retention(self.storage, self.retention_days, now=now, time_zone=self.time_zone)
        except StorageError as exc:
            log_event(log, "evaluation_retention_failed", level=logging.ERROR, error=str(exc))
            return None
        finally:
            fail_soft(lambda: self.storage.release_lease(LEASE_NAME, self.holder), None)
        log_event(log, "evaluation_retention_finished", before=report["before"], days=len(report["days"]),
                  rows=report["rows"], summaries=report["summaries"], calls=report["calls"],
                  complete=report["complete"])
        return report
