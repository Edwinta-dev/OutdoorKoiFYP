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
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterator, Optional

from koi.settings import Settings
from koi.storage.base import StorageError, daily_stats, pond_config_from_userdata_row

if TYPE_CHECKING:
    from supabase import Client

IMAGE_COLUMNS = "id, created_at, green_ratio, current_state, imageURL"
DAILY_COLUMNS = "avg_value, min_value, max_value, record_date"


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

    def save_engine_snapshot(self, user_id: int, snapshot: dict) -> None:
        with _operation("save_engine_snapshot"):
            self._db().table("pond_chemistry_state").upsert(
                {
                    "user_id": user_id,
                    "snapshot": snapshot,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="user_id",
            ).execute()

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
                .select(IMAGE_COLUMNS)
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
                .select(IMAGE_COLUMNS)
                .eq("user_ID", user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return list(res.data or [])

    def insert_image(self, user_id: int | str, green_ratio: float, current_state: Any, image_url: str) -> None:
        with _operation("insert_image"):
            self._db().table("imageTable").insert({
                "user_ID": user_id,
                "green_ratio": green_ratio,
                "current_state": current_state,
                "imageURL": image_url,
            }).execute()

    def upload_image(self, bucket: str, path: str, data: bytes) -> str:
        """Stores a JPEG and returns its public URL."""
        with _operation("upload_image"):
            files = self._db().storage.from_(bucket)
            files.upload(path=path, file=data, file_options={"content-type": "image/jpeg", "upsert": "true"})
            # supabase-py has historically appended a bare "?" here, which
            # makes cache keys inconsistent downstream. Strip it at the source.
            return str(files.get_public_url(path)).rstrip("?&")

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

    # --- sensors, weather and interventions ---------------------------
    def fetch_dashboard_payload(self, user_id: int) -> Optional[dict]:
        """The get_bundled_dashboard_payload RPC: latest sensor readings,
        NEA telemetry and NEA forecasts for one pond."""
        with _operation("fetch_dashboard_payload"):
            res = self._db().rpc("get_bundled_dashboard_payload", {"p_user_id": user_id}).execute()
            return res.data

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
