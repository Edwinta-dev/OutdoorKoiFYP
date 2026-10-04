"""The storage interface every service uses, and the helpers both
implementations share.

The API, the worker and the camera service each get one Storage object
(built from settings.storage by koi.storage.build_storage) and do all
database and file I/O through it:

    SupabaseStorage  the live Supabase project (koi/storage/supabase_storage.py)
    MemoryStorage    in-process dicts, for tests and offline runs (koi/storage/memory.py)

Every operation raises StorageError when it fails; nothing is logged or
swallowed inside storage. The caller decides whether a failure is fatal
(a lost snapshot write) or can be skipped (a missing evaluation log row);
fail_soft below is the one way to say "skip".

The current NEA telemetry and forecast reach the services inside the
dashboard payload (fetch_dashboard_payload), which
get_bundled_dashboard_payload assembles from the latest caches. The
weather operations below are the ingestion job's writes (koi/weather) and
the as-of reads over weather history (migration 0009).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional, Protocol, TypeVar

from koi.logs import log_event

T = TypeVar("T")

log = logging.getLogger(__name__)


class StorageError(Exception):
    """A storage operation failed. operation is the Storage method name."""

    def __init__(self, operation: str, detail: object = ""):
        self.operation = operation
        self.detail = str(detail)
        super().__init__(f"{operation} failed: {self.detail}" if self.detail else f"{operation} failed")


class StaleSnapshotError(StorageError):
    """save_engine_snapshot was given a base version that is no longer the
    stored one: another process saved this pond's snapshot since it was
    loaded."""

    def __init__(self, user_id: int, base_version: int):
        self.user_id = user_id
        self.base_version = base_version
        super().__init__("save_engine_snapshot", f"snapshot for user {user_id} is newer than version {base_version}")


class DuplicateProfileError(StorageError):
    """insert_pond_profile was given an effective_from at which the pond
    already has a profile row ((pond_id, effective_from) is unique)."""

    def __init__(self, user_id: int, effective_from: str):
        self.user_id = user_id
        self.effective_from = effective_from
        super().__init__("insert_pond_profile", f"pond {user_id} already has a profile effective {effective_from}")


def fail_soft(call: Callable[[], T], default: T) -> T:
    """Runs call(); on StorageError logs a storage_call_failed warning and
    returns default. For the reads and writes a caller can do without, such
    as an evaluation log row or the rating history."""
    try:
        return call()
    except StorageError as exc:
        log_event(log, "storage_call_failed", level=logging.WARNING, operation=exc.operation, error=str(exc))
        return default


class Storage(Protocol):
    # --- engine snapshots (pond_chemistry_state) ---------------------
    # snapshot_version counts saves of a pond's snapshot: 0 means no row,
    # rows written before migration 0002 read as 1.
    def load_engine_snapshot(self, user_id: int) -> Optional[dict]: ...

    def load_engine_state(self, user_id: int) -> Optional[tuple[dict, int]]:
        """(snapshot, snapshot_version), or None when there is no row."""
        ...

    def fetch_snapshot_version(self, user_id: int) -> int:
        """The stored snapshot_version, 0 when there is no row."""
        ...

    def save_engine_snapshot(self, user_id: int, snapshot: dict, base_version: Optional[int] = None,
                             ingest: Optional[dict] = None) -> int:
        """Stores the snapshot and returns its new version. With base_version
        set, raises StaleSnapshotError unless the stored version equals it
        (0: no row may exist yet). None saves unconditionally.

        ingest (Discovery.commit(), koi/models/sensor_inputs.py) records
        the sensor rows the snapshot has applied in sensor_ingest_ledger
        and moves the pond's sensor_ingest_cursor watermark forward, in the
        same transaction as the save (save_pond_snapshot_with_ingest,
        migration 0014). A row already in the ledger fails the whole save
        with StorageError."""
        ...

    # --- sensor ingestion (migration 0014) ----------------------------
    def fetch_pending_sensor_rows(self, user_id: int, sensor_types: tuple[str, ...], start: Optional[datetime],
                                  until: Optional[datetime], overlap_seconds: int, limit: int) -> dict:
        """The pond's SensorData rows not yet in the ingest ledger
        (sensor_ingest_pending): {cursor, scan_from, rows: [{id,
        sensor_type, value, created_at}]}, rows ordered by created_at, id.
        The scan starts overlap_seconds before the cursor's watermark, or
        at start when the pond has no cursor (None: every row)."""
        ...

    def fetch_ingested_sensor_rows(self, user_id: int, sensor_types: tuple[str, ...], after: Optional[datetime],
                                   until: datetime) -> list[dict]:
        """The pond's SensorData rows already in the ingest ledger with an
        effective sample time in (after, until] (after None: no lower
        bound), as [{id, sensor_type, value, created_at}] in that order
        (sensor_ingest_history, migration 0015). What a replay reads back."""
        ...

    # --- interventions (pondInterventions, migration 0015) -------------
    def fetch_interventions(self, user_id: int, since: Optional[datetime]) -> list[dict]:
        """The pond's rows with event_timestamp at or after since (None:
        all), INTERVENTION_COLUMNS, ordered by event_timestamp, id
        (pond_interventions_since). Only this pond's rows."""
        ...

    def backfill_intervention_event_ids(self, user_id: int) -> int:
        """Gives the pond's rows with a null event_id the UUID derived from
        their id (backfill_intervention_event_ids); returns how many.
        Deterministic, so a retry gives the same UUIDs."""
        ...

    # --- worker lease (worker_lease) ----------------------------------
    def take_lease(self, name: str, holder: str, ttl_seconds: int) -> bool:
        """Takes the named lease if it is free or expired, or renews it if
        holder already has it, for ttl_seconds. True when holder holds it."""
        ...

    def release_lease(self, name: str, holder: str) -> None:
        """Gives the lease up if holder has it, so a standby can take over
        without waiting for it to expire."""
        ...

    def fetch_lease(self, name: str) -> Optional[dict]:
        """The lease row {name, holder, expires_at}, expired or not, or None."""
        ...

    # --- worker status (worker_status) --------------------------------
    def record_worker_status(self, name: str, status: dict) -> None:
        """Replaces the named worker's last-cycle report. status has the
        worker_status columns other than name (migration 0003)."""
        ...

    def fetch_worker_status(self, name: str) -> Optional[dict]: ...

    def fetch_snapshot_times(self) -> dict[str, str]:
        """updated_at of every stored engine snapshot, by user id (as text)."""
        ...

    # --- evaluation logs ----------------------------------------------
    def push_evaluation(self, user_id: int, assessment: dict) -> None: ...

    def push_evaporation_evaluation(self, user_id: int, assessment: dict) -> None: ...

    def push_algae_evaluation(self, user_id: int, assessment: dict) -> None: ...

    def fetch_latest_evaluation(self, user_id: int) -> Optional[dict]: ...

    def fetch_latest_evaporation_evaluation(self, user_id: int) -> Optional[dict]: ...

    def fetch_latest_algae_evaluation(self, user_id: int) -> Optional[dict]: ...

    # --- evaluation retention (evaluation_daily, migration 0017) -------
    def summarize_evaluation_days(self, before: date, time_zone: str, max_days: int) -> dict:
        """Folds the detailed evaluation rows of every pond and domain on
        local dates before `before` (oldest max_days dates first, never a
        pond's newest date) into evaluation_daily and deletes them, in one
        transaction (summarize_evaluation_days). Returns {"days": [ISO
        dates], "rows": n, "summaries": n}; no days means nothing was left.
        Raises StorageError when before is after today in time_zone."""
        ...

    def fetch_evaluation_daily(self, user_id: int, domain: Optional[str] = None) -> list[dict]:
        """The pond's evaluation_daily rows (EVALUATION_DAILY_COLUMNS),
        oldest local_date first, then by domain."""
        ...

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
    ) -> Optional[dict]: ...

    def fetch_algae_ratings(self, user_id: int, limit: int = 200) -> list[dict]: ...

    def delete_algae_rating(self, user_id: int, rating_id: int) -> None: ...

    # --- camera frames (imageTable and the frame bucket) -------------
    def fetch_image_by_id(self, user_id: int, image_id: int) -> Optional[dict]: ...

    def fetch_image_history(self, user_id: int | str, limit: int = 200) -> list[dict]: ...

    def insert_image(self, user_id: int | str, green_ratio: float, current_state: Any, image_url: str,
                     mask_version: Optional[int] = None, baseline_reset: Optional[str] = None,
                     quality: Optional[dict] = None, thumbnail_path: Optional[str] = None) -> None:
        """mask_version and baseline_reset (migration 0010), quality and
        thumbnail_path (migration 0011) are left out of the insert when
        None, so a row without them is stored the same way as before."""
        ...

    def upload_image(self, bucket: str, path: str, data: bytes) -> str: ...

    def delete_images(self, bucket: str, paths: list[str]) -> None:
        """Removes stored objects; used to clean up after a failed row insert."""
        ...

    # --- camera water mask (camera_config, migration 0010) -------------
    def fetch_camera_mask(self, user_id: int | str) -> Optional[dict]:
        """The pond's camera_config row {pond_id, mask, mask_version,
        updated_at}, or None when no mask has been saved."""
        ...

    def fetch_camera_mask_versions(self, user_id: int | str) -> list[dict]:
        """Every saved mask of the pond {pond_id, mask_version, mask,
        created_at}, oldest version first."""
        ...

    def save_camera_mask(self, user_id: int, mask: list) -> dict:
        """Stores mask (a validated polygon, koi/camera/mask.py) as the
        pond's next mask version and makes it the one in force, in one
        step (save_camera_mask). Returns the new camera_config row."""
        ...

    # --- pond config (UserData) ---------------------------------------
    def fetch_active_pond_configs(self) -> list[dict]: ...

    def fetch_pond_config(self, user_id: int) -> Optional[dict]: ...

    # --- pond profile (pond_profile, migration 0008) -------------------
    def fetch_pond_profiles(self, user_id: int) -> list[dict]:
        """Every profile row of the pond (PROFILE_COLUMNS), oldest
        effective_from first. Empty when the pond has none."""
        ...

    def insert_pond_profile(self, user_id: int, profile: dict) -> dict:
        """Inserts one row from profile (effective_from as ISO 8601 and
        the PROFILE_FIELDS of koi/models/profile.py) with source 'api',
        and returns the stored row. Raises DuplicateProfileError when the
        pond already has a row at that effective_from."""
        ...

    # --- account links (UserData.auth_uid, migration 0007) -------------
    def fetch_pond_id_for_account(self, auth_uid: str) -> Optional[int]:
        """The userID of the pond linked to this Supabase account, or None."""
        ...

    def is_session_active(self, session_id: str, auth_uid: str) -> bool:
        """True while the account's auth session exists and has not reached
        its not_after time (the auth_session_active function)."""
        ...

    # --- sensors, weather and interventions ---------------------------
    def fetch_dashboard_payload(self, user_id: int) -> Optional[dict]: ...

    def fetch_dashboard_sources(self, user_id: int) -> dict:
        """The rows behind GET /v1/ponds/{pond}/dashboard
        (pond_dashboard_sources, migration 0012): {pond_exists, stations,
        readings, telemetry, forecasts}, uninterpreted. koi/api/dashboard.py
        turns them into the response, the same way for both storages."""
        ...

    def fetch_recent_feeding_events(self, user_id: int, limit: int = 30) -> list[dict]: ...

    def fetch_daily_sensor_stats(self, user_id: int, sensor_type: str, days: int = 14) -> Optional[dict]: ...

    def fetch_daily_sensor_series(self, user_id: int, sensor_type: str, days: int = 30) -> list[dict]: ...

    # --- weather history (migration 0009, koi/weather) -----------------
    def ingest_weather_batch(self, batch: dict) -> dict:
        """Stores one fetch through ingest_weather_batch: history rows
        deduplicated, latest caches updated only by newer values, station
        lookup maintained. batch and the returned counts are described
        in the migration."""
        ...

    def fetch_weather_windows(self, product: str) -> list[dict]:
        """Every weather_ingest_window row of the product."""
        ...

    def record_weather_window(self, window: dict) -> None:
        """Inserts or replaces the (product, window_start) row."""
        ...

    def fetch_assigned_weather_slots(self) -> list[dict]:
        """The "ClosestStations" map of every pond that has one."""
        ...

    def fetch_forecast_as_of(self, product: str, slot_id: str, as_of: datetime,
                             valid_at: Optional[datetime] = None) -> Optional[dict]:
        """The forecast issuance usable at as_of (weather_forecast_as_of),
        with issued_at, source_updated_at, available_at and fetched_at."""
        ...

    def fetch_weather_observations(self, station_id: str, metric: str, start: datetime, end: datetime,
                                   as_of: datetime) -> list[dict]:
        """Station observations inside [start, end] observed by as_of, with
        observed_from, observed_to and fetched_at (weather_observations_as_of)."""
        ...

    def fetch_rainfall_total(self, station_id: str, start: datetime, end: datetime,
                             as_of: Optional[datetime] = None) -> dict:
        """Rainfall over [start, end] with coverage (weather_rainfall_total)."""
        ...


# ---------------------------------------------------------------------
# Row conversions shared by both implementations
# ---------------------------------------------------------------------
_BIOMASS_KG_TO_GRAMS = 1000.0

# imageTable columns the services read (mask_version, baseline_reset: 0010;
# quality, thumbnail_path: 0011).
IMAGE_COLUMNS = ("id", "created_at", "green_ratio", "current_state", "imageURL", "mask_version", "baseline_reset",
                 "quality", "thumbnail_path")
# camera_config and camera_mask_version columns (migration 0010).
CAMERA_CONFIG_COLUMNS = ("pond_id", "mask", "mask_version", "updated_at")
CAMERA_MASK_VERSION_COLUMNS = ("pond_id", "mask_version", "mask", "created_at")

# pondInterventions columns the ledger reads (event_id: migration 0015).
INTERVENTION_COLUMNS = ("id", "event_id", "event_type", "event_timestamp", "volume_percentage", "volume_litres",
                        "food_grams", "protein_percentage", "algae_method", "created_at")

# Provenance columns of the three evaluation tables (migration 0016).
# Every evaluation push carries all four (koi/provenance.py); rows written
# before 0016 read them as null.
PROVENANCE_COLUMNS = ("model_version", "input_cutoff", "forecast_issued_at", "inputs")


def with_provenance(row: Optional[dict]) -> Optional[dict]:
    """A stored evaluation row with every provenance column present: a row
    written before migration 0016 (or read before it is applied) has
    null, meaning unknown, in each."""
    return None if row is None else {**{c: None for c in PROVENANCE_COLUMNS}, **row}

# pond_profile's columns (migration 0008).
PROFILE_COLUMNS = ("id", "pond_id", "effective_from", "volume_l", "depth_m", "biomass_g", "fish_type",
                   "fish_count", "tap_tds_ppm", "tap_nitrate_ppm", "aeration", "source", "created_at")


def pond_config_from_userdata_row(row: dict) -> Optional[dict]:
    """UserData is populated at onboarding (see onboarding_screen.dart) and
    only has volume (litres) + biomass (KG, aggregated across all fish
    entries). It is the legacy pond configuration: a pond's profile
    (pond_profile, koi/models/profile.py) is built from this only when
    the pond has no pond_profile rows."""
    if row.get("volume") is None or row.get("biomass") is None:
        return None
    return {
        "volume_litres": float(row["volume"]),
        "estimated_biomass_grams": float(row["biomass"]) * _BIOMASS_KG_TO_GRAMS,
    }


def daily_stats(rows: list[dict]) -> Optional[dict]:
    """Averages avg_value over the daily_sensor_averages rows of one
    sensor - a rolling recent baseline rather than a single day, so one
    unusually hot/cold or bright/dim day doesn't skew a projection.

    Returns None if there's no history yet for this sensor_type (e.g. a
    newly onboarded pond that hasn't had polls accumulate daily
    aggregates), so callers know to surface "not enough history" rather
    than silently projecting with a fabricated baseline."""
    avgs = [r["avg_value"] for r in rows if r.get("avg_value") is not None]
    if not avgs:
        return None
    mins = [r["min_value"] for r in rows if r.get("min_value") is not None]
    maxs = [r["max_value"] for r in rows if r.get("max_value") is not None]
    return {
        "avg": sum(avgs) / len(avgs),
        "min": min(mins) if mins else None,
        "max": max(maxs) if maxs else None,
        "sample_days": len(rows),
    }


def parse_timestamp(value: object) -> datetime:
    """A timestamp column value (ISO string or datetime) as an aware
    datetime; dates without a zone are taken as UTC."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
