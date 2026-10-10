"""
pond_twin.py

One PondTwin per user, holding all three domain engines:

    chemistry   WaterChemistryEngine    TAN -> NO2 -> NO3 + buffering trend
    evaporation EvaporationFeedEngine   water loss + temperature feed cap
    algae       AlgaeGrowthEngine       green coverage, camera-grounded

--------------------------------------------------------------------
WHY THESE ARE AGGREGATED RATHER THAN KEPT SEPARATE
--------------------------------------------------------------------
A single real-world action touches several engines at once, and it must
touch them ATOMICALLY:

    WATER_CHANGE  -> chemistry: dilutes TAN/NO2, blends NO3 toward tap
                  -> evaporation: refills the pond, clears accrued loss
                  -> algae: dilutes suspended (green-water) algae
    WATER_TOPUP   -> chemistry: partial TDS dilution
                  -> evaporation: clears accrued loss  <-- the state reset
    ALGAE_SCRUB   -> chemistry: suppresses NO3 uptake for ~5 days
                  -> algae: knocks visible biomass down  <-- state reset
    FEEDING       -> chemistry: adds TAN
                  (indirectly raises NO3, which the algae engine consumes
                   as its nutrient term on the next projection)

If those engines lived under separate locks and separate snapshot rows, a
water change could land in chemistry while the evaporation write lost a
race, leaving the pond "diluted but still 12% evaporated" - a state that
never existed physically. One object, one lock, one snapshot makes that
class of bug unrepresentable.

It also matters for reads. The algae forecast needs the chemistry
engine's projected NO3 as its nutrient input; having both in the same
aggregate means that is a direct method call rather than an internal HTTP
round-trip or a second registry lookup that could deadlock against the
first.

--------------------------------------------------------------------
SNAPSHOT COMPATIBILITY
--------------------------------------------------------------------
Existing rows in pond_chemistry_state.snapshot hold a BARE chemistry
snapshot (the shape WaterChemistryEngine.to_snapshot() emits). from_snapshot
detects that legacy shape and wraps it, so deploying this does not lose
any pond's accumulated nitrogen state. New snapshots are versioned and
namespaced per engine.

Version 3 adds "sensor_inputs": the newest reading of each sensor channel
with its time and time basis, and the model time of the last sensor input
applied (issue #18). A v2 or legacy snapshot loads with none.

Version 4 adds "event_ledger" (koi/models/event_ledger.py, issue #19):
the events applied, by event_id, and daily checkpoints of the chemistry
engine for replaying an event that arrives in the past. A v3 or older
snapshot loads with an empty ledger whose since is the snapshot's
saved_at (rows created before it count as applied) and one checkpoint at
its newest input.

Version 5 adds "tds_prompts": rolling baseline, pending steps, recent
readings, questions, answers and rejected explanations. Older snapshots
load with empty prompt state. Both storage adapters keep it in the same
snapshot transaction as sensors and events, with no schema change.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models import event_ledger as el
from koi.models.engine import EventKind, PondConfig, PondEvent, RawSample, WaterChemistryEngine, salt_tds_step
from koi.models.local_time import as_aware, local_date
from koi.models.profile import PondProfile, ProfileHistory
from koi.models.sensor_inputs import SensorInput, parse_timestamp
from koi.models.tds_prompts import TdsPrompts

log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 5

# Ledgered sensor inputs with a time in (after, until], oldest first
# (EngineRegistry.sensor_history): what a replay reads back.
SensorHistory = Callable[[Optional[datetime], datetime], list[SensorInput]]

# How far before a checkpoint's time the chemistry events it keeps reach:
# the sensor gate looks for a volume event within 6 hours of a reading.
_CHECKPOINT_EVENT_REACH = timedelta(hours=6)


class PondTwin:
    """Aggregate root. Not thread-safe on its own - EngineRegistry holds
    one lock per user around every method call here."""

    def __init__(
        self,
        chemistry: WaterChemistryEngine,
        evaporation: ev.EvaporationFeedEngine,
        algae: ae.AlgaeGrowthEngine,
    ):
        self.chemistry = chemistry
        self.evaporation = evaporation
        self.algae = algae
        # The pond's profile history (koi/models/profile.py), set by the
        # registry before each call. Not part of the snapshot: the
        # pond_profile table is its record. With it set, every step reads
        # the profile in force at its own time; without it the engines
        # keep the config they hold.
        self.profiles: Optional[ProfileHistory] = None
        # Newest reading of each sensor channel: {channel: {value,
        # reading_at, time_basis, row_id}}, and the model time of the
        # last sensor input applied (ingest_sensor_inputs).
        self.sensor_channels: dict[str, dict] = {}
        self.last_input_at: Optional[datetime] = None
        # The events applied and the chemistry checkpoints for replay
        # (event_ledger.py). A new twin has a genesis checkpoint.
        self.ledger = el.EventLedger()
        self.tds_prompts = TdsPrompts()
        self.ledger.add_checkpoint(el.Checkpoint(None, self._chemistry_checkpoint(None)))

    # ----------------------------------------------------------
    @classmethod
    def create(
        cls,
        pond_config: PondConfig,
        pond_depth_m: float = ev.DEFAULT_POND_DEPTH_M,
    ) -> "PondTwin":
        return cls(
            chemistry=WaterChemistryEngine(pond_config),
            evaporation=ev.EvaporationFeedEngine(
                ev.EvaporationConfig(
                    volume_litres=pond_config.volume_litres,
                    estimated_biomass_grams=pond_config.estimated_biomass_grams,
                    pond_depth_m=pond_depth_m,
                )
            ),
            algae=ae.AlgaeGrowthEngine(),
        )

    # ==========================================================
    # Interventions - fan out to every affected engine
    # ==========================================================
    def apply_event(self, event: PondEvent, now: Optional[datetime] = None) -> None:
        """Applies one logged intervention across all engines.

        With a profile history set, the event is applied under the
        profile in force at event.time (a percentage or litre volume is
        read against the pond as it was then), and the engines are left
        on the profile in force at now (default: the current time).

        Chemistry always sees the event (it owns the canonical event
        ledger used by the sensor gate's "was there a volume event
        nearby" check, so it must see even events that don't change its
        own pools). The other two engines are notified only for the
        events that physically affect them.

        This is the raw step: the event is not recorded in the ledger.
        Logged interventions go through record_event or reconcile_events.
        """
        self._chemistry_event(event, now)
        self._other_engines_event(event, now)

    def _validate_salt(self, event: PondEvent) -> None:
        if event.kind == EventKind.SALT:
            volume = (self.profiles.at(event.time).volume_l if self.profiles is not None
                      else self.chemistry.config.volume_litres)
            salt_tds_step(event.salt_grams, volume)

    def _chemistry_event(self, event: PondEvent, now: Optional[datetime]) -> None:
        """The chemistry half of apply_event, under the profile in force
        at event.time, leaving every engine on the profile at now."""
        self._validate_salt(event)
        if self.profiles is not None:
            self.use_profile(self.profiles.at(event.time))
        self.chemistry.apply_event(event)
        if self.profiles is not None:
            self.use_profile(self.profiles.at(now or datetime.now(timezone.utc)))

    def _other_engines_event(self, event: PondEvent, now: Optional[datetime]) -> None:
        """The evaporation and algae half of apply_event."""
        if self.profiles is not None:
            self.use_profile(self.profiles.at(event.time))

        if event.kind == EventKind.WATER_CHANGE:
            self.evaporation.apply_water_change(
                event.time, event.volume_percent, event.volume_litres
            )
            self.algae.apply_water_change(event.time, event.volume_percent)

        elif event.kind == EventKind.TOP_UP:
            self.evaporation.apply_topup(
                event.time, event.volume_percent, event.volume_litres
            )

        elif event.kind == EventKind.ALGAL_SCRUB:
            self.algae.apply_scrub(event.time, event.scrub_type)

        # FEEDING affects chemistry only (directly). Its downstream effect
        # on algae arrives through the projected NO3 term, not here.

        if self.profiles is not None:
            self.use_profile(self.profiles.at(now or datetime.now(timezone.utc)))

    # ==========================================================
    # Sensor inputs - every reading once, in event-time order
    # ==========================================================
    def ingest_sensor_inputs(self, inputs: list[SensorInput], history: Optional[SensorHistory] = None,
                             now: Optional[datetime] = None) -> tuple[list[dict], list[str]]:
        """Applies the poller's discovered inputs (oldest first) to the
        chemistry engine, each at its own time, and records each channel's
        newest reading. Returns one provenance entry per input and the
        sensor warnings of the newest input.

        An input older than the last one applied (a reading committed
        late, found by the scan's overlap) cannot be put back in the past:
        it is applied at the last input's time, and its entry says
        late=True with both times. Placing late readings by their sample
        time is issue #82.

        An input at or before the newest event already applied (an event
        logged between the node's upload and this poll) belongs before
        that event. With history (the ledgered inputs, for the replay)
        the chemistry engine is rewound and replayed with these inputs in
        their place (event_ledger.py), and their entries say
        replayed=True; without it, or without a checkpoint before them,
        they are applied as above."""
        for item in inputs:
            self._record_channels(item)
        newest_event = self.ledger.newest_event_time()
        if (history is not None and inputs and newest_event is not None
                and inputs[0].time <= newest_event):
            replay = self._replay(inputs[0].time, history, now, extra_inputs=inputs)
            if replay is not None:
                replayed = [{
                    "time": item.time.isoformat(), "time_basis": item.time_basis,
                    "model_time": item.time.isoformat(), "late": False, "replayed": True,
                    "row_ids": dict(item.row_ids), "missing": item.missing} for item in inputs]
                return replayed, replay["warnings"]

        provenance: list[dict] = []
        warnings: list[str] = []
        for item in inputs:
            late = self.last_input_at is not None and item.time < self.last_input_at
            model_time = self.last_input_at if late and self.last_input_at is not None else item.time
            warnings = self._chemistry_sensor(item, model_time)
            provenance.append({
                "time": item.time.isoformat(), "time_basis": item.time_basis,
                "model_time": model_time.isoformat(), "late": late,
                "row_ids": dict(item.row_ids), "missing": item.missing})
        return provenance, warnings

    def _record_channels(self, item: SensorInput) -> None:
        """Keeps each channel's newest reading (sensor_channels)."""
        for channel, value in item.channels.items():
            if value is None:
                continue
            current = self.sensor_channels.get(channel)
            if current is None or parse_timestamp(current["reading_at"]) <= item.time:
                self.sensor_channels[channel] = {
                    "value": value, "reading_at": item.time.isoformat(),
                    "time_basis": item.time_basis, "row_id": item.row_ids.get(channel)}

    def _chemistry_sensor(self, item: SensorInput, model_time: datetime) -> list[str]:
        """One sensor input into the chemistry engine at model_time,
        checkpointing first when it starts a new local day."""
        self._checkpoint_before(model_time)
        warnings = self.chemistry.ingest_sensor_sample(RawSample(
            time=model_time,
            ph=item.channels.get("ph"),
            tds=item.channels.get("tds"),
            temp_c=item.channels.get("temp"),
            lux=item.channels.get("lux"),
        ))
        self.last_input_at = model_time
        self._advance_newest(model_time)
        return warnings

    # ==========================================================
    # Event ledger - idempotent, time-ordered, replayable (#19)
    # ==========================================================
    def record_event(self, event_id: Optional[str], event: PondEvent, *, now: Optional[datetime] = None,
                     history: Optional[SensorHistory] = None, source: str = "api") -> dict:
        """Applies a logged intervention once (event_ledger.py).

        event_id None is a post from an app build without one (a legacy
        entry). An event_id already in the ledger, applied or deleted,
        changes nothing. An event before the newest input applied is put
        in its place by a replay when a checkpoint before it exists, and
        otherwise applied at the current state (placed_late).

        Returns {event_id, status: applied | duplicate | deleted,
        replayed, replayed_from, placed_late}."""
        now = now or datetime.now(timezone.utc)
        existing = self.ledger.get(event_id) if event_id is not None else None
        if existing is not None:
            return {"event_id": existing.event_id,
                    "status": "duplicate" if existing.status == el.APPLIED else "deleted",
                    "replayed": False, "replayed_from": None, "placed_late": existing.placed_late}
        self._validate_salt(event)
        entry = self.ledger.add(event_id, event, recorded_at=now, source=source)
        self._other_engines_event(event, now)
        outcome = self._place([entry], [], now, history)
        return {"event_id": entry.event_id, "status": "applied", **outcome, "placed_late": entry.placed_late}

    def reconcile_events(self, rows: list[dict], *, now: Optional[datetime] = None,
                         history: Optional[SensorHistory] = None) -> dict:
        """Brings the ledger in line with the pond's pondInterventions rows
        (pond_interventions_since over the last WINDOW): applies rows not
        in it, edits and deletes entries whose row changed or went, as
        described in event_ledger.py, with one replay for all of them.
        Returns what it did, by event_id."""
        now = now or datetime.now(timezone.utc)
        window_start = now - el.WINDOW
        report: dict = {"applied": [], "claimed": [], "revised": [], "deleted": [], "restored": [],
                        "not_replayable": [], "skipped": [], "replayed": False, "replayed_from": None}
        new: list[el.LedgerEntry] = []
        changed: list[tuple[el.LedgerEntry, dict, datetime]] = []  # entry, its old state, earliest time
        seen: set[str] = set()

        for row in rows:
            event = el.event_from_row(row)
            event_id = row.get("event_id")
            if event is None or event_id is None or event.time < window_start:
                report["skipped"].append({"row_id": row.get("id"), "reason": "unknown event_type" if event is None
                                          else "no event_id" if event_id is None else "outside the window"})
                continue
            try:
                self._validate_salt(event)
            except ValueError as exc:
                report["skipped"].append({"row_id": row.get("id"), "reason": str(exc)})
                continue
            event_id = el.normalise_event_id(event_id)
            seen.add(event_id)
            row_fp = el.event_values(event)
            entry = self.ledger.get(event_id)
            if entry is None:
                created = row.get("created_at")
                if self.ledger.since is not None and created is not None \
                        and parse_timestamp(created) < self.ledger.since:
                    continue  # logged before the ledger: applied by the twin of that time
                legacy = self.ledger.legacy_match(event)
                if legacy is not None:
                    self.ledger.rekey(legacy, event_id)
                    legacy.row_id, legacy.row = row.get("id"), row_fp
                    report["claimed"].append(event_id)
                    continue
                entry = self.ledger.add(event_id, event, recorded_at=now, source="reconcile",
                                        row_id=row.get("id"), row=row_fp)
                new.append(entry)
                continue
            if entry.row is None:
                # First sight of the row of a posted event: the post's
                # values stand (the row may hold phone-local time).
                entry.row_id, entry.row = row.get("id"), row_fp
                continue
            if entry.status == el.DELETED:
                changed.append((entry, entry.to_dict(), min(entry.model_time, event.time)))
                entry.status, entry.event, entry.model_time, entry.row = el.APPLIED, event, event.time, row_fp
                entry.row_id = row.get("id")
                report["restored"].append(event_id)
            elif row_fp != entry.row:
                changed.append((entry, entry.to_dict(), min(entry.model_time, event.time)))
                entry.event, entry.model_time, entry.row = event, event.time, row_fp
                entry.revision += 1
                report["revised"].append(event_id)

        for entry in list(self.ledger.entries.values()):
            if (entry.status == el.APPLIED and entry.row is not None and entry.event_id not in seen
                    and parse_timestamp(entry.row["time"]) >= window_start):
                changed.append((entry, entry.to_dict(), entry.model_time))
                entry.status = el.DELETED
                report["deleted"].append(entry.event_id)

        for entry in new:
            self._other_engines_event(entry.event, now)
        outcome = self._place(new, changed, now, history)
        if outcome.get("not_replayable"):
            # Edits and deletions that could not be replayed are undone in
            # the ledger, so the entry still describes the engine state.
            for entry, old, _ in changed:
                self.ledger.entries[entry.event_id] = el.LedgerEntry.from_dict(old)
                report["not_replayable"].append(entry.event_id)
                for key in ("revised", "deleted", "restored"):
                    if entry.event_id in report[key]:
                        report[key].remove(entry.event_id)
        report["applied"] = [e.event_id for e in new]
        report["replayed"], report["replayed_from"] = outcome["replayed"], outcome["replayed_from"]
        return report

    def _place(self, new: list[el.LedgerEntry], changed: list[tuple[el.LedgerEntry, dict, datetime]],
               now: datetime, history: Optional[SensorHistory]) -> dict:
        """Puts new entries (already in the ledger, not yet in chemistry)
        and changed ones into the chemistry engine: in order when all are
        new and none is before newest_input_at, else by one replay from
        the earliest. Without a checkpoint for the replay, new entries
        are placed late and {not_replayable: True} is returned when
        changed entries could not be applied."""
        newest = self.ledger.newest_input_at
        earliest = min([e.event.time for e in new] + [t for _, _, t in changed], default=None)
        if earliest is None:
            return {"replayed": False, "replayed_from": None}
        if not changed and (newest is None or earliest >= newest):
            for entry in sorted(new, key=lambda e: (e.event.time, e.seq)):
                self._checkpoint_before(entry.model_time)
                self._chemistry_event(entry.event, now)
                self._advance_newest(entry.model_time)
            return {"replayed": False, "replayed_from": None}
        replay = self._replay(earliest, history, now)
        if replay is not None:
            return {"replayed": True, "replayed_from": replay["replayed_from"]}
        for entry in sorted(new, key=lambda e: (e.event.time, e.seq)):
            if newest is not None and entry.event.time < newest:
                entry.model_time = newest + el.LATE_STEP
            self._chemistry_event(entry.event, now)
            self._advance_newest(entry.model_time)
            newest = self.ledger.newest_input_at
        out = {"replayed": False, "replayed_from": None}
        if changed:
            out["not_replayable"] = True
        return out

    def _replay(self, earliest: datetime, history: Optional[SensorHistory], now: Optional[datetime],
                extra_inputs: tuple[SensorInput, ...] | list[SensorInput] = ()) -> Optional[dict]:
        """Rewinds chemistry to the newest checkpoint before earliest and
        applies every input after it in model order: the ledgered sensor
        inputs (history), extra_inputs (this cycle's, not yet ledgered)
        and the live events. None, with nothing changed, when there is no
        such checkpoint or sensor inputs are needed and history is None."""
        checkpoint = self.ledger.checkpoint_before(earliest)
        if checkpoint is None:
            return None
        through = checkpoint.through
        sensor_until = self.last_input_at
        inputs: list[SensorInput] = []
        if sensor_until is not None and (through is None or sensor_until > through):
            if history is None:
                return None
            inputs = list(history(through, sensor_until))
        inputs += [i for i in extra_inputs if through is None or i.time > through]
        events = [e for e in self.ledger.live() if through is None or e.model_time > through]

        self.chemistry = self._restore_chemistry(checkpoint)
        self.ledger.drop_checkpoints_after(through)
        self.ledger.newest_input_at = through
        self.last_input_at = through if inputs else self.last_input_at
        now = now or datetime.now(timezone.utc)
        ordered: list[tuple] = [(i.time, 0, n, i) for n, i in enumerate(inputs)]
        ordered += [(e.model_time, 1, e.seq, e) for e in events]
        warnings: list[str] = []
        for _, rank, _, item in sorted(ordered, key=lambda o: o[:3]):
            if rank == 0:
                warnings = self._chemistry_sensor(item, item.time)
            else:
                self._checkpoint_before(item.model_time)
                self._chemistry_event(item.event, now)
                self._advance_newest(item.model_time)
        log.info("event_ledger_replay", extra={"replayed_from": through.isoformat() if through else None,
                                               "sensor_inputs": len(inputs), "events": len(events)})
        return {"replayed_from": through.isoformat() if through else None, "warnings": warnings}

    def _advance_newest(self, t: datetime) -> None:
        if self.ledger.newest_input_at is None or t > self.ledger.newest_input_at:
            self.ledger.newest_input_at = t

    def _checkpoint_before(self, t: datetime) -> None:
        """Before applying an input at t: takes a checkpoint at the newest
        input when t falls on a later local day, and prunes old ones."""
        last = self.ledger.newest_input_at
        if last is None or t <= last:
            return
        tz = self.chemistry.config.time_zone
        if local_date(t, tz) == local_date(last, tz):
            return
        if any(c.through == last for c in self.ledger.checkpoints):
            return
        self.ledger.add_checkpoint(el.Checkpoint(last, self._chemistry_checkpoint(last)))
        self.ledger.prune()

    def _chemistry_checkpoint(self, through: Optional[datetime]) -> dict:
        """The chemistry snapshot as of through, trimmed: only the day
        buckets from through's local day on and the events the sensor
        gate can still look back to. The rest cannot change in a replay
        from through, so _restore_chemistry takes it from the live
        engine."""
        snap = self.chemistry.to_snapshot()
        if through is not None:
            tz = self.chemistry.config.time_zone
            day = local_date(through, tz)
            snap["daily"] = {k: v.to_dict() for k, v in self.chemistry._daily.items()
                             if v.calendar_date(tz) >= day}
            reach = through - _CHECKPOINT_EVENT_REACH
            snap["events"] = [e.to_dict() for e in self.chemistry._events
                              if e.time >= (through - timedelta(hours=24)
                                            if e.kind == EventKind.FILTER_CLEAN else reach)]
        return snap

    def _restore_chemistry(self, checkpoint: el.Checkpoint) -> WaterChemistryEngine:
        """A chemistry engine at the checkpoint, on the live engine's
        config, with the live engine's older day buckets and events."""
        restored = WaterChemistryEngine.from_snapshot(checkpoint.chemistry)
        restored.config = self.chemistry.config
        through = checkpoint.through
        if through is not None:
            tz = self.chemistry.config.time_zone
            day = local_date(through, tz)
            older = {k: v for k, v in self.chemistry._daily.items() if v.calendar_date(tz) < day}
            restored._daily = {**older, **restored._daily}
            reach = through - _CHECKPOINT_EVENT_REACH
            restored._events = [e for e in self.chemistry._events
                                if e.time < (through - timedelta(hours=24)
                                             if e.kind == EventKind.FILTER_CLEAN else reach)] + restored._events
        return restored

    # ==========================================================
    # Environmental update - the poller's per-cycle entry point
    # ==========================================================
    def ingest_environment(
        self,
        *,
        now: datetime,
        sample: Optional[RawSample],
        evaporation_env: Optional[ev.DayEnvironment],
        algae_env: Optional[ae.AlgaeDayEnvironment],
        camera_samples: Optional[list] = None,
        rain_incoming: bool = False,
        rain_intensity: str = "unknown",
        measured_water_temp_c: Optional[float] = None,
        sensor_warnings: Optional[list] = None,
        observed_rain: Optional[dict] = None,
    ) -> dict:
        """Advances every engine to `now` using freshly polled conditions,
        then produces one assessment per domain.

        Order matters:
          1. camera assimilation FIRST, so the algae engine's level is
             corrected to the newest measurement before it is propagated
             forward - otherwise this cycle's growth is applied to a stale
             level and then immediately overwritten.
          2. sensor ingest into chemistry (which internally advances its
             own pools over elapsed time).
          3. evaporation and algae time-advance. With a profile history
             set, the evaporation integral is split at each profile
             change since the last advance, so each part uses the
             surface area in force over it.
          4. assess all three, under the profile in force at now.

        The poller passes sample=None: its readings went in through
        ingest_sensor_inputs first, and it passes the fresh water
        temperature as measured_water_temp_c and that step's warnings as
        sensor_warnings. With a sample, its temperature and warnings are
        used instead.

        observed_rain is the rolling 24-hour station rain
        (forecast_utils.observed_rain_24h); with the profile's depth it
        gives the chemistry rain term (issue #27).
        """
        result: dict = {"assimilated_camera_frames": 0}

        # --- 1. camera assimilation + rate refit ---
        if camera_samples:
            n = self.algae.ingest_camera_samples(camera_samples)
            result["assimilated_camera_frames"] = n
            if n > 0 and algae_env is not None:
                self.algae.refit_growth_rate(
                    recent_lux=algae_env.lux,
                    recent_temp_c=algae_env.temp_c,
                    recent_no3_ppm=algae_env.no3_ppm,
                    # ROUND 3 FIX: reuse the list ingest_camera_samples() just
                    # built instead of having refit_growth_rate re-parse the
                    # whole camera history a second time.
                    history_samples=self.algae._last_history_samples,
                )

        # --- 2. chemistry ---
        warnings: list = list(sensor_warnings or [])
        water_temp_c = measured_water_temp_c
        if sample is not None:
            warnings = self.chemistry.ingest_sensor_sample(sample)
            water_temp_c = sample.temp_c

        # --- 3. time advance ---
        if evaporation_env is not None:
            config_changes = None
            last = self.evaporation.last_advance_time
            if self.profiles is not None and last is not None:
                self.evaporation.config = self._evaporation_config(self.profiles.at(last))
                config_changes = [(at, self._evaporation_config(p))
                                  for at, p in self.profiles.changes_between(last, now)]
            self.evaporation.advance_state(
                now,
                evaporation_env,
                measured_water_temp_c=water_temp_c,
                config_changes=config_changes,
            )
        if algae_env is not None:
            self.algae.advance_state(now, algae_env)

        # --- 4. assess ---
        if self.profiles is not None:
            self.use_profile(self.profiles.at(now))
        chem = self.chemistry.assess(
            rain_incoming=rain_incoming,
            rain_intensity=rain_intensity,
            recent_sensor_warnings=warnings,
            observed_rain=observed_rain,
            depth_m=self.depth_m_at(now),
        )
        result["chemistry"] = chem
        result["evaporation"] = self.evaporation.assess(current_water_temp_c=water_temp_c)
        result["algae"] = self.algae.assess()
        return result

    # ==========================================================
    # Human severity ratings (algae only)
    # ==========================================================
    def apply_severity_rating(self, **kwargs) -> dict:
        """Routes a human algae rating into the algae engine.

        Unlike apply_event this touches ONE engine, because a severity
        judgement is an observation of algae specifically - it carries no
        information about nitrogen or water level. It still goes through
        the twin so it happens under the same per-user lock and lands in
        the same snapshot as everything else.
        """
        return self.algae.apply_severity_rating(**kwargs)

    def undo_severity_rating(self, rating_id=None) -> bool:
        return self.algae.undo_last_rating(rating_id)

    # ==========================================================
    # Cross-domain read: nitrate feeds the algae nutrient term
    # ==========================================================
    def no3_projection(
        self, avg_daily_tan_mg: float, fallback_temp_c: float, horizon_days: int
    ) -> list:
        """Runs the chemistry engine's own forward projection purely to
        extract its NO3 trajectory, which the algae projection consumes as
        its nutrient input. project_forward does not mutate chemistry
        state, so this is a safe read.

        Returns [] on failure - algae_engine.nutrient_factor falls back to
        a neutral value, so a missing nitrate projection degrades the
        algae forecast rather than breaking it.
        """
        try:
            projection = self.chemistry.project_forward(
                avg_daily_tan_mg=avg_daily_tan_mg,
                fallback_temp_c=fallback_temp_c,
                fallback_lux=None,
                horizon_days=horizon_days,
            )
            return [d["no3_ppm"] for d in projection.get("trajectory", [])]
        except Exception as exc:  # noqa: BLE001 - nutrient input is optional
            log.warning("no3_projection_unavailable", extra={"error": str(exc)})
            return []

    def input_cutoff(self) -> Optional[datetime]:
        """The newest input time this twin has consumed: the latest of its
        sensor sample times, logged event times (both in model time, the
        ledger's newest_input_at) and camera frame times. None before any
        input. Recorded on evaluation rows (koi/provenance.py)."""
        times = (self.ledger.newest_input_at, self.last_input_at, self.chemistry._last_ingest_time,
                 self.algae._last_camera_time)
        return max((as_aware(t) for t in times if t is not None), default=None)

    # ==========================================================
    # Snapshot
    # ==========================================================
    def to_snapshot(self) -> dict:
        return {
            "version": SNAPSHOT_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "chemistry": self.chemistry.to_snapshot(),
            "evaporation": self.evaporation.to_snapshot(),
            "algae": self.algae.to_snapshot(),
            "sensor_inputs": {
                "channels": self.sensor_channels,
                "last_input_at": self.last_input_at.isoformat() if self.last_input_at else None,
            },
            "event_ledger": self.ledger.to_dict(),
            "tds_prompts": self.tds_prompts.to_dict(),
        }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict,
        pond_depth_m: float = ev.DEFAULT_POND_DEPTH_M,
    ) -> "PondTwin":
        """Rehydrates a twin, tolerating these shapes:

          v5  - adds per-pond TDS baseline, prompts and rejected explanations
          v4  - the same without "tds_prompts": empty prompt state
          v3  - the same without "event_ledger": a new ledger (_load_ledger)
          v2  - the same without "sensor_inputs": no channel readings yet
          v1  - a BARE WaterChemistryEngine snapshot, which is what every
                existing row in pond_chemistry_state contains. Detected by
                the presence of 'tan_mg' at the top level. The chemistry
                state is preserved exactly; the other two engines start
                fresh, which is correct - there is no historical
                evaporation or algae state to recover, and both rebuild
                within a poll cycle or two.
          partial v2 - a namespaced snapshot missing one engine (e.g.
                written by a version that only knew about two of them).
        """
        if "tan_mg" in snapshot and "chemistry" not in snapshot:
            # --- legacy bare chemistry snapshot ---
            chemistry = WaterChemistryEngine.from_snapshot(snapshot)
            legacy = cls(
                chemistry=chemistry,
                evaporation=ev.EvaporationFeedEngine(
                    ev.EvaporationConfig(
                        volume_litres=chemistry.config.volume_litres,
                        estimated_biomass_grams=chemistry.config.estimated_biomass_grams,
                        pond_depth_m=pond_depth_m,
                    )
                ),
                algae=ae.AlgaeGrowthEngine(),
            )
            legacy._load_ledger(snapshot)
            return legacy

        chem_snap = snapshot.get("chemistry")
        if chem_snap is None:
            raise ValueError("Snapshot has no chemistry state and is not a legacy bare snapshot")
        chemistry = WaterChemistryEngine.from_snapshot(chem_snap)

        evap_snap = snapshot.get("evaporation")
        if evap_snap:
            evaporation = ev.EvaporationFeedEngine.from_snapshot(evap_snap)
        else:
            evaporation = ev.EvaporationFeedEngine(
                ev.EvaporationConfig(
                    volume_litres=chemistry.config.volume_litres,
                    estimated_biomass_grams=chemistry.config.estimated_biomass_grams,
                    pond_depth_m=pond_depth_m,
                )
            )

        algae_snap = snapshot.get("algae")
        algae = (
            ae.AlgaeGrowthEngine.from_snapshot(algae_snap)
            if algae_snap
            else ae.AlgaeGrowthEngine()
        )

        twin = cls(chemistry=chemistry, evaporation=evaporation, algae=algae)
        inputs = snapshot.get("sensor_inputs") or {}
        twin.sensor_channels = dict(inputs.get("channels") or {})
        last = inputs.get("last_input_at")
        twin.last_input_at = parse_timestamp(last) if last else None
        twin._load_ledger(snapshot)
        twin.tds_prompts = TdsPrompts.from_dict(snapshot.get("tds_prompts") or {})
        return twin

    def _load_ledger(self, snapshot: dict) -> None:
        """The snapshot's event ledger (v4), or for an older snapshot a new
        one: rows created after the snapshot was saved are not counted as
        applied (since = saved_at; a bare v1 snapshot has none, so the
        load time), and the one checkpoint is the state as loaded, at its
        newest input."""
        stored = snapshot.get("event_ledger")
        if stored:
            self.ledger = el.EventLedger.from_dict(stored)
            return
        saved_at = snapshot.get("saved_at")
        newest = max((as_aware(t) for t in (self.last_input_at, self.chemistry._last_ingest_time,
                                            *(e.time for e in self.chemistry._events)) if t is not None),
                     default=None)
        self.ledger = el.EventLedger(
            since=parse_timestamp(saved_at) if saved_at else datetime.now(timezone.utc),
            newest_input_at=newest)
        self.ledger.add_checkpoint(el.Checkpoint(newest, self._chemistry_checkpoint(newest)))

    # ==========================================================
    @staticmethod
    def _evaporation_config(profile: PondProfile) -> ev.EvaporationConfig:
        """The evaporation config under a profile. An unmeasured depth
        uses the model's default depth (DEFAULT_POND_DEPTH_M), which the
        forecast reports as assumed_depth_m."""
        return ev.EvaporationConfig(
            volume_litres=float(profile.volume_l),
            estimated_biomass_grams=float(profile.biomass_g),
            pond_depth_m=float(profile.depth_m) if profile.depth_m is not None else ev.DEFAULT_POND_DEPTH_M,
        )

    def depth_m_at(self, t: datetime) -> Optional[float]:
        """The profile's measured depth at t, or None when there is no
        profile or it has no depth. Unlike evaporation, the chemistry rain
        term does not fall back to DEFAULT_POND_DEPTH_M (issue #27)."""
        if self.profiles is None:
            return None
        depth = self.profiles.at(t).depth_m
        return float(depth) if depth is not None else None

    def use_profile(self, profile: PondProfile) -> None:
        """Puts every engine on one profile: volume, biomass, fish and tap
        water for chemistry, volume, biomass and depth for evaporation.
        The algae engine reads none of them."""
        self.chemistry.config = profile.pond_config(base=self.chemistry.config)
        self.evaporation.config = self._evaporation_config(profile)

    def sync_config(self, pond_config: PondConfig, pond_depth_m: Optional[float] = None) -> None:
        """Re-applies pond volume/biomass from UserData onto every engine.

        Called on each poll so that a user editing their pond volume in
        onboarding actually propagates - without this, a resized pond
        would keep computing ppm and loss percentages against the volume
        captured when its snapshot was first written.
        """
        self.chemistry.config = pond_config
        self.evaporation.config = ev.EvaporationConfig(
            volume_litres=pond_config.volume_litres,
            estimated_biomass_grams=pond_config.estimated_biomass_grams,
            pond_depth_m=pond_depth_m
            if pond_depth_m is not None
            else self.evaporation.config.pond_depth_m,
        )
