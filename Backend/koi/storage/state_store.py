"""
state_store.py

All Supabase I/O lives here - nothing else in the app talks to Supabase
directly. Responsibilities:

1. Durability: persist/restore the WaterChemistryEngine's internal snapshot
   so in-memory state survives a process restart. Requires a table that
   is defined in supabase/migrations/0001_baseline.sql. Pond volume
   and biomass config comes from the existing `UserData` table (populated
   at onboarding); there is no separate pond_configs table.

2. Push computed evaluations to `pond_chemistry_evaluations` for the
   Flutter UI to read directly from Supabase (no Flask round-trip needed
   for reads).

3. Feed data for /forecast/<user_id> (see app.py + engine.py's
   project_forward): recent feeding history from `pondInterventions` to
   estimate an average daily TAN input rate, and rolling daily sensor
   baselines from `daily_sensor_averages` to use as a fallback environment
   assumption beyond the NEA forecast horizon.

Uses the SERVICE ROLE key because this is trusted server-side code writing
on behalf of users - never ship the service role key to Flutter/the client.

Schema for #3 (confirmed against the live tables): `pondInterventions` has
userID, event_type, food_grams, protein_percentage, event_timestamp;
`daily_sensor_averages` has userid, sensor_type, avg_value, min_value,
max_value, record_date. `pond_chemistry_evaluations` (see #2) has no
risk_score column - WaterChemistryAssessment now carries risk_score for
the JSON responses, but push_evaluation below intentionally omits it from
the insert rather than erroring on an unknown column; add a migration
first if you want it in the historical log too.
"""
from datetime import datetime, timezone

from koi.storage.client import get_client as _db


def load_engine_snapshot(user_id: int) -> dict | None:
    res = (
        _db().table("pond_chemistry_state")
        .select("snapshot")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["snapshot"]
    return None


def save_engine_snapshot(user_id: int, snapshot: dict) -> None:
    _db().table("pond_chemistry_state").upsert(
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
    the original UNIQUE(userID, evaluated_at) constraint should be dropped.

    NOTE: pond_chemistry_evaluations has no risk_score column in the real
    schema (confirmed against the live table) - assessment_dict still
    carries risk_score (added to WaterChemistryAssessment for the
    /forecast and event-endpoint JSON responses), it's just not persisted
    here. Add a risk_score column via migration first if you want it in
    the historical log too; until then this intentionally drops it rather
    than erroring on an unknown column."""
    _db().table("pond_chemistry_evaluations").insert(
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


def push_evaporation_evaluation(user_id: int, assessment: dict) -> None:
    """Appends an evaporation evaluation row.

    FAIL-SOFT BY DESIGN: pond_evaporation_evaluations does not exist until
    supabase/migrations/0001_baseline.sql is applied. A missing table must not take down the
    poll cycle for a user whose chemistry evaluation wrote fine, so the
    error is logged once per call and swallowed. Everything still works -
    the engine state is snapshotted regardless, and /forecast/evaporation
    computes live from that state - you just lose the cached history until
    the migration lands.
    """
    try:
        _db().table("pond_evaporation_evaluations").insert(
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
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] push_evaporation_evaluation skipped: {exc}")


def push_algae_evaluation(user_id: int, assessment: dict) -> None:
    """Appends an algae evaluation row. Fail-soft for the same reason as
    push_evaporation_evaluation above."""
    try:
        _db().table("pond_algae_evaluations").insert(
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
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] push_algae_evaluation skipped: {exc}")


def fetch_latest_evaporation_evaluation(user_id: int) -> dict | None:
    try:
        res = (
            _db().table("pond_evaporation_evaluations")
            .select("*")
            .eq("userid", user_id)
            .order("evaluated_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] fetch_latest_evaporation_evaluation failed: {exc}")
        return None


def fetch_latest_algae_evaluation(user_id: int) -> dict | None:
    try:
        res = (
            _db().table("pond_algae_evaluations")
            .select("*")
            .eq("userid", user_id)
            .order("evaluated_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] fetch_latest_algae_evaluation failed: {exc}")
        return None


def insert_algae_rating(
    user_id: int,
    severity: str,
    is_obstructed: bool,
    image_id: int | None,
    image_url: str | None,
    green_ratio_at_rating: float | None,
    image_captured_at: str | None = None,
    notes: str | None = None,
) -> dict | None:
    """Persists one human severity rating and returns the inserted row.

    green_ratio_at_rating is denormalised deliberately: it is the other
    half of the (label, measurement) pair that makes calibration possible,
    and keeping it here means the calibration set survives imageTable
    being pruned by the retention policy.
    """
    try:
        res = (
            _db().table("algae_severity_ratings")
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
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] insert_algae_rating failed: {exc}")
        return None


def fetch_algae_ratings(user_id: int, limit: int = 200) -> list[dict]:
    """Oldest-first rating history, used to rehydrate the engine's
    calibration set. Fail-soft: a missing table means no calibration, not
    a broken algae domain."""
    try:
        res = (
            _db().table("algae_severity_ratings")
            .select("*")
            .eq("userid", user_id)
            .order("rated_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = res.data or []
        rows.reverse()
        return rows
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] fetch_algae_ratings unavailable: {exc}")
        return []


def fetch_latest_algae_rating(user_id: int) -> dict | None:
    """The most recent rating - shown on the rating card so the user can
    anchor today's judgement against what they called it last time."""
    rows = fetch_algae_ratings(user_id, limit=1)
    return rows[-1] if rows else None


def delete_algae_rating(user_id: int, rating_id: int) -> bool:
    try:
        _db().table("algae_severity_ratings").delete().eq(
            "id", rating_id
        ).eq("userid", user_id).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] delete_algae_rating failed: {exc}")
        return False


def fetch_image_by_id(user_id: int, image_id: int) -> dict | None:
    try:
        res = (
            _db().table("imageTable")
            .select("id, created_at, green_ratio, current_state, imageURL")
            .eq("user_ID", user_id)
            .eq("id", image_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:  # noqa: BLE001
        print(f"[state_store] fetch_image_by_id failed: {exc}")
        return None


def fetch_image_history_since(user_id: int, since: datetime | None, limit: int = 100) -> list[dict]:
    """Camera frames newer than `since`. The algae engine keeps its own
    watermark and discards duplicates anyway, but filtering server-side
    keeps the payload small once imageTable has months of frames in it."""
    q = (
        _db().table("imageTable")
        .select("id, created_at, green_ratio, current_state, imageURL")
        .eq("user_ID", user_id)
    )
    if since is not None:
        q = q.gt("created_at", since.isoformat())
    res = q.order("created_at", desc=True).limit(limit).execute()
    return res.data or []


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
    res = _db().table("UserData").select("userID, volume, biomass").execute()
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
        _db().table("UserData")
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
        _db().table("pond_chemistry_evaluations")
        .select("*")
        .eq("userid", user_id)
        .order("evaluated_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def fetch_dashboard_payload(user_id: int) -> dict:
    """Calls the existing get_bundled_dashboard_payload RPC for one user."""
    res = _db().rpc("get_bundled_dashboard_payload", {"p_user_id": user_id}).execute()
    return res.data


def fetch_recent_feeding_events(user_id: int, limit: int = 30) -> list[dict]:
    """Used by GET /forecast/<user_id> to estimate an average daily TAN
    input rate (see WaterChemistryEngine.estimate_avg_daily_tan_mg).
    Mirrors:

        select food_grams, protein_percentage
        from "pondInterventions"
        where "userID" = :user_id and event_type = 'FEEDING'
        order by event_timestamp desc
        limit :limit

    plus event_timestamp, which the estimator needs to turn a bag of
    recent feedings into a *rate* (mg/day), not just a total."""
    res = (
        _db().table("pondInterventions")
        .select("food_grams, protein_percentage, event_timestamp")
        .eq("userID", user_id)
        .eq("event_type", "FEEDING")
        .order("event_timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def fetch_daily_sensor_stats(user_id: int, sensor_type: str, days: int = 14) -> dict | None:
    """Used by GET /forecast/<user_id> as the fallback temp/lux baseline
    for projection days that fall outside the NEA 4-day outlook window
    (and as the sole baseline for lux, which NEA doesn't forecast at all).

    Reads the trailing `days` rows from daily_sensor_averages for this
    user + sensor_type (e.g. 'temp', 'lux') and averages avg_value across
    them - a rolling recent baseline rather than a single day, so one
    unusually hot/cold or bright/dim day doesn't skew the whole horizon.

    Returns None if there's no history yet for this sensor_type (e.g. a
    newly onboarded pond that hasn't had 15-minute polls accumulate daily
    aggregates), so callers know to surface "not enough history" rather
    than silently projecting with a fabricated baseline.
    """
    res = (
        _db().table("daily_sensor_averages")
        .select("avg_value, min_value, max_value, record_date")
        .eq("userid", user_id)
        .eq("sensor_type", sensor_type)
        .order("record_date", desc=True)
        .limit(days)
        .execute()
    )
    rows = res.data or []
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


def fetch_daily_sensor_series(user_id: int, sensor_type: str, days: int = 30) -> list[dict]:
    """Ordered oldest-first daily series for one sensor_type. Used by the
    evaporation lookahead to fit this pond's water-vs-air temperature
    offset, and to measure the observed TDS slope for the grounding
    cross-check against the physical evaporation model."""
    res = (
        _db().table("daily_sensor_averages")
        .select("avg_value, min_value, max_value, record_date")
        .eq("userid", user_id)
        .eq("sensor_type", sensor_type)
        .order("record_date", desc=True)
        .limit(days)
        .execute()
    )
    rows = res.data or []
    rows.reverse()  # oldest first, so slopes come out with the right sign
    return rows


def fetch_recent_interventions(user_id: int, event_type: str, limit: int = 30) -> list[dict]:
    """Recent rows of one intervention type, newest first.

    event_type values in the live table are upper snake case:
    'FEEDING', 'WATER_CHANGE', 'WATER_TOPUP', 'ALGAE_SCRUB'. Note these do
    NOT match engine.py's EventKind values ('feeding', 'water_change',
    'top_up', 'algal_scrub') - the DB is the source of truth for reads,
    and EventKind is the internal representation. Callers reading from
    here must use the DB spelling.
    """
    res = (
        _db().table("pondInterventions")
        .select(
            "id, event_type, volume_percentage, volume_litres, food_grams, "
            "protein_percentage, algae_method, event_timestamp"
        )
        .eq("userID", user_id)
        .eq("event_type", event_type)
        .order("event_timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def fetch_last_intervention_time(user_id: int, event_type: str) -> datetime | None:
    """Timestamp of the most recent intervention of a given type, or None.

    The evaporation lookahead uses the last WATER_TOPUP to know how much
    loss has already accrued; the algae lookahead uses the last
    ALGAE_SCRUB to understand why green_ratio may have stepped down.
    """
    rows = fetch_recent_interventions(user_id, event_type, limit=1)
    if not rows:
        return None
    ts = rows[0].get("event_timestamp")
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def fetch_image_history(user_id: int, limit: int = 200) -> list[dict]:
    """Raw `imageTable` rows for the ESP32-CAM HSV green-ratio series,
    newest first (algae_engine.parse_image_rows re-sorts to oldest first).

    IMPORTANT - the column is `user_ID` here, not `userID` as in
    pondInterventions, and not `userid` as in daily_sensor_averages. All
    three spellings genuinely coexist in this schema; see the write-up.
    """
    res = (
        _db().table("imageTable")
        .select("id, created_at, green_ratio, current_state, imageURL")
        .eq("user_ID", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def slope_per_day(rows: list[dict], value_key: str = "avg_value") -> float | None:
    """Least-squares slope in units-per-day over a daily series from
    fetch_daily_sensor_series. Returns None with fewer than 3 points.

    Uses real elapsed days from record_date rather than assuming the rows
    are contiguous - daily_sensor_averages will have gaps whenever the
    sensor was offline, and treating a gap as one day would inflate the
    slope.
    """
    points = []
    for r in rows:
        v = r.get(value_key)
        d = r.get("record_date")
        if v is None or d is None:
            continue
        if isinstance(d, datetime):
            dt = d
        else:
            try:
                dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
            except ValueError:
                continue
        points.append((dt, float(v)))

    if len(points) < 3:
        return None

    points.sort(key=lambda p: p[0])
    t0 = points[0][0]
    xs = [(p[0] - t0).total_seconds() / 86400.0 for p in points]
    ys = [p[1] for p in points]

    if xs[-1] - xs[0] < 1.0:
        return None

    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    return None if den == 0 else num / den