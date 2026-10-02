"""
koi.worker.poller

Polls get_bundled_dashboard_payload for each active pond every
Settings.poll_interval_minutes and drives ALL THREE domain engines
forward from what it finds. Runs on its own with `python -m koi.worker`,
or on a background thread inside the API process when KOI_ENV=development
(see koi/api/__main__.py).

--------------------------------------------------------------------
ONE ACTIVE WORKER: THE LEASE
--------------------------------------------------------------------
Each cycle a Worker takes or renews the "poller" row in worker_lease
(Storage.take_lease) before polling anything. A second worker started by
mistake, or kept as a hot spare, finds the lease held, logs that it is on
standby and polls nothing that cycle. The lease lasts two poll intervals,
so it survives the gap between cycles and a standby takes over within
two intervals of the active worker stopping. A worker that shuts down
cleanly releases it at once.

--------------------------------------------------------------------
THE CYCLE REPORT
--------------------------------------------------------------------
After each cycle the active worker writes one worker_status row
(Storage.record_worker_status, migration 0003): when the cycle ran, how
long it took, each pond's result ("ok", "skipped" when there was no
usable sensor reading, "failed" when polling raised), each pond's
failure count since the worker started and the recorded_at of the
sensor reading it used. The API's /ready and /metrics read that row,
because the worker usually runs in another process. last_success_at
moves forward when a cycle completes and either no pond failed or at
least one pond was advanced, so one broken pond does not mark the whole
poller as down; it is listed in the report instead.

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

One APScheduler job per tick runs the cycle; within it, ponds are polled
on a small thread pool (Settings.worker_threads, default 4). Each pond
still goes through registry.with_twin, so one lock per pond and the
snapshot version check apply exactly as they do to API requests.
"""
import logging
import os
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from koi.logs import log_event, pond_context
from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models import forecast_utils
from koi.models.engine import RawSample, WaterChemistryEngine
from koi.models.profile import ProfileHistory, profile_from_userdata_config
from koi.registry import EngineRegistry
from koi.settings import Settings, get_settings
from koi.storage import StorageError, fail_soft

# How far ahead each cycle projects when writing the cached "days until
# next intervention" figures into the evaluation rows. Kept modest - the
# detail screens ask for a longer horizon on demand via /forecast/*.
CACHED_HORIZON_DAYS = 21

LEASE_NAME = "poller"

log = logging.getLogger(__name__)


class PondSkipped(Exception):
    """The pond has no usable sensor reading this cycle, so it was not
    advanced. Not a failure of the poller."""

    def __init__(self, reason: str, sensor_recorded_at: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.sensor_recorded_at = sensor_recorded_at


def lease_seconds(settings: Settings) -> int:
    """How long one take of the lease lasts: two poll intervals."""
    return 2 * settings.poll_interval_minutes * 60


def default_holder() -> str:
    """Names this worker process in worker_lease.holder."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class Worker:
    """Runs poll cycles for one worker process, gated by the lease."""

    def __init__(self, settings: Settings, registry: EngineRegistry, holder: Optional[str] = None):
        self.settings = settings
        self.registry = registry
        self.holder = holder or default_holder()
        self.failures_total: dict[str, int] = {}
        self.last_success_at: Optional[str] = None

    def run_cycle(self) -> bool:
        """Takes or renews the lease and, if held, polls every active pond
        and writes the cycle report. Returns whether this worker polled."""
        storage = self.registry.storage
        try:
            held = storage.take_lease(LEASE_NAME, self.holder, lease_seconds(self.settings))
        except StorageError as exc:
            log_event(log, "lease_unavailable", level=logging.WARNING, holder=self.holder, error=str(exc))
            return False
        if not held:
            log_event(log, "worker_standby", holder=self.holder)
            return False

        started_at = datetime.now(timezone.utc)
        clock = time.perf_counter()
        completed = True
        try:
            results = _poll_once(self.registry, self.settings.worker_threads)
        except Exception as exc:  # noqa: BLE001 - reported below; the next tick tries again
            log_event(log, "poll_cycle_failed", level=logging.ERROR, exc_info=True, error=str(exc))
            results, completed = {}, False
        duration = time.perf_counter() - clock
        finished_at = datetime.now(timezone.utc)

        counts = {kind: sum(1 for r in results.values() if r["result"] == kind)
                  for kind in ("ok", "skipped", "failed")}
        for pond_id, result in results.items():
            failures = self.failures_total.get(pond_id, 0) + (1 if result["result"] == "failed" else 0)
            self.failures_total[pond_id] = result["failures_total"] = failures
        succeeded = completed and (counts["failed"] == 0 or counts["ok"] > 0)
        if succeeded:
            self.last_success_at = finished_at.isoformat()
        elif self.last_success_at is None:
            # A worker that has not succeeded yet keeps the previous
            # worker's last success rather than erasing it.
            previous = fail_soft(lambda: storage.fetch_worker_status(LEASE_NAME), None)
            self.last_success_at = (previous or {}).get("last_success_at")

        fail_soft(lambda: storage.record_worker_status(LEASE_NAME, {
            "holder": self.holder,
            "cycle_started_at": started_at.isoformat(),
            "cycle_finished_at": finished_at.isoformat(),
            "cycle_duration_sec": round(duration, 3),
            "last_success_at": self.last_success_at,
            "ponds": results,
        }), None)
        log_event(log, "poll_cycle_finished", level=logging.INFO if succeeded else logging.WARNING,
                  holder=self.holder, duration_sec=round(duration, 3), succeeded=succeeded,
                  ponds_ok=counts["ok"], ponds_skipped=counts["skipped"], ponds_failed=counts["failed"])
        return True

    def stop(self) -> None:
        """Releases the lease so a standby worker can take over next cycle."""
        fail_soft(lambda: self.registry.storage.release_lease(LEASE_NAME, self.holder), None)


def _poll_once(registry: EngineRegistry, threads: int = 1) -> dict[str, dict]:
    """Polls every active pond, up to `threads` at a time. Returns each
    pond's result by user id (as text): {"result", "reason",
    "sensor_recorded_at"}. Raises only if the pond list cannot be read."""
    configs = registry.storage.fetch_active_pond_configs()
    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="poll") as pool:
        return dict(pool.map(lambda row: _poll_user_safely(registry, row), configs))


def _poll_user_safely(registry: EngineRegistry, row: dict) -> tuple[str, dict]:
    user_id = row["user_id"]
    with pond_context(user_id):
        try:
            sensor_recorded_at = _poll_user(registry, user_id, row)
        except PondSkipped as exc:
            log_event(log, "pond_poll_skipped", level=logging.WARNING, reason=exc.reason)
            return str(user_id), {"result": "skipped", "reason": exc.reason,
                                  "sensor_recorded_at": exc.sensor_recorded_at}
        except Exception as exc:  # noqa: BLE001 - one user's failure must not kill the poll loop
            log_event(log, "pond_poll_failed", level=logging.ERROR, exc_info=True, error=str(exc))
            return str(user_id), {"result": "failed", "reason": f"{type(exc).__name__}: {exc}"[:200],
                                  "sensor_recorded_at": None}
    return str(user_id), {"result": "ok", "reason": None,
                          "sensor_recorded_at": sensor_recorded_at if isinstance(sensor_recorded_at, str) else None}


def _poll_user(registry: EngineRegistry, user_id: int, config_row: dict) -> Optional[str]:
    """Advances one pond. Returns the recorded_at of the sensor reading it
    used; raises PondSkipped when there is no usable reading."""
    storage = registry.storage
    payload = storage.fetch_dashboard_payload(user_id)
    if not payload or "raw_sensor" not in payload:
        raise PondSkipped("no sensor reading in the dashboard payload")

    now = datetime.now(timezone.utc)
    raw = payload["raw_sensor"]
    recorded_at = raw.get("recorded_at") if isinstance(raw, dict) else None
    sensor_recorded_at = str(recorded_at) if recorded_at is not None else None

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
        raise PondSkipped(f"unusable sensor reading ({type(exc).__name__}: {exc})", sensor_recorded_at) from exc

    # The pond's profile history (pond_profile, or its UserData volume and
    # biomass when it has no profile rows). The twin applies the profile
    # in force at each step: a profile change since the last cycle splits
    # this cycle's evaporation integral at the change.
    profiles = registry.profile_history(user_id) or ProfileHistory(
        [profile_from_userdata_config(config_row)])

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
            storage.fetch_image_history(user_id, limit=50)
        )
    except Exception as exc:  # noqa: BLE001 - camera is optional
        log_event(log, "camera_history_unavailable", level=logging.WARNING, error=str(exc))

    # --- nitrate for the algae nutrient term ---------------------
    # Needs the twin, so it is resolved inside the locked section below.
    feeding_rows = []
    try:
        feeding_rows = storage.fetch_recent_feeding_events(user_id, limit=30)
    except Exception as exc:  # noqa: BLE001
        log_event(log, "feeding_history_unavailable", level=logging.WARNING, error=str(exc))
    avg_tan = WaterChemistryEngine.estimate_avg_daily_tan_mg(feeding_rows)

    water_temp = sample.temp_c
    lux_now = _first_not_none(now_conditions.get("lux"), raw.get("LUX"), 10000.0)

    def cycle(twin):
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
            log_event(log, "evaporation_projection_failed", level=logging.WARNING, error=str(exc))

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
                log_event(log, "algae_projection_failed", level=logging.WARNING, error=str(exc))

        # Re-assess with the projected day counts folded in, so the cached
        # rows carry both current status and next predicted intervention.
        evap_assessment = twin.evaporation.assess(
            current_water_temp_c=water_temp, days_to_topup=evap_days
        )
        algae_assessment = twin.algae.assess(days_to_scrub=algae_days)

        return outcome["chemistry"], evap_assessment, algae_assessment, outcome

    chem, evap, algae, outcome = registry.with_twin(user_id, cycle, profiles=profiles)

    # --- push evaluations (outside the lock: storage I/O is slow and
    #     the twin is already durably snapshotted by with_twin). The
    #     evaporation and algae logs are optional: a failed write loses
    #     one cached row, and the forecasts compute from the snapshot. ---
    storage.push_evaluation(user_id, chem.to_dict())
    if evap is not None:
        fail_soft(lambda: storage.push_evaporation_evaluation(user_id, evap.to_dict()), None)
    if algae is not None:
        fail_soft(lambda: storage.push_algae_evaluation(user_id, algae.to_dict()), None)

    log_event(log, "pond_polled", chemistry=f"{chem.status}/{chem.category}",
              evaporation=evap.status if evap else None, algae=algae.status if algae else None,
              new_camera_frames=outcome.get("assimilated_camera_frames", 0))
    return sensor_recorded_at


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


def start(settings: Optional[Settings], registry: EngineRegistry, blocking: bool = False):
    """Schedules a Worker's run_cycle every poll_interval_minutes, first run
    now. blocking=True runs the scheduler in the calling thread (the worker
    process) and releases the lease when it stops; otherwise it runs on a
    background thread and this returns the scheduler."""
    settings = settings or get_settings()
    if blocking:
        from apscheduler.schedulers.blocking import BlockingScheduler as Scheduler
    else:
        from apscheduler.schedulers.background import BackgroundScheduler as Scheduler
    worker = Worker(settings, registry)
    scheduler = Scheduler()
    # APScheduler's default max_instances=1 skips a tick while the previous
    # cycle is still running, so cycles never overlap within one worker.
    scheduler.add_job(
        worker.run_cycle, "interval", minutes=settings.poll_interval_minutes,
        next_run_time=datetime.now(),
    )
    if not blocking:
        scheduler.start()
        return scheduler
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        worker.stop()
    return scheduler
