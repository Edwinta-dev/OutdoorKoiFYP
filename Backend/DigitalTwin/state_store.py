"""
state_store.py

All Supabase I/O lives here - nothing else in the app talks to Supabase
directly. Two responsibilities:

1. Durability: persist/restore the WaterChemistryEngine's internal snapshot
   so in-memory state survives a process restart. Requires a table that
   is NOT in the original schema - see schema_additions.sql. Pond volume
   and biomass config comes from the existing `UserData` table (populated
   at onboarding); there is no separate pond_configs table.

2. Push computed evaluations to `pond_chemistry_evaluations` for the
   Flutter UI to read directly from Supabase (no Flask round-trip needed
   for reads).

Uses the SERVICE ROLE key because this is trusted server-side code writing
on behalf of users - never ship the service role key to Flutter/the client.
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Same env var names as Backend/DataGovAPI/data.py and Backend/SensorAPI/camera/camera.py
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICEROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def load_engine_snapshot(user_id: int) -> dict | None:
    res = (
        supabase.table("pond_chemistry_state")
        .select("snapshot")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["snapshot"]
    return None


def save_engine_snapshot(user_id: int, snapshot: dict) -> None:
    supabase.table("pond_chemistry_state").upsert(
        {
            "user_id": user_id,
            "snapshot": snapshot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id",
    ).execute()


def push_evaluation(user_id: int, assessment_dict: dict) -> None:
    """Appends a new row - this is a time-series log, not a single 'latest'
    record, so no upsert/on_conflict here. See the schema critique for why
    the original UNIQUE(userID, evaluated_at) constraint should be dropped."""
    supabase.table("pond_chemistry_evaluations").insert(
        {
            "userid": user_id,
            "status": assessment_dict["status"],
            "category": assessment_dict["category"],
            "tan_ppm": assessment_dict["tan_ppm"],
            "no2_ppm": assessment_dict["no2_ppm"],
            "no3_ppm": assessment_dict["no3_ppm"],
            "ph_reactivity": assessment_dict["ph_reactivity"],
            "reactivity_trend": assessment_dict["reactivity_trend"],
            "tds_trend": assessment_dict["tds_trend"],
            "sensor_warnings": assessment_dict["sensor_warnings"],
            "advisory": assessment_dict["advisory"],
            "add_hardener_now": assessment_dict["add_hardener_now"],
        }
    ).execute()


_BIOMASS_KG_TO_GRAMS = 1000.0


def _pond_config_from_userdata_row(row: dict) -> dict | None:
    """UserData is populated at onboarding (see onboarding_screen.dart) and
    only has volume (litres) + biomass (KG, aggregated across all fish
    entries) - not fish_type/fish_count, which live in the phone's
    SharedPreferences and never get synced server-side. Callers that need
    those two fields (app.py's event endpoints) fill them in from the
    request body; the poller falls back to PondConfig's own defaults."""
    if row.get("volume") is None or row.get("biomass") is None:
        return None
    return {
        "volume_litres": float(row["volume"]),
        "estimated_biomass_grams": float(row["biomass"]) * _BIOMASS_KG_TO_GRAMS,
    }


def fetch_active_pond_configs() -> list[dict]:
    """Used by the poller to know which users to poll each cycle."""
    res = supabase.table("UserData").select("userID, volume, biomass").execute()
    print(f"Fetched {len(res.data or [])} active pond configs from Supabase")
    print(res.data)
    configs = []
    for row in res.data or []:
        config = _pond_config_from_userdata_row(row)
        if config is None:
            continue  # onboarding incomplete (no volume/biomass yet) - skip until it is
        config["user_id"] = row["userID"]
        configs.append(config)
    return configs


def fetch_pond_config(user_id: int) -> dict | None:
    """Used by event endpoints to bootstrap an engine for a user who hasn't
    had a poll cycle run yet - e.g. they configure their pond and log a
    feeding event before the next 15-minute poll tick."""
    res = (
        supabase.table("UserData")
        .select("volume, biomass")
        .eq("userID", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return _pond_config_from_userdata_row(res.data[0])


def fetch_latest_evaluation(user_id: int) -> dict | None:
    """Used by GET /assessment/<user_id> so Flutter can pull the most recent
    computed evaluation on demand (e.g. when the detail graph screen opens),
    rather than only ever seeing evaluations pushed from event endpoints."""
    res = (
        supabase.table("pond_chemistry_evaluations")
        .select("*")
        .eq("userid", user_id)
        .order("evaluated_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def fetch_dashboard_payload(user_id: int) -> dict:
    """Calls the existing get_bundled_dashboard_payload RPC for one user."""
    res = supabase.rpc("get_bundled_dashboard_payload", {"p_user_id": user_id}).execute()
    return res.data
