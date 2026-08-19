"""
poller.py

Runs on a background scheduler INSIDE the same Flask process (not a
separate worker), polling get_bundled_dashboard_payload for each active
pond on a fixed interval, feeding fresh readings into that user's engine,
and pushing a new evaluation to Supabase.

Deliberately NOT using multiple scheduler threads per user - APScheduler's
single BackgroundScheduler with one job per tick, looping over users
sequentially, keeps this simple and avoids needing to reason about many
concurrent poll threads on top of the request threads already covered by
registry.py's per-user locks.
"""
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from engine import RawSample, PondConfig
from registry import registry
import state_store

POLL_INTERVAL_MINUTES = 15


def _rain_context_from_forecast(payload: dict) -> tuple[bool, str]:
    """Pulls a simple rain-incoming flag + intensity out of the NEA forecast
    blob. Looks at the 2hr nowcast first (most reliable near-term signal),
    falls back to today's slot in the 4-day outlook."""
    forecast_2hr = (payload.get("nea_forecasts") or {}).get("forecast_2hr", {})
    text = (forecast_2hr.get("forecast") or "").lower()
    if "thundery" in text or "heavy" in text:
        return True, "heavy"
    if "shower" in text or "rain" in text:
        return True, "moderate"

    outlook = (payload.get("nea_forecasts") or {}).get("outlook_4day", [])
    if outlook:
        today_text = (outlook[0].get("data", {}).get("forecast", {}).get("text") or "").lower()
        if "thundery" in today_text:
            return True, "heavy"
        if "shower" in today_text:
            return True, "moderate"
    return False, "unknown"


def _poll_once():
    configs = state_store.fetch_active_pond_configs()
    for row in configs:
        user_id = row["user_id"]
        try:
            _poll_user(user_id, row)
        except Exception as exc:  # noqa: BLE001 - one user's failure must not kill the poll loop
            print(f"[poller] user {user_id} failed: {exc}")


def _poll_user(user_id: int, config_row: dict):
    payload = state_store.fetch_dashboard_payload(user_id)
    if not payload or "raw_sensor" not in payload:
        print(f"[poller] user {user_id}: no payload / missing raw_sensor, skipping")
        return

    raw = payload["raw_sensor"]
    sample = RawSample(
        time=datetime.now(timezone.utc),
        ph=float(raw["pH"]),
        tds=float(raw["TDS"]),
        temp_c=float(raw["temp"]),
        lux=float(raw["LUX"]),
    )

    # fish_type/fish_count aren't in UserData (they live in the phone's
    # SharedPreferences, which the poller has no way to read) - PondConfig
    # falls back to its own defaults for those two fields here.
    default_config = PondConfig(
        volume_litres=config_row["volume_litres"],
        estimated_biomass_grams=config_row["estimated_biomass_grams"],
        tap_tds_ppm=config_row.get("tap_tds_ppm", 30.0),
        tap_nitrate_ppm=config_row.get("tap_nitrate_ppm", 0.0),
    )

    rain_incoming, rain_intensity = _rain_context_from_forecast(payload)

    def mutate(engine):
        warnings = engine.ingest_sensor_sample(sample)
        assessment = engine.assess(
            rain_incoming=rain_incoming,
            rain_intensity=rain_intensity,
            recent_sensor_warnings=warnings,
        )
        state_store.push_evaluation(user_id, assessment.to_dict())
        return assessment

    registry.with_engine(user_id, mutate, default_config=default_config)


_scheduler = BackgroundScheduler()


def start():
    _scheduler.add_job(_poll_once, "interval", minutes=POLL_INTERVAL_MINUTES, next_run_time=datetime.now())
    _scheduler.start()
