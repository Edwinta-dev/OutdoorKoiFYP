"""Offline stand-ins for the NEA service: recorded-shape responses from
tests/fixtures/nea_responses.json served by a fake transport. Nothing here
opens a socket."""
from __future__ import annotations

import copy
import json
import urllib.parse
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from koi.weather.nea import NeaClient

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nea_responses.json"
BASE_URL = "https://nea.test.invalid/v2/real-time/api"
FETCHED_AT = datetime(2026, 10, 2, 2, 6, tzinfo=timezone.utc)  # 10:06 Singapore


def response(product: str) -> dict:
    """A deep copy of the fixture body of one product."""
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))[product])


def ok(body: object) -> tuple[int, dict, bytes]:
    return 200, {}, json.dumps(body).encode("utf-8")


class FakeTransport:
    """Serves queued responses per (product, params). An entry may be a
    (status, headers, body) tuple, a JSON-able body (served with 200), or
    an exception instance to raise. With nothing queued, the fixture body
    of the product is served."""

    def __init__(self) -> None:
        self.queued: dict[tuple, deque] = defaultdict(deque)
        self.calls: list[tuple[str, dict]] = []
        self.handler: Optional[Callable[[str, dict], object]] = None

    @staticmethod
    def key(product: str, **params: str) -> tuple:
        return (product, tuple(sorted(params.items())))

    def queue(self, product: str, *entries: object, **params: str) -> None:
        self.queued[self.key(product, **params)].extend(entries)

    def __call__(self, url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
        self.calls.append((url, dict(headers)))
        parts = urllib.parse.urlsplit(url)
        product = parts.path.rsplit("/", 1)[-1]
        params = dict(urllib.parse.parse_qsl(parts.query))
        if self.handler is not None:
            entry = self.handler(product, params)
        else:
            pending = self.queued.get(self.key(product, **params))
            entry = pending.popleft() if pending else response(product)
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, tuple):
            return entry
        return ok(entry)


class FakeClock:
    """Monotonic clock advanced only by sleep, which it records."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def make_client(transport: FakeTransport, clock: Optional[FakeClock] = None, **options) -> NeaClient:
    clock = clock or FakeClock()
    defaults = {"min_interval_seconds": 0.0, "backoff_seconds": 1.0}
    return NeaClient(BASE_URL, transport=transport, sleep=clock.sleep, monotonic=clock.monotonic,
                     **{**defaults, **options})


def fixed_clock(at: datetime = FETCHED_AT) -> Callable[[], datetime]:
    return lambda: at


def stepping_clock(start: datetime = FETCHED_AT, step: timedelta = timedelta(seconds=1)) -> Callable[[], datetime]:
    state = {"t": start - step}

    def tick() -> datetime:
        state["t"] += step
        return state["t"]
    return tick
