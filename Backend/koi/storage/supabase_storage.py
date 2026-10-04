"""SupabaseStorage: the Storage interface against the live Supabase project.

Uses the SERVICE ROLE key because this is trusted server-side code writing
on behalf of users - never ship that key to Flutter or the firmware. The
client is built on first use, not at construction, so building the app
needs no credentials.

Table notes (schema in supabase/migrations/):
- Three spellings of the user column coexist: "userID" (UserData,
  pondInterventions), "user_ID" (imageTable) and userid (the evaluation
  tables, algae_severity_ratings, daily_sensor_averages).
- pondInterventions.event_type is upper snake case ('FEEDING',
  'WATER_CHANGE', 'WATER_TOPUP', 'ALGAE_SCRUB'), not engine.py's EventKind
  spelling.
- The evaluation inserts copy only the columns the tables have: for
  example the chemistry assessment's risk_score and the algae
  assessment's label_count are not stored. Add a migration first to keep
  them in the log.
- Snapshot saves and the worker lease go through the save_pond_snapshot
  and take_worker_lease functions (migration 0002), which compare and set
  in one statement. A save that also records ingested sensor rows goes
  through save_pond_snapshot_with_ingest (migration 0014), one
  transaction for the snapshot, the ledger and the cursor.
- Weather ingestion writes only through ingest_weather_batch, and the
  as-of weather reads are the database functions of migration 0009.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterator, Optional

from koi.settings import Settings
from koi.storage.base import (
    CAMERA_CONFIG_COLUMNS,
    CAMERA_MASK_VERSION_COLUMNS,
    IMAGE_COLUMNS,
    PROFILE_COLUMNS,
    DuplicateProfileError,
    StaleSnapshotError,
    StorageError,
    daily_stats,
    pond_config_from_userdata_row,
)

if TYPE_CHECKING:
    from supabase import Client

DAILY_COLUMNS = "avg_value, min_value, max_value, record_date"
WINDOW_COLUMNS = ("product, window_start, window_end, status, attempts, pages, records, inserted, duplicates, "
                  "malformed, last_error, updated_at")


@contextmanager
def _operation(name: str) -> Iterator[None]:
    """Turns any failure inside one storage operation into StorageError.

    This is the only broad except in koi/storage: supabase-py raises
    postgrest, httpx, storage3 and plain Python errors (a missing key in
    an unexpected row, a missing credential), and callers need one type
    to handle. The original exception stays attached as __cause__."""
    try:
        yield
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - see docstring
        raise StorageError(name, exc) from exc


class SupabaseStorage:
    def __init__(self, settings: Settings, client: Optional["Client"] = None):
        """client is for tests; normally it is built from settings on first use."""
        self._settings = settings
        self._lock = threading.Lock()
        self._client = client

    def _db(self) -> "Client":
        with self._lock:
            if self._client is None:
                key = self._settings.supabase_servicerole_key.get_secret_value()
                if not self._settings.supabase_url or not key:
                    raise StorageError(
                        "connect",
                        "SUPABASE_URL and SUPABASE_SERVICEROLE_KEY must be set "
                        "(copy Backend/.env.example to Backend/.env)")
                from supabase import create_client

                self._client = create_client(self._settings.supabase_url, key)
            return self._client

    # --- engine snapshots ---------------------------------------------
    def load_engine_snapshot(self, user_id: int) -> Optional[dict]:
        with _operation("load_engine_snapshot"):
            res = (
                self._db().table("pond_chemistry_state")
                .select("snapshot")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            return res.data[0]["snapshot"] if res.data else None

    def load_engine_state(self, user_id: int) -> Optional[tuple[dict, int]]:
        with _operation("load_engine_state"):
            res = (
                self._db().table("pond_chemistry_state")
                .select("snapshot, snapshot_version")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if not res.data:
                return None
            row = res.data[0]
            return row["snapshot"], int(row.get("snapshot_version") or 1)

    def fetch_snapshot_version(self, user_id: int) -> int:
        with _operation("fetch_snapshot_version"):
            res = (
                self._db().table("pond_chemistry_state")
                .select("snapshot_version")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            return int(res.data[0].get("snapshot_version") or 1) if res.data else 0

    def save_engine_snapshot(self, user_id: int, snapshot: dict, base_version: Optional[int] = None,
                             ingest: Optional[dict] = None) -> int:
        with _operation("save_engine_snapshot"):
            params = {"p_user_id": user_id, "p_snapshot": snapshot, "p_base_version": base_version}
            if ingest is None:
                res = self._db().rpc("save_pond_snapshot", params).execute()
            else:
                res = self._db().rpc("save_pond_snapshot_with_ingest", {**params, "p_ingest": ingest}).execute()
            if res.data is None:
                raise StaleSnapshotError(user_id, base_version or 0)
            return int(res.data)

    # --- sensor ingestion (migration 0014) ----------------------------
    def fetch_pending_sensor_rows(self, user_id: int, sensor_types: tuple[str, ...], start: Optional[datetime],
                                  until: Optional[datetime], overlap_seconds: int, limit: int) -> dict:
        with _operation("fetch_pending_sensor_rows"):
            res = self._db().rpc("sensor_ingest_pending", {
                "p_pond_id": user_id, "p_sensor_types": list(sensor_types),
                "p_start": start.isoformat() if start is not None else None,
                "p_until": until.isoformat() if until is not None else None,
                "p_overlap_seconds": overlap_seconds, "p_limit": limit,
            }).execute()
            data = res.data
            if isinstance(data, str):
                data = json.loads(data)
            return data or {"cursor": None, "scan_from": None, "rows": []}

    # --- worker lease -------------------------------------------------
    def take_lease(self, name: str, holder: str, ttl_seconds: int) -> bool:
        with _operation("take_lease"):
            res = self._db().rpc(
                "take_worker_lease", {"p_name": name, "p_holder": holder, "p_ttl_seconds": ttl_seconds}
            ).execute()
            return bool(res.data)

    def release_lease(self, name: str, holder: str) -> None:
        with _operation("release_lease"):
            self._db().table("worker_lease").delete().eq("name", name).eq("holder", holder).execute()

    def fetch_lease(self, name: str) -> Optional[dict]:
        with _operation("fetch_lease"):
            res = (
                self._db().table("worker_lease")
                .select("name, holder, expires_at")
                .eq("name", name)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

    # --- worker status (migration 0003) -------------------------------
    def record_worker_status(self, name: str, status: dict) -> None:
        with _operation("record_worker_status"):
            self._db().table("worker_status").upsert({
                "name": name,
                "holder": status["holder"],
                "cycle_started_at": status["cycle_started_at"],
                "cycle_finished_at": status["cycle_finished_at"],
                "cycle_duration_sec": status["cycle_duration_sec"],
                "last_success_at": status.get("last_success_at"),
                "ponds": status.get("ponds") or {},
            }, on_conflict="name").execute()

    def fetch_worker_status(self, name: str) -> Optional[dict]:
        with _operation("fetch_worker_status"):
            res = (
                self._db().table("worker_status")
                .select("name, holder, cycle_started_at, cycle_finished_at, cycle_duration_sec, "
                        "last_success_at, ponds")
                .eq("name", name)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

    def fetch_snapshot_times(self) -> dict[str, str]:
        with _operation("fetch_snapshot_times"):
            res = self._db().table("pond_chemistry_state").select("user_id, updated_at").execute()
            return {str(r["user_id"]): r["updated_at"] for r in res.data or [] if r.get("updated_at")}

    # --- evaluation logs (append-only time series) ---------------------
    def push_evaluation(self, user_id: int, assessment: dict) -> None:
        with _operation("push_evaluation"):
            self._db().table("pond_chemistry_evaluations").insert(
                {
                    "userid": user_id,
                    "status": assessment["status"],
                    "category": assessment["category"],
                    "tan_ppm": assessment["tan_ppm"],
                    "no2_ppm": assessment["no2_ppm"],
                    "no3_ppm": assessment["no3_ppm"],
                    "ph_reactivity": assessment["ph_reactivity"],
                    "reactivity_trend": assessment["reactivity_trend"],
                    "tds_trend": assessment["tds_trend"],
                    "sensor_warnings": assessment["sensor_warnings"],
                    "advisory": assessment["advisory"],
                    "add_hardener_now": assessment["add_hardener_now"],
                }
            ).execute()

    def push_evaporation_evaluation(self, user_id: int, assessment: dict) -> None:
        with _operation("push_evaporation_evaluation"):
            self._db().table("pond_evaporation_evaluations").insert(
                {
                    "userid": user_id,
                    "status": assessment["status"],
                    "category": assessment["category"],
                    "loss_litres": assessment["loss_litres"],
                    "loss_pct": assessment["loss_pct"],
                    "evaporation_mm_per_day": assessment["evaporation_mm_per_day"],
                    "loss_litres_per_day": assessment["loss_litres_per_day"],
                    "water_temp_c": assessment["water_temp_c"],
                    "feed_cap_grams": assessment["feed_cap_grams"],
                    "feed_note": assessment["feed_note"],
                    "days_to_topup": assessment["days_to_topup"],
                    "advisory": assessment["advisory"],
                    "topup_now": assessment["topup_now"],
                }
            ).execute()

    def push_algae_evaluation(self, user_id: int, assessment: dict) -> None:
        with _operation("push_algae_evaluation"):
            self._db().table("pond_algae_evaluations").insert(
                {
                    "userid": user_id,
                    "status": assessment["status"],
                    "category": assessment["category"],
                    "green_ratio": assessment["green_ratio"],
                    "watch_threshold": assessment["watch_threshold"],
                    "action_threshold": assessment["action_threshold"],
                    "threshold_mode": assessment["threshold_mode"],
                    "growth_rate_per_day": assessment["growth_rate_per_day"],
                    "intrinsic_rate_per_day": assessment["intrinsic_rate_per_day"],
                    "rate_source": assessment["rate_source"],
                    "confidence": assessment["confidence"],
                    "sample_count": assessment["sample_count"],
                    "days_to_scrub": assessment["days_to_scrub"],
                    "advisory": assessment["advisory"],
                    "scrub_now": assessment["scrub_now"],
                }
            ).execute()

    def fetch_latest_evaluation(self, user_id: int) -> Optional[dict]:
        with _operation("fetch_latest_evaluation"):
            res = (
                self._db().table("pond_chemistry_evaluations")
                .select("*")
                .eq("userid", user_id)
                .order("evaluated_at", desc=True)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

    def fetch_latest_evaporation_evaluation(self, user_id: int) -> Optional[dict]:
        with _operation("fetch_latest_evaporation_evaluation"):
            res = (
                self._db().table("pond_evaporation_evaluations")
                .select("*")
                .eq("userid", user_id)
                .order("evaluated_at", desc=True)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

    def fetch_latest_algae_evaluation(self, user_id: int) -> Optional[dict]:
        with _operation("fetch_latest_algae_evaluation"):
            res = (
                self._db().table("pond_algae_evaluations")
                .select("*")
                .eq("userid", user_id)
                .order("evaluated_at", desc=True)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

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
        """Persists one human severity rating and returns the inserted row.

        green_ratio_at_rating is denormalised deliberately: it is the other
        half of the (label, measurement) pair that makes calibration
        possible, and keeping it here means the calibration set survives
        imageTable being pruned by the retention policy."""
        with _operation("insert_algae_rating"):
            res = (
                self._db().table("algae_severity_ratings")
                .insert({
                    "userid": user_id,
                    "image_id": image_id,
                    "image_url": image_url,
                    "severity": severity,
                    "is_obstructed": is_obstructed,
                    "green_ratio_at_rating": green_ratio_at_rating,
                    "image_captured_at": image_captured_at,
                    "notes": notes,
                })
                .execute()
            )
            return res.data[0] if res.data else None

    def fetch_algae_ratings(self, user_id: int, limit: int = 200) -> list[dict]:
        """The newest `limit` ratings, oldest first - the order the engine
        rehydrates its calibration set in."""
        with _operation("fetch_algae_ratings"):
            res = (
                self._db().table("algae_severity_ratings")
                .select("*")
                .eq("userid", user_id)
                .order("rated_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = list(res.data or [])
            rows.reverse()
            return rows

    def delete_algae_rating(self, user_id: int, rating_id: int) -> None:
        with _operation("delete_algae_rating"):
            self._db().table("algae_severity_ratings").delete().eq(
                "id", rating_id
            ).eq("userid", user_id).execute()

    # --- camera frames ------------------------------------------------
    def fetch_image_by_id(self, user_id: int, image_id: int) -> Optional[dict]:
        with _operation("fetch_image_by_id"):
            res = (
                self._db().table("imageTable")
                .select(", ".join(IMAGE_COLUMNS))
                .eq("user_ID", user_id)
                .eq("id", image_id)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

    def fetch_image_history(self, user_id: int | str, limit: int = 200) -> list[dict]:
        """imageTable rows for the HSV green-ratio series, newest first
        (algae_engine.parse_image_rows re-sorts to oldest first)."""
        with _operation("fetch_image_history"):
            res = (
                self._db().table("imageTable")
                .select(", ".join(IMAGE_COLUMNS))
                .eq("user_ID", user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return list(res.data or [])

    def insert_image(self, user_id: int | str, green_ratio: float, current_state: Any, image_url: str,
                     mask_version: Optional[int] = None, baseline_reset: Optional[str] = None,
                     quality: Optional[dict] = None, thumbnail_path: Optional[str] = None) -> None:
        with _operation("insert_image"):
            row = {
                "user_ID": user_id,
                "green_ratio": green_ratio,
                "current_state": current_state,
                "imageURL": image_url,
                "mask_version": mask_version,
                "baseline_reset": baseline_reset,
                "quality": quality,
                "thumbnail_path": thumbnail_path,
            }
            self._db().table("imageTable").insert({k: v for k, v in row.items() if v is not None}).execute()

    def upload_image(self, bucket: str, path: str, data: bytes) -> str:
        """Stores a JPEG and returns its public URL."""
        with _operation("upload_image"):
            files = self._db().storage.from_(bucket)
            files.upload(path=path, file=data, file_options={"content-type": "image/jpeg", "upsert": "true"})
            # supabase-py has historically appended a bare "?" here, which
            # makes cache keys inconsistent downstream. Strip it at the source.
            return str(files.get_public_url(path)).rstrip("?&")

    def delete_images(self, bucket: str, paths: list[str]) -> None:
        with _operation("delete_images"):
            self._db().storage.from_(bucket).remove(paths)

    # --- camera water mask --------------------------------------------
    def fetch_camera_mask(self, user_id: int | str) -> Optional[dict]:
        with _operation("fetch_camera_mask"):
            res = (
                self._db().table("camera_config")
                .select(", ".join(CAMERA_CONFIG_COLUMNS))
                .eq("pond_id", user_id)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

    def fetch_camera_mask_versions(self, user_id: int | str) -> list[dict]:
        with _operation("fetch_camera_mask_versions"):
            res = (
                self._db().table("camera_mask_version")
                .select(", ".join(CAMERA_MASK_VERSION_COLUMNS))
                .eq("pond_id", user_id)
                .order("mask_version")
                .execute()
            )
            return list(res.data or [])

    def save_camera_mask(self, user_id: int, mask: list) -> dict:
        with _operation("save_camera_mask"):
            res = self._db().rpc("save_camera_mask", {"p_pond_id": user_id, "p_mask": mask}).execute()
            return res.data[0]

    # --- pond config --------------------------------------------------
    def fetch_active_pond_configs(self) -> list[dict]:
        """Every pond that has finished onboarding (volume and biomass
        set); used by the poller to know which users to poll."""
        with _operation("fetch_active_pond_configs"):
            res = self._db().table("UserData").select("userID, volume, biomass").execute()
            configs = []
            for row in res.data or []:
                config = pond_config_from_userdata_row(row)
                if config is not None:
                    config["user_id"] = row["userID"]
                    configs.append(config)
            return configs

    def fetch_pond_config(self, user_id: int) -> Optional[dict]:
        """Used by event endpoints to bootstrap an engine for a user who
        hasn't had a poll cycle run yet."""
        with _operation("fetch_pond_config"):
            res = (
                self._db().table("UserData")
                .select("volume, biomass")
                .eq("userID", user_id)
                .limit(1)
                .execute()
            )
            return pond_config_from_userdata_row(res.data[0]) if res.data else None

    # --- pond profile -------------------------------------------------
    def fetch_pond_profiles(self, user_id: int) -> list[dict]:
        with _operation("fetch_pond_profiles"):
            res = (
                self._db().table("pond_profile")
                .select(", ".join(PROFILE_COLUMNS))
                .eq("pond_id", user_id)
                .order("effective_from")
                .order("id")
                .execute()
            )
            return list(res.data or [])

    def insert_pond_profile(self, user_id: int, profile: dict) -> dict:
        with _operation("insert_pond_profile"):
            row = {k: v for k, v in profile.items() if k in PROFILE_COLUMNS and k not in ("id", "created_at")}
            row.update(pond_id=user_id, source="api")
            try:
                res = self._db().table("pond_profile").insert(row).execute()
            except Exception as exc:
                if getattr(exc, "code", None) == "23505":  # unique (pond_id, effective_from)
                    raise DuplicateProfileError(user_id, str(row.get("effective_from"))) from exc
                raise
            return res.data[0]

    # --- account links ------------------------------------------------
    def fetch_pond_id_for_account(self, auth_uid: str) -> Optional[int]:
        with _operation("fetch_pond_id_for_account"):
            res = (
                self._db().table("UserData")
                .select("userID")
                .eq("auth_uid", auth_uid)
                .limit(1)
                .execute()
            )
            return int(res.data[0]["userID"]) if res.data else None

    def is_session_active(self, session_id: str, auth_uid: str) -> bool:
        with _operation("is_session_active"):
            res = self._db().rpc(
                "auth_session_active", {"p_session_id": session_id, "p_user_id": auth_uid}
            ).execute()
            return res.data is True

    # --- sensors, weather and interventions ---------------------------
    def fetch_dashboard_payload(self, user_id: int) -> Optional[dict]:
        """The get_bundled_dashboard_payload RPC: latest sensor readings,
        NEA telemetry and NEA forecasts for one pond."""
        with _operation("fetch_dashboard_payload"):
            res = self._db().rpc("get_bundled_dashboard_payload", {"p_user_id": user_id}).execute()
            return res.data

    def fetch_dashboard_sources(self, user_id: int) -> dict:
        """The pond_dashboard_sources RPC (migration 0012): the rows behind
        the /v1 dashboard, uninterpreted."""
        with _operation("fetch_dashboard_sources"):
            res = self._db().rpc("pond_dashboard_sources", {"p_pond_id": user_id}).execute()
            return res.data or {}

    def fetch_recent_feeding_events(self, user_id: int, limit: int = 30) -> list[dict]:
        """Recent FEEDING interventions, newest first, with the timestamp
        the TAN-rate estimator needs to turn them into mg/day."""
        with _operation("fetch_recent_feeding_events"):
            res = (
                self._db().table("pondInterventions")
                .select("food_grams, protein_percentage, event_timestamp")
                .eq("userID", user_id)
                .eq("event_type", "FEEDING")
                .order("event_timestamp", desc=True)
                .limit(limit)
                .execute()
            )
            return list(res.data or [])

    def fetch_daily_sensor_stats(self, user_id: int, sensor_type: str, days: int = 14) -> Optional[dict]:
        """Rolling baseline over the trailing `days` daily_sensor_averages
        rows; see base.daily_stats."""
        with _operation("fetch_daily_sensor_stats"):
            return daily_stats(self._daily_rows(user_id, sensor_type, days))

    def fetch_daily_sensor_series(self, user_id: int, sensor_type: str, days: int = 30) -> list[dict]:
        """The trailing `days` daily rows, oldest first, so slopes come out
        with the right sign."""
        with _operation("fetch_daily_sensor_series"):
            rows = self._daily_rows(user_id, sensor_type, days)
            rows.reverse()
            return rows

    def _daily_rows(self, user_id: int, sensor_type: str, days: int) -> list[dict]:
        res = (
            self._db().table("daily_sensor_averages")
            .select(DAILY_COLUMNS)
            .eq("userid", user_id)
            .eq("sensor_type", sensor_type)
            .order("record_date", desc=True)
            .limit(days)
            .execute()
        )
        return list(res.data or [])

    # --- weather history ----------------------------------------------
    def ingest_weather_batch(self, batch: dict) -> dict:
        """One call to ingest_weather_batch (migration 0009), which
        deduplicates, guards the caches and updates the station lookup in
        one transaction."""
        with _operation("ingest_weather_batch"):
            res = self._db().rpc("ingest_weather_batch", {"p_batch": batch}).execute()
            return dict(res.data or {})

    def fetch_weather_windows(self, product: str) -> list[dict]:
        with _operation("fetch_weather_windows"):
            res = (
                self._db().table("weather_ingest_window")
                .select(WINDOW_COLUMNS)
                .eq("product", product)
                .execute()
            )
            return list(res.data or [])

    def record_weather_window(self, window: dict) -> None:
        with _operation("record_weather_window"):
            row = {**window, "updated_at": datetime.now(timezone.utc).isoformat()}
            self._db().table("weather_ingest_window").upsert(row, on_conflict="product,window_start").execute()

    def fetch_assigned_weather_slots(self) -> list[dict]:
        """ClosestStations is a json column; PostgREST returns it parsed,
        a text-encoded value is parsed here."""
        with _operation("fetch_assigned_weather_slots"):
            res = self._db().table("UserData").select("ClosestStations").execute()
            slots = []
            for row in res.data or []:
                value = row.get("ClosestStations")
                if isinstance(value, str):
                    value = json.loads(value)
                if isinstance(value, dict):
                    slots.append(value)
            return slots

    def fetch_forecast_as_of(self, product: str, slot_id: str, as_of: datetime,
                             valid_at: Optional[datetime] = None) -> Optional[dict]:
        with _operation("fetch_forecast_as_of"):
            res = self._db().rpc("weather_forecast_as_of", {
                "p_product": product, "p_slot_id": slot_id, "p_as_of": as_of.isoformat(),
                "p_valid_at": valid_at.isoformat() if valid_at else None}).execute()
            rows = list(res.data or [])
            return rows[0] if rows else None

    def fetch_weather_observations(self, station_id: str, metric: str, start: datetime, end: datetime,
                                   as_of: datetime) -> list[dict]:
        with _operation("fetch_weather_observations"):
            res = self._db().rpc("weather_observations_as_of", {
                "p_station_id": station_id, "p_metric": metric, "p_from": start.isoformat(),
                "p_to": end.isoformat(), "p_as_of": as_of.isoformat()}).execute()
            return list(res.data or [])

    def fetch_rainfall_total(self, station_id: str, start: datetime, end: datetime,
                             as_of: Optional[datetime] = None) -> dict:
        with _operation("fetch_rainfall_total"):
            res = self._db().rpc("weather_rainfall_total", {
                "p_station_id": station_id, "p_from": start.isoformat(), "p_to": end.isoformat(),
                "p_as_of": as_of.isoformat() if as_of else None}).execute()
            return dict(res.data or {})
