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
DEVICE CONTACTS (issue #23)
--------------------------------------------------------------------
After the save, each distinct insert time the cycle ingested is recorded
as a contact of the pond's sensor node (Storage.record_device_contacts,
migration 0018), which moves devices.last_seen_at forward to the newest
receipt time. Each contact is expected to be followed one configured
interval later (devices.expected_interval_seconds, else
Settings.sensor_cadence_minutes). GET /v1/ponds/{pond}/devices and the
confidence on each assessment read them (koi/models/device_health.py).
A failure here is logged and skipped.

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
import bisect
import logging
import os
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from koi import notifications
from koi.logs import log_event, pond_context
from koi.models import algae_engine as ae
from koi.models import device_health, forecast_utils
from koi.models import evaporation_engine as ev
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
        self.registry.tds_prompt_config = settings.tds_prompt_config
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
    # Rolling 24-hour station rain from the observation history (#24),
    # for the chemistry rain term (#27). Unreadable leaves it unknown.
    observed_rain = fail_soft(lambda: forecast_utils.observed_rain_24h(
        storage, storage.fetch_dashboard_sources(user_id), now), None)

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
    image_rows = None
    try:
        image_rows = storage.fetch_image_history(user_id, limit=50)
        camera_samples = ae.parse_image_rows(image_rows)
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
    # The pond's calibration versions; None (unreadable) leaves the twin's
    # as they are.
    calibrations = fail_soft(lambda: storage.fetch_calibrations(user_id), None)

    commit: dict[str, Any] = {}

    def cycle(twin):
        if calibrations is not None:
            apply_calibration(twin, calibrations, now)
        # --- every sensor reading since the last cycle, in order ---
        discovery = _discover(registry, user_id, twin, now)
        commit["ingest"] = discovery.commit()
        commit["received"] = discovery.received
        # A twin from before the ledger has read sensors (its chemistry
        # has an ingest time) even with no channel readings recorded.
        if not discovery.inputs and not twin.sensor_channels and twin.chemistry._last_ingest_time is None:
            raise PondSkipped("no sensor reading for this pond yet")
        # Events first: one logged since the last cycle usually falls
        # before this cycle's readings, so both go in in order.
        events = (_events_report(twin.reconcile_events(intervention_rows, now=now, history=history))
                  if intervention_rows is not None else {"reconciled": False})
        provenance, sensor_warnings = twin.ingest_sensor_inputs(discovery.inputs, history=history, now=now)
        twin.tds_prompts.observe(discovery.inputs, twin.ledger, registry.tds_prompt_config)

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
            observed_rain=observed_rain,
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
    _record_sensor_contacts(registry, user_id, commit.get("received") or [], now)

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
    # Read before this cycle's rows are pushed: the outbox's "turned red"
    # compares against them.
    previous = notifications.previous_assessments(storage, user_id)
    storage.push_evaluation(user_id, run.stamp(CHEMISTRY, chem.to_dict()))
    if evap is not None:
        fail_soft(lambda: storage.push_evaporation_evaluation(user_id, run.stamp(EVAPORATION, evap.to_dict())), None)
    if algae is not None:
        fail_soft(lambda: storage.push_algae_evaluation(user_id, run.stamp(ALGAE, algae.to_dict())), None)

    # --- notification outbox (issue #36): best effort, never fails the poll ---
    notifications.notify_cycle(
        storage, user_id, now,
        current={"chemistry": chem.to_dict(), "evaporation": evap.to_dict() if evap else None,
                 "algae": algae.to_dict() if algae else None},
        previous=previous, image_rows=image_rows, default_sensor_interval=cadence)

    log_event(log, "pond_polled", chemistry=f"{chem.status}/{chem.category}",
              evaporation=evap.status if evap else None, algae=algae.status if algae else None,
              hypoxia=hypoxia.level, hypoxia_raised_by=hypoxia.raised_by,
              new_camera_frames=outcome.get("assimilated_camera_frames", 0),
              sensor_inputs=sensor["inputs"], late_sensor_inputs=sensor["late_inputs"],
              events_applied=outcome["events"].get("applied", 0), events_replayed=outcome["events"].get("replayed"))
    times = [c["reading_at"] for c in sensor["channels"].values() if c["reading_at"]]
    return PollResult(max(times, key=parse_timestamp) if times else "", sensor, outcome["events"])


def _record_sensor_contacts(registry: EngineRegistry, user_id: int, received: list, now: datetime) -> None:
    """Records each upload the cycle ingested as a contact of the pond's
    sensor node (devices.last_seen_at is its receipt time), expected next
    one configured interval later (koi/models/device_health.py). Skipped,
    with a warning, when storage cannot do it: health tracking never
    holds up ingestion."""
    if not received:
        return
    storage = registry.storage
    activity = fail_soft(lambda: storage.fetch_device_activity(user_id, now), None)
    interval = device_health.sensor_interval(activity or {}, registry.sensor_ingest.cadence)
    contacts = device_health.sensor_contacts([{"created_at": t} for t in received], interval)
    fail_soft(lambda: storage.record_device_contacts(user_id, device_health.SENSOR, contacts), None)


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


# ---------------------------------------------------------------
# Calibration (issue #32, migration 0022)
# ---------------------------------------------------------------
# Once a day the worker fits three pond-specific constants from the
# pond's own history and appends each result to pond_calibration:
#
#   evaporation_shelter_factor  litres added at each logged top-up
#                               against the modelled loss since the
#                               previous top-up or water change
#                               (ev.fit_shelter_factor, >= 3 top-ups)
#   water_air_temperature       the pond's hourly water temperature
#                               against the assigned station's hourly air
#                               temperature: offset and lag, error on a
#                               later evaluation interval beside
#                               persistence (ev.fit_water_air_temperature)
#   nitrification_rate_scale    kit ammonia and nitrite against the
#                               simulated pools (kit_readings.
#                               fit_nitrification_scale, >= 4 readings)
#
# Each cycle applies the newest fitted row of each parameter whose
# effective_from is at or before the cycle's time (calibration_in_force),
# so a rebuild replaying a past time uses the version in force then. With
# no fitted row the engines keep their defaults.
#
# Missing-data rules, all over the CALIBRATION_WINDOW before the run:
#   * Hours are UTC hour bins keyed by the observation's start
#     (observed_from) or the sensor row's insert time; a bin's value is
#     the mean of its samples (rainfall: the sum of its 5-minute totals,
#     scaled to the hour, when they cover at least half of it).
#   * A shelter-fit hour is complete when air temperature, wind, rainfall
#     and the 24-hour forecast's humidity are all known; water
#     temperature is the pond's own in that hour, else the air estimate.
#     A top-up interval counts with 75% of its hours complete, between
#     1 and MAX_TOPUP_INTERVAL_DAYS long, with a logged volume.
#   * A temperature hour needs the pond's water value and the station's
#     air value at every lag tried (0 to 12 hours).
#   * The nitrification simulation starts from empty pools at the window
#     start, uses the hourly water temperature (else air, carried forward
#     over gaps) and leaves out readings in its first week.
# Too little data gives a 'pending' row (stored only while no fit exists,
# and only when it changes), never a value.
CALIBRATION_LEASE = "calibration"
CALIBRATION_LEASE_SECONDS = 3600
CALIBRATION_RUN_HOUR, CALIBRATION_RUN_MINUTE = 4, 0
CALIBRATION_WINDOW = timedelta(days=60)
MAX_TOPUP_INTERVAL_DAYS = 45
CALIBRATION_METHOD_VERSION = 1

SHELTER = "evaporation_shelter_factor"
TEMPERATURE = "water_air_temperature"
NITRIFICATION = "nitrification_rate_scale"
CALIBRATION_PARAMETERS = (SHELTER, TEMPERATURE, NITRIFICATION)
# What each parameter falls back to with no fit.
CALIBRATION_DEFAULTS: dict[str, dict[str, Optional[float]]] = {
    SHELTER: {"value": ev.POND_SHELTER_FACTOR, "lag_hours": None},
    TEMPERATURE: {"value": ev.DEFAULT_WATER_AIR_OFFSET_C, "lag_hours": 0.0},
    NITRIFICATION: {"value": 1.0, "lag_hours": None},
}
_KNOTS_TO_MS = 0.514444
_HOUR = timedelta(hours=1)
# Hours of the 24-hour forecast's humidity looked up at once.
_HUMIDITY_BLOCK_HOURS = 6


def calibration_in_force(rows: list[dict], at: datetime) -> dict[str, dict]:
    """The newest fitted row of each parameter with effective_from at or
    before `at` (ties by id). A parameter with none is absent."""
    found: dict[str, dict] = {}
    for row in rows:
        if row.get("status") != "fitted" or parse_timestamp(row["effective_from"]) > at:
            continue
        current = found.get(row["parameter"])
        if current is None or ((parse_timestamp(row["effective_from"]), row["id"])
                               > (parse_timestamp(current["effective_from"]), current["id"])):
            found[row["parameter"]] = row
    return found


def apply_calibration(twin: PondTwin, rows: list[dict], at: datetime) -> dict[str, int]:
    """Puts the calibration in force at `at` into the twin's engines and
    returns the row ids applied, by parameter."""
    in_force = calibration_in_force(rows, at)
    shelter, temperature, scale = (in_force.get(p) for p in CALIBRATION_PARAMETERS)
    versions = {p: int(row["id"]) for p, row in in_force.items()}
    twin.evaporation.apply_calibration(
        shelter_factor=float(shelter["value"]) if shelter else None,
        water_air_offset_c=float(temperature["value"]) if temperature else None,
        water_air_lag_hours=float(temperature["lag_hours"] or 0.0) if temperature else None,
        versions={p: v for p, v in versions.items() if p != NITRIFICATION})
    twin.chemistry.nitrification_scale = float(scale["value"]) if scale else 1.0
    twin.chemistry.nitrification_scale_version = versions.get(NITRIFICATION)
    return versions


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def _observations(storage, station: Optional[str], metric: str, start: datetime, end: datetime) -> list[dict]:
    """Station observations over [start, end], a day per call so no call
    comes near a response cap."""
    if not station:
        return []
    rows: list[dict] = []
    day = start
    while day < end:
        until = min(day + timedelta(days=1), end)
        rows.extend(o for o in storage.fetch_weather_observations(str(station), metric, day, until, end)
                    if o.get("value") is not None and parse_timestamp(o["observed_from"]) < until)
        day = until
    return rows


def _hourly_mean(samples: list[tuple[datetime, float]]) -> dict[datetime, float]:
    bins: dict[datetime, list[float]] = {}
    for t, value in samples:
        bins.setdefault(_hour(t), []).append(float(value))
    return {h: sum(v) / len(v) for h, v in bins.items()}


def _hourly_rain(rows: list[dict]) -> dict[datetime, float]:
    totals: dict[datetime, list[float]] = {}
    for o in rows:
        if o.get("semantics") != "interval_total" or o.get("unit") not in (None, "mm"):
            continue
        start, end = parse_timestamp(o["observed_from"]), parse_timestamp(o["observed_to"])
        entry = totals.setdefault(_hour(start), [0.0, 0.0])
        entry[0] += float(o["value"])
        entry[1] += max((end - start).total_seconds(), 0.0)
    return {h: total * 3600.0 / covered for h, (total, covered) in totals.items() if covered >= 1800.0}


def _hourly_humidity(storage, hours: set[datetime]) -> dict[datetime, float]:
    """The 24-hour general forecast's humidity midpoint usable at each
    block of _HUMIDITY_BLOCK_HOURS hours that holds a wanted hour (the
    value the poller uses, as of then)."""
    out: dict[datetime, float] = {}
    blocks = sorted({h.replace(hour=h.hour - h.hour % _HUMIDITY_BLOCK_HOURS) for h in hours})
    for block in blocks:
        found = storage.fetch_forecast_as_of("24hr", "GENERAL", block)
        payload = (found or {}).get("payload") or {}
        humidity = forecast_utils.current_conditions(
            {"nea_forecasts": {"forecast_24hr": {"general": payload}}}).get("humidity_pct")
        if humidity is None:
            continue
        for offset in range(_HUMIDITY_BLOCK_HOURS):
            out[block + offset * _HOUR] = float(humidity)
    return out


def _interval_hours(start: datetime, end: datetime) -> list[datetime]:
    count = int((end - start).total_seconds() // 3600)
    return [_hour(start + i * _HOUR) for i in range(count)]


def _topup_samples(rows: list[dict], profiles: ProfileHistory, weather: dict, water: dict,
                   offset_c: Optional[float]) -> tuple[list[ev.TopUpSample], list[dict]]:
    """Each logged top-up with the hours since the previous top-up or
    water change, and the top-ups left out with the reason."""
    boundaries = [r for r in rows if r.get("event_type") in ("WATER_TOPUP", "WATER_CHANGE")]
    samples: list[ev.TopUpSample] = []
    skipped: list[dict] = []
    for previous, row in zip(boundaries, boundaries[1:], strict=False):
        if row["event_type"] != "WATER_TOPUP":
            continue
        start, end = parse_timestamp(previous["event_timestamp"]), parse_timestamp(row["event_timestamp"])
        days = (end - start).total_seconds() / 86400.0
        profile = profiles.at(end)
        if row.get("volume_litres") is not None:
            litres = float(row["volume_litres"])
        elif row.get("volume_percentage") is not None:
            litres = float(row["volume_percentage"]) / 100.0 * profile.volume_l
        else:
            skipped.append({"event_id": row.get("event_id"), "reason": "no_volume_logged"})
            continue
        if not 1.0 <= days <= MAX_TOPUP_INTERVAL_DAYS:
            skipped.append({"event_id": row.get("event_id"), "reason": "interval_length", "days": round(days, 2)})
            continue
        hours: list[Optional[ev.HourConditions]] = []
        for h in _interval_hours(start, end):
            air, wind = weather["air"].get(h), weather["wind"].get(h)
            rain, humidity = weather["rain"].get(h), weather["humidity"].get(h)
            if air is None or wind is None or rain is None or humidity is None:
                hours.append(None)
                continue
            at = profiles.at(h)
            area = ev.EvaporationConfig(volume_litres=at.volume_l, estimated_biomass_grams=at.biomass_g,
                                        pond_depth_m=at.depth_m or ev.DEFAULT_POND_DEPTH_M).surface_area_m2
            hours.append(ev.HourConditions(
                air_temp_c=air, relative_humidity_pct=humidity, wind_speed_ms=wind, rain_mm=rain,
                water_temp_c=water.get(h, ev.predict_water_temp(air, offset_c)), surface_area_m2=area))
        samples.append(ev.TopUpSample(start=start, end=end, litres_added=litres, hours=hours))
    return samples, skipped


def _temp_lookup(water: dict[datetime, float], air: dict[datetime, float]) -> Any:
    """Hourly temperature for the nitrogen simulation: the pond's water,
    else the station air, carried forward over gaps (backward before the
    first value). None when neither has any value."""
    merged = {**air, **water}
    if not merged:
        return None
    hours = sorted(merged)

    def temp_at(t: datetime) -> float:
        index = bisect.bisect_right(hours, _hour(t)) - 1
        return merged[hours[max(index, 0)]]
    return temp_at


def fit_pond_calibration(storage, profiles: ProfileHistory, pond: int, now: datetime,
                         in_force: Optional[dict[str, dict]] = None) -> dict[str, dict]:
    """The three fits for one pond from its history over the
    CALIBRATION_WINDOW before now, as pond_calibration results."""
    start = now - CALIBRATION_WINDOW
    sources = storage.fetch_dashboard_sources(pond) or {}
    found = sources.get("stations")
    stations: dict = found if isinstance(found, dict) else {}
    air = _hourly_mean([(parse_timestamp(o["observed_from"]), o["value"])
                        for o in _observations(storage, stations.get("air-temperature"), "air_temperature",
                                               start, now)])
    wind = {h: v * _KNOTS_TO_MS for h, v in _hourly_mean(
        [(parse_timestamp(o["observed_from"]), o["value"])
         for o in _observations(storage, stations.get("wind-speed"), "wind_speed", start, now)]).items()}
    rain = _hourly_rain(_observations(storage, stations.get("rainfall"), "rainfall", start, now))
    water = _hourly_mean([(parse_timestamp(r["created_at"]), r["value"])
                          for r in storage.fetch_ingested_sensor_rows(pond, ("temp",), start, now)
                          if r.get("value") is not None])
    interventions = storage.fetch_interventions(pond, start)

    results: dict[str, dict] = {}
    results[TEMPERATURE] = ev.fit_water_air_temperature(water, air)
    if not stations.get("air-temperature"):
        results[TEMPERATURE]["details"]["station"] = "no air-temperature station assigned"

    temperature = (in_force or {}).get(TEMPERATURE)
    offset = float(temperature["value"]) if temperature else None
    wanted = {h for r in interventions if r.get("event_type") == "WATER_TOPUP"
              for h in _interval_hours(max(start, parse_timestamp(r["event_timestamp"]) - MAX_TOPUP_INTERVAL_DAYS
                                           * timedelta(days=1)), parse_timestamp(r["event_timestamp"]))}
    humidity = _hourly_humidity(storage, wanted) if wanted else {}
    samples, skipped = _topup_samples(interventions, profiles, {"air": air, "wind": wind, "rain": rain,
                                                                 "humidity": humidity}, water, offset)
    results[SHELTER] = ev.fit_shelter_factor(samples)
    results[SHELTER]["details"]["skipped_topups"] = skipped

    # Imported here: kit_readings imports the rebuild, which imports this module.
    from koi import kit_readings
    temp_at = _temp_lookup(water, air)
    readings = storage.fetch_kit_readings(pond)
    if temp_at is None:
        results[NITRIFICATION] = ev.pending_calibration("no_temperature_history", 0)
    else:
        results[NITRIFICATION] = kit_readings.fit_nitrification_scale(
            readings, kit_readings.nitrogen_events(interventions), temp_at,
            lambda t: profiles.at(t).volume_l, start)
    return results


def _calibration_row(parameter: str, result: dict, now: datetime) -> dict:
    def when(value: Any) -> Optional[str]:
        return value.isoformat() if isinstance(value, datetime) else value
    details = {**(result.get("details") or {}), "default": CALIBRATION_DEFAULTS[parameter]}
    return {"parameter": parameter, "status": result["status"], "value": result["value"],
            "lag_hours": result.get("lag_hours"), "fitted_at": now.isoformat(), "effective_from": now.isoformat(),
            "sample_count": int(result["sample_count"]), "error": result.get("error"),
            "training_from": when(result.get("training_from")), "training_to": when(result.get("training_to")),
            "evaluation_from": when(result.get("evaluation_from")), "evaluation_to": when(result.get("evaluation_to")),
            "details": details, "method_version": CALIBRATION_METHOD_VERSION}


def _changed(row: dict, existing: list[dict]) -> bool:
    """Whether row is worth a new version: a fit whose value, lag, sample
    count or training end differs from the newest fit, or a pending
    result while the parameter has no fit, differing from the newest
    pending row in reason or sample count."""
    same = [r for r in existing if r["parameter"] == row["parameter"]]
    fits = [r for r in same if r["status"] == "fitted"]
    if row["status"] == "fitted":
        if not fits:
            return True
        last = fits[-1]
        return (last["value"] != row["value"] or last.get("lag_hours") != row["lag_hours"]
                or last["sample_count"] != row["sample_count"]
                or (last.get("training_to") and parse_timestamp(last["training_to"]))
                != (row["training_to"] and parse_timestamp(row["training_to"])))
    if fits:
        return False
    if not same:
        return True
    last = same[-1]
    return (last["sample_count"] != row["sample_count"]
            or (last.get("details") or {}).get("reason") != row["details"].get("reason"))


def calibrate_pond(registry: EngineRegistry, pond: int, now: Optional[datetime] = None) -> dict[str, dict]:
    """Fits the pond's three constants and stores each result that is new
    (see _changed). Returns, by parameter, {status, value, lag_hours,
    sample_count, stored, reason}."""
    storage = registry.storage
    now = now or datetime.now(timezone.utc)
    profiles = registry.profile_history(pond)
    if profiles is None:
        raise LookupError(f"pond {pond} has no profile and no UserData volume and biomass")
    existing = storage.fetch_calibrations(pond)
    results = fit_pond_calibration(storage, profiles, pond, now, calibration_in_force(existing, now))
    report: dict[str, dict] = {}
    for parameter in CALIBRATION_PARAMETERS:
        row = _calibration_row(parameter, results[parameter], now)
        stored = _changed(row, existing)
        if stored:
            existing.append(storage.insert_calibration(pond, row))
        report[parameter] = {"status": row["status"], "value": row["value"], "lag_hours": row["lag_hours"],
                             "sample_count": row["sample_count"], "stored": stored,
                             "reason": row["details"].get("reason")}
    return report


class CalibrationJob:
    """The scheduled run: takes the calibration lease, calibrates every
    active pond (one pond's failure is logged and the rest go on)."""

    def __init__(self, registry: EngineRegistry, holder: str):
        self.registry = registry
        self.holder = holder

    def run(self, now: Optional[datetime] = None) -> Optional[dict]:
        storage = self.registry.storage
        try:
            held = storage.take_lease(CALIBRATION_LEASE, self.holder, CALIBRATION_LEASE_SECONDS)
        except StorageError as exc:
            log_event(log, "calibration_lease_unavailable", level=logging.WARNING, error=str(exc))
            return None
        if not held:
            log_event(log, "calibration_standby", holder=self.holder)
            return None
        report: dict[str, dict] = {}
        try:
            for row in storage.fetch_active_pond_configs():
                pond = int(row["user_id"])
                with pond_context(pond):
                    try:
                        report[str(pond)] = calibrate_pond(self.registry, pond, now)
                    except Exception as exc:  # noqa: BLE001 - one pond's failure must not stop the others
                        log_event(log, "pond_calibration_failed", level=logging.ERROR, exc_info=True,
                                  error=str(exc))
                        report[str(pond)] = {"failed": f"{type(exc).__name__}: {exc}"[:200]}
                        continue
                    log_event(log, "pond_calibrated", **{p: r["status"] for p, r in report[str(pond)].items()})
        except StorageError as exc:
            log_event(log, "calibration_failed", level=logging.ERROR, error=str(exc))
            return None
        finally:
            fail_soft(lambda: storage.release_lease(CALIBRATION_LEASE, self.holder), None)
        return report


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
    scheduler.add_job(
        CalibrationJob(registry, worker.holder).run, "cron", hour=CALIBRATION_RUN_HOUR,
        minute=CALIBRATION_RUN_MINUTE, timezone=retention.DEFAULT_POND_TIME_ZONE, id="calibration",
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
