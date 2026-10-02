"""The NEA ingestion job: one live cycle, or a resumable backfill.

Live cycle (run_live): fetch the latest response of each product, keep
history for the configured and pond-assigned stations and areas, and
update the latest caches from it. Run it on a schedule (every 5 minutes
for the station products and UV, every 30 minutes for the forecasts).

Backfill (run_backfill): fetch one Singapore calendar day per product per
window (?date=YYYY-MM-DD) and store history only; no cache candidates are
sent, and the database guard would refuse older ones anyway. Each window
is recorded in weather_ingest_window with its counts and status: 'done'
and 'empty' windows are skipped on the next run (unless forced), 'failed'
windows are retried, so an interrupted backfill resumes where it stopped.

Every product's outcome is logged as one weather_product_ingested event
with its counts (pages, records, malformed, inserted, duplicates, cache
rows updated, stations changed) and any error.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

from koi.logs import log_event
from koi.settings import Settings
from koi.storage.base import Storage, StorageError, parse_timestamp
from koi.weather import cache, nea
from koi.weather.history import local_day_window

log = logging.getLogger("koi.weather")

# ClosestStations keys naming a station, and the one naming a 2-hour area.
STATION_SLOTS = ("air-temperature", "rainfall", "wind-speed")
AREA_SLOT = "two-hr-forecast"
SKIP_STATUSES = ("done", "empty")


@dataclass
class ProductRun:
    product: str
    mode: str
    status: str = "ok"  # ok, empty, failed, skipped
    window_start: Optional[str] = None
    pages: int = 0
    records: int = 0
    malformed: int = 0
    observations: int = 0
    forecasts: int = 0
    inserted: int = 0
    duplicates: int = 0
    cache_updated: int = 0
    stations_changed: int = 0
    error: Optional[str] = None
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HistoryFilter:
    """Which history rows to keep. None keeps everything."""

    stations: Optional[frozenset[str]]
    areas: Optional[frozenset[str]]

    def keep_observation(self, obs: dict) -> bool:
        if obs["station_id"] == nea.UV_STATION:
            return True
        return self.stations is None or obs["station_id"] in self.stations

    def keep_forecast(self, issuance: dict) -> bool:
        if issuance["product"] != "2hr":
            return True
        return self.areas is None or issuance["slot_id"] in self.areas


def build_filter(settings: Settings, assigned_slots: Iterable[dict]) -> HistoryFilter:
    slots = list(assigned_slots)

    def chosen(configured: tuple[str, ...], keys: tuple[str, ...]) -> Optional[frozenset[str]]:
        if "*" in configured:
            return None
        names = set(configured)
        for slot in slots:
            names.update(str(slot[k]) for k in keys if slot.get(k))
        return frozenset(names)

    return HistoryFilter(chosen(settings.weather_history_stations, STATION_SLOTS),
                         chosen(settings.weather_history_areas, (AREA_SLOT,)))


def build_client(settings: Settings, **overrides) -> nea.NeaClient:
    options = {"api_key": settings.nea_api_key.get_secret_value(),
               "min_interval_seconds": settings.nea_min_request_interval_seconds,
               "max_attempts": settings.nea_max_attempts, **overrides}
    return nea.NeaClient(settings.nea_api_base_url, **options)


def run_product(storage: Storage, client: nea.NeaClient, product: str, history: HistoryFilter,
                clock: Callable[[], datetime], day: Optional[date] = None) -> ProductRun:
    """Fetches, parses and stores one product (the latest response, or
    one day when day is given). Caches are written only without day."""
    run = ProductRun(product, "backfill" if day else "live")
    fetched_at = clock()
    try:
        pages = client.fetch(product, day)
    except nea.NeaFetchError as exc:
        run.status, run.error = "failed", f"{exc.kind}: {exc.detail}"
        return run
    run.pages = len(pages)
    parsed = nea.parse(product, pages, fetched_at)
    run.records, run.malformed, run.reasons = parsed.records, parsed.malformed, parsed.reasons
    observations = [o for o in parsed.observations if history.keep_observation(o)]
    forecasts = [f for f in parsed.forecasts if history.keep_forecast(f)]
    batch: dict = {"observations": observations, "forecasts": forecasts, "telemetry_cache": [],
                   "forecast_cache": [], "stations": parsed.stations}
    if day is None:
        batch["telemetry_cache"] = cache.telemetry_candidates(parsed)
        batch["forecast_cache"] = (cache.uv_candidates(parsed) if product == nea.UV_PRODUCT
                                   else cache.forecast_candidates(parsed, fetched_at))
    try:
        result = storage.ingest_weather_batch(batch)
    except StorageError as exc:
        run.status, run.error = "failed", str(exc)
        return run
    obs, fc = result.get("observations") or {}, result.get("forecasts") or {}
    run.observations, run.forecasts = len(observations), len(forecasts)
    run.inserted = int(obs.get("inserted", 0)) + int(fc.get("inserted", 0))
    run.duplicates = run.observations + run.forecasts - run.inserted
    run.cache_updated = int(result.get("telemetry_cache_updated", 0)) + int(result.get("forecast_cache_updated", 0))
    run.stations_changed = int(result.get("stations_changed", 0))
    if parsed.records == 0:
        run.status = "empty"
    elif parsed.malformed == parsed.records:
        run.status, run.error = "failed", f"all {parsed.records} records malformed"
    return run


def _log(run: ProductRun) -> None:
    level = logging.WARNING if run.status == "failed" else logging.INFO
    log_event(log, "weather_product_ingested", level=level, **asdict(run))


def run_live(storage: Storage, client: nea.NeaClient, settings: Settings, products: Iterable[str] = nea.PRODUCTS,
             clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> list[ProductRun]:
    history = build_filter(settings, storage.fetch_assigned_weather_slots())
    runs = []
    for product in products:
        run = run_product(storage, client, product, history, clock)
        _log(run)
        runs.append(run)
    return runs


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def run_backfill(storage: Storage, client: nea.NeaClient, settings: Settings, start: date, end: date,
                 products: Iterable[str] = nea.PRODUCTS, force: bool = False,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> list[ProductRun]:
    if end < start:
        raise ValueError("end must not be before start")
    history = build_filter(settings, storage.fetch_assigned_weather_slots())
    runs = []
    for product in products:
        windows = {parse_timestamp(w["window_start"]): w for w in storage.fetch_weather_windows(product)}
        for day in days(start, end):
            window_start, window_end = local_day_window(day, nea.SINGAPORE)
            previous = windows.get(window_start)
            if previous is not None and previous["status"] in SKIP_STATUSES and not force:
                run = ProductRun(product, "backfill", status="skipped", window_start=nea.iso(window_start))
                runs.append(run)
                continue
            run = run_product(storage, client, product, history, clock, day=day)
            run.window_start = nea.iso(window_start)
            try:
                storage.record_weather_window({
                    "product": product, "window_start": nea.iso(window_start), "window_end": nea.iso(window_end),
                    "status": "done" if run.status == "ok" else run.status,
                    "attempts": int(previous["attempts"]) + 1 if previous else 1, "pages": run.pages,
                    "records": run.records, "inserted": run.inserted, "duplicates": run.duplicates,
                    "malformed": run.malformed, "last_error": run.error})
            except StorageError as exc:
                run.status, run.error = "failed", f"window not recorded: {exc}"
            _log(run)
            runs.append(run)
    return runs
