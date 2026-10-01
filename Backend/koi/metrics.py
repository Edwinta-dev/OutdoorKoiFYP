"""In-process metrics in the Prometheus text exposition format (0.0.4).

    metrics = Metrics()
    uploads = metrics.counter("koi_camera_uploads_total", "Frame uploads by result.", ["result"])
    uploads.inc(result="stored")
    metrics.render()          # the body of GET /metrics

Each Flask app holds one Metrics in app.extensions["koi_metrics"]. Values
live in the process and reset when it restarts, which Prometheus expects
of counters. Label values must come from small fixed sets (route
patterns, not raw paths) so the number of series stays bounded; pond ids
are the one exception, bounded by the number of ponds.
"""
from __future__ import annotations

import math
import threading
from typing import Any, Iterable, Optional, Sequence

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Seconds. Covers a fast cached read up to a slow forecast or poll.
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _label_text(pairs: Sequence[tuple[str, object]]) -> str:
    if not pairs:
        return ""
    return "{" + ",".join(f'{name}="{_escape(value)}"' for name, value in pairs) + "}"


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    return repr(float(value)) if not float(value).is_integer() else str(int(value))


class _Family:
    kind = ""

    def __init__(self, name: str, help_text: str, labelnames: Iterable[str] = ()):
        self.name = name
        self.help = help_text
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()
        self._values: dict[tuple, Any] = {}

    def _key(self, labels: dict) -> tuple:
        if set(labels) != set(self.labelnames):
            raise ValueError(f"{self.name} takes labels {self.labelnames}, got {tuple(labels)}")
        return tuple(str(labels[n]) for n in self.labelnames)

    def _pairs(self, key: tuple) -> list[tuple[str, object]]:
        return list(zip(self.labelnames, key, strict=True))

    def samples(self) -> list[str]:
        raise NotImplementedError

    def render(self) -> str:
        lines = [f"# HELP {self.name} {_escape(self.help)}", f"# TYPE {self.name} {self.kind}"]
        lines += self.samples()
        return "\n".join(lines) + "\n"


class Counter(_Family):
    kind = "counter"

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(self._values.get(key, 0.0)) + amount

    def value(self, **labels: object) -> float:
        return float(self._values.get(self._key(labels), 0.0))

    def samples(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return [f"{self.name}{_label_text(self._pairs(k))} {_number(v)}" for k, v in items]


class Gauge(Counter):
    kind = "gauge"

    def set(self, value: float, **labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class Histogram(_Family):
    kind = "histogram"

    def __init__(self, name: str, help_text: str, labelnames: Iterable[str] = (),
                 buckets: Sequence[float] = DEFAULT_BUCKETS):
        super().__init__(name, help_text, labelnames)
        self.buckets = tuple(sorted(buckets))

    def observe(self, value: float, **labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            state = self._values.setdefault(key, {"buckets": [0] * len(self.buckets), "sum": 0.0, "count": 0})
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    state["buckets"][i] += 1
            state["sum"] += value
            state["count"] += 1

    def count(self, **labels: object) -> int:
        state = self._values.get(self._key(labels))
        return state["count"] if state else 0

    def samples(self) -> list[str]:
        with self._lock:
            items = sorted(((k, dict(v)) for k, v in self._values.items()), key=lambda kv: kv[0])
        lines = []
        for key, state in items:
            pairs = self._pairs(key)
            for bound, cumulative in zip(self.buckets, state["buckets"], strict=True):
                lines.append(f"{self.name}_bucket{_label_text(pairs + [('le', _number(bound))])} {cumulative}")
            lines.append(f"{self.name}_bucket{_label_text(pairs + [('le', '+Inf')])} {state['count']}")
            lines.append(f"{self.name}_sum{_label_text(pairs)} {_number(state['sum'])}")
            lines.append(f"{self.name}_count{_label_text(pairs)} {state['count']}")
        return lines


class Metrics:
    """A set of metric families, rendered together."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._families: dict[str, _Family] = {}

    def _get(self, cls, name: str, help_text: str, labelnames: Iterable[str], **kwargs):
        with self._lock:
            family = self._families.get(name)
            if family is None:
                family = self._families[name] = cls(name, help_text, labelnames, **kwargs)
            elif type(family) is not cls:
                raise ValueError(f"{name} is already a {family.kind}")
            return family

    def counter(self, name: str, help_text: str, labelnames: Iterable[str] = ()) -> Counter:
        return self._get(Counter, name, help_text, labelnames)

    def gauge(self, name: str, help_text: str, labelnames: Iterable[str] = ()) -> Gauge:
        return self._get(Gauge, name, help_text, labelnames)

    def histogram(self, name: str, help_text: str, labelnames: Iterable[str] = (),
                  buckets: Optional[Sequence[float]] = None) -> Histogram:
        return self._get(Histogram, name, help_text, labelnames, buckets=buckets or DEFAULT_BUCKETS)

    def render(self) -> str:
        with self._lock:
            families = sorted(self._families.values(), key=lambda f: f.name)
        return "".join(f.render() for f in families)


__all__ = ["CONTENT_TYPE", "Counter", "DEFAULT_BUCKETS", "Gauge", "Histogram", "Metrics"]
