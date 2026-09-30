"""The storage interface every service uses, and the helpers both
implementations share.

The API, the worker and the camera service each get one Storage object
(built from settings.storage by koi.storage.build_storage) and do all
database and file I/O through it:

    SupabaseStorage  the live Supabase project (koi/storage/supabase_storage.py)
    MemoryStorage    in-process dicts, for tests and offline runs (koi/storage/memory.py)

Every operation raises StorageError when it fails; nothing is printed or
swallowed inside storage. The caller decides whether a failure is fatal
(a lost snapshot write) or can be skipped (a missing evaluation log row);
fail_soft below is the one way to say "skip".

Weather has no operation of its own: the NEA telemetry and forecast reach
the backend inside the dashboard payload (fetch_dashboard_payload), which
get_bundled_dashboard_payload assembles in the database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol, TypeVar

T = TypeVar("T")


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


def fail_soft(call: Callable[[], T], default: T) -> T:
    """Runs call(); on StorageError prints it and returns default. For
    the reads and writes a caller can do without, such as an evaluation
    log row or the rating history."""
    try:
        return call()
    except StorageError as exc:
        print(f"[storage] {exc}")
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

    def save_engine_snapshot(self, user_id: int, snapshot: dict, base_version: Optional[int] = None) -> int:
        """Stores the snapshot and returns its new version. With base_version
        set, raises StaleSnapshotError unless the stored version equals it
        (0: no row may exist yet). None saves unconditionally."""
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

    # --- evaluation logs ----------------------------------------------
    def push_evaluation(self, user_id: int, assessment: dict) -> None: ...

    def push_evaporation_evaluation(self, user_id: int, assessment: dict) -> None: ...

    def push_algae_evaluation(self, user_id: int, assessment: dict) -> None: ...

    def fetch_latest_evaluation(self, user_id: int) -> Optional[dict]: ...

    def fetch_latest_evaporation_evaluation(self, user_id: int) -> Optional[dict]: ...

    def fetch_latest_algae_evaluation(self, user_id: int) -> Optional[dict]: ...

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

    def insert_image(self, user_id: int | str, green_ratio: float, current_state: Any, image_url: str) -> None: ...

    def upload_image(self, bucket: str, path: str, data: bytes) -> str: ...

    # --- pond config (UserData) ---------------------------------------
    def fetch_active_pond_configs(self) -> list[dict]: ...

    def fetch_pond_config(self, user_id: int) -> Optional[dict]: ...

    # --- sensors, weather and interventions ---------------------------
    def fetch_dashboard_payload(self, user_id: int) -> Optional[dict]: ...

    def fetch_recent_feeding_events(self, user_id: int, limit: int = 30) -> list[dict]: ...

    def fetch_daily_sensor_stats(self, user_id: int, sensor_type: str, days: int = 14) -> Optional[dict]: ...

    def fetch_daily_sensor_series(self, user_id: int, sensor_type: str, days: int = 30) -> list[dict]: ...


# ---------------------------------------------------------------------
# Row conversions shared by both implementations
# ---------------------------------------------------------------------
_BIOMASS_KG_TO_GRAMS = 1000.0


def pond_config_from_userdata_row(row: dict) -> Optional[dict]:
    """UserData is populated at onboarding (see onboarding_screen.dart) and
    only has volume (litres) + biomass (KG, aggregated across all fish
    entries) - not fish_type/fish_count, which live in the phone's
    SharedPreferences and never get synced server-side. Callers that need
    those two fields (the event endpoints) fill them in from the request
    body; the poller falls back to PondConfig's own defaults."""
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
