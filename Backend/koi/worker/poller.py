"""
koi.worker.poller

Each Settings.poll_interval_minutes, reads every new SensorData row of
each active pond and the weather in get_bundled_dashboard_payload, and
drives ALL THREE domain engines forward from what it finds. Runs on its own with `python -m koi.worker`,
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
EVERY SENSOR READING ONCE (issue #18)
--------------------------------------------------------------------
The dashboard payload's raw_sensor holds only the newest value of each
sensor type, so readings stored between two polls were never seen, and
with no new rows the same reading went into the model again. Instead,
inside the pond's lock, each cycle asks storage for the pond's SensorData
rows not yet in the ingest ledger (Storage.fetch_pending_sensor_rows,
migration 0014), scanning from Settings.sensor_ingest_overlap_minutes
before the newest insert time already ingested, so a row committed after
a newer one is still found. koi/models/sensor_inputs.py groups them into
inputs (one per upload, a channel the node did not send passed as
missing) in (effective_sample_time, id) order, and
PondTwin.ingest_sensor_inputs applies each at its own time. The rows
read and the new watermark are saved with the snapshot in one
transaction (registry.with_twin's ingest); if the cycle fails before
that, nothing is recorded and the next cycle applies the same rows to
the same stored snapshot. No new rows means no sensor input this cycle:
evaporation and algae still advance on the weather, chemistry does not
see an old reading again.

A pond with no snapshot yet reads all its rows; one whose snapshot was
written before the ledger existed starts at the snapshot's last chemistry
ingest time. Until the node sends a sample time (issue #17) every reading
time is the database insert time, labelled time_basis "ingestion" in the
twin's channel state, the ledger and the cycle report. A channel's
reading is used for water temperature and light while it is fresh: no
older than two of the node's expected upload intervals
(Settings.sensor_cadence_minutes), measured from the reading's own time.

--------------------------------------------------------------------
EVERY LOGGED EVENT ONCE (issue #19)
--------------------------------------------------------------------
The app writes each intervention to pondInterventions and then posts it
to the twin; if the post failed, the model never saw it. Each cycle now
gives any of the pond's rows without an event_id the UUID derived from
its id (Storage.backfill_intervention_event_ids), reads the rows of the
last 30 days (Storage.fetch_interventions) and, inside the pond's lock
and before the sensor inputs, hands them to PondTwin.reconcile_events:
a row whose event_id is not in the twin's ledger is applied, an edited
row is re-applied and a deleted one removed, with a replay of the
chemistry engine when that is in the past (koi/models/event_ledger.py).
The ledger is part of the snapshot, so a cycle that fails before the
save leaves the ledger as it was and the next cycle does the same work;
the backfill is deterministic, so repeating it changes nothing. If the
rows cannot be read, the cycle goes on without reconciling and says so
in its report. Sensor inputs at or before an event already applied are
replayed into their place in the same way.

--------------------------------------------------------------------
THE CYCLE REPORT
--------------------------------------------------------------------
After each cycle the active worker writes one worker_status row
(Storage.record_worker_status, migration 0003): when the cycle ran, how
long it took, each pond's result ("ok", "skipped" when the pond has no
sensor reading at all, "failed" when polling raised), each pond's
failure count since the worker started, the time of its newest sensor
reading and "sensor": what the cycle ingested (inputs, late inputs, time
basis) and each channel's reading time, age and freshness, and "events": what
reconciliation did (counts of applied, revised, deleted and skipped
rows, and whether it replayed). The API's /ready and /metrics read that row,
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
each pushes a fresh evaluation row, stamped with the model version, the
input cutoff, the forecast issue times and the inputs the cycle applied
(koi/provenance.py). The UI then reads cached assessments instead of
triggering recomputes.

One APScheduler job per tick runs the cycle; within it, ponds are polled
on a small thread pool (Settings.worker_threads, default 4). Each pond
still goes through registry.with_twin, so one lock per pond and the
snapshot version check apply exactly as they do to API requests.

A second job runs once a day at 03:30 pond local time: evaluation
retention (koi/worker/retention.py), which folds the evaluation rows of
days older than Settings.evaluation_retention_days into evaluation_daily.
"""
import logging
import os
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from koi.logs import log_event, pond_context
from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models import forecast_utils
from koi.models.engine import WaterChemistryEngine
from koi.models.event_ledger import WINDOW
from koi.models.hypoxia import assess_hypoxia
from koi.models.pond_twin import PondTwin
from koi.models.profile import ProfileHistory, profile_from_userdata_config
from koi.models.sensor_inputs import (
    SENSOR_TYPES,
    TIME_BASIS_INGESTION,
    Discovery,
    IngestConfig,
    channel_freshness,
    complete_groups,
    fresh_value,
    group_rows,
    parse_timestamp,
)
from koi.provenance import ALGAE, CHEMISTRY, EVAPORATION, RunProvenance, forecast_provenance
from koi.registry import EngineRegistry
from koi.settings import Settings, get_settings
from koi.storage import StorageError, fail_soft
from koi.worker import retention

# How far ahead each cycle projects when writing the cached "days until
# next intervention" figures into the evaluation rows. Kept modest - the
# detail screens ask for a longer horizon on demand via /forecast/*.
CACHED_HORIZON_DAYS = 21

LEASE_NAME = "poller"

log = logging.getLogger(__name__)


class PondSkipped(Exception):
    """The pond has no sensor reading at all, so it was not advanced. Not
    a failure of the poller."""

    def __init__(self, reason: str, sensor_recorded_at: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.sensor_recorded_at = sensor_recorded_at


class PollResult(str):
    """What _poll_user returns: the time of the pond's newest sensor
    reading (a str, as before), with the cycle's ingestion report in
    .sensor and its event reconciliation report in .events."""

    sensor: dict
    events: Optional[dict]

    def __new__(cls, recorded_at: str, sensor: dict, events: Optional[dict] = None) -> "PollResult":
        result = super().__new__(cls, recorded_at)
        result.sensor = sensor
        result.events = events
        return result


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
            polled = _poll_user(registry, user_id, row)
        except PondSkipped as exc:
            log_event(log, "pond_poll_skipped", level=logging.WARNING, reason=exc.reason)
            return str(user_id), {"result": "skipped", "reason": exc.reason,
                                  "sensor_recorded_at": exc.sensor_recorded_at}
        except Exception as exc:  # noqa: BLE001 - one user's failure must not kill the poll loop
            log_event(log, "pond_poll_failed", level=logging.ERROR, exc_info=True, error=str(exc))
            return str(user_id), {"result": "failed", "reason": f"{type(exc).__name__}: {exc}"[:200],
                                  "sensor_recorded_at": None}
    result: dict[str, Any] = {"result": "ok", "reason": None,
                              "sensor_recorded_at": str(polled) if isinstance(polled, str) and polled else None}
    if isinstance(polled, PollResult):
        result["sensor"] = polled.sensor
        if polled.events is not None:
            result["events"] = polled.events
    return str(user_id), result


def _discover(registry: EngineRegistry, user_id: int, twin: PondTwin, now: datetime) -> Discovery:
    """The pond's sensor rows not yet ingested and stored by now, as
    inputs in event-time order. A pond with no cursor starts at its
    twin's last chemistry ingest (a snapshot from before the ledger) or,
    for a new twin, at its first row."""
    config: IngestConfig = registry.sensor_ingest
    start = twin.chemistry._last_ingest_time if twin.last_input_at is None else twin.last_input_at
    found = registry.storage.fetch_pending_sensor_rows(
        user_id, SENSOR_TYPES, start=start, until=now,
        overlap_seconds=config.overlap_minutes * 60, limit=config.batch_rows)
    return group_rows(complete_groups(list(found.get("rows") or []), config.batch_rows))


def _poll_user(registry: EngineRegistry, user_id: int, config_row: dict,
               now: Optional[datetime] = None) -> PollResult:
    """Advances one pond to now (default: the current time; koi.dev
    replays a recorded history by passing past times in order), applying
    every sensor reading stored since the last cycle. Returns the time of
    the pond's newest sensor reading with the cycle's ingestion report;
    raises PondSkipped when the pond has no sensor reading at all."""
    storage = registry.storage
    payload = storage.fetch_dashboard_payload(user_id) or {}
    now = now or datetime.now(timezone.utc)
    cadence = registry.sensor_ingest.cadence

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

    # --- logged interventions of the window, for reconciliation ----
    intervention_rows = _intervention_rows(registry, user_id, now)
    history = registry.sensor_history(user_id)

    commit: dict[str, Optional[dict]] = {}

    def cycle(twin):
        # --- every sensor reading since the last cycle, in order ---
        discovery = _discover(registry, user_id, twin, now)
        commit["ingest"] = discovery.commit()
        # A twin from before the ledger has read sensors (its chemistry
        # has an ingest time) even with no channel readings recorded.
        if not discovery.inputs and not twin.sensor_channels and twin.chemistry._last_ingest_time is None:
            raise PondSkipped("no sensor reading for this pond yet")
        # Events first: one logged since the last cycle usually falls
        # before this cycle's readings, so both go in in order.
        events = (_events_report(twin.reconcile_events(intervention_rows, now=now, history=history))
                  if intervention_rows is not None else {"reconciled": False})
        provenance, sensor_warnings = twin.ingest_sensor_inputs(discovery.inputs, history=history, now=now)

        # The newest reading of each channel, used while it is fresh. A
        # stale or missing water temperature is not measured: the air
        # temperature stands in for the algae and nitrate terms, and
        # evaporation and the hypoxia flag get no measured value.
        freshness = channel_freshness(twin.sensor_channels, now, cadence)
        water_temp = fresh_value(freshness, "temp")
        model_temp = water_temp if water_temp is not None else float(air_temp)
        light = fresh_value(freshness, "lux")
        lux_now = _first_not_none(now_conditions.get("lux"), light, 10000.0)

        # Nitrate trajectory first: the algae engine's growth depends on
        # it, and it is a pure read off the chemistry engine.
        no3_series = []
        if avg_tan is not None:
            no3_series = twin.no3_projection(
                avg_daily_tan_mg=avg_tan,
                fallback_temp_c=float(model_temp),
                horizon_days=CACHED_HORIZON_DAYS,
            )
        no3_now = no3_series[0] if no3_series else None

        algae_env = ae.AlgaeDayEnvironment(
            lux=float(lux_now), temp_c=float(model_temp), no3_ppm=no3_now
        )

        outcome = twin.ingest_environment(
            now=now,
            sample=None,
            evaporation_env=evaporation_env,
            algae_env=algae_env,
            camera_samples=camera_samples,
            rain_incoming=rain_incoming,
            rain_intensity=rain_intensity,
            measured_water_temp_c=water_temp,
            sensor_warnings=sensor_warnings,
        )
        outcome["sensor"] = {
            "inputs": len(provenance),
            "late_inputs": sum(1 for p in provenance if p["late"]),
            "replayed_inputs": sum(1 for p in provenance if p.get("replayed")),
            "rows": len(discovery.ledger),
            "time_basis": sorted({p["time_basis"] for p in provenance}) or [TIME_BASIS_INGESTION],
            "newest_input_at": provenance[-1]["time"] if provenance else None,
            "channels": {c: {k: f[k] for k in ("reading_at", "time_basis", "age_minutes", "fresh")}
                         for c, f in freshness.items()},
        }

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
                forecast_days, float(lux_now), float(model_temp), no3_series, no3_now
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

        # Night-time hypoxia risk from the pond's fresh readings (its own
        # light sensor, not NEA's), the profile in force now and the
        # algae assessment just made.
        outcome["hypoxia"] = assess_hypoxia(
            lux=light,
            water_temp_c=water_temp,
            aeration=profiles.at(now).aeration,
            algae_high=algae_assessment.scrub_now if algae_assessment is not None else None,
            thresholds=registry.hypoxia_thresholds,
        )

        outcome["events"] = events
        outcome["input_cutoff"] = twin.input_cutoff()
        return outcome["chemistry"], evap_assessment, algae_assessment, outcome

    chem, evap, algae, outcome = registry.with_twin(user_id, cycle, profiles=profiles,
                                                    ingest=lambda: commit.get("ingest"))
    hypoxia = outcome["hypoxia"]
    sensor = outcome["sensor"]

    # --- push evaluations (outside the lock: storage I/O is slow and
    #     the twin is already durably snapshotted by with_twin). The
    #     evaporation and algae logs are optional: a failed write loses
    #     one cached row, and the forecasts compute from the snapshot.
    #     Each row carries the run's provenance (koi/provenance.py); the
    #     forecast issue times come from the cache rows, unknown when
    #     they cannot be read. ---
    run = RunProvenance(
        run="poll", input_cutoff=outcome["input_cutoff"],
        forecasts=forecast_provenance(payload, fail_soft(lambda: storage.fetch_dashboard_sources(user_id), None)),
        sensor_groups=sensor["inputs"], events=outcome["events"].get("applied", 0),
        camera_frames=outcome.get("assimilated_camera_frames", 0))
    storage.push_evaluation(user_id, run.stamp(CHEMISTRY, chem.to_dict()))
    if evap is not None:
        fail_soft(lambda: storage.push_evaporation_evaluation(user_id, run.stamp(EVAPORATION, evap.to_dict())), None)
    if algae is not None:
        fail_soft(lambda: storage.push_algae_evaluation(user_id, run.stamp(ALGAE, algae.to_dict())), None)

    log_event(log, "pond_polled", chemistry=f"{chem.status}/{chem.category}",
              evaporation=evap.status if evap else None, algae=algae.status if algae else None,
              hypoxia=hypoxia.level, hypoxia_raised_by=hypoxia.raised_by,
              new_camera_frames=outcome.get("assimilated_camera_frames", 0),
              sensor_inputs=sensor["inputs"], late_sensor_inputs=sensor["late_inputs"],
              events_applied=outcome["events"].get("applied", 0), events_replayed=outcome["events"].get("replayed"))
    times = [c["reading_at"] for c in sensor["channels"].values() if c["reading_at"]]
    return PollResult(max(times, key=parse_timestamp) if times else "", sensor, outcome["events"])


def _intervention_rows(registry: EngineRegistry, user_id: int, now: datetime) -> Optional[list[dict]]:
    """The pond's pondInterventions rows of the last WINDOW, after giving
    any without an event_id its derived one; None when they cannot be
    read (the cycle then does not reconcile)."""
    storage = registry.storage
    fail_soft(lambda: storage.backfill_intervention_event_ids(user_id), 0)
    return fail_soft(lambda: storage.fetch_interventions(user_id, since=now - WINDOW), None)


def _events_report(report: dict) -> dict:
    """The cycle report's "events": counts, and the event_ids that were
    changed or could not be replayed."""
    out: dict[str, Any] = {"reconciled": True, "replayed": report["replayed"],
                           "replayed_from": report["replayed_from"], "skipped": len(report["skipped"])}
    for key in ("applied", "claimed", "revised", "deleted", "restored", "not_replayable"):
        out[key] = len(report[key])
    for key in ("revised", "deleted", "not_replayable"):
        if report[key]:
            out[f"{key}_ids"] = list(report[key])
    return out


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
    now, and the evaluation retention job daily at RUN_HOUR:RUN_MINUTE pond
    local time. blocking=True runs the scheduler in the calling thread (the
    worker process) and releases the lease when it stops; otherwise it runs
    on a background thread and this returns the scheduler."""
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
    job = retention.RetentionJob(registry.storage, settings.evaluation_retention_days, worker.holder)
    scheduler.add_job(
        job.run, "cron", hour=retention.RUN_HOUR, minute=retention.RUN_MINUTE,
        timezone=retention.DEFAULT_POND_TIME_ZONE, id="evaluation_retention",
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
