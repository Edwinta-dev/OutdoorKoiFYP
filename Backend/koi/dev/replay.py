"""Fast-forward: runs the worker over the seed's recorded history, so the
stack answers its first request with assessments already made.

For each pond, at each time its node reported (oldest first), the
poller's own pond step (koi.worker.poller._poll_user) runs at that time.
The step reads the sensor rows, bundled payload, camera frames, feeds and
interventions through ReplayStorage, which answers them from the seed as
they stood at that time, so the engines never see a reading, a frame or
an event from their future. The interventions logged up to then are
applied by the step's own reconciliation (issue #19), as the worker
applies events whose post never reached the twin.

ReplayStorage keeps the twin's snapshot (with its event ledger), the sensor ingest ledger and
the evaluation rows to itself during the run and writes them to the real
storage once at the end: the snapshot (against the version found at the
start) with every sensor row ingested, and the last evaluation of each
kind. A replay of 14 days of hourly readings then
costs a handful of writes, which keeps the local database profile fast.
Everything else (the pond list, profiles, rating history) is read from
the real storage.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from koi.dev.seed import SeedHistory
from koi.logs import log_event
from koi.models.event_ledger import derived_event_id
from koi.registry import EngineRegistry
from koi.settings import Settings
from koi.storage import Storage
from koi.storage.base import parse_timestamp
from koi.worker import poller

log = logging.getLogger("koi.dev")

# Flushed in this order, one row per kind and pond.
_EVALUATIONS = ("push_evaluation", "push_evaporation_evaluation", "push_algae_evaluation")


class ReplayStorage:
    """The storage the worker sees during the fast-forward (see the module
    docstring). Methods not defined here go to the real storage."""

    def __init__(self, storage: Storage, history: SeedHistory):
        self._storage = storage
        self._history = history
        self.at: Optional[datetime] = None
        self._snapshots: dict[int, tuple[dict, int]] = {}
        self._base_versions: dict[int, int] = {}
        self._evaluations: dict[tuple[str, int], dict] = {}
        # Sensor rows ingested during the run, and each pond's watermark.
        self._ledger: dict[int, list[dict]] = {}
        self._watermarks: dict[int, str] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._storage, name)

    def _now(self) -> datetime:
        if self.at is None:
            raise RuntimeError("ReplayStorage.at is not set")
        return self.at

    # --- history as of `at` -------------------------------------------
    def fetch_dashboard_payload(self, user_id: int) -> Optional[dict]:
        return self._history.payload(user_id, self._now())

    def fetch_image_history(self, user_id: int | str, limit: int = 200) -> list[dict]:
        return self._history.images(int(user_id), self._now(), limit)

    def fetch_recent_feeding_events(self, user_id: int, limit: int = 30) -> list[dict]:
        return self._history.feeds(user_id, self._now(), limit)

    def fetch_pending_sensor_rows(self, user_id: int, sensor_types: tuple[str, ...], start: Optional[datetime],
                                  until: Optional[datetime], overlap_seconds: int, limit: int) -> dict:
        """sensor_ingest_pending over the seed, with this run's ledger."""
        watermark = self._watermarks.get(user_id)
        scan_from = parse_timestamp(watermark) - timedelta(seconds=overlap_seconds) if watermark else start
        ingested = {e["sensor_row_id"] for e in self._ledger.get(user_id, [])}
        until = until or self._now()
        rows = [r for r in self._history.sensor_rows(user_id)
                if r.get("sensor_type") in sensor_types and int(r["id"]) not in ingested
                and parse_timestamp(r["created_at"]) <= until
                and (scan_from is None or parse_timestamp(r["created_at"]) >= scan_from)]
        return {"cursor": watermark, "scan_from": scan_from.isoformat() if scan_from else None,
                "rows": [{"id": r["id"], "sensor_type": r["sensor_type"], "value": r.get("data1"),
                          "created_at": r["created_at"]} for r in rows[:limit]]}

    def fetch_ingested_sensor_rows(self, user_id: int, sensor_types: tuple[str, ...], after: Optional[datetime],
                                   until: datetime) -> list[dict]:
        """sensor_ingest_history over the seed, with this run's ledger."""
        times = {int(e["sensor_row_id"]): parse_timestamp(e["effective_sample_time"])
                 for e in self._ledger.get(user_id, [])}
        rows = [r for r in self._history.sensor_rows(user_id)
                if int(r["id"]) in times and r.get("sensor_type") in sensor_types
                and (after is None or times[int(r["id"])] > after) and times[int(r["id"])] <= until]
        rows.sort(key=lambda r: (times[int(r["id"])], int(r["id"])))
        return [{"id": r["id"], "sensor_type": r["sensor_type"], "value": r.get("data1"),
                 "created_at": r["created_at"]} for r in rows]

    def fetch_interventions(self, user_id: int, since: Optional[datetime]) -> list[dict]:
        """pond_interventions_since over the seed: the rows logged by now,
        each with its event_id (the derived one when the seed has none)."""
        return [{**r, "event_id": r.get("event_id") or derived_event_id(r["id"])}
                for r in self._history.interventions(user_id, self._now(), since)]

    def backfill_intervention_event_ids(self, user_id: int) -> int:
        return 0  # fetch_interventions already gives every seed row its event_id

    # --- snapshot, kept here until flush -------------------------------
    def _version(self, user_id: int) -> int:
        if user_id not in self._base_versions:
            self._base_versions[user_id] = self._storage.fetch_snapshot_version(user_id)
        return self._snapshots[user_id][1] if user_id in self._snapshots else self._base_versions[user_id]

    def load_engine_state(self, user_id: int) -> Optional[tuple[dict, int]]:
        if user_id in self._snapshots:
            snapshot, version = self._snapshots[user_id]
            return json.loads(json.dumps(snapshot)), version
        state = self._storage.load_engine_state(user_id)
        self._base_versions[user_id] = state[1] if state is not None else 0
        return state

    def load_engine_snapshot(self, user_id: int) -> Optional[dict]:
        state = self.load_engine_state(user_id)
        return state[0] if state is not None else None

    def fetch_snapshot_version(self, user_id: int) -> int:
        return self._version(user_id)

    def save_engine_snapshot(self, user_id: int, snapshot: dict, base_version: Optional[int] = None,
                             ingest: Optional[dict] = None) -> int:
        version = self._version(user_id) + 1
        self._snapshots[user_id] = (json.loads(json.dumps(snapshot)), version)
        if ingest:
            self._ledger.setdefault(user_id, []).extend(json.loads(json.dumps(ingest.get("rows") or [])))
            mark = ingest.get("watermark")
            old = self._watermarks.get(user_id)
            if mark and (old is None or parse_timestamp(mark) > parse_timestamp(old)):
                self._watermarks[user_id] = mark
        return version

    # --- evaluations, last of each kind kept until flush ---------------
    def push_evaluation(self, user_id: int, assessment: dict) -> None:
        self._evaluations[("push_evaluation", user_id)] = assessment

    def push_evaporation_evaluation(self, user_id: int, assessment: dict) -> None:
        self._evaluations[("push_evaporation_evaluation", user_id)] = assessment

    def push_algae_evaluation(self, user_id: int, assessment: dict) -> None:
        self._evaluations[("push_algae_evaluation", user_id)] = assessment

    def flush(self) -> None:
        """Writes the final snapshots and evaluations to the real storage."""
        for user_id, (snapshot, _) in sorted(self._snapshots.items()):
            base = self._base_versions.get(user_id, 0)
            if self._ledger.get(user_id):
                ingest = {"rows": self._ledger[user_id], "watermark": self._watermarks.get(user_id)}
                self._storage.save_engine_snapshot(user_id, snapshot, base_version=base, ingest=ingest)
            else:
                self._storage.save_engine_snapshot(user_id, snapshot, base_version=base)
        for name in _EVALUATIONS:
            for (kind, user_id), assessment in sorted(self._evaluations.items(), key=lambda item: item[0][1]):
                if kind == name:
                    getattr(self._storage, name)(user_id, assessment)


@dataclass
class PondReplay:
    pond_id: int
    polls_ok: int = 0
    polls_skipped: int = 0
    events_applied: int = 0
    first: Optional[str] = None
    last: Optional[str] = None
    skip_reasons: list[str] = field(default_factory=list)


def fast_forward(storage: Storage, tables: dict[str, list[dict]], settings: Settings,
                 ponds: Optional[list[int]] = None) -> list[PondReplay]:
    """Replays the seed's history (already shifted, koi.dev.seed.shift)
    for every pond with a configuration, or only those in ponds, and
    writes the result to storage. Returns what happened per pond."""
    history = SeedHistory(tables)
    replay = ReplayStorage(storage, history)
    registry = EngineRegistry(replay, settings.hypoxia_thresholds, settings.sensor_ingest)  # type: ignore[arg-type]
    results = []
    for config in storage.fetch_active_pond_configs():
        pond = int(config["user_id"])
        if ponds is not None and pond not in ponds:
            continue
        result = PondReplay(pond)
        applied = 0
        for at in history.poll_times(pond):
            replay.at = at
            try:
                polled = poller._poll_user(registry, pond, config, now=at)
                applied += (polled.events or {}).get("applied", 0)
                result.polls_ok += 1
            except poller.PondSkipped as exc:
                result.polls_skipped += 1
                if exc.reason not in result.skip_reasons:
                    result.skip_reasons.append(exc.reason)
            result.first = result.first or at.isoformat()
            result.last = at.isoformat()
        result.events_applied = applied
        log_event(log, "dev_replay_finished", pond_id=pond, polls_ok=result.polls_ok,
                  polls_skipped=result.polls_skipped, events_applied=applied, first=result.first, last=result.last)
        results.append(result)
    replay.flush()
    return results
