"""
engine.py

Python port of the Dart WaterChemistryEngine. Same rules, same rate
constants, same gating logic - this file is the single source of chemistry
truth shared by the event endpoints and the poller.

Design note: this class is stateful and mutated in place. It is NOT
thread-safe on its own - callers (app.py, poller.py) are responsible for
holding a per-user lock around any sequence of mutate-then-persist calls.
See EngineRegistry in registry.py for how that's enforced.

`project_forward()` is the one exception to "stateful and mutated in
place": it operates on local copies of the pools only and never touches
self, so it's safe to call from a read-only endpoint without going through
the mutate-then-persist contract (though routing it through
registry.with_engine is still fine/simplest, since the persisted snapshot
comes out unchanged).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional

from koi.models.local_time import DEFAULT_POND_TIME_ZONE, local_date, local_day_key

log = logging.getLogger(__name__)

# ============================================================
# 1. SENSOR GATING LAYER
# ============================================================

class SensorChannel(str, Enum):
    PH = "ph"
    TDS = "tds"
    TEMP = "temp"
    LUX = "lux"


class SampleVerdict(str, Enum):
    OK = "ok"
    SUSPECT_STALE = "suspect_stale"
    SUSPECT_RANGE = "suspect_range"
    SUSPECT_RATE = "suspect_rate"
    REJECTED = "rejected"
    # The input carried no reading for the channel (koi/models/sensor_inputs.py).
    MISSING = "missing"


@dataclass
class ChannelReading:
    # value is None only for a MISSING channel with no earlier trusted value.
    value: Optional[float]
    raw_value: Optional[float]
    verdict: SampleVerdict
    reason: Optional[str] = None

    @property
    def is_trusted(self) -> bool:
        return self.verdict == SampleVerdict.OK


@dataclass
class RawSample:
    # None: the channel was not in this input. The other channels are
    # still used.
    time: datetime
    ph: Optional[float]
    tds: Optional[float]
    temp_c: Optional[float]
    lux: Optional[float]


@dataclass
class GatedSample:
    time: datetime
    channels: dict  # SensorChannel -> ChannelReading

    @property
    def ph(self) -> ChannelReading:
        return self.channels[SensorChannel.PH]

    @property
    def tds(self) -> ChannelReading:
        return self.channels[SensorChannel.TDS]

    @property
    def temp(self) -> ChannelReading:
        return self.channels[SensorChannel.TEMP]

    @property
    def lux(self) -> ChannelReading:
        return self.channels[SensorChannel.LUX]


# (min, max, max_rate_per_hour) - 0 rate = no rate check (LUX swings fast legitimately)
_BOUNDS = {
    SensorChannel.PH: (4.0, 10.0, 1.0),
    SensorChannel.TEMP: (-5.0, 45.0, 5.0),
    SensorChannel.TDS: (0.0, 3000.0, 150.0),
    SensorChannel.LUX: (0.0, 150000.0, 0.0),
}
_TDS_OUT_OF_WATER_THRESHOLD = 5.0

# Light below this (lux, pond sensor) counts as night: the cut for the
# night pH samples here and for the hypoxia flag (koi/models/hypoxia.py).
NIGHT_LUX_THRESHOLD = 20.0
_STALE_RUN_LENGTH = 4


class SensorGate:
    """Validates and reroutes raw sensor samples. See docstring in the
    Dart original for rationale; logic is a 1:1 port."""

    def __init__(self, has_volume_event_nearby: Callable[[datetime], bool]):
        self.has_volume_event_nearby = has_volume_event_nearby
        self._last_trusted: dict = {}
        self._last_raw_seen: dict = {}
        self._run_length: dict = {}
        self._last_sample_time: Optional[datetime] = None

    def validate(self, raw: RawSample) -> GatedSample:
        hours = None
        if self._last_sample_time is not None:
            hours = (raw.time - self._last_sample_time).total_seconds() / 3600.0

        channels = {
            SensorChannel.PH: self._validate_channel(SensorChannel.PH, raw.ph, hours, raw.time),
            SensorChannel.TEMP: self._validate_channel(SensorChannel.TEMP, raw.temp_c, hours, raw.time),
            SensorChannel.TDS: self._validate_tds(raw.tds, hours, raw.time),
            SensorChannel.LUX: self._validate_channel(SensorChannel.LUX, raw.lux, hours, raw.time),
        }
        self._last_sample_time = raw.time
        return GatedSample(raw.time, channels)

    def _missing(self, channel) -> ChannelReading:
        """No reading for the channel: not trusted, the last trusted value
        (if any) stands in, and the stale-run count is left as it was."""
        return ChannelReading(
            self._last_trusted.get(channel), None, SampleVerdict.MISSING,
            f"{channel.value} missing from this reading - last trusted value kept.",
        )

    def _validate_tds(self, value: Optional[float], hours, time: datetime) -> ChannelReading:
        if value is None:
            return self._missing(SensorChannel.TDS)
        if value < _TDS_OUT_OF_WATER_THRESHOLD:
            return ChannelReading(
                self._last_trusted.get(SensorChannel.TDS, value),
                value,
                SampleVerdict.SUSPECT_RANGE,
                f"TDS {value:.2f}ppm implausible for a stocked pond - probe likely "
                f"out of water or disconnected. Using last trusted value.",
            )
        return self._validate_channel(SensorChannel.TDS, value, hours, time)

    def _validate_channel(self, channel, value: Optional[float], hours, time: datetime) -> ChannelReading:
        if value is None:
            return self._missing(channel)
        lo, hi, max_rate = _BOUNDS[channel]

        if value < lo or value > hi:
            fallback = self._last_trusted.get(channel, min(max(value, lo), hi))
            return ChannelReading(
                fallback, value, SampleVerdict.REJECTED,
                f"{channel.value} reading {value} outside plausible range "
                f"[{lo}, {hi}] - rejected, reusing last trusted value.",
            )

        last_raw = self._last_raw_seen.get(channel)
        is_repeat = last_raw is not None and abs(value - last_raw) < 1e-9
        self._run_length[channel] = (self._run_length.get(channel, 1) + 1) if is_repeat else 1
        self._last_raw_seen[channel] = value

        if self._run_length[channel] >= _STALE_RUN_LENGTH:
            return ChannelReading(
                self._last_trusted.get(channel, value), value, SampleVerdict.SUSPECT_STALE,
                f"{channel.value} unchanged for {self._run_length[channel]} consecutive "
                f"samples - sensor possibly offline or replaying a cached value.",
            )

        if hours is not None and hours > 0 and max_rate > 0:
            prior = self._last_trusted.get(channel)
            if prior is not None:
                delta = abs(value - prior)
                allowed_delta = max_rate * hours
                if delta > allowed_delta and not self.has_volume_event_nearby(time):
                    return ChannelReading(
                        prior, value, SampleVerdict.SUSPECT_RATE,
                        f"{channel.value} moved {delta:.2f} in {hours:.1f}h "
                        f"(limit {allowed_delta:.2f}) with no logged water change/top-up "
                        f"nearby - treated as drift, not real chemistry change.",
                    )

        self._last_trusted[channel] = value
        return ChannelReading(value, value, SampleVerdict.OK)

    # --- serialization for durability across process restarts ---
    def to_snapshot(self) -> dict:
        return {
            "last_trusted": {k.value: v for k, v in self._last_trusted.items()},
            "last_raw_seen": {k.value: v for k, v in self._last_raw_seen.items()},
            "run_length": {k.value: v for k, v in self._run_length.items()},
            "last_sample_time": self._last_sample_time.isoformat() if self._last_sample_time else None,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict, has_volume_event_nearby) -> "SensorGate":
        gate = cls(has_volume_event_nearby)
        gate._last_trusted = {SensorChannel(k): v for k, v in snapshot.get("last_trusted", {}).items()}
        gate._last_raw_seen = {SensorChannel(k): v for k, v in snapshot.get("last_raw_seen", {}).items()}
        gate._run_length = {SensorChannel(k): v for k, v in snapshot.get("run_length", {}).items()}
        ts = snapshot.get("last_sample_time")
        gate._last_sample_time = datetime.fromisoformat(ts) if ts else None
        return gate


# ============================================================
# 2. POND EVENTS + CONFIG
# ============================================================

class EventKind(str, Enum):
    WATER_CHANGE = "water_change"
    TOP_UP = "top_up"
    ALGAL_SCRUB = "algal_scrub"
    FEEDING = "feeding"
    SALT = "salt"
    FILTER_CLEAN = "filter_clean"


@dataclass
class PondEvent:
    kind: EventKind
    time: datetime
    volume_percent: Optional[float] = None
    volume_litres: Optional[float] = None
    scrub_type: Optional[str] = None
    food_grams: Optional[float] = None
    protein_percent: Optional[float] = None
    salt_grams: Optional[float] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["kind"] = self.kind.value
        d["time"] = self.time.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PondEvent":
        d = dict(d)
        d["kind"] = EventKind(d["kind"])
        d["time"] = datetime.fromisoformat(d["time"])
        return cls(**d)


def salt_tds_step(grams: Optional[float], volume_litres: float) -> float:
    """Nominal dissolved mass in mg/L, an estimate of the meter's TDS step."""
    if not math.isfinite(volume_litres) or volume_litres <= 0:
        raise ValueError("Salt addition requires a finite positive pond volume in litres")
    if grams is None or not math.isfinite(grams) or grams <= 0:
        raise ValueError("salt_grams must be finite and positive")
    return 1000.0 * grams / volume_litres


@dataclass
class PondConfig:
    volume_litres: float
    estimated_biomass_grams: float
    # Not currently consumed by any chemistry calculation below - descriptive
    # only. Set from the pond's profile (koi/models/profile.py, the
    # pond_profile table); the defaults stand in when the profile leaves
    # them unknown.
    fish_type: str = "Unspecified"
    fish_count: int = 0
    tap_tds_ppm: float = 30.0
    tap_nitrate_ppm: float = 0.0
    # IANA zone whose local midnight starts each calendar day in _daily,
    # matching aggregate_daily_sensor_data(). Snapshots written before this
    # field existed load with the default.
    time_zone: str = DEFAULT_POND_TIME_ZONE


# ============================================================
# 3. CHEMISTRY STATE + ASSESSMENT
# ============================================================

# How a DailyAggregate's day was bounded.
#   local  - local midnight to local midnight in PondConfig.time_zone.
#   legacy - written before daily snapshot version 2: bounded by the
#            calendar fields of each timestamp's own offset, which was UTC
#            for poller samples. The samples carry no timestamps, so these
#            cannot be re-bucketed into local days; they are kept as they
#            were and age out of the retention window.
DAY_BASIS_LOCAL = "local"
DAY_BASIS_LEGACY = "legacy"
LEGACY_KEY_PREFIX = "legacy:"

# Version of the chemistry snapshot's "daily" representation. Absent in
# snapshots written before local-day keys (treated as version 1).
DAILY_SNAPSHOT_VERSION = 3


@dataclass
class DailyAggregate:
    day: datetime
    trusted_ph: list = field(default_factory=list)
    trusted_lux: list = field(default_factory=list)
    trusted_tds: list = field(default_factory=list)
    # Per calendar day, so one night spans two entries. No model reads it.
    night_ph: list = field(default_factory=list)
    had_volume_event: bool = False
    ph_after_maintenance: bool = False
    tds_after_maintenance: bool = False
    basis: str = DAY_BASIS_LOCAL
    # The calendar date this bucket covers, in its basis. When None,
    # calendar_date() derives it from day.
    calendar_day: Optional[date] = None

    def calendar_date(self, time_zone: str = DEFAULT_POND_TIME_ZONE) -> date:
        return self.calendar_day if self.calendar_day is not None else local_date(self.day, time_zone)

    @property
    def has_enough_data(self) -> bool:
        return len(self.trusted_ph) >= 6 and len(self.trusted_lux) >= 6

    @property
    def reactivity(self) -> Optional[float]:
        if not self.has_enough_data:
            return None
        swing = max(self.trusted_ph) - min(self.trusted_ph)
        avg_lux = sum(self.trusted_lux) / len(self.trusted_lux)
        return swing / (avg_lux + 50.0)

    @property
    def avg_tds(self) -> Optional[float]:
        if not self.trusted_tds:
            return None
        return sum(self.trusted_tds) / len(self.trusted_tds)

    def to_dict(self) -> dict:
        # Copies: a ledger checkpoint keeps this dict in memory, and the
        # live lists keep growing after it is taken.
        return {
            "day": self.day.isoformat(),
            "trusted_ph": list(self.trusted_ph),
            "trusted_lux": list(self.trusted_lux),
            "trusted_tds": list(self.trusted_tds),
            "night_ph": list(self.night_ph),
            "had_volume_event": self.had_volume_event,
            "ph_after_maintenance": self.ph_after_maintenance,
            "tds_after_maintenance": self.tds_after_maintenance,
            "basis": self.basis,
            "calendar_day": self.calendar_day.isoformat() if self.calendar_day is not None else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DailyAggregate":
        stored_date = d.get("calendar_day")
        return cls(
            day=datetime.fromisoformat(d["day"]),
            trusted_ph=list(d.get("trusted_ph", [])),
            trusted_lux=list(d.get("trusted_lux", [])),
            trusted_tds=list(d.get("trusted_tds", [])),
            night_ph=list(d.get("night_ph", [])),
            had_volume_event=d.get("had_volume_event", False),
            ph_after_maintenance=d.get("ph_after_maintenance", False),
            tds_after_maintenance=d.get("tds_after_maintenance", False),
            basis=d.get("basis", DAY_BASIS_LOCAL),
            calendar_day=date.fromisoformat(stored_date) if stored_date else None,
        )

    @classmethod
    def from_legacy_dict(cls, key: str, d: dict) -> "DailyAggregate":
        """An entry from a version 1 snapshot, keyed "{year}-{month}-{day}"
        (not zero-padded) on its own timestamp's calendar fields."""
        agg = cls.from_dict(d)
        agg.basis = DAY_BASIS_LEGACY
        try:
            y, m, dd = (int(part) for part in key.split("-"))
            agg.calendar_day = date(y, m, dd)
        except ValueError:
            agg.calendar_day = agg.day.date()
        return agg


@dataclass
class WaterChemistryAssessment:
    status: str
    category: str
    tan_ppm: float
    no2_ppm: float
    no3_ppm: float
    ph_reactivity: Optional[float]
    reactivity_trend: Optional[float]
    tds_trend: Optional[float]
    sensor_warnings: list
    advisory: str
    add_hardener_now: bool
    risk_score: int = 0
    # Issue #27: the observed-rain dilution term
    # (WaterChemistryEngine.rain_dilution_term). Like risk_score, no
    # column stores it.
    rain_dilution: Optional[dict] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _day_key(t: datetime, time_zone: str = DEFAULT_POND_TIME_ZONE) -> str:
    """Local calendar-day key (YYYY-MM-DD), the same date
    aggregate_daily_sensor_data() gives a sample with this created_at."""
    return local_day_key(t, time_zone)


# Ammonia (TAN) production per gram of protein fed - ~16% of protein mass
# is nitrogen, and roughly `excretion_fraction` of that nitrogen load ends
# up excreted as TAN rather than retained in fish tissue. Pulled out as a
# module-level helper so both real event application (apply_event) and the
# historical-average estimator used by project_forward (see
# estimate_avg_daily_tan_mg below) use the exact same formula - no risk of
# the two drifting apart.
def _ammonia_mg(food_grams: float, protein_percent: float, excretion_fraction: float = 0.70) -> float:
    return food_grams * (protein_percent / 100.0) * 0.16 * excretion_fraction * 1000.0


class WaterChemistryEngine:
    """Persistent, stateful per-pond engine. One instance per userID,
    kept alive across requests via EngineRegistry, snapshotted to Supabase
    after every mutation for crash/restart durability."""

    # Composite risk_score cutoffs - shared by assess() (current-state
    # classification) and project_forward() (forward simulation), so the
    # "when would this need attention" projection uses the exact same bar
    # as the live Green/Amber/Red status. These are the same cutoffs the
    # original assess() used inline before being pulled out into
    # RISK_WATCH_THRESHOLD / RISK_HIGH_THRESHOLD constants here:
    #
    #   score >= 3 already corresponds to things like "TAN alone in the
    #   0.5-1.0ppm band plus a mild reactivity read" or "NO3 past 40ppm
    #   plus a rising buffering trend" - i.e. one clear stressor, or two
    #   mild ones compounding. That's a reasonable "start planning a
    #   water change" bar: not an emergency, but not noise either.
    #
    #   score >= 6 requires multiple stressors compounding at once (e.g.
    #   TAN > 1.0 AND NO3 > 80, or high reactivity AND a fast-rising
    #   trend AND incoming rain) - that's already "Red / High Risk" today,
    #   i.e. change the water now, not "soon".
    #
    # The NO2 > 0.5ppm nitrite override is independent of risk_score in
    # both assess() and project_forward() - a small amount of nitrite is
    # dangerous regardless of how the rest of the score reads.
    RISK_WATCH_THRESHOLD = 3
    RISK_HIGH_THRESHOLD = 6
    NITRITE_OVERRIDE_PPM = 0.5

    # Issue #27: one risk point per level the observed 24-hour rain
    # dilution reaches. 0.08 needs about 100 mm on a 1.2 m pond (33 mm on
    # 0.4 m); 0.16 about 209 mm on 1.2 m (70 mm on 0.4 m). They replace
    # the forecast-wording points (+2 thundery/heavy, +1 showers/rain);
    # see docs/models.md.
    RAIN_DILUTION_LEVELS: tuple[float, ...] = (0.08, 0.16)

    # The chemistry rain term reads observed rain at the pond's assigned
    # station over the rolling 24 hours before the assessment. The
    # lead-time ladder's REACT rule separately reads yesterday's local day.
    RAIN_DILUTION_WINDOW = timedelta(hours=24)

    @classmethod
    def rain_dilution_window(cls, now: datetime) -> tuple[datetime, datetime]:
        """[now - 24 h, now]: the chemistry rain term's observation window."""
        return now - cls.RAIN_DILUTION_WINDOW, now

    @staticmethod
    def rain_dilution_fraction(rain_mm: float, depth_m: float) -> float:
        """Fraction of the pond's water replaced by rain_mm of rain on a pond
        depth_m deep: 1 - exp(-R/D), with R converted from mm to metres.
        30 mm on 1.2 m is about 2.5 %; 100 mm on 1.2 m about 8.0 %."""
        if not math.isfinite(depth_m) or depth_m <= 0:
            raise ValueError("depth_m must be a positive number of metres")
        return 1.0 - math.exp(-(max(rain_mm, 0.0) / 1000.0) / depth_m)

    # ROUND 2 FIX: _daily previously had no retention cap at all - unlike
    # EvaporationFeedEngine._recent_env (capped 200) and
    # AlgaeGrowthEngine._camera_history/_ratings (capped 300/200), it grew
    # one entry per calendar day for the pond's ENTIRE lifetime, each
    # holding raw per-sample lists (trusted_ph/trusted_lux/trusted_tds -
    # up to ~96 appends/day at the poller's 15-minute cadence). Three
    # compounding costs, all growing without bound as a pond ages:
    #   1. assess() calls sorted(self._daily.values()) on every single call
    #      (every 15-minute poll, per user) - O(n log n) over ALL history.
    #   2. _slope() (the reactivity/TDS trend) averaged over every day ever
    #      recorded, not a recent window - months-old readings got equal
    #      weight to yesterday's, which is arguably wrong as well as slow.
    #   3. to_snapshot() serializes the whole dict and save_engine_snapshot
    #      does a full-row Supabase upsert - EVERY mutation (every poll
    #      tick, every logged event) rewrote an ever-growing JSON blob.
    # 30 days matches the retention window _events already uses just below,
    # and turns "trend" into an actually-recent trend rather than an
    # all-time one.
    DAILY_RETENTION_DAYS = 30

    def __init__(self, config: PondConfig):
        self.config = config
        self._events: list[PondEvent] = []
        self._daily: dict[str, DailyAggregate] = {}

        self._tan_mg = 0.0
        self._no2_mg = 0.0
        self._no3_mg = 0.0
        self._algae_suppression_days_remaining = 0
        self._last_tds_ppm = 0.0
        self._last_ingest_time: Optional[datetime] = None
        # Multiplier on both nitrification rates, fitted per pond from kit
        # readings (issue #32, koi/kit_readings.py); 1.0 without a fit.
        self.nitrification_scale = 1.0
        self.nitrification_scale_version: Optional[int] = None

        self._gate = SensorGate(self._has_volume_event_within_6h)

    def _has_volume_event_within_6h(self, t: datetime) -> bool:
        return any(
            e.kind in (EventKind.WATER_CHANGE, EventKind.TOP_UP, EventKind.SALT)
            and abs((t - e.time).total_seconds()) <= 6 * 3600
            for e in self._events
        )

    def _prune_daily(self, reference_time: datetime) -> None:
        """Drops DailyAggregate entries older than DAILY_RETENTION_DAYS
        relative to reference_time. See the DAILY_RETENTION_DAYS comment
        above for why this exists.

        Compares calendar dates, not the dict's string keys (legacy keys
        are not zero-padded and carry a prefix, so they do not sort by
        date) and not the first-sample datetime (which would make the
        window depend on the time of day each bucket started).
        """
        # Strictly-greater-than: reference_time's local date counts as "day 0
        # ago", so the window retains exactly DAILY_RETENTION_DAYS calendar
        # dates (that day plus the N-1 before it), not N+1.
        tz = self.config.time_zone
        cutoff = local_date(reference_time, tz) - timedelta(days=self.DAILY_RETENTION_DAYS)
        self._daily = {k: v for k, v in self._daily.items() if v.calendar_date(tz) > cutoff}

    def _day_for(self, t: datetime) -> DailyAggregate:
        """The bucket for t's local calendar day, created on first use."""
        tz = self.config.time_zone
        return self._daily.setdefault(_day_key(t, tz), DailyAggregate(t, calendar_day=local_date(t, tz)))

    def daily_provenance(self) -> dict:
        """How the retained day buckets were bounded. legacy_days lists the
        dates of buckets from before local-day keys; their boundaries are
        UTC midnight (08:00 SGT), not local midnight."""
        legacy = sorted(
            v.calendar_date().isoformat() for v in self._daily.values() if v.basis == DAY_BASIS_LEGACY
        )
        return {
            "time_zone": self.config.time_zone,
            "local_days": len(self._daily) - len(legacy),
            "legacy_days": legacy,
        }

    @staticmethod
    def _load_daily(snapshot: dict) -> dict[str, DailyAggregate]:
        """The "daily" map of a chemistry snapshot in the current key form.

        Version 1 (no "daily_version"): keys were "{year}-{month}-{day}" of
        each timestamp's own offset (UTC for poller samples). The stored
        samples have no timestamps, so they cannot be re-bucketed into local
        days or merged with local buckets; each is kept unchanged under a
        "legacy:YYYY-MM-DD" key, marked legacy, and ages out of the window.
        """
        raw = snapshot.get("daily", {}) or {}
        if snapshot.get("daily_version", 1) >= 2:
            return {k: DailyAggregate.from_dict(v) for k, v in raw.items()}
        daily = {}
        for key, value in raw.items():
            agg = DailyAggregate.from_legacy_dict(key, value)
            daily[LEGACY_KEY_PREFIX + agg.calendar_date().isoformat()] = agg
        return daily

    # --------------------------------------------------------
    # Event ingestion
    # --------------------------------------------------------
    def apply_event(self, event: PondEvent) -> None:
        if event.kind == EventKind.SALT:
            salt_tds_step(event.salt_grams, self.config.volume_litres)
        self._events.append(event)
        # Keep the event ledger bounded - only the "volume event nearby" check
        # and future analytics need recent history, not an unbounded log.
        cutoff = event.time - timedelta(days=30)
        self._events = [e for e in self._events if e.time >= cutoff]
        self._prune_daily(event.time)

        day = self._day_for(event.time)

        if event.kind == EventKind.WATER_CHANGE:
            pct = self._resolve_percent(event.volume_percent, event.volume_litres)
            day.had_volume_event = True
            self._tan_mg *= (1 - pct)
            self._no2_mg *= (1 - pct)
            current_no3_ppm = self._no3_mg / self.config.volume_litres
            blended_no3_ppm = current_no3_ppm * (1 - pct) + self.config.tap_nitrate_ppm * pct
            self._no3_mg = blended_no3_ppm * self.config.volume_litres
            self._last_tds_ppm = self._last_tds_ppm * (1 - pct) + self.config.tap_tds_ppm * pct

        elif event.kind == EventKind.TOP_UP:
            pct = self._resolve_percent(event.volume_percent, event.volume_litres)
            day.had_volume_event = True
            self._last_tds_ppm = self._last_tds_ppm * (1 - pct * 0.3)

        elif event.kind == EventKind.SALT:
            self._last_tds_ppm += salt_tds_step(event.salt_grams, self.config.volume_litres)
            day.had_volume_event = True

        elif event.kind == EventKind.ALGAL_SCRUB:
            self._algae_suppression_days_remaining = 5

        elif event.kind == EventKind.FEEDING:
            tan_added = _ammonia_mg(
                food_grams=event.food_grams or 0.0,
                protein_percent=event.protein_percent or 0.0,
            )
            self._tan_mg += tan_added

    def _resolve_percent(self, pct: Optional[float], litres: Optional[float]) -> float:
        if pct is not None:
            return min(max(pct / 100.0, 0.0), 1.0)
        if litres is not None:
            return min(max(litres / self.config.volume_litres, 0.0), 1.0)
        return 0.0

    # --------------------------------------------------------
    # Sensor ingestion
    # --------------------------------------------------------
    def ingest_sensor_sample(self, raw: RawSample) -> list[str]:
        gated = self._gate.validate(raw)
        warnings = [c.reason for c in gated.channels.values() if c.reason]

        temp_c = gated.temp.value
        if temp_c is None and self._last_ingest_time is not None:
            # No water temperature has ever been read, so the pools cannot
            # be stepped; the interval is stepped by the first input that
            # brings one.
            warnings.append("No water temperature yet - nitrogen pools not advanced.")
        else:
            if self._last_ingest_time is not None and temp_c is not None:
                hours = (raw.time - self._last_ingest_time).total_seconds() / 3600.0
                if hours > 0:
                    self._advance_pools(
                        hours,
                        temp_c,
                        gated.lux.value if gated.lux.is_trusted else None,
                    )
            self._last_ingest_time = raw.time
        self._prune_daily(raw.time)

        day = self._day_for(raw.time)
        # A trusted reading always carries its value.
        ph, lux, tds = gated.ph.value, gated.lux.value, gated.tds.value
        after_maintenance = any(
            e.kind == EventKind.FILTER_CLEAN and e.time <= raw.time < e.time + timedelta(hours=24)
            for e in self._events
        )
        if after_maintenance:
            day.ph_after_maintenance |= gated.ph.is_trusted and ph is not None
            day.tds_after_maintenance |= gated.tds.is_trusted and tds is not None
        if gated.ph.is_trusted and gated.lux.is_trusted and ph is not None and lux is not None:
            day.trusted_ph.append(ph)
            day.trusted_lux.append(lux)
            if lux < NIGHT_LUX_THRESHOLD:
                day.night_ph.append(ph)
        if gated.tds.is_trusted and tds is not None:
            day.trusted_tds.append(tds)
            self._last_tds_ppm = tds

        return warnings

    def _advance_pools(self, hours: float, temp_c: float, lux_value: Optional[float]) -> None:
        (
            self._tan_mg,
            self._no2_mg,
            self._no3_mg,
            self._algae_suppression_days_remaining,
        ) = self._step_pools(
            self._tan_mg,
            self._no2_mg,
            self._no3_mg,
            self._algae_suppression_days_remaining,
            hours,
            temp_c,
            lux_value,
            0.05 * self.nitrification_scale,
            0.035 * self.nitrification_scale,
        )

    @staticmethod
    def _step_pools(
        tan_mg: float,
        no2_mg: float,
        no3_mg: float,
        algae_suppression_days_remaining: int,
        hours: float,
        temp_c: float,
        lux_value: Optional[float],
        base_tan_to_no2: float = 0.05,
        base_no2_to_no3: float = 0.035,
    ) -> tuple[float, float, float, int]:
        """Pure pool-kinetics step - no self, no side effects. This is the
        single implementation shared by real sensor ingestion
        (_advance_pools, above) and forward simulation (project_forward,
        below), so a forecast day and a real elapsed-time step always
        behave identically for the same (hours, temp_c, lux) inputs."""
        mult = WaterChemistryEngine._nitrification_temp_multiplier(temp_c)

        tan_converted = tan_mg * (1 - math.exp(-base_tan_to_no2 * mult * hours))
        tan_mg -= tan_converted
        no2_mg += tan_converted

        no2_converted = no2_mg * (1 - math.exp(-base_no2_to_no3 * mult * hours))
        no2_mg -= no2_converted
        no3_mg += no2_converted

        if lux_value is not None:
            suppression = 0.3 if algae_suppression_days_remaining > 0 else 1.0
            uptake_rate = 0.01 * min(max(lux_value / 10000.0, 0.0), 3.0) * suppression
            no3_mg -= no3_mg * (1 - math.exp(-uptake_rate * hours))
            no3_mg = max(no3_mg, 0.0)

        if hours >= 24 and algae_suppression_days_remaining > 0:
            algae_suppression_days_remaining -= 1

        return tan_mg, no2_mg, no3_mg, algae_suppression_days_remaining

    @staticmethod
    def _nitrification_temp_multiplier(temp_c: float) -> float:
        def lerp(x, x0, x1, y0, y1):
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0))
        if temp_c < 5:
            return 0.02
        if temp_c < 10:
            return lerp(temp_c, 5, 10, 0.02, 0.15)
        if temp_c < 15:
            return lerp(temp_c, 10, 15, 0.15, 0.4)
        if temp_c < 20:
            return lerp(temp_c, 15, 20, 0.4, 0.75)
        if temp_c < 28:
            return lerp(temp_c, 20, 28, 0.75, 1.0)
        if temp_c < 34:
            return lerp(temp_c, 28, 34, 1.0, 0.85)
        return 0.6

    @staticmethod
    def _slope(values: list) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(values) / n
        num = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        den = sum((xs[i] - x_mean) ** 2 for i in range(n))
        return 0.0 if den == 0 else num / den

    # --------------------------------------------------------
    # Risk scoring (shared by assess() and project_forward())
    # --------------------------------------------------------
    @staticmethod
    def _score_risk(
        tan_ppm: float,
        no2_ppm: float,
        no3_ppm: float,
        current_reactivity: Optional[float],
        reactivity_trend: Optional[float],
        tds_trend: Optional[float],
        rain_points: int = 0,
    ) -> tuple[int, bool]:
        """Returns (risk_score, nitrite_override). Pulled out of assess()
        so project_forward() scores each simulated day with exactly the
        same rules the live status uses - current_reactivity/
        reactivity_trend/tds_trend are None for simulated days (the
        pH-buffering trend has no forecastable analogue), which simply
        skips those terms rather than double-counting a stale value.

        rain_points comes from rain_dilution_term (observed rain and pond
        depth); forecast rain wording no longer scores (issue #27)."""
        risk_score = 0
        nitrite_override = no2_ppm > WaterChemistryEngine.NITRITE_OVERRIDE_PPM

        if tan_ppm > 1.0:
            risk_score += 2
        elif tan_ppm > 0.5:
            risk_score += 1

        if no3_ppm > 80:
            risk_score += 2
        elif no3_ppm > 40:
            risk_score += 1

        if current_reactivity is not None:
            if current_reactivity > 0.02:
                risk_score += 2
            elif current_reactivity > 0.012:
                risk_score += 1

        if reactivity_trend is not None:
            if reactivity_trend > 0.0015:
                risk_score += 2
            elif reactivity_trend > 0.0005:
                risk_score += 1

        if tds_trend is not None and reactivity_trend is not None:
            if tds_trend < -5.0 and reactivity_trend > 0:
                risk_score += 1
            if tds_trend > 5.0 and reactivity_trend > 0:
                risk_score += 1

        risk_score += max(int(rain_points), 0)

        return risk_score, nitrite_override

    @classmethod
    def rain_dilution_term(cls, rain: Optional[dict], depth_m: Optional[float],
                           levels: Optional[tuple] = None) -> dict:
        """The chemistry rain term from observed rain and the pond's depth.

        rain is a rainfall_total result (koi/weather/history.py) over
        cls.rain_dilution_window, or None when no station is assigned; depth_m
        is the profile's depth. A known depth and any observed total give a
        dilution and its points; a partial window's total is a lower bound,
        so its points still count. Missing or partial rain, or an unknown
        depth, is not read as zero dilution: status is insufficient_data
        and confidence reduced."""
        levels = tuple(cls.RAIN_DILUTION_LEVELS if levels is None else levels)
        rain = rain if isinstance(rain, dict) else None
        total = rain.get("total_mm") if rain else None
        rain_status = rain.get("status") if rain else "no_station"
        depth: Optional[float] = None
        if (isinstance(depth_m, (int, float)) and not isinstance(depth_m, bool)
                and math.isfinite(depth_m) and depth_m > 0):
            depth = float(depth_m)
        depth_ok = depth is not None
        fraction = None
        points = 0
        if depth is not None and total is not None:
            fraction = cls.rain_dilution_fraction(float(total), depth)
            points = sum(1 for level in levels if fraction >= level)
        reasons = []
        if not depth_ok:
            reasons.append("The pond profile has no depth, so rain dilution cannot be worked out.")
        if rain is None:
            reasons.append("No rainfall station is assigned to this pond.")
        elif rain_status != "complete" or total is None:
            reasons.append("The last 24 hours of station rainfall are incomplete"
                           + ("; the total so far is a lower bound." if total is not None else "."))
        assessed = not reasons
        return {
            "window": "rolling_24h",
            "station_id": rain.get("station_id") if rain else None,
            "from": rain.get("from") if rain else None,
            "to": rain.get("to") if rain else None,
            "rain_mm": total,
            "rain_status": rain_status,
            "coverage": rain.get("coverage") if rain else None,
            "depth_m": depth,
            "dilution_fraction": fraction,
            "levels": list(levels),
            "points": points,
            "status": "assessed" if assessed else "insufficient_data",
            "confidence": "full" if assessed else "reduced",
            "reasons": reasons,
        }

    # --------------------------------------------------------
    # Assessment (current state)
    # --------------------------------------------------------
    def assess(self, rain_incoming: bool = False, rain_intensity: str = "unknown",
               recent_sensor_warnings: Optional[list] = None, *,
               observed_rain: Optional[dict] = None,
               depth_m: Optional[float] = None) -> WaterChemistryAssessment:
        """rain_incoming (forecast wording) only shapes the advice
        (add_hardener_now at Watch); rain_intensity is no longer used. The
        risk score's rain term comes from observed_rain (rolling 24 h at
        the assigned station) and depth_m via rain_dilution_term (#27)."""
        recent_sensor_warnings = recent_sensor_warnings or []
        rain_term = self.rain_dilution_term(observed_rain, depth_m)
        tan_ppm = self._tan_mg / self.config.volume_litres
        no2_ppm = self._no2_mg / self.config.volume_litres
        no3_ppm = self._no3_mg / self.config.volume_litres

        usable_days = sorted(
            (d for d in self._daily.values() if d.has_enough_data and not d.had_volume_event
             and not d.ph_after_maintenance and not d.tds_after_maintenance),
            key=lambda d: d.day,
        )

        current_reactivity = None
        reactivity_trend = None
        tds_trend = None

        if usable_days:
            current_reactivity = usable_days[-1].reactivity
            reactivities = [d.reactivity for d in usable_days]
            if len(reactivities) >= 3:
                reactivity_trend = self._slope(reactivities)
            tds_series = [d.avg_tds for d in usable_days if d.avg_tds is not None]
            if len(tds_series) >= 3:
                tds_trend = self._slope(tds_series)

        risk_score, nitrite_override = self._score_risk(
            tan_ppm, no2_ppm, no3_ppm,
            current_reactivity, reactivity_trend, tds_trend,
            rain_points=rain_term["points"],
        )

        add_hardener = False
        if nitrite_override:
            status, category = "Red", "Nitrite Risk"
            advisory = (
                f"NO2 estimate is {no2_ppm:.2f}ppm - even low nitrite is dangerous to "
                f"fish. This is a model estimate, not a direct measurement; if you have "
                f"any nitrite test kit available, verify directly and consider a partial "
                f"water change regardless of other readings."
            )
        elif risk_score >= self.RISK_HIGH_THRESHOLD:
            status, category = "Red", "High Risk"
            advisory = (
                "Multiple stress signals (waste load, buffering trend, and/or incoming "
                "rain) are compounding. Consider a partial water change and, if buffering "
                "is the driver, add KH/Calcium buffer before rain arrives."
            )
            add_hardener = True
        elif risk_score >= self.RISK_WATCH_THRESHOLD:
            status, category = "Amber", "Watch"
            advisory = (
                "Some drift in waste load or buffering trend. No urgent action, but "
                "monitor closely, especially if rain is forecast soon."
            )
            add_hardener = rain_incoming
        else:
            status, category = "Green", "Stable"
            advisory = "Estimated waste load and buffering trend both look stable."

        return WaterChemistryAssessment(
            status=status,
            category=category,
            tan_ppm=tan_ppm,
            no2_ppm=no2_ppm,
            no3_ppm=no3_ppm,
            ph_reactivity=current_reactivity,
            reactivity_trend=reactivity_trend,
            tds_trend=tds_trend,
            sensor_warnings=recent_sensor_warnings,
            advisory=advisory,
            add_hardener_now=add_hardener,
            risk_score=risk_score,
            rain_dilution=rain_term,
        )

    # --------------------------------------------------------
    # Forward projection ("when would this need a water change")
    # --------------------------------------------------------
    @staticmethod
    def estimate_avg_daily_tan_mg(
        feeding_rows: list[dict],
        excretion_fraction: float = 0.70,
    ) -> Optional[float]:
        """feeding_rows: recent FEEDING rows from pondInterventions (most
        recent first), each with food_grams/protein_percentage/
        event_timestamp - see Storage.fetch_recent_feeding_events.
        Returns the average TAN mg produced per day across the span the
        sample covers (min(fetched) to max(fetched) timestamp), or None
        if there isn't enough history to establish a meaningful span
        (fewer than 2 events, or they're all within the same ~12 hours -
        e.g. one heavy feeding day isn't a "daily rate")."""
        if not feeding_rows or len(feeding_rows) < 2:
            return None

        total_tan_mg = 0.0
        timestamps: list[datetime] = []
        for row in feeding_rows:
            grams = float(row.get("food_grams") or 0.0)
            protein = float(row.get("protein_percentage") or row.get("protein_percent") or 0.0)
            total_tan_mg += _ammonia_mg(grams, protein, excretion_fraction)
            ts = row.get("event_timestamp") or row.get("timestamp")
            if ts:
                timestamps.append(ts if isinstance(ts, datetime) else datetime.fromisoformat(ts))

        if len(timestamps) < 2:
            return None

        span_days = (max(timestamps) - min(timestamps)).total_seconds() / 86400.0
        if span_days < 0.5:
            return None

        return total_tan_mg / span_days

    def project_forward(
        self,
        *,
        avg_daily_tan_mg: float,
        daily_temp_forecast_c: Optional[list] = None,
        daily_lux_forecast: Optional[list] = None,
        daily_rain: Optional[list] = None,
        fallback_temp_c: Optional[float] = None,
        fallback_lux: Optional[float] = None,
        horizon_days: int = 21,
        steps_per_day: int = 4,
        tan_to_no2_rate: float = 0.05,
        no2_to_no3_rate: float = 0.035,
    ) -> dict:
        """Simulates the pond forward assuming feeding continues at
        avg_daily_tan_mg/day and NO further water changes, top-ups, or
        algal scrubs occur - i.e. "if nothing changes, when would one
        become necessary". Operates on local copies of the pools only;
        never mutates self, so it's safe to call from a read-only request.

        daily_temp_forecast_c / daily_lux_forecast: explicit per-day
        values for as many days as you have real forecast data for (e.g.
        NEA's 4-day outlook - see forecast_utils.py). Once the horizon runs
        past the supplied lists, fallback_temp_c/fallback_lux (recent
        historical daily averages) are used for the remaining days.

        daily_rain (per-day forecast wording) is accepted but not scored:
        since issue #27 the rain term uses observed rain only, which a
        projection does not have, and forecast rain feeds the lead-time
        ladder's advice instead.

        Returns a dict with the day each risk threshold is first crossed,
        as "N days from now" (0 = already true right now, based on
        current pool state; 1+ = that many full simulated days out), plus
        the full day-by-day trajectory for charting.

        Caveat: this only projects the waste-load side of risk
        (TAN/NO2/NO3). assess()'s buffering/reactivity trend comes from
        measured pH-vs-light swings and has no forecastable analogue, so
        it is intentionally excluded here - a real future assess() could
        still read worse than this projection if buffering is degrading
        on top of waste load.

        Both rates are multiplied by self.nitrification_scale, the pond's
        fitted scale (1.0 without one), so the uncertainty scenarios spread
        around the fitted rates.
        """
        if horizon_days < 1:
            raise ValueError("horizon_days must be >= 1")
        if steps_per_day < 1:
            raise ValueError("steps_per_day must be >= 1")

        daily_temp_forecast_c = daily_temp_forecast_c or []
        daily_lux_forecast = daily_lux_forecast or []

        tan_mg, no2_mg, no3_mg = self._tan_mg, self._no2_mg, self._no3_mg
        algae_days = self._algae_suppression_days_remaining
        hours_per_step = 24.0 / steps_per_day
        tan_per_step = avg_daily_tan_mg / steps_per_day

        trajectory = []
        first_watch_day = None
        first_high_risk_day = None
        first_nitrite_day = None

        # Evaluate the CURRENT state (day 0, before any simulated time
        # passes) first. This matters because pool advancement can pull a
        # value back under a threshold within less than a day (e.g. NO2
        # actively converting to NO3) - without this check, a pond that is
        # already past the nitrite threshold right now could decay just
        # enough by the end of day 1 to never trip the day-by-day loop
        # below, silently reporting "no action needed" for a pond that
        # needs one today. assess() surfaces this separately for the live
        # status, but project_forward should not contradict it.
        current_tan_ppm = self._tan_mg / self.config.volume_litres
        current_no2_ppm = self._no2_mg / self.config.volume_litres
        current_no3_ppm = self._no3_mg / self.config.volume_litres
        current_risk_score, current_nitrite_override = self._score_risk(
            current_tan_ppm, current_no2_ppm, current_no3_ppm,
            current_reactivity=None, reactivity_trend=None, tds_trend=None,
        )
        if current_nitrite_override:
            first_nitrite_day = 0
        if current_risk_score >= self.RISK_WATCH_THRESHOLD:
            first_watch_day = 0
        if current_risk_score >= self.RISK_HIGH_THRESHOLD:
            first_high_risk_day = 0

        for day_index in range(horizon_days):
            days_from_now = day_index + 1

            temp_c = (
                daily_temp_forecast_c[day_index]
                if day_index < len(daily_temp_forecast_c) and daily_temp_forecast_c[day_index] is not None
                else fallback_temp_c
            )
            lux_value = (
                daily_lux_forecast[day_index]
                if day_index < len(daily_lux_forecast) and daily_lux_forecast[day_index] is not None
                else fallback_lux
            )
            if temp_c is None:
                raise ValueError(
                    f"No temperature forecast or fallback available for day {days_from_now} - "
                    "cannot project nitrification without a temperature assumption."
                )

            for _ in range(steps_per_day):
                tan_mg += tan_per_step
                tan_mg, no2_mg, no3_mg, algae_days = self._step_pools(
                    tan_mg, no2_mg, no3_mg, algae_days, hours_per_step, temp_c, lux_value,
                    tan_to_no2_rate * self.nitrification_scale, no2_to_no3_rate * self.nitrification_scale,
                )

            tan_ppm = tan_mg / self.config.volume_litres
            no2_ppm = no2_mg / self.config.volume_litres
            no3_ppm = no3_mg / self.config.volume_litres

            risk_score, nitrite_override = self._score_risk(
                tan_ppm, no2_ppm, no3_ppm,
                current_reactivity=None, reactivity_trend=None, tds_trend=None,
            )

            if nitrite_override and first_nitrite_day is None:
                first_nitrite_day = days_from_now
            if risk_score >= self.RISK_WATCH_THRESHOLD and first_watch_day is None:
                first_watch_day = days_from_now
            if risk_score >= self.RISK_HIGH_THRESHOLD and first_high_risk_day is None:
                first_high_risk_day = days_from_now

            trajectory.append({
                "days_from_now": days_from_now,
                "tan_ppm": tan_ppm,
                "no2_ppm": no2_ppm,
                "no3_ppm": no3_ppm,
                "risk_score": risk_score,
                "nitrite_override": nitrite_override,
                "temp_c_assumed": temp_c,
                "lux_assumed": lux_value,
            })

        candidates = [d for d in (first_nitrite_day, first_watch_day, first_high_risk_day) if d is not None]
        predicted_action_days_from_now = min(candidates) if candidates else None

        return {
            "assumptions": {
                "avg_daily_tan_mg": avg_daily_tan_mg,
                "fallback_temp_c": fallback_temp_c,
                "fallback_lux": fallback_lux,
                "horizon_days": horizon_days,
            },
            "predicted_action_days_from_now": predicted_action_days_from_now,
            "first_watch_days_from_now": first_watch_day,
            "first_high_risk_days_from_now": first_high_risk_day,
            "first_nitrite_days_from_now": first_nitrite_day,
            "trajectory": trajectory,
            "caveat": (
                "Projection covers TAN/NO2/NO3 waste load only - buffering/reactivity "
                "trend is not forecastable and is excluded, so actual future risk could "
                "read higher than shown here if buffering is also degrading."
            ),
        }

    # --------------------------------------------------------
    # Snapshot serialization (durability across process restarts)
    # --------------------------------------------------------
    def to_snapshot(self) -> dict:
        return {
            "config": self.config.__dict__,
            "events": [e.to_dict() for e in self._events],
            "daily_version": DAILY_SNAPSHOT_VERSION,
            "daily": {k: v.to_dict() for k, v in self._daily.items()},
            "tan_mg": self._tan_mg,
            "no2_mg": self._no2_mg,
            "no3_mg": self._no3_mg,
            "algae_suppression_days_remaining": self._algae_suppression_days_remaining,
            "last_tds_ppm": self._last_tds_ppm,
            "last_ingest_time": self._last_ingest_time.isoformat() if self._last_ingest_time else None,
            "gate": self._gate.to_snapshot(),
            # Issue #32; absent from older snapshots, which load at 1.0.
            "nitrification_scale": self.nitrification_scale,
            "nitrification_scale_version": self.nitrification_scale_version,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "WaterChemistryEngine":
        config = PondConfig(**snapshot["config"])
        engine = cls(config)
        engine._events = [PondEvent.from_dict(e) for e in snapshot.get("events", [])]
        engine._daily = cls._load_daily(snapshot)
        # Self-heal any snapshot written before DAILY_RETENTION_DAYS existed -
        # an old unbounded _daily dict shrinks to the rolling window on the
        # very next load, with no migration needed.
        engine._prune_daily(datetime.now(timezone.utc))
        legacy_days = engine.daily_provenance()["legacy_days"]
        if legacy_days:
            log.info("legacy_daily_aggregates_kept",
                     extra={"legacy_days": len(legacy_days), "time_zone": config.time_zone})
        engine._tan_mg = snapshot.get("tan_mg", 0.0)
        engine._no2_mg = snapshot.get("no2_mg", 0.0)
        engine._no3_mg = snapshot.get("no3_mg", 0.0)
        engine._algae_suppression_days_remaining = snapshot.get("algae_suppression_days_remaining", 0)
        engine._last_tds_ppm = snapshot.get("last_tds_ppm", 0.0)
        lit = snapshot.get("last_ingest_time")
        engine._last_ingest_time = datetime.fromisoformat(lit) if lit else None
        engine.nitrification_scale = float(snapshot.get("nitrification_scale") or 1.0)
        engine.nitrification_scale_version = snapshot.get("nitrification_scale_version")
        engine._gate =SensorGate.from_snapshot(snapshot.get("gate", {}), engine._has_volume_event_within_6h)
        return engine