"""Persistent per-pond TDS observations and owner questions. Pure, no I/O.

Step persistence and the rolling median use the viability detector unchanged.
Confidence describes the signal, never the probability of an event kind.
"""
from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from koi.models.event_ledger import EventLedger
from koi.models.sensor_inputs import SensorInput, parse_timestamp
from koi.models.tds_steps import DEFAULT_BASELINE_READINGS, detect_steps, detection_floor


@dataclass(frozen=True)
class PromptConfig:
    enabled: bool = False
    min_step_ppm: float = 20.0
    confidence_threshold: float = 0.8
    cooldown_hours: float = 24.0


@dataclass
class TdsPrompts:
    baseline: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    recent: list = field(default_factory=list)
    last_reading_at: str | None = None
    last_prompt_at: str | None = None
    prompts: list[dict] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, value: dict) -> TdsPrompts:
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})

    def observe(self, inputs: list[SensorInput], ledger: EventLedger, config: PromptConfig) -> None:
        if not config.enabled:
            return
        for sample in sorted(inputs, key=lambda s: s.time):
            ppm = sample.channels.get("tds")
            if (ppm is None or not math.isfinite(ppm) or not 5 <= ppm <= 5000
                    or (self.last_reading_at and sample.time <= parse_timestamp(self.last_reading_at))):
                continue
            point = [sample.time.isoformat(), ppm]
            self.last_reading_at = sample.time.isoformat()
            self.recent = (self.recent + [point])[-DEFAULT_BASELINE_READINGS:]
            if len(self.baseline) < DEFAULT_BASELINE_READINGS:
                self.baseline.append(point)
                continue
            values = [p[1] for p in self.baseline]
            baseline = statistics.median(values)
            spread = max(values) - min(values)
            floor = max(config.min_step_ppm, detection_floor(spread))
            if abs(ppm - baseline) <= floor:
                self.pending = []
                self.baseline = (self.baseline + [point])[-DEFAULT_BASELINE_READINGS:]
            else:
                if self.pending and (self.pending[0][1] - baseline) * (ppm - baseline) < 0:
                    self.pending = []
                self.pending.append(point)
                steps = detect_steps([(parse_timestamp(t), v) for t, v in self.baseline + self.pending], floor)
                if steps:
                    step = steps[-1]
                    self._candidate("step", step.time, sample.time, step.baseline_ppm, step.level_ppm,
                                    max(0.0, 1 - spread / abs(step.delta_ppm)), None,
                                    sample.time_basis, ledger, config)
                    self.baseline, self.pending = self.pending, []
                    self.recent = list(self.baseline)
                    continue
            # A slope needs 12 readings, a net movement above the floor,
            # and no adjacent jump above it. Linear fit R-squared is its confidence.
            if len(self.recent) == DEFAULT_BASELINE_READINGS and not self.pending:
                times = [parse_timestamp(p[0]) for p in self.recent]
                ys = [p[1] for p in self.recent]
                xs = [(t - times[0]).total_seconds() / 3600 for t in times]
                mx, my = statistics.mean(xs), statistics.mean(ys)
                xx = sum((x - mx) ** 2 for x in xs)
                yy = sum((y - my) ** 2 for y in ys)
                xy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
                if (xx and yy and abs(ys[-1] - ys[0]) > config.min_step_ppm
                        and max(abs(b - a) for a, b in zip(ys, ys[1:], strict=False)) <= config.min_step_ppm):
                    self._candidate("slope", times[0], sample.time, ys[0], ys[-1],
                                    min(1.0, xy ** 2 / (xx * yy)), xy / xx,
                                    sample.time_basis, ledger, config)

    def _candidate(self, detection: str, start: datetime, end: datetime, baseline: float, level: float,
                   confidence: float, slope: float | None, time_basis: str,
                   ledger: EventLedger, config: PromptConfig) -> None:
        if confidence < config.confidence_threshold:
            return
        if self.last_prompt_at and end - parse_timestamp(self.last_prompt_at) < timedelta(hours=config.cooldown_hours):
            return
        # A logged intervention near the signal already supplies an explanation.
        if any(start - timedelta(hours=6) <= e.event.time <= end for e in ledger.live()):
            return
        direction = "rise" if level > baseline else "fall"
        kinds = ["salt", "filter_clean"] if direction == "rise" else ["water_change", "top_up", "filter_clean"]
        signature = f"{detection}:{direction}"
        kinds = [k for k in kinds if f"{signature}:{k}" not in self.rejected]
        if not kinds:
            return
        self.prompts.append({"id": str(uuid.uuid4()), "detection": detection, "signature": signature,
                             "detected_at": end.isoformat(), "event_at": start.isoformat(),
                             "time_basis": time_basis, "baseline_ppm": baseline, "level_ppm": level,
                             "delta_ppm": level - baseline, "slope_ppm_hour": slope,
                             "confidence": confidence, "event_kinds": kinds, "answer": None,
                             "message": f"TDS moved from {baseline:.1f} ppm to {level:.1f} ppm. "
                                        "Confirm whether a listed pond-care action happened, "
                                        "or choose none of these. Enter any amount you measured."})
        self.last_prompt_at = end.isoformat()

    def reject(self, prompt: dict) -> None:
        for kind in prompt["event_kinds"]:
            key = f"{prompt['signature']}:{kind}"
            if key not in self.rejected:
                self.rejected.append(key)
