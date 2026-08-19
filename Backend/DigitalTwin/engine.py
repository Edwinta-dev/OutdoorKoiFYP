"""
engine.py

Python port of the Dart WaterChemistryEngine. Same rules, same rate
constants, same gating logic - this file is the single source of chemistry
truth shared by the event endpoints and the poller.

Design note: this class is stateful and mutated in place. It is NOT
thread-safe on its own - callers (app.py, poller.py) are responsible for
holding a per-user lock around any sequence of mutate-then-persist calls.
See EngineRegistry in registry.py for how that's enforced.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional


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


@dataclass
class ChannelReading:
    value: float
    raw_value: float
    verdict: SampleVerdict
    reason: Optional[str] = None

    @property
    def is_trusted(self) -> bool:
        return self.verdict == SampleVerdict.OK


@dataclass
class RawSample:
    time: datetime
    ph: float
    tds: float
    temp_c: float
    lux: float


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

    def _validate_tds(self, value: float, hours, time: datetime) -> ChannelReading:
        if value < _TDS_OUT_OF_WATER_THRESHOLD:
            return ChannelReading(
                self._last_trusted.get(SensorChannel.TDS, value),
                value,
                SampleVerdict.SUSPECT_RANGE,
                f"TDS {value:.2f}ppm implausible for a stocked pond - probe likely "
                f"out of water or disconnected. Using last trusted value.",
            )
        return self._validate_channel(SensorChannel.TDS, value, hours, time)

    def _validate_channel(self, channel, value: float, hours, time: datetime) -> ChannelReading:
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


@dataclass
class PondEvent:
    kind: EventKind
    time: datetime
    volume_percent: Optional[float] = None
    volume_litres: Optional[float] = None
    scrub_type: Optional[str] = None
    food_grams: Optional[float] = None
    protein_percent: Optional[float] = None

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


@dataclass
class PondConfig:
    volume_litres: float
    estimated_biomass_grams: float
    # Not currently consumed by any chemistry calculation below - descriptive
    # only. Not stored server-side (UserData has no fish_type/fish_count
    # columns); sourced from the phone's SharedPreferences when available,
    # see app.py's event endpoints. Default when unavailable (e.g. the
    # poller, which has no per-request phone input to read from).
    fish_type: str = "Unspecified"
    fish_count: int = 0
    tap_tds_ppm: float = 30.0
    tap_nitrate_ppm: float = 0.0


# ============================================================
# 3. CHEMISTRY STATE + ASSESSMENT
# ============================================================

@dataclass
class DailyAggregate:
    day: datetime
    trusted_ph: list = field(default_factory=list)
    trusted_lux: list = field(default_factory=list)
    trusted_tds: list = field(default_factory=list)
    night_ph: list = field(default_factory=list)
    had_volume_event: bool = False

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
        return {
            "day": self.day.isoformat(),
            "trusted_ph": self.trusted_ph,
            "trusted_lux": self.trusted_lux,
            "trusted_tds": self.trusted_tds,
            "night_ph": self.night_ph,
            "had_volume_event": self.had_volume_event,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DailyAggregate":
        return cls(
            day=datetime.fromisoformat(d["day"]),
            trusted_ph=list(d.get("trusted_ph", [])),
            trusted_lux=list(d.get("trusted_lux", [])),
            trusted_tds=list(d.get("trusted_tds", [])),
            night_ph=list(d.get("night_ph", [])),
            had_volume_event=d.get("had_volume_event", False),
        )


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

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _day_key(t: datetime) -> str:
    return f"{t.year}-{t.month}-{t.day}"


class WaterChemistryEngine:
    """Persistent, stateful per-pond engine. One instance per userID,
    kept alive across requests via EngineRegistry, snapshotted to Supabase
    after every mutation for crash/restart durability."""

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

        self._gate = SensorGate(self._has_volume_event_within_6h)

    def _has_volume_event_within_6h(self, t: datetime) -> bool:
        return any(
            e.kind in (EventKind.WATER_CHANGE, EventKind.TOP_UP)
            and abs((t - e.time).total_seconds()) <= 6 * 3600
            for e in self._events
        )

    # --------------------------------------------------------
    # Event ingestion
    # --------------------------------------------------------
    def apply_event(self, event: PondEvent) -> None:
        self._events.append(event)
        # Keep the event ledger bounded - only the "volume event nearby" check
        # and future analytics need recent history, not an unbounded log.
        cutoff = event.time - timedelta(days=30)
        self._events = [e for e in self._events if e.time >= cutoff]

        day = self._daily.setdefault(_day_key(event.time), DailyAggregate(event.time))

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

        elif event.kind == EventKind.ALGAL_SCRUB:
            self._algae_suppression_days_remaining = 5

        elif event.kind == EventKind.FEEDING:
            tan_added = self._calculate_ammonia_mg(
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

    def _calculate_ammonia_mg(self, food_grams: float, protein_percent: float,
                               excretion_fraction: float = 0.70) -> float:
        return food_grams * (protein_percent / 100.0) * 0.16 * excretion_fraction * 1000.0

    # --------------------------------------------------------
    # Sensor ingestion
    # --------------------------------------------------------
    def ingest_sensor_sample(self, raw: RawSample) -> list[str]:
        gated = self._gate.validate(raw)
        warnings = [c.reason for c in gated.channels.values() if c.reason]

        if self._last_ingest_time is not None:
            hours = (raw.time - self._last_ingest_time).total_seconds() / 3600.0
            if hours > 0:
                self._advance_pools(hours, gated.temp.value, gated.lux)
        self._last_ingest_time = raw.time

        day = self._daily.setdefault(_day_key(raw.time), DailyAggregate(raw.time))
        if gated.ph.is_trusted and gated.lux.is_trusted:
            day.trusted_ph.append(gated.ph.value)
            day.trusted_lux.append(gated.lux.value)
            if gated.lux.value < 20:
                day.night_ph.append(gated.ph.value)
        if gated.tds.is_trusted:
            day.trusted_tds.append(gated.tds.value)
            self._last_tds_ppm = gated.tds.value

        return warnings

    def _advance_pools(self, hours: float, temp_c: float, lux: ChannelReading) -> None:
        mult = self._nitrification_temp_multiplier(temp_c)
        base_tan_to_no2 = 0.05
        base_no2_to_no3 = 0.035

        tan_converted = self._tan_mg * (1 - math.exp(-base_tan_to_no2 * mult * hours))
        self._tan_mg -= tan_converted
        self._no2_mg += tan_converted

        no2_converted = self._no2_mg * (1 - math.exp(-base_no2_to_no3 * mult * hours))
        self._no2_mg -= no2_converted
        self._no3_mg += no2_converted

        if lux.is_trusted:
            suppression = 0.3 if self._algae_suppression_days_remaining > 0 else 1.0
            uptake_rate = 0.01 * min(max(lux.value / 10000.0, 0.0), 3.0) * suppression
            self._no3_mg -= self._no3_mg * (1 - math.exp(-uptake_rate * hours))
            self._no3_mg = max(self._no3_mg, 0.0)

        if hours >= 24 and self._algae_suppression_days_remaining > 0:
            self._algae_suppression_days_remaining -= 1

    def _nitrification_temp_multiplier(self, temp_c: float) -> float:
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
    # Assessment
    # --------------------------------------------------------
    def assess(self, rain_incoming: bool, rain_intensity: str = "unknown",
               recent_sensor_warnings: Optional[list] = None) -> WaterChemistryAssessment:
        recent_sensor_warnings = recent_sensor_warnings or []
        tan_ppm = self._tan_mg / self.config.volume_litres
        no2_ppm = self._no2_mg / self.config.volume_litres
        no3_ppm = self._no3_mg / self.config.volume_litres

        usable_days = sorted(
            (d for d in self._daily.values() if d.has_enough_data and not d.had_volume_event),
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

        risk_score = 0
        nitrite_override = no2_ppm > 0.5

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

        if rain_incoming:
            risk_score += 2 if rain_intensity == "heavy" else 1

        add_hardener = False
        if nitrite_override:
            status, category = "Red", "Nitrite Risk"
            advisory = (
                f"NO2 estimate is {no2_ppm:.2f}ppm - even low nitrite is dangerous to "
                f"fish. This is a model estimate, not a direct measurement; if you have "
                f"any nitrite test kit available, verify directly and consider a partial "
                f"water change regardless of other readings."
            )
        elif risk_score >= 6:
            status, category = "Red", "High Risk"
            advisory = (
                "Multiple stress signals (waste load, buffering trend, and/or incoming "
                "rain) are compounding. Consider a partial water change and, if buffering "
                "is the driver, add KH/Calcium buffer before rain arrives."
            )
            add_hardener = True
        elif risk_score >= 3:
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
        )

    # --------------------------------------------------------
    # Snapshot serialization (durability across process restarts)
    # --------------------------------------------------------
    def to_snapshot(self) -> dict:
        return {
            "config": self.config.__dict__,
            "events": [e.to_dict() for e in self._events],
            "daily": {k: v.to_dict() for k, v in self._daily.items()},
            "tan_mg": self._tan_mg,
            "no2_mg": self._no2_mg,
            "no3_mg": self._no3_mg,
            "algae_suppression_days_remaining": self._algae_suppression_days_remaining,
            "last_tds_ppm": self._last_tds_ppm,
            "last_ingest_time": self._last_ingest_time.isoformat() if self._last_ingest_time else None,
            "gate": self._gate.to_snapshot(),
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "WaterChemistryEngine":
        config = PondConfig(**snapshot["config"])
        engine = cls(config)
        engine._events = [PondEvent.from_dict(e) for e in snapshot.get("events", [])]
        engine._daily = {k: DailyAggregate.from_dict(v) for k, v in snapshot.get("daily", {}).items()}
        engine._tan_mg = snapshot.get("tan_mg", 0.0)
        engine._no2_mg = snapshot.get("no2_mg", 0.0)
        engine._no3_mg = snapshot.get("no3_mg", 0.0)
        engine._algae_suppression_days_remaining = snapshot.get("algae_suppression_days_remaining", 0)
        engine._last_tds_ppm = snapshot.get("last_tds_ppm", 0.0)
        lit = snapshot.get("last_ingest_time")
        engine._last_ingest_time = datetime.fromisoformat(lit) if lit else None
        engine._gate = SensorGate.from_snapshot(snapshot.get("gate", {}), engine._has_volume_event_within_6h)
        return engine
