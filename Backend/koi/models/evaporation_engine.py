"""
evaporation_engine.py

Lookahead model for the "Temperature & Feed Monitor" detail graph screen
(DetailGraphScreen metricType == 'temperature').

Answers two outcome questions:
  1. "When will I next need to top up the pond?"  -> evaporation projection
  2. "Should I be feeding less?"                  -> temperature-driven feed cap

Both are projections, in the same spirit as
WaterChemistryEngine.project_forward: take the current grounded state,
push it forward through the NEA forecast, and report the first day a
threshold is crossed.

--------------------------------------------------------------------
WHY THIS IS NOT JUST A TDS HEURISTIC
--------------------------------------------------------------------
Rising TDS is a *symptom* of evaporation (dissolved solids concentrate as
water volume drops), but it is confounded by feeding, rain dilution and
probe drift. So the primary model here is physical - a Penman open-water
evaporation calculation from the NEA forecast - and the TDS trend is used
separately as a *grounding check* on that physical model
(see cross_check_against_tds below). When the two agree, confidence is
high; when they diverge, the projection says so rather than pretending.

--------------------------------------------------------------------
THE EVAPORATION FORMULA
--------------------------------------------------------------------
Penman (1948) aerodynamic/mass-transfer term for open water:

    E [mm/day] = f(u) * (es_water - ea_air)

    f(u)  = 0.26 * (1 + 0.54 * u2)      u2 = wind speed at 2 m, m/s
    es, ea in mmHg
    es_water = saturation vapour pressure at the WATER surface temperature
    ea_air   = actual vapour pressure of air = es(T_air) * RH/100

Sanity check for typical Singapore conditions (water 29C, air 30C,
RH 75%, wind 3 m/s) this yields ~4.2 mm/day, which sits in the accepted
3.5-5 mm/day band for open water in the tropics. The constants 0.26 and
0.54 are Penman's originals and are exposed as WIND_FN_A / WIND_FN_B so
they can be calibrated against this specific pond later (see the
calibration note in the write-up).

Volume conversion: 1 mm of depth over 1 m^2 of surface = 1 litre. So
    litres_lost_per_day = E_mm_per_day * surface_area_m2

Surface area is NOT stored anywhere in the schema, so it is derived as
volume / depth, with the depth from the pond's profile (pond_profile,
migration 0008) when the owner has measured it. Otherwise an assumed
depth is used: koi ponds are typically 1.0-1.5 m deep; DEFAULT_POND_DEPTH_M
is 1.2. This is the
single largest source of error in the volume projection and is reported
back in the response so the UI can caveat it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# ============================================================
# Physical constants / tunables
# ============================================================

# Penman wind function coefficients: f(u) = A * (1 + B * u2)
WIND_FN_A = 0.26
WIND_FN_B = 0.54

# Pond geometry fallback for a profile with no measured depth.
DEFAULT_POND_DEPTH_M = 1.2

# NEA reports wind at 10 m; evaporation formulae want 2 m. Standard
# logarithmic wind profile correction for open terrain.
_WIND_10M_TO_2M = 0.748

# NEA forecasts wind for open/coastal conditions. A garden koi pond is
# almost always sheltered by walls, fencing, planting or a house, and sees
# a fraction of that. Without this correction the model badly over-reads:
# for the supplied 18 Aug payload, NEA's 4-day outlook bands work out to a
# 15-knot midpoint (~7.7 m/s) while the live nea_telemetry anemometer that
# same hour reported 6.9 KNOTS (~3.5 m/s) - a 2x discrepancy that flows
# straight into evaporation as roughly +45%. 0.6 brings projected
# evaporation back into the 4-5 mm/day band that matches both the live
# telemetry and published Singapore open-water evaporation figures.
#
# This is the single most calibratable constant in this file: if you ever
# log actual top-up volumes against elapsed days, fit this first.
POND_SHELTER_FACTOR = 0.6

# NEA's 4-day outlook gives a forecast code/text but no rainfall depth.
# These are coarse daily accumulations (mm) used to offset evaporation.
# Deliberately conservative - under-crediting rain makes the top-up
# prediction early rather than late, which is the safer error direction.
_RAIN_MM_BY_CATEGORY = {
    "heavy": 12.0,
    "moderate": 4.0,
    "light": 1.0,
    "unknown": 0.0,
}

# Fraction of pond volume lost before a top-up is advised.
TOPUP_WATCH_LOSS_PCT = 5.0    # "plan one"
TOPUP_ACTION_LOSS_PCT = 10.0  # "do it now" - evaporative concentration of
                              # dissolved solids becomes measurable and
                              # water level starts exposing pump intakes


# ============================================================
# Vapour pressure helpers
# ============================================================

_KPA_TO_MMHG = 7.50062


def saturation_vapour_pressure_kpa(temp_c: float) -> float:
    """Tetens equation - saturation vapour pressure over water, kPa."""
    return 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))


def saturation_vapour_pressure_mmhg(temp_c: float) -> float:
    return saturation_vapour_pressure_kpa(temp_c) * _KPA_TO_MMHG


def evaporation_mm_per_day(
    water_temp_c: float,
    air_temp_c: float,
    relative_humidity_pct: float,
    wind_speed_ms: float,
    wind_measured_at_10m: bool = True,
    shelter_factor: float = POND_SHELTER_FACTOR,
) -> float:
    """Penman aerodynamic term. Returns mm/day, floored at 0 (condensation
    onto the surface is physically possible but negligible for a pond and
    would otherwise let the model predict the pond *gaining* water on a
    humid still night, which would be misleading in the UI).

    shelter_factor discounts the forecast/open-terrain wind down to what a
    sheltered garden pond surface actually experiences - see
    POND_SHELTER_FACTOR. Pass 1.0 to disable."""
    u2 = wind_speed_ms * (_WIND_10M_TO_2M if wind_measured_at_10m else 1.0) * shelter_factor
    es_water = saturation_vapour_pressure_mmhg(water_temp_c)
    ea_air = saturation_vapour_pressure_mmhg(air_temp_c) * (
        min(max(relative_humidity_pct, 0.0), 100.0) / 100.0
    )
    wind_fn = WIND_FN_A * (1.0 + WIND_FN_B * max(u2, 0.0))
    return max(wind_fn * (es_water - ea_air), 0.0)


# ============================================================
# Feed guidance
# ============================================================

# Koi daily ration as a percentage of body weight, by water temperature.
# Standard aquaculture feeding tables: metabolism climbs with temperature
# up to roughly 28C, then the ration is pulled BACK above ~30C because
# warm water holds less dissolved oxygen and digestion load becomes a
# respiratory risk rather than a growth opportunity. That upper rollback
# is the clinically important part in Singapore, where the pond sits at
# the top of the curve nearly year-round.
_FEED_TABLE = [
    # (max_temp_c, pct_body_weight_per_day, note)
    (10.0, 0.0,  "Below 10C koi barely digest - do not feed."),
    (15.0, 0.5,  "Cold water: wheatgerm only, once every 1-2 days."),
    (20.0, 1.0,  "Cool water: reduced ration, easily digestible feed."),
    (26.0, 2.0,  "Comfortable range: normal ration."),
    (30.0, 2.5,  "Peak metabolism: full ration, split across 2-3 feeds."),
    (33.0, 1.5,  "Warm water holds less oxygen - cut the ration back."),
    (99.0, 0.75, "Heat stress: minimal feeding, prioritise aeration."),
]


def feed_cap_grams_per_day(biomass_grams: float, water_temp_c: float) -> tuple[float, str]:
    """Returns (recommended_max_grams_per_day, rationale)."""
    for max_temp, pct, note in _FEED_TABLE:
        if water_temp_c < max_temp:
            return biomass_grams * (pct / 100.0), note
    return biomass_grams * 0.0075, _FEED_TABLE[-1][2]


# ============================================================
# Config + assessment types
# ============================================================

@dataclass
class EvaporationConfig:
    volume_litres: float
    estimated_biomass_grams: float
    pond_depth_m: float = DEFAULT_POND_DEPTH_M

    @property
    def surface_area_m2(self) -> float:
        """volume(L) -> m^3 -> divided by depth. Guarded against a zero or
        missing depth so a bad config can't produce an infinite area."""
        depth = self.pond_depth_m if self.pond_depth_m and self.pond_depth_m > 0 else DEFAULT_POND_DEPTH_M
        return (self.volume_litres / 1000.0) / depth

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class DayEnvironment:
    """One day's environmental assumption - used both for real polled
    conditions (advance_state) and for forecast days (project_forward)."""
    air_temp_c: float
    relative_humidity_pct: float
    wind_speed_ms: float
    rain_category: str = "unknown"
    water_temp_c: Optional[float] = None  # if None, derived from air temp
    rain_mm_measured: Optional[float] = None  # live gauge overrides category


@dataclass
class EvaporationAssessment:
    """Current-state readout, the evaporation analogue of
    WaterChemistryAssessment. Pushed to Supabase every poll cycle so the
    dashboard card and its alert badge can read a cached value instead of
    triggering a recompute."""
    status: str          # "Green" | "Amber" | "Red"
    category: str        # "Stable" | "Top-Up Soon" | "Top-Up Due"
    loss_litres: float
    loss_pct: float
    evaporation_mm_per_day: float
    loss_litres_per_day: float
    water_temp_c: Optional[float]
    feed_cap_grams: float
    feed_note: str
    days_to_topup: Optional[int]
    advisory: str
    topup_now: bool

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ============================================================
# Water temperature from air temperature
# ============================================================

def fit_water_air_offset(water_temps: list, air_temps: list) -> Optional[float]:
    """A pond's water temperature tracks air temperature with a damped,
    lagged offset. Rather than assume that offset, fit it from this pond's
    own paired history: mean(water) - mean(air).

    Deliberately a mean offset rather than a regression slope - with daily
    means over Singapore's narrow ~25-34C air band, a fitted slope would
    be dominated by noise. A constant offset is the honest amount of
    structure this data supports.
    """
    n = min(len(water_temps), len(air_temps))
    if n < 3:
        return None
    pairs = [(w, a) for w, a in zip(water_temps[:n], air_temps[:n], strict=True)
             if w is not None and a is not None]
    if len(pairs) < 3:
        return None
    return (sum(p[0] for p in pairs) / len(pairs)) - (sum(p[1] for p in pairs) / len(pairs))


def predict_water_temp(air_temp_c: float, offset_c: Optional[float]) -> float:
    """Applies the fitted offset. Falls back to -1.0C (ponds usually sit
    slightly below mean air temperature thanks to evaporative cooling and
    thermal mass) when no history is available."""
    return air_temp_c + (offset_c if offset_c is not None else -1.0)


# ============================================================
# Engine  (STATEFUL)
# ============================================================

class EvaporationFeedEngine:
    """Persistent, stateful per-pond evaporation model.

    STATEFULNESS - WHY IT CHANGED
    -----------------------------
    The first version of this engine was a pure request-time projector: it
    estimated "loss so far" retroactively by taking the elapsed days since
    the last logged WATER_TOPUP and multiplying by ONE representative day's
    conditions. That was a reasonable stopgap but structurally wrong for
    two reasons:

      1. It threw away real weather. A fortnight containing a monsoon week
         and a dry hot week got the same answer as a fortnight of flat
         average conditions.
      2. It had nothing to "reset". A logged top-up couldn't update any
         state because there was no state - the number was recomputed from
         a timestamp on every request.

    This version integrates loss continuously. The poller calls
    advance_state() every cycle with the conditions actually measured in
    that window, so _cumulative_loss_litres is a genuine running integral
    over real weather. A logged top-up calls apply_topup(), which removes
    the replaced volume from that integral - a real state reset.

    NOT thread-safe on its own. Callers hold the per-user lock; see
    PondTwin and EngineRegistry.
    """

    def __init__(self, config: EvaporationConfig):
        self.config = config
        self._cumulative_loss_litres = 0.0
        self._last_advance_time: Optional[datetime] = None
        self._water_air_offset_c: Optional[float] = None
        self._last_topup_time: Optional[datetime] = None
        # Rolling record of the conditions each poll actually saw, used to
        # report a representative recent rate and to refit the water/air
        # offset. Bounded so the persisted snapshot cannot grow unbounded.
        self._recent_env: list = []
        self._last_evaporation_mm_per_day = 0.0

    @property
    def last_advance_time(self) -> Optional[datetime]:
        """When the loss integral was last advanced; None before the first
        advance."""
        return self._last_advance_time

    # ----------------------------------------------------------
    # State advancement (called by the poller each cycle)
    # ----------------------------------------------------------
    def advance_state(
        self,
        now: datetime,
        env: DayEnvironment,
        measured_water_temp_c: Optional[float] = None,
        config_changes: Optional[list[tuple[datetime, EvaporationConfig]]] = None,
    ) -> float:
        """Integrates evaporative loss over the interval since the last
        call, using the conditions measured for THIS interval. Returns the
        litres lost during this step (0.0 on the very first call, which
        only establishes the time baseline).

        config_changes are (time, config) pairs for pond profile changes
        (koi/models/profile.py): the integral is split at each change
        that falls inside the integrated window, and each part uses the
        surface area in force over it. A change before the window applies
        to all of it; one after now is ignored. self.config ends as the
        config in force at now.

        measured_water_temp_c, when the sensor provides it, is preferred
        over the air-temp-derived estimate - it is the actual evaporating
        surface temperature and drives es_water directly.
        """
        water_temp = (
            measured_water_temp_c
            if measured_water_temp_c is not None
            else (env.water_temp_c if env.water_temp_c is not None
                  else predict_water_temp(env.air_temp_c, self._water_air_offset_c))
        )

        e_mm_per_day = evaporation_mm_per_day(
            water_temp_c=water_temp,
            air_temp_c=env.air_temp_c,
            relative_humidity_pct=env.relative_humidity_pct,
            wind_speed_ms=env.wind_speed_ms,
        )
        self._last_evaporation_mm_per_day = e_mm_per_day

        # Record conditions for the offset fit regardless of whether this
        # is the first call.
        self._recent_env.append({
            "t": now.isoformat(),
            "air": env.air_temp_c,
            "water": water_temp,
            "rh": env.relative_humidity_pct,
            "wind": env.wind_speed_ms,
            "evap_mm_day": round(e_mm_per_day, 4),
        })
        if len(self._recent_env) > 200:
            self._recent_env = self._recent_env[-200:]

        changes = sorted((c for c in config_changes or [] if c[0] <= now), key=lambda c: c[0])

        if self._last_advance_time is None:
            self._last_advance_time = now
            if changes:
                self.config = changes[-1][1]
            return 0.0

        elapsed_days = (now - self._last_advance_time).total_seconds() / 86400.0
        if elapsed_days <= 0:
            # Clock went backwards, or a duplicate tick. Do not integrate.
            self._last_advance_time = now
            if changes:
                self.config = changes[-1][1]
            return 0.0

        # Guard against a long outage silently dumping a huge one-shot
        # loss into the integral. If the process was down for a week, the
        # honest thing is to integrate at most a day and flag the gap by
        # leaving the rest unaccounted rather than inventing a week of
        # weather we never observed.
        capped_days = min(elapsed_days, 1.0)

        # Rain: prefer the live gauge, fall back to the forecast category.
        if env.rain_mm_measured is not None:
            rain_mm_per_day = env.rain_mm_measured
        else:
            rain_mm_per_day = _RAIN_MM_BY_CATEGORY.get(env.rain_category, 0.0)

        net_mm = (e_mm_per_day - rain_mm_per_day) * capped_days
        window_start = now - timedelta(days=capped_days)
        before = [c for c in changes if c[0] <= window_start]
        inside = [c for c in changes if c[0] > window_start]
        if before:
            self.config = before[-1][1]
        if not inside:
            step_litres = net_mm * self.config.surface_area_m2
        else:
            step_litres = 0.0
            seg_start = window_start
            for at, config in inside:
                seg_days = (at - seg_start).total_seconds() / 86400.0
                step_litres += (e_mm_per_day - rain_mm_per_day) * seg_days * self.config.surface_area_m2
                self.config = config
                seg_start = at
            seg_days = (now - seg_start).total_seconds() / 86400.0
            step_litres += (e_mm_per_day - rain_mm_per_day) * seg_days * self.config.surface_area_m2

        self._cumulative_loss_litres = max(
            self._cumulative_loss_litres + step_litres, 0.0
        )
        self._last_advance_time = now
        return step_litres

    def refit_water_air_offset(self, water_temps: list, air_temps: list) -> Optional[float]:
        fitted = fit_water_air_offset(water_temps, air_temps)
        if fitted is not None:
            self._water_air_offset_c = fitted
        return self._water_air_offset_c

    # ----------------------------------------------------------
    # Intervention handling (the "state reset")
    # ----------------------------------------------------------
    def apply_topup(
        self,
        time: datetime,
        volume_percent: Optional[float] = None,
        volume_litres: Optional[float] = None,
    ) -> None:
        """A logged WATER_TOPUP puts water back. Removes the replaced
        volume from the accumulated loss integral.

        Live pondInterventions rows log top-ups as volume_percentage with
        volume_litres null (confirmed against real data), so the percent
        path is the one that actually fires in practice.

        Note the percentage is read as a percentage OF POND VOLUME, which
        matches how the Flutter quick-log modal presents it ("top up 25%").
        A top-up larger than the outstanding loss simply clears it to zero
        rather than going negative - you cannot have less than no loss.
        """
        litres = self._resolve_litres(volume_percent, volume_litres)
        self._cumulative_loss_litres = max(self._cumulative_loss_litres - litres, 0.0)
        self._last_topup_time = time

    def apply_water_change(
        self,
        time: datetime,
        volume_percent: Optional[float] = None,
        volume_litres: Optional[float] = None,
    ) -> None:
        """A water change also refills the pond, so it resets the loss
        integral exactly like a top-up does. Tracked separately only so
        the two remain distinguishable if the models ever diverge (a water
        change replaces water, a top-up adds to it - identical from a
        volume standpoint, different for chemistry)."""
        self.apply_topup(time, volume_percent, volume_litres)

    def _resolve_litres(
        self, volume_percent: Optional[float], volume_litres: Optional[float]
    ) -> float:
        if volume_litres is not None:
            return max(float(volume_litres), 0.0)
        if volume_percent is not None:
            pct = min(max(float(volume_percent) / 100.0, 0.0), 1.0)
            return pct * self.config.volume_litres
        # Neither supplied - assume the owner topped the pond back to full,
        # which is the common real-world case for an unquantified top-up.
        return self._cumulative_loss_litres

    # ----------------------------------------------------------
    # Current-state assessment
    # ----------------------------------------------------------
    @property
    def cumulative_loss_litres(self) -> float:
        return self._cumulative_loss_litres

    @property
    def loss_pct(self) -> float:
        if self.config.volume_litres <= 0:
            return 0.0
        return (self._cumulative_loss_litres / self.config.volume_litres) * 100.0

    def assess(
        self,
        current_water_temp_c: Optional[float] = None,
        days_to_topup: Optional[int] = None,
    ) -> EvaporationAssessment:
        pct = self.loss_pct
        area = self.config.surface_area_m2
        litres_per_day = self._last_evaporation_mm_per_day * area

        water_temp = current_water_temp_c
        if water_temp is None and self._recent_env:
            water_temp = self._recent_env[-1].get("water")

        cap_g, cap_note = feed_cap_grams_per_day(
            self.config.estimated_biomass_grams,
            water_temp if water_temp is not None else 28.0,
        )

        if pct >= TOPUP_ACTION_LOSS_PCT:
            status, category = "Red", "Top-Up Due"
            advisory = (
                f"Estimated {pct:.1f}% of pond volume lost to evaporation since the "
                f"last logged top-up (~{self._cumulative_loss_litres:.0f} L). Top up now - "
                f"falling level concentrates dissolved solids and can expose pump intakes."
            )
            topup_now = True
        elif pct >= TOPUP_WATCH_LOSS_PCT:
            status, category = "Amber", "Top-Up Soon"
            advisory = (
                f"About {pct:.1f}% of volume lost (~{self._cumulative_loss_litres:.0f} L) at "
                f"roughly {litres_per_day:.0f} L/day. Plan a top-up in the next few days."
            )
            topup_now = False
        else:
            status, category = "Green", "Stable"
            advisory = (
                f"Water level stable - about {litres_per_day:.0f} L/day evaporating, "
                f"{pct:.1f}% of volume lost since the last top-up."
            )
            topup_now = False

        return EvaporationAssessment(
            status=status,
            category=category,
            loss_litres=round(self._cumulative_loss_litres, 2),
            loss_pct=round(pct, 3),
            evaporation_mm_per_day=round(self._last_evaporation_mm_per_day, 3),
            loss_litres_per_day=round(litres_per_day, 2),
            water_temp_c=round(water_temp, 2) if water_temp is not None else None,
            feed_cap_grams=round(cap_g, 1),
            feed_note=cap_note,
            days_to_topup=days_to_topup,
            advisory=advisory,
            topup_now=topup_now,
        )

    # ----------------------------------------------------------
    # Forward projection
    # ----------------------------------------------------------
    def project_forward(
        self,
        *,
        daily_environment: list,
        horizon_days: int = 14,
        shelter_factor: float = POND_SHELTER_FACTOR,
    ) -> dict:
        """Projects cumulative loss forward from the CURRENT accumulated
        state (no longer from a retroactive estimate). Operates on local
        copies only - never mutates the engine - so it is safe to call
        from a read-only endpoint.

        When the horizon outruns daily_environment, the LAST entry repeats:
        NEA only publishes 4 days, and holding the final forecast day
        steady is more honest than inventing a trend. Reported back as
        days_using_real_forecast.
        """
        if not daily_environment:
            raise ValueError("daily_environment must contain at least one day")
        if horizon_days < 1:
            raise ValueError("horizon_days must be >= 1")

        area = self.config.surface_area_m2
        volume = self.config.volume_litres

        cumulative = self._cumulative_loss_litres
        trajectory = []
        first_watch_day = None
        first_action_day = None

        # Day 0 - the pond may ALREADY be past a threshold on accumulated
        # state. Same reasoning as the chemistry engine's day-0 check:
        # without this, a pond needing a top-up today reports "in N days".
        start_pct = (cumulative / volume) * 100.0 if volume > 0 else 0.0
        if start_pct >= TOPUP_ACTION_LOSS_PCT:
            first_action_day = 0
        if start_pct >= TOPUP_WATCH_LOSS_PCT:
            first_watch_day = 0

        for day_index in range(horizon_days):
            days_from_now = day_index + 1
            env = daily_environment[min(day_index, len(daily_environment) - 1)]

            water_temp = env.water_temp_c
            if water_temp is None:
                water_temp = predict_water_temp(env.air_temp_c, self._water_air_offset_c)

            e_mm = evaporation_mm_per_day(
                water_temp_c=water_temp,
                air_temp_c=env.air_temp_c,
                relative_humidity_pct=env.relative_humidity_pct,
                wind_speed_ms=env.wind_speed_ms,
                shelter_factor=shelter_factor,
            )
            rain_mm = _RAIN_MM_BY_CATEGORY.get(env.rain_category, 0.0)

            net_litres = (e_mm - rain_mm) * area
            cumulative = max(cumulative + net_litres, 0.0)
            loss_pct = (cumulative / volume) * 100.0 if volume > 0 else 0.0

            if loss_pct >= TOPUP_WATCH_LOSS_PCT and first_watch_day is None:
                first_watch_day = days_from_now
            if loss_pct >= TOPUP_ACTION_LOSS_PCT and first_action_day is None:
                first_action_day = days_from_now

            cap_g, cap_note = feed_cap_grams_per_day(
                self.config.estimated_biomass_grams, water_temp
            )

            trajectory.append({
                "days_from_now": days_from_now,
                "evaporation_mm": round(e_mm, 3),
                "rain_offset_mm": rain_mm,
                "net_loss_litres": round(net_litres, 2),
                "cumulative_loss_litres": round(cumulative, 2),
                "cumulative_loss_pct": round(loss_pct, 3),
                "water_temp_c_assumed": round(water_temp, 2),
                "air_temp_c_assumed": env.air_temp_c,
                "humidity_pct_assumed": env.relative_humidity_pct,
                "wind_ms_assumed": env.wind_speed_ms,
                "feed_cap_grams": round(cap_g, 1),
                "feed_note": cap_note,
            })

        candidates = [d for d in (first_watch_day, first_action_day) if d is not None]
        avg_daily_mm = sum(t["evaporation_mm"] for t in trajectory) / len(trajectory)

        return {
            "predicted_topup_days_from_now": min(candidates) if candidates else None,
            "first_watch_days_from_now": first_watch_day,
            "first_action_days_from_now": first_action_day,
            "avg_evaporation_mm_per_day": round(avg_daily_mm, 3),
            "avg_loss_litres_per_day": round(avg_daily_mm * area, 2),
            "starting_loss_litres": round(self._cumulative_loss_litres, 2),
            "starting_loss_pct": round(start_pct, 3),
            "surface_area_m2": round(area, 3),
            "assumed_depth_m": self.config.pond_depth_m,
            "wind_shelter_factor": shelter_factor,
            "water_air_offset_c": self._water_air_offset_c,
            "days_using_real_forecast": min(len(daily_environment), horizon_days),
            "volume_litres": volume,
            "last_topup_at": self._last_topup_time.isoformat() if self._last_topup_time else None,
            "trajectory": trajectory,
            "thresholds": {
                "watch_loss_pct": TOPUP_WATCH_LOSS_PCT,
                "action_loss_pct": TOPUP_ACTION_LOSS_PCT,
            },
        }

    # ----------------------------------------------------------
    # Snapshot serialization
    # ----------------------------------------------------------
    def to_snapshot(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "cumulative_loss_litres": self._cumulative_loss_litres,
            "last_advance_time": self._last_advance_time.isoformat() if self._last_advance_time else None,
            "water_air_offset_c": self._water_air_offset_c,
            "last_topup_time": self._last_topup_time.isoformat() if self._last_topup_time else None,
            "recent_env": self._recent_env[-200:],
            "last_evaporation_mm_per_day": self._last_evaporation_mm_per_day,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "EvaporationFeedEngine":
        engine = cls(EvaporationConfig(**snapshot["config"]))
        engine._cumulative_loss_litres = snapshot.get("cumulative_loss_litres", 0.0)
        lat = snapshot.get("last_advance_time")
        engine._last_advance_time = datetime.fromisoformat(lat) if lat else None
        engine._water_air_offset_c = snapshot.get("water_air_offset_c")
        ltt = snapshot.get("last_topup_time")
        engine._last_topup_time = datetime.fromisoformat(ltt) if ltt else None
        engine._recent_env = list(snapshot.get("recent_env", []))
        engine._last_evaporation_mm_per_day = snapshot.get("last_evaporation_mm_per_day", 0.0)
        return engine


# ============================================================
# Grounding: does the measured TDS trend agree with the model?
# ============================================================

def cross_check_against_tds(
    *,
    predicted_daily_loss_litres: float,
    volume_litres: float,
    observed_tds_slope_ppm_per_day: Optional[float],
    current_tds_ppm: Optional[float],
) -> dict:
    """Evaporation removes water but leaves dissolved solids behind, so
    TDS should concentrate at a predictable rate:

        dTDS/dt = TDS * (loss_per_day / volume)

    Comparing that against the slope the probe actually measured is a
    genuine independent check - the two come from completely different
    sources (NEA weather vs the pond's own TDS sensor), so agreement is
    real corroboration rather than a model confirming itself.

    Verdicts:
      corroborated     - measured rise within tolerance of predicted
      under_predicted  - TDS climbing faster than evaporation explains
                         (suspect unlogged feeding, or hard top-up water)
      over_predicted   - TDS flat/falling while the model says concentrating
                         (suspect unlogged top-ups, rain ingress, probe drift)
      insufficient_data
    """
    if (
        observed_tds_slope_ppm_per_day is None
        or current_tds_ppm is None
        or current_tds_ppm <= 0
        or volume_litres <= 0
    ):
        return {
            "verdict": "insufficient_data",
            "predicted_tds_slope_ppm_per_day": None,
            "observed_tds_slope_ppm_per_day": observed_tds_slope_ppm_per_day,
            "confidence": "low",
        }

    predicted_slope = current_tds_ppm * (predicted_daily_loss_litres / volume_litres)

    # TDS probes are noisy and daily aggregation is coarse, so require the
    # observed slope within a factor of ~2.5 of prediction (or 1 ppm/day
    # absolute, covering the case where both are near zero).
    abs_tolerance = 1.0
    diff = observed_tds_slope_ppm_per_day - predicted_slope

    if abs(diff) <= abs_tolerance or (
        predicted_slope > 0
        and 0.4 <= (observed_tds_slope_ppm_per_day / predicted_slope) <= 2.5
    ):
        verdict, confidence = "corroborated", "high"
    elif diff > 0:
        verdict, confidence = "under_predicted", "medium"
    else:
        verdict, confidence = "over_predicted", "medium"

    return {
        "verdict": verdict,
        "predicted_tds_slope_ppm_per_day": round(predicted_slope, 4),
        "observed_tds_slope_ppm_per_day": round(observed_tds_slope_ppm_per_day, 4),
        "confidence": confidence,
    }
