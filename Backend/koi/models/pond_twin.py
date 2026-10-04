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
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models.engine import EventKind, PondConfig, PondEvent, RawSample, WaterChemistryEngine
from koi.models.profile import PondProfile, ProfileHistory
from koi.models.sensor_inputs import SensorInput, parse_timestamp

log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 3


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
        """
        if self.profiles is not None:
            self.use_profile(self.profiles.at(event.time))

        self.chemistry.apply_event(event)

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
    def ingest_sensor_inputs(self, inputs: list[SensorInput]) -> tuple[list[dict], list[str]]:
        """Applies the poller's discovered inputs (oldest first) to the
        chemistry engine, each at its own time, and records each channel's
        newest reading. Returns one provenance entry per input and the
        sensor warnings of the newest input.

        An input older than the last one applied (a reading committed
        late, found by the scan's overlap) cannot be put back in the past:
        it is applied at the last input's time, and its entry says
        late=True with both times. Placing late readings by their sample
        time is issue #82."""
        provenance: list[dict] = []
        warnings: list[str] = []
        for item in inputs:
            late = self.last_input_at is not None and item.time < self.last_input_at
            model_time = self.last_input_at if late and self.last_input_at is not None else item.time
            sample = RawSample(
                time=model_time,
                ph=item.channels.get("ph"),
                tds=item.channels.get("tds"),
                temp_c=item.channels.get("temp"),
                lux=item.channels.get("lux"),
            )
            warnings = self.chemistry.ingest_sensor_sample(sample)
            self.last_input_at = model_time
            for channel, value in item.channels.items():
                if value is None:
                    continue
                current = self.sensor_channels.get(channel)
                if current is None or parse_timestamp(current["reading_at"]) <= item.time:
                    self.sensor_channels[channel] = {
                        "value": value, "reading_at": item.time.isoformat(),
                        "time_basis": item.time_basis, "row_id": item.row_ids.get(channel)}
            provenance.append({
                "time": item.time.isoformat(), "time_basis": item.time_basis,
                "model_time": model_time.isoformat(), "late": late,
                "row_ids": dict(item.row_ids), "missing": item.missing})
        return provenance, warnings

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
        }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict,
        pond_depth_m: float = ev.DEFAULT_POND_DEPTH_M,
    ) -> "PondTwin":
        """Rehydrates a twin, tolerating three shapes:

          v3  - the namespaced shape to_snapshot() emits now
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
            return cls(
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
        return twin

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
