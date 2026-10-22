"""Bounded event-time history used to warm up the hypoxia advisory."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from koi.models.sensor_inputs import parse_timestamp

HISTORY_BIN_HOURS = 1
HISTORY_SPAN_DAYS = 14
HISTORY_SAMPLE_CAP = HISTORY_SPAN_DAYS * 24 // HISTORY_BIN_HOURS


@dataclass
class HypoxiaState:
    first_seen_at: datetime | None = None
    samples: list[tuple[datetime, float, float]] = field(default_factory=list)

    def add(self, at: datetime, ph: float, water_temp_c: float, *, now: datetime | None = None) -> bool:
        at = parse_timestamp(at)
        if now is not None and at < parse_timestamp(now) - timedelta(days=HISTORY_SPAN_DAYS):
            return False
        if self.samples and at < self.samples[-1][0] - timedelta(days=HISTORY_SPAN_DAYS):
            return False
        if self.first_seen_at is None or at < self.first_seen_at:
            self.first_seen_at = at
        bin_start = at.replace(minute=0, second=0, microsecond=0)
        by_bin = {sample[0].replace(minute=0, second=0, microsecond=0): sample for sample in self.samples}
        current = by_bin.get(bin_start)
        if current is None or at >= current[0]:
            by_bin[bin_start] = (at, float(ph), float(water_temp_c))
        cutoff = at - timedelta(days=HISTORY_SPAN_DAYS)
        self.samples = sorted((s for s in by_bin.values() if s[0] >= cutoff), key=lambda s: s[0])[-HISTORY_SAMPLE_CAP:]
        return True

    def history_hours(self, now: datetime) -> float:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.first_seen_at is None:
            return 0.0
        return max(0.0, (now.astimezone(timezone.utc) - self.first_seen_at).total_seconds() / 3600)

    def to_dict(self) -> dict:
        return {"first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
                "samples": [[at.isoformat(), ph, temp] for at, ph, temp in self.samples]}

    @classmethod
    def from_dict(cls, raw: dict | None) -> "HypoxiaState":
        if not isinstance(raw, dict):
            return cls()
        state = cls()
        try:
            first = raw.get("first_seen_at")
            state.first_seen_at = parse_timestamp(first) if first else None
            for row in raw.get("samples", []):
                if isinstance(row, (list, tuple)) and len(row) == 3:
                    state.add(parse_timestamp(row[0]), float(row[1]), float(row[2]))
        except (TypeError, ValueError, KeyError):
            return cls()
        return state
