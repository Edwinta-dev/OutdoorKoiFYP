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

from koi.models.event_ledger import derived_event_id
from koi.storage.base import (
    CAMERA_CONFIG_COLUMNS,
    CAMERA_MASK_VERSION_COLUMNS,
    IMAGE_COLUMNS,
    INTERVENTION_COLUMNS,
    PROFILE_COLUMNS,
    DuplicateProfileError,
    StaleSnapshotError,
    StorageError,
    daily_stats,
    parse_timestamp,
    pond_config_from_userdata_row,
)
from koi.weather import cache as weather_cache
from koi.weather import history as weather_history

# Columns of each table this store serves, from supabase/migrations/.
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "pond_chemistry_state": ("user_id", "snapshot", "updated_at", "snapshot_version"),
    # Sensor ingestion progress (migration 0014).
    "sensor_ingest_cursor": ("pond_id", "watermark", "updated_at"),
    "sensor_ingest_ledger": ("pond_id", "sensor_row_id", "sensor_type", "effective_sample_time", "time_basis",
                             "disposition", "snapshot_version", "ingested_at"),
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
    "imageTable": ("id", "created_at", "user_ID", "green_ratio", "current_state", "imageURL", "mask_version",
                   "baseline_reset", "quality", "thumbnail_path"),
    "camera_config": CAMERA_CONFIG_COLUMNS,
    "camera_mask_version": CAMERA_MASK_VERSION_COLUMNS,
    "UserData": ("userID", "created_at", "volume", "biomass", "latitude", "longitude",
                 "manualpostallocation", "ClosestStations", "auth_uid"),
    "pondInterventions": (
        "id", "created_at", "userID", "event_type", "event_timestamp", "volume_percentage",
        "volume_litres", "food_grams", "protein_percentage", "algae_method", "event_id"),
    "daily_sensor_averages": ("id", "userid", "sensor_type", "avg_value", "min_value", "max_value",
                              "record_date"),
    # The sensor node's readings, read by fetch_dashboard_sources.
    "SensorData": ("id", "created_at", "sensor_type", "data1", "userID"),
    "pond_profile": PROFILE_COLUMNS,
    # Supabase Auth's session table, read by auth_session_active (0007).
    "auth.sessions": ("id", "user_id", "not_after"),
    # Weather caches (0001, columns added by 0009), station lookup and
    # weather history (0009).
    "weather_telemetry": ("station_id", "metric_type", "data", "valid_start", "valid_end", "updated_at",
                          "source_times"),
    "weather_forecasts": ("forecast_type", "slot_id", "data", "valid_period", "updated_at", "source_issued_at"),
    "WeatherStationLookup": ("Id", "station__id", "station__name", "location__latitude", "location__longitude",
                             "Measurement"),
    "weather_observation": ("id", "source", "series", "station_id", "metric", "value", "unit", "semantics",
                            "observed_from", "observed_to", "fetched_at", "regime_id", "provenance",
                            "created_at"),
    "weather_forecast_issuance": ("id", "source", "product", "slot_id", "issued_at", "source_updated_at",
                                  "valid_from", "valid_to", "payload", "fetched_at", "available_at",
                                  "provenance", "created_at"),
    "weather_ingest_window": ("product", "window_start", "window_end", "status", "attempts", "pages", "records",
                              "inserted", "duplicates", "malformed", "last_error", "updated_at"),
}

# The user column of each table (three spellings coexist in the schema).
USER_COLUMN = {
    "pond_chemistry_state": "user_id",
    "sensor_ingest_cursor": "pond_id",
    "sensor_ingest_ledger": "pond_id",
    "pond_chemistry_evaluations": "userid",
    "pond_evaporation_evaluations": "userid",
    "pond_algae_evaluations": "userid",
    "algae_severity_ratings": "userid",
    "imageTable": "user_ID",
    "UserData": "userID",
    "pondInterventions": "userID",
    "daily_sensor_averages": "userid",
    "SensorData": "userID",
    "pond_profile": "pond_id",
    "camera_config": "pond_id",
    "camera_mask_version": "pond_id",
}

DAILY_COLUMNS = ("avg_value", "min_value", "max_value", "record_date")
FEEDING_COLUMNS = ("food_grams", "protein_percentage", "event_timestamp")
# Columns returned by weather_forecast_as_of and weather_observations_as_of.
FORECAST_AS_OF_COLUMNS = ("id", "product", "slot_id", "issued_at", "source_updated_at", "available_at",
                          "fetched_at", "valid_from", "valid_to", "payload")
OBSERVATION_AS_OF_COLUMNS = ("id", "source", "station_id", "metric", "value", "unit", "semantics",
                             "observed_from", "observed_to", "fetched_at", "regime_id")

# Table timestamps filled in on insert when the row does not carry one.
_STAMPED = {
    "pond_chemistry_evaluations": "evaluated_at",
    "pond_evaporation_evaluations": "evaluated_at",
    "pond_algae_evaluations": "evaluated_at",
    "algae_severity_ratings": "rated_at",
    "imageTable": "created_at",
    "pondInterventions": "created_at",
    "SensorData": "created_at",
    "UserData": "created_at",
    "pond_profile": "created_at",
    "camera_mask_version": "created_at",
    "weather_observation": "created_at",
    "weather_forecast_issuance": "created_at",
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
            if table == "pondInterventions":
                self._event_id(new)
            self._tables[table].append(new)
            stored.append(_copy(new))
        return stored

    def _event_id(self, row: dict) -> None:
        """pondInterventions.event_id (0015): a row without the key gets
        the UUID derived from its id, standing in for the column default
        deterministically; an explicit None stays None until
        backfill_intervention_event_ids. The column is unique."""
        if "event_id" not in row:
            row["event_id"] = derived_event_id(row["id"])
        if row["event_id"] is not None and any(
                r.get("event_id") == row["event_id"] for r in self._tables["pondInterventions"]):
            raise ValueError(f"pondInterventions.event_id {row['event_id']} already exists")

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

    def save_engine_snapshot(self, user_id: int, snapshot: dict, base_version: Optional[int] = None,
                             ingest: Optional[dict] = None) -> int:
        self._check("save_engine_snapshot")
        with self._lock:
            current = self._version(self._snapshot_row(user_id))
            if base_version is not None and base_version != current:
                raise StaleSnapshotError(user_id, base_version)
            now = self._clock().isoformat()
            row = {"user_id": user_id, "snapshot": snapshot, "updated_at": now,
                   "snapshot_version": current + 1}
            try:
                row = _copy(row)
            except (TypeError, ValueError) as exc:  # a value jsonb could not hold
                raise StorageError("save_engine_snapshot", exc) from exc
            ledger = self._tables["sensor_ingest_ledger"]
            entries = (ingest or {}).get("rows") or []
            # Checked before anything is written, as the transaction rolls
            # the save back in save_pond_snapshot_with_ingest.
            seen = {(str(r["pond_id"]), int(r["sensor_row_id"])) for r in ledger}
            for e in entries:
                if (str(user_id), int(e["sensor_row_id"])) in seen:
                    raise StorageError("save_engine_snapshot",
                                       f"SensorData row {e['sensor_row_id']} is already in the ingest ledger")
            table = self._tables["pond_chemistry_state"]
            table[:] = [r for r in table if not _same_user(r, "user_id", user_id)]
            table.append(row)
            for e in entries:
                ledger.append({"pond_id": user_id, "sensor_row_id": int(e["sensor_row_id"]),
                               "sensor_type": e.get("sensor_type"),
                               "effective_sample_time": e["effective_sample_time"],
                               "time_basis": e["time_basis"], "disposition": e["disposition"],
                               "snapshot_version": row["snapshot_version"], "ingested_at": now})
            watermark = (ingest or {}).get("watermark")
            if watermark is not None:
                cursors = self._tables["sensor_ingest_cursor"]
                old = next((c for c in cursors if _same_user(c, "pond_id", user_id)), None)
                if old is None:
                    cursors.append({"pond_id": user_id, "watermark": watermark, "updated_at": now})
                else:
                    if parse_timestamp(watermark) > parse_timestamp(old["watermark"]):
                        old["watermark"] = watermark
                    old["updated_at"] = now
            return row["snapshot_version"]

    # --- sensor ingestion ---------------------------------------------
    def fetch_pending_sensor_rows(self, user_id: int, sensor_types: tuple[str, ...], start: Optional[datetime],
                                  until: Optional[datetime], overlap_seconds: int, limit: int) -> dict:
        """The same rows sensor_ingest_pending (migration 0014) returns."""
        self._check("fetch_pending_sensor_rows")
        with self._lock:
            cursor = next((c for c in self._tables["sensor_ingest_cursor"] if _same_user(c, "pond_id", user_id)),
                          None)
            scan_from = (parse_timestamp(cursor["watermark"]) - timedelta(seconds=overlap_seconds)
                         if cursor is not None else start)
            ingested = {int(r["sensor_row_id"]) for r in self._tables["sensor_ingest_ledger"]
                        if _same_user(r, "pond_id", user_id)}
            rows = [r for r in self._select("SensorData", user_id)
                    if r.get("sensor_type") in sensor_types and int(r["id"]) not in ingested
                    and (scan_from is None or parse_timestamp(r["created_at"]) >= scan_from)
                    and (until is None or parse_timestamp(r["created_at"]) <= until)]
        rows.sort(key=self._reading_key)
        return {"cursor": cursor["watermark"] if cursor is not None else None,
                "scan_from": scan_from.isoformat() if scan_from is not None else None,
                "rows": [{"id": r["id"], "sensor_type": r["sensor_type"], "value": r.get("data1"),
                          "created_at": r["created_at"]} for r in rows[:limit]]}

    def fetch_ingested_sensor_rows(self, user_id: int, sensor_types: tuple[str, ...], after: Optional[datetime],
                                   until: datetime) -> list[dict]:
        """The same rows sensor_ingest_history (migration 0015) returns."""
        self._check("fetch_ingested_sensor_rows")
        with self._lock:
            times = {int(r["sensor_row_id"]): parse_timestamp(r["effective_sample_time"])
                     for r in self._tables["sensor_ingest_ledger"] if _same_user(r, "pond_id", user_id)}
            rows = [(times[int(r["id"])], r) for r in self._select("SensorData", user_id)
                    if int(r["id"]) in times and r.get("sensor_type") in sensor_types
                    and (after is None or times[int(r["id"])] > after) and times[int(r["id"])] <= until]
        rows.sort(key=lambda item: (item[0], int(item[1]["id"])))
        return [{"id": r["id"], "sensor_type": r["sensor_type"], "value": r.get("data1"),
                 "created_at": r["created_at"]} for _, r in rows]

    # --- interventions ------------------------------------------------
    def fetch_interventions(self, user_id: int, since: Optional[datetime]) -> list[dict]:
        """The same rows pond_interventions_since (migration 0015) returns."""
        self._check("fetch_interventions")
        rows = [r for r in self._select("pondInterventions", user_id)
                if since is None or parse_timestamp(r["event_timestamp"]) >= since]
        rows.sort(key=lambda r: (parse_timestamp(r["event_timestamp"]), int(r["id"])))
        return [self._project(r, INTERVENTION_COLUMNS) for r in rows]

    def backfill_intervention_event_ids(self, user_id: int) -> int:
        self._check("backfill_intervention_event_ids")
        with self._lock:
            filled = 0
            for r in self._tables["pondInterventions"]:
                if _same_user(r, "userID", user_id) and r.get("event_id") is None:
                    r["event_id"] = derived_event_id(r["id"])
                    filled += 1
            return filled

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

    def insert_image(self, user_id: int | str, green_ratio: float, current_state: Any, image_url: str,
                     mask_version: Optional[int] = None, baseline_reset: Optional[str] = None,
                     quality: Optional[dict] = None, thumbnail_path: Optional[str] = None) -> None:
        self._check("insert_image")
        row = {"user_ID": user_id, "green_ratio": green_ratio, "current_state": current_state,
               "imageURL": image_url}
        optional = {"mask_version": mask_version, "baseline_reset": baseline_reset, "quality": quality,
                    "thumbnail_path": thumbnail_path}
        row.update({k: v for k, v in optional.items() if v is not None})
        self._insert("insert_image", "imageTable", [row])

    def upload_image(self, bucket: str, path: str, data: bytes) -> str:
        self._check("upload_image")
        self.uploads[(bucket, path)] = bytes(data)
        return f"memory://{bucket}/{path}"

    def delete_images(self, bucket: str, paths: list[str]) -> None:
        self._check("delete_images")
        for path in paths:
            self.uploads.pop((bucket, path), None)

    # --- camera water mask --------------------------------------------
    def fetch_camera_mask(self, user_id: int | str) -> Optional[dict]:
        self._check("fetch_camera_mask")
        rows = self._select("camera_config", user_id, limit=1)
        return self._project(rows[0], CAMERA_CONFIG_COLUMNS) if rows else None

    def fetch_camera_mask_versions(self, user_id: int | str) -> list[dict]:
        self._check("fetch_camera_mask_versions")
        rows = sorted(self._select("camera_mask_version", user_id), key=lambda r: r["mask_version"])
        return [self._project(r, CAMERA_MASK_VERSION_COLUMNS) for r in rows]

    def save_camera_mask(self, user_id: int, mask: list) -> dict:
        self._check("save_camera_mask")
        with self._lock:
            current = self._select("camera_config", user_id, limit=1)
            version = (current[0]["mask_version"] if current else 0) + 1
            self._insert("save_camera_mask", "camera_mask_version",
                         [{"pond_id": user_id, "mask_version": version, "mask": mask}])
            table = self._tables["camera_config"]
            table[:] = [r for r in table if not _same_user(r, "pond_id", user_id)]
            return self._insert("save_camera_mask", "camera_config", [{
                "pond_id": user_id, "mask": mask, "mask_version": version,
                "updated_at": self._clock().isoformat()}])[0]

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

    # --- pond profile -------------------------------------------------
    def fetch_pond_profiles(self, user_id: int) -> list[dict]:
        self._check("fetch_pond_profiles")
        indexed = list(enumerate(self._select("pond_profile", user_id)))
        indexed.sort(key=lambda item: (parse_timestamp(item[1]["effective_from"]), item[1]["id"], item[0]))
        return [r for _, r in indexed]

    def insert_pond_profile(self, user_id: int, profile: dict) -> dict:
        self._check("insert_pond_profile")
        row = {k: v for k, v in profile.items() if k in PROFILE_COLUMNS and k not in ("id", "created_at")}
        row.update(pond_id=user_id, source="api")
        with self._lock:
            at = parse_timestamp(row["effective_from"])
            if any(parse_timestamp(r["effective_from"]) == at for r in self._select("pond_profile", user_id)):
                raise DuplicateProfileError(user_id, str(row["effective_from"]))
            return self._insert("insert_pond_profile", "pond_profile", [row])[0]

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

    def fetch_dashboard_sources(self, user_id: int) -> dict:
        """The same rows pond_dashboard_sources (migration 0012) returns,
        from this store's tables."""
        self._check("fetch_dashboard_sources")
        users = self._select("UserData", user_id)
        stations = users[0].get("ClosestStations") if users else None
        if isinstance(stations, str):  # a json column can hold text
            try:
                stations = json.loads(stations)
            except ValueError:
                stations = None
        slots = stations if isinstance(stations, dict) else {}

        newest: dict[str, dict] = {}
        for row in self._select("SensorData", user_id):
            sensor_type = row.get("sensor_type")
            if sensor_type is None:
                continue
            current = newest.get(sensor_type)
            if current is None or self._reading_key(row) > self._reading_key(current):
                newest[sensor_type] = row
        readings = [{"id": r["id"], "sensor_type": t, "value": r.get("data1"), "created_at": r["created_at"]}
                    for t, r in sorted(newest.items())]

        assigned = {v for k, v in slots.items() if k in ("air-temperature", "rainfall", "wind-speed")}
        telemetry = sorted(
            ({k: r.get(k) for k in ("station_id", "metric_type", "data", "source_times", "valid_start", "valid_end",
                                    "updated_at")}
             for r in self.rows("weather_telemetry")
             if r.get("metric_type") == "realtime_sensor" and r.get("station_id") in assigned),
            key=lambda r: str(r["station_id"]))

        def wanted(r: dict) -> bool:
            kind, slot = r.get("forecast_type"), r.get("slot_id")
            if kind == "2hr":
                return slot is not None and slot == slots.get("two-hr-forecast")
            if kind == "24hr":
                return slot == "GENERAL" or (slot is not None and slot == slots.get("twenty-four-hr-forecast"))
            return kind in ("4day", "uv")

        forecasts = sorted(
            ({k: r.get(k) for k in ("forecast_type", "slot_id", "data", "valid_period", "updated_at",
                                    "source_issued_at")}
             for r in self.rows("weather_forecasts") if wanted(r)),
            key=lambda r: (str(r["forecast_type"]), str(r["slot_id"])))
        return {"pond_exists": bool(users), "stations": stations, "readings": readings, "telemetry": telemetry,
                "forecasts": forecasts}

    @staticmethod
    def _reading_key(row: dict) -> tuple[datetime, int]:
        """Newest SensorData row: created_at, then id (the SQL tie breaker)."""
        return parse_timestamp(row["created_at"]), int(row["id"])

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

    # --- weather history ----------------------------------------------
    @staticmethod
    def _observation_key(row: dict) -> tuple:
        return (row["source"], row.get("series") or "station", row["station_id"], row["metric"],
                parse_timestamp(row["observed_from"]), parse_timestamp(row["observed_to"]))

    @staticmethod
    def _issuance_key(row: dict) -> tuple:
        def t(value: object) -> Optional[datetime]:
            return parse_timestamp(value) if value else None
        return (row["source"], row["product"], row["slot_id"], t(row["valid_from"]), t(row["valid_to"]),
                t(row.get("issued_at")), t(row.get("source_updated_at")))

    def ingest_weather_batch(self, batch: dict) -> dict:
        self._check("ingest_weather_batch")
        with self._lock:
            now = self._clock().isoformat()
            counts: dict[str, Any] = {}
            seen = {self._observation_key(r) for r in self._tables["weather_observation"]}
            new_rows = []
            for row in batch.get("observations") or []:
                key = self._observation_key(row)
                if key not in seen:
                    seen.add(key)
                    new_rows.append({**row, "series": row.get("series") or "station",
                                     "provenance": row.get("provenance") or {}})
            self._insert("ingest_weather_batch", "weather_observation", new_rows)
            counts["observations"] = {"received": len(batch.get("observations") or []), "inserted": len(new_rows)}

            seen = {self._issuance_key(r) for r in self._tables["weather_forecast_issuance"]}
            new_rows = []
            for row in batch.get("forecasts") or []:
                key = self._issuance_key(row)
                if key not in seen:
                    seen.add(key)
                    new_rows.append({**row, "available_at": weather_cache.available_at(row),
                                     "provenance": row.get("provenance") or {}})
            self._insert("ingest_weather_batch", "weather_forecast_issuance", new_rows)
            counts["forecasts"] = {"received": len(batch.get("forecasts") or []), "inserted": len(new_rows)}

            updated = 0
            table = self._tables["weather_telemetry"]
            for cand in sorted(batch.get("telemetry_cache") or [],
                               key=lambda c: (c["station_id"], parse_timestamp(c["observed_at"]))):
                index = next((i for i, r in enumerate(table) if r["station_id"] == cand["station_id"]
                              and r["metric_type"] == "realtime_sensor"), None)
                new = weather_cache.apply_telemetry_candidate(
                    _copy(table[index]) if index is not None else None, cand, now)
                if new is not None:
                    if index is None:
                        table.append(_copy(new))
                    else:
                        table[index] = _copy(new)
                    updated += 1
            counts["telemetry_cache_updated"] = updated

            updated = 0
            table = self._tables["weather_forecasts"]
            for cand in sorted(batch.get("forecast_cache") or [],
                               key=lambda c: (c["forecast_type"], c["slot_id"], parse_timestamp(c["source_time"]))):
                issued_4day = [parse_timestamp(r["source_issued_at"]) for r in table
                               if r["forecast_type"] == "4day" and r.get("source_issued_at")]
                newest = max(issued_4day).isoformat() if issued_4day else None
                index = next((i for i, r in enumerate(table) if r["forecast_type"] == cand["forecast_type"]
                              and r["slot_id"] == cand["slot_id"]), None)
                new = weather_cache.apply_forecast_candidate(
                    _copy(table[index]) if index is not None else None, cand, newest, now)
                if new is not None:
                    if index is None:
                        table.append(_copy(new))
                    else:
                        table[index] = _copy(new)
                    updated += 1
            counts["forecast_cache_updated"] = updated
            counts["stations_changed"] = sum(self._upsert_station(s) for s in batch.get("stations") or [])
            return _copy(counts)

    def _upsert_station(self, station: dict) -> int:
        """The station step of ingest_weather_batch; 1 when a row changed."""
        table = self._tables["WeatherStationLookup"]
        tag = station["tag"]
        if station.get("station_id") is not None:
            row = next((r for r in table if r.get("station__id") == station["station_id"]), None)
        else:
            row = next((r for r in table if r.get("station__name") == station["station_name"]
                        and tag in (r.get("Measurement") or [])), None)
        if row is None:
            table.append({"Id": max((r["Id"] for r in table), default=0) + 1,
                          "station__id": station.get("station_id"), "station__name": station.get("station_name"),
                          "location__latitude": station.get("latitude"),
                          "location__longitude": station.get("longitude"), "Measurement": [tag]})
            return 1
        before = _copy(row)
        if tag not in (row.get("Measurement") or []):
            row["Measurement"] = [*(row.get("Measurement") or []), tag]
        if station.get("station_id") is not None and station.get("station_name") is not None:
            row["station__name"] = station["station_name"]
        for key, column in (("latitude", "location__latitude"), ("longitude", "location__longitude")):
            if station.get(key) is not None:
                row[column] = station[key]
        return int(row != before)

    def fetch_weather_windows(self, product: str) -> list[dict]:
        self._check("fetch_weather_windows")
        return [r for r in self.rows("weather_ingest_window") if r["product"] == product]

    def record_weather_window(self, window: dict) -> None:
        self._check("record_weather_window")
        with self._lock:
            row = {**window, "updated_at": self._clock().isoformat()}
            unknown = set(row) - set(TABLE_COLUMNS["weather_ingest_window"])
            if unknown:
                raise StorageError("record_weather_window", f"no column(s) {sorted(unknown)}")
            start = parse_timestamp(row["window_start"])
            table = self._tables["weather_ingest_window"]
            table[:] = [r for r in table
                        if not (r["product"] == row["product"] and parse_timestamp(r["window_start"]) == start)]
            table.append(_copy(row))

    def fetch_assigned_weather_slots(self) -> list[dict]:
        self._check("fetch_assigned_weather_slots")
        slots = []
        for row in self.rows("UserData"):
            value = row.get("ClosestStations")
            if isinstance(value, str):
                value = json.loads(value)
            if isinstance(value, dict):
                slots.append(value)
        return slots

    def fetch_forecast_as_of(self, product: str, slot_id: str, as_of: datetime,
                             valid_at: Optional[datetime] = None) -> Optional[dict]:
        self._check("fetch_forecast_as_of")
        rows = [r for r in self.rows("weather_forecast_issuance")
                if r["product"] == product and r["slot_id"] == slot_id]
        found = weather_history.select_forecast_as_of(rows, as_of, valid_at)
        if found is None:
            return None
        return {k: found.get(k) for k in FORECAST_AS_OF_COLUMNS}

    def fetch_weather_observations(self, station_id: str, metric: str, start: datetime, end: datetime,
                                   as_of: datetime) -> list[dict]:
        self._check("fetch_weather_observations")
        rows = weather_history.observations_as_of(self.rows("weather_observation"), station_id, metric, start, end,
                                                  as_of)
        return [{k: r.get(k) for k in OBSERVATION_AS_OF_COLUMNS} for r in rows]

    def fetch_rainfall_total(self, station_id: str, start: datetime, end: datetime,
                             as_of: Optional[datetime] = None) -> dict:
        self._check("fetch_rainfall_total")
        return weather_history.rainfall_total(self.rows("weather_observation"), station_id, start, end, as_of)
