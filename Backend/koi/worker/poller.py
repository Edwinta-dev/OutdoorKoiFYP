"""
koi.worker.poller

Polls get_bundled_dashboard_payload for each active pond every
Settings.poll_interval_minutes and drives ALL THREE domain engines
forward from what it finds. Runs on its own with `python -m koi.worker`,
or on a background thread inside the API process when KOI_ENV=development
(see koi/api/__main__.py).

--------------------------------------------------------------------
WHY ALL THREE, NOT JUST CHEMISTRY
--------------------------------------------------------------------
Evaporation and algae are both driven by the environment, not just by
user actions:

  * Evaporation is almost entirely weather. A hot dry windy week loses
    materially more water than a monsoon week, and the only way to
    capture that is to integrate loss against conditions as they actually
    occur. Recomputing from a timestamp at request time throws that away.
  * Algae growth tracks light, temperature and nitrate continuously,
    while the camera that measures it sleeps for hours between frames
    (imageSchedule's base schedule is six fixed slots a day). Without a
    model running in between, the dashboard would show a stale number
    most of the time and could not answer "when will this cross the
    line" at all.

So a poll cycle is a genuine state advancement for every engine, and
each pushes a fresh evaluation row. The UI then reads cached assessments
instead of triggering recomputes.

Deliberately NOT using multiple scheduler threads per user - APScheduler's
single BackgroundScheduler with one job per tick, looping over users
sequentially, keeps this simple and avoids reasoning about many
concurrent poll threads on top of the request threads already covered by
registry.py's per-user locks.
"""
from datetime import datetime, timezone
from typing import Optional

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models import forecast_utils
from koi.models.engine import PondConfig, RawSample, WaterChemistryEngine
from koi.registry import registry
from koi.settings import Settings, get_settings
from koi.storage import state_store

# How far ahead each cycle projects when writing the cached "days until
# next intervention" figures into the evaluation rows. Kept modest - the
# detail screens ask for a longer horizon on demand via /forecast/*.
CACHED_HORIZON_DAYS = 21


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

    now = datetime.now(timezone.utc)
    raw = payload["raw_sensor"]

    # --- sensor sample for the chemistry engine -----------------
    try:
        sample = RawSample(
            time=now,
            ph=float(raw["pH"]),
            tds=float(raw["TDS"]),
            temp_c=float(raw["temp"]),
            lux=float(raw["LUX"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"[poller] user {user_id}: unusable raw_sensor ({exc}), skipping")
        return

    # fish_type/fish_count aren't in UserData (they live in the phone's
    # SharedPreferences, which the poller has no way to read) - PondConfig
    # falls back to its own defaults for those two fields here.
    pond_config = PondConfig(
        volume_litres=config_row["volume_litres"],
        estimated_biomass_grams=config_row["estimated_biomass_grams"],
        tap_tds_ppm=config_row.get("tap_tds_ppm", 30.0),
        tap_nitrate_ppm=config_row.get("tap_nitrate_ppm", 0.0),
    )

    # --- environmental context ----------------------------------
    now_conditions = forecast_utils.current_conditions(payload)
    rain_incoming, rain_intensity = forecast_utils.today_rain_context(payload)
    outlook = (payload.get("nea_forecasts") or {}).get("outlook_4day", [])
    forecast_days = forecast_utils.daily_environment_from_outlook(outlook)

    # Live telemetry is the best estimate of conditions over the interval
    # just elapsed; the forecast's first day fills anything telemetry
    # doesn't carry (NEA telemetry has no humidity field of its own).
    first_forecast = forecast_days[0] if forecast_days else {}
    air_temp = _first_not_none(
        now_conditions.get("air_temp_c"), first_forecast.get("air_temp_c"), 30.0
    )
    humidity = _first_not_none(
        now_conditions.get("humidity_pct"), first_forecast.get("humidity_pct"), 80.0
    )
    wind_ms = _first_not_none(
        now_conditions.get("wind_ms"), first_forecast.get("wind_ms"), 2.0
    )
    rainfall_mm = now_conditions.get("rainfall_mm")

    evaporation_env = ev.DayEnvironment(
        air_temp_c=float(air_temp),
        relative_humidity_pct=float(humidity),
        wind_speed_ms=float(wind_ms),
        rain_category=rain_intensity if rain_incoming else "unknown",
        # The live rain gauge reports mm accumulated; prefer it over the
        # coarse forecast-category estimate when present.
        rain_mm_measured=float(rainfall_mm) if rainfall_mm is not None else None,
    )

    # --- camera frames since this twin last looked ---------------
    # Fetched unconditionally but cheap: the engine's own watermark
    # discards anything it has already assimilated, so a sleeping camera
    # costs one small query per cycle and changes nothing.
    camera_samples = []
    try:
        camera_samples = ae.parse_image_rows(
            state_store.fetch_image_history(user_id, limit=50)
        )
    except Exception as exc:  # noqa: BLE001 - camera is optional
        print(f"[poller] user {user_id}: camera history unavailable ({exc})")

    # --- nitrate for the algae nutrient term ---------------------
    # Needs the twin, so it is resolved inside the locked section below.
    feeding_rows = []
    try:
        feeding_rows = state_store.fetch_recent_feeding_events(user_id, limit=30)
    except Exception as exc:  # noqa: BLE001
        print(f"[poller] user {user_id}: feeding history unavailable ({exc})")
    avg_tan = WaterChemistryEngine.estimate_avg_daily_tan_mg(feeding_rows)

    water_temp = sample.temp_c
    lux_now = _first_not_none(now_conditions.get("lux"), raw.get("LUX"), 10000.0)

    def cycle(twin):
        # Keep engine config in step with UserData - a pond resized in
        # onboarding must not keep computing against its old volume.
        twin.sync_config(pond_config)

        # Nitrate trajectory first: the algae engine's growth depends on
        # it, and it is a pure read off the chemistry engine.
        no3_series = []
        if avg_tan is not None:
            no3_series = twin.no3_projection(
                avg_daily_tan_mg=avg_tan,
                fallback_temp_c=float(water_temp),
                horizon_days=CACHED_HORIZON_DAYS,
            )
        no3_now = no3_series[0] if no3_series else None

        algae_env = ae.AlgaeDayEnvironment(
            lux=float(lux_now), temp_c=float(water_temp), no3_ppm=no3_now
        )

        outcome = twin.ingest_environment(
            now=now,
            sample=sample,
            evaporation_env=evaporation_env,
            algae_env=algae_env,
            camera_samples=camera_samples,
            rain_incoming=rain_incoming,
            rain_intensity=rain_intensity,
        )

        # --- forward projections, so the cached rows carry a
        #     "next predicted intervention" the UI can show without
        #     recomputing on every dashboard open ---
        evap_days = None
        algae_days = None

        evap_forecast_env = _evaporation_forecast_env(
            forecast_days, air_temp, humidity, wind_ms
        )
        try:
            evap_proj = twin.evaporation.project_forward(
                daily_environment=evap_forecast_env,
                horizon_days=CACHED_HORIZON_DAYS,
            )
            evap_days = evap_proj.get("predicted_topup_days_from_now")
        except Exception as exc:  # noqa: BLE001
            print(f"[poller] user {user_id}: evaporation projection failed ({exc})")

        if twin.algae.has_measurement:
            algae_forecast_env = _algae_forecast_env(
                forecast_days, float(lux_now), float(water_temp), no3_series, no3_now
            )
            try:
                algae_proj = twin.algae.project_forward(
                    daily_environment=algae_forecast_env,
                    horizon_days=CACHED_HORIZON_DAYS,
                )
                algae_days = algae_proj.get("predicted_scrub_days_from_now")
            except Exception as exc:  # noqa: BLE001
                print(f"[poller] user {user_id}: algae projection failed ({exc})")

        # Re-assess with the projected day counts folded in, so the cached
        # rows carry both current status and next predicted intervention.
        evap_assessment = twin.evaporation.assess(
            current_water_temp_c=water_temp, days_to_topup=evap_days
        )
        algae_assessment = twin.algae.assess(days_to_scrub=algae_days)

        return outcome["chemistry"], evap_assessment, algae_assessment, outcome

    chem, evap, algae, outcome = registry.with_twin(
        user_id, cycle, default_config=pond_config
    )

    # --- push evaluations (outside the lock: Supabase I/O is slow and
    #     the twin is already durably snapshotted by with_twin) ---
    state_store.push_evaluation(user_id, chem.to_dict())
    if evap is not None:
        state_store.push_evaporation_evaluation(user_id, evap.to_dict())
    if algae is not None:
        state_store.push_algae_evaluation(user_id, algae.to_dict())

    frames = outcome.get("assimilated_camera_frames", 0)
    print(
        f"[poller] user {user_id}: chem={chem.status}/{chem.category} "
        f"evap={evap.status if evap else 'n/a'} "
        f"algae={algae.status if algae else 'no-camera'} "
        f"({frames} new camera frame(s))"
    )


# ---------------------------------------------------------------
def _first_not_none(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _evaporation_forecast_env(forecast_days, air_temp, humidity, wind_ms):
    """Builds the forward environment list, substituting live telemetry
    for any field NEA's outlook omits."""
    env = [
        ev.DayEnvironment(
            air_temp_c=_first_not_none(d.get("air_temp_c"), air_temp, 30.0),
            relative_humidity_pct=_first_not_none(d.get("humidity_pct"), humidity, 80.0),
            wind_speed_ms=_first_not_none(d.get("wind_ms"), wind_ms, 2.0),
            rain_category=d.get("rain_category", "unknown"),
        )
        for d in forecast_days
    ]
    if not env:
        env = [
            ev.DayEnvironment(
                air_temp_c=float(air_temp),
                relative_humidity_pct=float(humidity),
                wind_speed_ms=float(wind_ms),
            )
        ]
    return env


def _algae_forecast_env(forecast_days, baseline_lux, baseline_temp, no3_series, no3_now):
    """NEA has no light forecast, so the pond's own current lux is scaled
    by a cloud-cover multiplier derived from each day's forecast code."""
    env = [
        ae.AlgaeDayEnvironment(
            lux=baseline_lux * d.get("lux_multiplier", 0.85),
            temp_c=_first_not_none(d.get("air_temp_c"), baseline_temp),
            no3_ppm=no3_series[i] if i < len(no3_series) else no3_now,
        )
        for i, d in enumerate(forecast_days)
    ]
    if not env:
        env = [
            ae.AlgaeDayEnvironment(
                lux=baseline_lux, temp_c=baseline_temp, no3_ppm=no3_now
            )
        ]
    return env


def start(settings: Optional[Settings] = None, blocking: bool = False):
    """Schedules _poll_once every poll_interval_minutes, first run now.
    blocking=True runs the scheduler in the calling thread (the worker
    process); otherwise it runs on a background thread and this returns
    the scheduler."""
    settings = settings or get_settings()
    if blocking:
        from apscheduler.schedulers.blocking import BlockingScheduler as Scheduler
    else:
        from apscheduler.schedulers.background import BackgroundScheduler as Scheduler
    scheduler = Scheduler()
    scheduler.add_job(
        _poll_once, "interval", minutes=settings.poll_interval_minutes, next_run_time=datetime.now()
    )
    scheduler.start()
    return scheduler
