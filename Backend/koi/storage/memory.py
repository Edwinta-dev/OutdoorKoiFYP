"""MemoryStorage: the Storage interface over in-process dicts.

Used by the tests and for running the services without a database
(KOI_STORAGE=memory). Rows are kept per table under the real Supabase
table and column names, and every operation reads and writes the same
columns SupabaseStorage does, so a test against this store sees what
production would store: an assessment field with no column is dropped
here too.

Deterministic: ids count up per table, "latest" and "newest first" order
by the timestamp column and then by insertion order, and the only clock
is the injectable `clock` used to stamp evaluated_at, rated_at,
created_at and updated_at and to expire worker leases. Everything stored or returned is a JSON
round-trip copy, the same as a jsonb column, so a value that cannot be
serialised fails here rather than in production.

Safe to share between threads, so tests can run an API registry and
several workers against one store: the versioned snapshot save and the
lease take are compare-and-set under one lock, as the save_pond_snapshot
and take_worker_lease functions are in the database.

Seed it from a dict or a JSON file of the form
    {"tables": {"UserData": [...], "imageTable": [...], ...},
     "dashboard_payloads": {"455": {...}}}
Table names are the Supabase ones; see TABLE_COLUMNS.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from koi.storage.base import (
    StaleSnapshotError,
    StorageError,
    daily_stats,
    parse_timestamp,
    pond_config_from_userdata_row,
)

# Columns of each table this store serves, from supabase/migrations/.
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "pond_chemistry_state": ("user_id", "snapshot", "updated_at", "snapshot_version"),
    "worker_lease": ("name", "holder", "expires_at"),
    "worker_status": ("name", "holder", "cycle_started_at", "cycle_finished_at", "cycle_duration_sec",
                      "last_success_at", "ponds"),
    "pond_chemistry_evaluations": (
        "id", "userid", "evaluated_at", "status", "category", "tan_ppm", "no2_ppm", "no3_ppm",
        "ph_reactivity", "reactivity_trend", "tds_trend", "sensor_warnings", "advisory",
        "add_hardener_now"),
    "pond_evaporation_evaluations": (
        "id", "userid", "evaluated_at", "status", "category", "loss_litres", "loss_pct",
        "evaporation_mm_per_day", "loss_litres_per_day", "water_temp_c", "feed_cap_grams",
        "feed_note", "days_to_topup", "advisory", "topup_now"),
    "pond_algae_evaluations": (
        "id", "userid", "evaluated_at", "status", "category", "green_ratio", "watch_threshold",
        "action_threshold", "threshold_mode", "growth_rate_per_day", "intrinsic_rate_per_day",
        "rate_source", "confidence", "sample_count", "days_to_scrub", "advisory", "scrub_now"),
    "algae_severity_ratings": (
        "id", "userid", "image_id", "image_url", "severity", "is_obstructed",
        "green_ratio_at_rating", "image_captured_at", "rated_at", "notes"),
    "imageTable": ("id", "created_at", "user_ID", "green_ratio", "current_state", "imageURL"),
    "UserData": ("userID", "created_at", "volume", "biomass", "latitude", "longitude",
                 "manualpostallocation", "ClosestStations", "auth_uid"),
    "pondInterventions": (
        "id", "created_at", "userID", "event_type", "event_timestamp", "volume_percentage",
        "volume_litres", "food_grams", "protein_percentage", "algae_method"),
    "daily_sensor_averages": ("id", "userid", "sensor_type", "avg_value", "min_value", "max_value",
                              "record_date"),
    # Supabase Auth's session table, read by auth_session_active (0007).
    "auth.sessions": ("id", "user_id", "not_after"),
}

# The user column of each table (three spellings coexist in the schema).
USER_COLUMN = {
    "pond_chemistry_state": "user_id",
    "pond_chemistry_evaluations": "userid",
    "pond_evaporation_evaluations": "userid",
    "pond_algae_evaluations": "userid",
    "algae_severity_ratings": "userid",
    "imageTable": "user_ID",
    "UserData": "userID",
    "pondInterventions": "userID",
    "daily_sensor_averages": "userid",
}

IMAGE_COLUMNS = ("id", "created_at", "green_ratio", "current_state", "imageURL")
DAILY_COLUMNS = ("avg_value", "min_value", "max_value", "record_date")
FEEDING_COLUMNS = ("food_grams", "protein_percentage", "event_timestamp")

# Table timestamps filled in on insert when the row does not carry one.
_STAMPED = {
    "pond_chemistry_evaluations": "evaluated_at",
    "pond_evaporation_evaluations": "evaluated_at",
    "pond_algae_evaluations": "evaluated_at",
    "algae_severity_ratings": "rated_at",
    "imageTable": "created_at",
    "pondInterventions": "created_at",
    "UserData": "created_at",
}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _same_user(row: dict, column: str, user_id: object) -> bool:
    # Postgres compares bigint to the text "15" by value; so does this.
    return str(row.get(column)) == str(user_id)


class MemoryStorage:
    def __init__(self, seed: Optional[dict] = None, clock: Optional[Callable[[], datetime]] = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._tables: dict[str, list[dict]] = {name: [] for name in TABLE_COLUMNS}
        self._payloads: dict[str, Optional[dict]] = {}
        self.uploads: dict[tuple[str, str], bytes] = {}
        # Operation names that raise StorageError, for testing how callers
        # handle a failing database.
        self.failing: set[str] = set()
        if seed:
            self.seed(seed)

    @classmethod
    def from_json(cls, path: str | Path, clock: Optional[Callable[[], datetime]] = None) -> "MemoryStorage":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), clock=clock)

    # --- seeding and inspection (not part of the Storage interface) ---
    def seed(self, data: dict) -> None:
        for table, rows in (data.get("tables") or {}).items():
            self.add_rows(table, rows)
        for user_id, payload in (data.get("dashboard_payloads") or {}).items():
            self.set_dashboard_payload(user_id, payload)

    def add_rows(self, table: str, rows: Iterable[dict]) -> list[dict]:
        """Appends rows to a table as an insert would: unknown columns are
        rejected, and a missing id or timestamp is filled in."""
        if table not in TABLE_COLUMNS:
            raise KeyError(f"MemoryStorage has no table {table!r}")
        columns = TABLE_COLUMNS[table]
        stored = []
        for row in rows:
            unknown = set(row) - set(columns)
            if unknown:
                raise KeyError(f"{table} has no column(s) {sorted(unknown)}")
            new = _copy(row)
            if "id" in columns and new.get("id") is None:
                new["id"] = max((r["id"] for r in self._tables[table]), default=0) + 1
            stamp = _STAMPED.get(table)
            if stamp and new.get(stamp) is None:
                new[stamp] = self._clock().isoformat()
            self._tables[table].append(new)
            stored.append(_copy(new))
        return stored

    def rows(self, table: str) -> list[dict]:
        """A copy of every row in a table, in insertion order."""
        return _copy(self._tables[table])

    def set_dashboard_payload(self, user_id: int | str, payload: Optional[dict]) -> None:
        self._payloads[str(user_id)] = _copy(payload)

    # --- query helpers -------------------------------------------------
    def _check(self, operation: str) -> None:
        if operation in self.failing:
            raise StorageError(operation, "simulated failure")

    def _insert(self, operation: str, table: str, rows: list[dict]) -> list[dict]:
        try:
            return self.add_rows(table, rows)
        except (TypeError, ValueError) as exc:  # a value jsonb could not hold
            raise StorageError(operation, exc) from exc

    def _select(self, table: str, user_id: object, newest_first_by: Optional[str] = None,
                limit: Optional[int] = None, **equal: object) -> list[dict]:
        column = USER_COLUMN[table]
        indexed = [
            (i, r) for i, r in enumerate(self._tables[table])
            if _same_user(r, column, user_id) and all(r.get(k) == v for k, v in equal.items())
        ]
        if newest_first_by:
            def key(item: tuple[int, dict]) -> tuple[datetime, int]:
                value = item[1].get(newest_first_by)
                return (parse_timestamp(value) if value is not None else _EPOCH, item[0])
            indexed.sort(key=key, reverse=True)
        rows = [_copy(r) for _, r in indexed]
        return rows if limit is None else rows[:limit]

    @staticmethod
    def _project(row: dict, columns: Iterable[str]) -> dict:
        return {c: row.get(c) for c in columns}

    def _latest(self, operation: str, table: str, user_id: int) -> Optional[dict]:
        self._check(operation)
        rows = self._select(table, user_id, newest_first_by="evaluated_at", limit=1)
        return rows[0] if rows else None

    def _append(self, operation: str, table: str, user_id: int, assessment: dict) -> None:
        self._check(operation)
        columns = TABLE_COLUMNS[table]
        missing = [c for c in columns if c not in ("id", "userid", "evaluated_at") and c not in assessment]
        if missing:
            raise StorageError(operation, f"assessment has no {missing}")
        row = {c: assessment[c] for c in columns if c not in ("id", "userid", "evaluated_at")}
        row["userid"] = user_id
        self._insert(operation, table, [row])

    # --- engine snapshots ---------------------------------------------
    def _snapshot_row(self, user_id: int) -> Optional[dict]:
        rows = self._select("pond_chemistry_state", user_id, limit=1)
        return rows[0] if rows else None

    @staticmethod
    def _version(row: Optional[dict]) -> int:
        # Rows seeded without the column were written before migration 0002.
        if row is None:
            return 0
        return int(row.get("snapshot_version") or 1)

    def load_engine_snapshot(self, user_id: int) -> Optional[dict]:
        self._check("load_engine_snapshot")
        row = self._snapshot_row(user_id)
        return row["snapshot"] if row else None

    def load_engine_state(self, user_id: int) -> Optional[tuple[dict, int]]:
        self._check("load_engine_state")
        with self._lock:
            row = self._snapshot_row(user_id)
        return (row["snapshot"], self._version(row)) if row else None

    def fetch_snapshot_version(self, user_id: int) -> int:
        self._check("fetch_snapshot_version")
        return self._version(self._snapshot_row(user_id))

    def save_engine_snapshot(self, user_id: int, snapshot: dict, base_version: Optional[int] = None) -> int:
        self._check("save_engine_snapshot")
        with self._lock:
            current = self._version(self._snapshot_row(user_id))
            if base_version is not None and base_version != current:
                raise StaleSnapshotError(user_id, base_version)
            row = {"user_id": user_id, "snapshot": snapshot, "updated_at": self._clock().isoformat(),
                   "snapshot_version": current + 1}
            try:
                row = _copy(row)
            except (TypeError, ValueError) as exc:  # a value jsonb could not hold
                raise StorageError("save_engine_snapshot", exc) from exc
            table = self._tables["pond_chemistry_state"]
            table[:] = [r for r in table if not _same_user(r, "user_id", user_id)]
            table.append(row)
            return row["snapshot_version"]

    # --- worker lease -------------------------------------------------
    def take_lease(self, name: str, holder: str, ttl_seconds: int) -> bool:
        self._check("take_lease")
        with self._lock:
            now = self._clock()
            table = self._tables["worker_lease"]
            row = next((r for r in table if r["name"] == name), None)
            if row is not None and row["holder"] != holder and parse_timestamp(row["expires_at"]) >= now:
                return False
            table[:] = [r for r in table if r["name"] != name]
            table.append({"name": name, "holder": holder,
                          "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat()})
            return True

    def release_lease(self, name: str, holder: str) -> None:
        self._check("release_lease")
        with self._lock:
            table = self._tables["worker_lease"]
            table[:] = [r for r in table if not (r["name"] == name and r["holder"] == holder)]

    def fetch_lease(self, name: str) -> Optional[dict]:
        self._check("fetch_lease")
        with self._lock:
            row = next((r for r in self._tables["worker_lease"] if r["name"] == name), None)
            return _copy(row) if row else None

    # --- worker status ------------------------------------------------
    def record_worker_status(self, name: str, status: dict) -> None:
        self._check("record_worker_status")
        columns = TABLE_COLUMNS["worker_status"]
        row = {c: status.get(c) for c in columns if c != "name"}
        row["name"] = name
        row["ponds"] = row["ponds"] or {}
        try:
            row = _copy(row)
        except (TypeError, ValueError) as exc:
            raise StorageError("record_worker_status", exc) from exc
        with self._lock:
            table = self._tables["worker_status"]
            table[:] = [r for r in table if r["name"] != name]
            table.append(row)

    def fetch_worker_status(self, name: str) -> Optional[dict]:
        self._check("fetch_worker_status")
        with self._lock:
            row = next((r for r in self._tables["worker_status"] if r["name"] == name), None)
            return _copy(row) if row else None

    def fetch_snapshot_times(self) -> dict[str, str]:
        self._check("fetch_snapshot_times")
        with self._lock:
            return {str(r["user_id"]): r["updated_at"] for r in self._tables["pond_chemistry_state"]
                    if r.get("updated_at")}

    # --- evaluation logs ----------------------------------------------
    def push_evaluation(self, user_id: int, assessment: dict) -> None:
        self._append("push_evaluation", "pond_chemistry_evaluations", user_id, assessment)

    def push_evaporation_evaluation(self, user_id: int, assessment: dict) -> None:
        self._append("push_evaporation_evaluation", "pond_evaporation_evaluations", user_id, assessment)

    def push_algae_evaluation(self, user_id: int, assessment: dict) -> None:
        self._append("push_algae_evaluation", "pond_algae_evaluations", user_id, assessment)

    def fetch_latest_evaluation(self, user_id: int) -> Optional[dict]:
        return self._latest("fetch_latest_evaluation", "pond_chemistry_evaluations", user_id)

    def fetch_latest_evaporation_evaluation(self, user_id: int) -> Optional[dict]:
        return self._latest("fetch_latest_evaporation_evaluation", "pond_evaporation_evaluations", user_id)

    def fetch_latest_algae_evaluation(self, user_id: int) -> Optional[dict]:
        return self._latest("fetch_latest_algae_evaluation", "pond_algae_evaluations", user_id)

    # --- algae severity ratings ---------------------------------------
    def insert_algae_rating(
        self,
        user_id: int,
        severity: Optional[str],
        is_obstructed: bool,
        image_id: Optional[int],
        image_url: Optional[str],
        green_ratio_at_rating: Optional[float],
        image_captured_at: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[dict]:
        self._check("insert_algae_rating")
        return self._insert("insert_algae_rating", "algae_severity_ratings", [{
            "userid": user_id, "image_id": image_id, "image_url": image_url,
            "severity": severity, "is_obstructed": is_obstructed,
            "green_ratio_at_rating": green_ratio_at_rating,
            "image_captured_at": image_captured_at, "notes": notes,
        }])[0]

    def fetch_algae_ratings(self, user_id: int, limit: int = 200) -> list[dict]:
        self._check("fetch_algae_ratings")
        rows = self._select("algae_severity_ratings", user_id, newest_first_by="rated_at", limit=limit)
        rows.reverse()
        return rows

    def delete_algae_rating(self, user_id: int, rating_id: int) -> None:
        self._check("delete_algae_rating")
        table = self._tables["algae_severity_ratings"]
        table[:] = [r for r in table if not (r["id"] == rating_id and _same_user(r, "userid", user_id))]

    # --- camera frames ------------------------------------------------
    def fetch_image_by_id(self, user_id: int, image_id: int) -> Optional[dict]:
        self._check("fetch_image_by_id")
        rows = self._select("imageTable", user_id, limit=1, id=image_id)
        return self._project(rows[0], IMAGE_COLUMNS) if rows else None

    def fetch_image_history(self, user_id: int | str, limit: int = 200) -> list[dict]:
        self._check("fetch_image_history")
        rows = self._select("imageTable", user_id, newest_first_by="created_at", limit=limit)
        return [self._project(r, IMAGE_COLUMNS) for r in rows]

    def insert_image(self, user_id: int | str, green_ratio: float, current_state: Any, image_url: str) -> None:
        self._check("insert_image")
        self._insert("insert_image", "imageTable", [{
            "user_ID": user_id, "green_ratio": green_ratio,
            "current_state": current_state, "imageURL": image_url}])

    def upload_image(self, bucket: str, path: str, data: bytes) -> str:
        self._check("upload_image")
        self.uploads[(bucket, path)] = bytes(data)
        return f"memory://{bucket}/{path}"

    # --- pond config --------------------------------------------------
    def fetch_active_pond_configs(self) -> list[dict]:
        self._check("fetch_active_pond_configs")
        configs = []
        for row in self.rows("UserData"):
            config = pond_config_from_userdata_row(row)
            if config is not None:
                config["user_id"] = row["userID"]
                configs.append(config)
        return configs

    def fetch_pond_config(self, user_id: int) -> Optional[dict]:
        self._check("fetch_pond_config")
        rows = self._select("UserData", user_id, limit=1)
        return pond_config_from_userdata_row(rows[0]) if rows else None

    # --- account links ------------------------------------------------
    def fetch_pond_id_for_account(self, auth_uid: str) -> Optional[int]:
        self._check("fetch_pond_id_for_account")
        row = next((r for r in self.rows("UserData") if r.get("auth_uid") == auth_uid), None)
        return int(row["userID"]) if row else None

    def is_session_active(self, session_id: str, auth_uid: str) -> bool:
        self._check("is_session_active")
        now = self._clock()
        return any(r["id"] == session_id and r["user_id"] == auth_uid
                   and (r.get("not_after") is None or parse_timestamp(r["not_after"]) > now)
                   for r in self.rows("auth.sessions"))

    # --- sensors, weather and interventions ---------------------------
    def fetch_dashboard_payload(self, user_id: int) -> Optional[dict]:
        self._check("fetch_dashboard_payload")
        return _copy(self._payloads.get(str(user_id)))

    def fetch_recent_feeding_events(self, user_id: int, limit: int = 30) -> list[dict]:
        self._check("fetch_recent_feeding_events")
        rows = self._select("pondInterventions", user_id, newest_first_by="event_timestamp",
                            limit=limit, event_type="FEEDING")
        return [self._project(r, FEEDING_COLUMNS) for r in rows]

    def fetch_daily_sensor_stats(self, user_id: int, sensor_type: str, days: int = 14) -> Optional[dict]:
        self._check("fetch_daily_sensor_stats")
        return daily_stats(self._daily_rows(user_id, sensor_type, days))

    def fetch_daily_sensor_series(self, user_id: int, sensor_type: str, days: int = 30) -> list[dict]:
        self._check("fetch_daily_sensor_series")
        rows = self._daily_rows(user_id, sensor_type, days)
        rows.reverse()
        return rows

    def _daily_rows(self, user_id: int, sensor_type: str, days: int) -> list[dict]:
        rows = self._select("daily_sensor_averages", user_id, newest_first_by="record_date",
                            limit=days, sensor_type=sensor_type)
        return [self._project(r, DAILY_COLUMNS) for r in rows]
