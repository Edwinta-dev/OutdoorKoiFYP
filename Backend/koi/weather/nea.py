"""NEA (data.gov.sg v2 real-time) client and response parsers.

Endpoint and auth assumptions, for the owner to confirm before the job
replaces the edge functions (docs/weather-ingestion.md):

- Base URL https://api-open.data.gov.sg/v2/real-time/api, one path per
  product (PRODUCTS below). GET without parameters returns the latest
  readings; ?date=YYYY-MM-DD returns one Singapore calendar day. A page
  carries data.paginationToken while more pages remain, and the next page
  is requested with ?paginationToken=<token>.
- An API key, when set, is sent as the x-api-key header; without one the
  public rate limit applies. HTTP 429 means rate limited (Retry-After is
  honoured when present).
- A response body is {"code": 0, "data": {...}, "errorMsg": ...}; any
  other code is an API error and is not retried.
- Station readings: data.stations [{id, name, location{latitude,
  longitude}}], data.readings [{timestamp, data [{stationId, value}]}],
  data.readingType and data.readingUnit. Rainfall's readingType names a
  5-minute total ("... 5 Minute Total ..."), taken as the total over the
  5 minutes ending at the reading's timestamp.
- UV: data.records [{timestamp, updatedTimestamp, index [{hour, value}]}],
  each value the UV index averaged over the hour ending at "hour".
- Forecasts: data.items (2-hour) and data.records (24-hour, 4-day) with
  the provider's issue time in "timestamp" and its revision time in
  "update_timestamp" / "updatedTimestamp".

Nothing here touches storage. Tests drive the client with a recorded or
synthetic transport; no test calls the live service.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from koi.models.local_time import zone

SOURCE = "nea_v2"
SINGAPORE = "Asia/Singapore"
# UV is a national reading; it has no station of its own.
UV_STATION = "NATIONAL"
REPORTING_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class ObservationSpec:
    metric: str
    unit: str
    # WeatherStationLookup."Measurement" tag of stations reporting it
    # (the tags resolve_closest_station already uses), or None.
    tag: Optional[str]
    # data key in the weather_telemetry cache row, or None if the cache
    # does not carry this metric.
    cache_key: Optional[str]


# Product name (the endpoint path) -> what it carries.
OBSERVATION_PRODUCTS: dict[str, ObservationSpec] = {
    "air-temperature": ObservationSpec("air_temperature", "degC", "AirTemp", "air_temperature"),
    "rainfall": ObservationSpec("rainfall", "mm", "Rainfall", "rainfall"),
    "wind-speed": ObservationSpec("wind_speed", "knot", "Wind", "wind_speed"),
    "relative-humidity": ObservationSpec("relative_humidity", "%", "RelativeHumidity", None),
    "wind-direction": ObservationSpec("wind_direction", "deg", None, None),
}
UV_PRODUCT = "uv"
FORECAST_PRODUCTS = {"two-hr-forecast": "2hr", "twenty-four-hr-forecast": "24hr", "four-day-outlook": "4day"}
PRODUCTS = (*OBSERVATION_PRODUCTS, UV_PRODUCT, *FORECAST_PRODUCTS)
AREA_TAG = "2HourForecast"

# Provider unit spellings -> the normalised unit of weather_observation.
_UNITS = {
    "deg c": "degC", "degc": "degC", "degrees celsius": "degC", "°c": "degC",
    "percentage": "%", "%": "%", "percent": "%",
    "mm": "mm", "millimetres": "mm", "millimeters": "mm",
    "knots": "knot", "knot": "knot", "kn": "knot",
    "degrees": "deg", "degree": "deg", "deg": "deg",
}


class NeaFetchError(Exception):
    """A product could not be fetched. kind is one of network, http,
    rate_limited, malformed, api, pagination."""

    def __init__(self, product: str, kind: str, detail: str):
        self.product = product
        self.kind = kind
        self.detail = detail
        super().__init__(f"{product}: {kind}: {detail}")


# transport(url, headers, timeout_seconds) -> (status, response headers, body)
Transport = Callable[[str, dict, float], tuple[int, dict, bytes]]


def urllib_transport(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    """The default transport (standard library only). Network failures
    raise OSError; HTTP errors come back as their status."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https base URL
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


class NeaClient:
    """Fetches every page of one product, with retry and backoff.

    Retried: network errors, HTTP 5xx, HTTP 429 (waiting Retry-After
    seconds when given) and bodies that are not JSON, up to max_attempts
    per page with backoff_seconds * 2**(attempt-1), capped at
    max_backoff_seconds. Not retried: other HTTP 4xx and code != 0.
    Requests are spaced at least min_interval_seconds apart."""

    def __init__(self, base_url: str, api_key: str = "", transport: Optional[Transport] = None,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
                 max_attempts: int = 5, backoff_seconds: float = 2.0, max_backoff_seconds: float = 60.0,
                 min_interval_seconds: float = 1.0, timeout_seconds: float = 30.0, max_pages: int = 200):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or urllib_transport
        self._sleep = sleep
        self._monotonic = monotonic
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.min_interval_seconds = min_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self._last_request: Optional[float] = None
        self.requests = 0
        self.retries = 0

    def fetch(self, product: str, day: Optional[date] = None) -> list[dict]:
        """The data object of every page of product: the latest readings,
        or one Singapore calendar day when day is given."""
        params: dict[str, str] = {"date": day.isoformat()} if day else {}
        pages: list[dict] = []
        while True:
            if len(pages) >= self.max_pages:
                raise NeaFetchError(product, "pagination", f"more than {self.max_pages} pages")
            data = self._fetch_page(product, params)
            pages.append(data)
            token = data.get("paginationToken")
            if not token:
                return pages
            params = {**params, "paginationToken": str(token)}

    def _url(self, product: str, params: dict[str, str]) -> str:
        url = f"{self.base_url}/{product}"
        return f"{url}?{urllib.parse.urlencode(params)}" if params else url

    def _wait_turn(self) -> None:
        if self._last_request is not None:
            gap = self.min_interval_seconds - (self._monotonic() - self._last_request)
            if gap > 0:
                self._sleep(gap)
        self._last_request = self._monotonic()

    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_seconds * 2 ** (attempt - 1), self.max_backoff_seconds)

    def _fetch_page(self, product: str, params: dict[str, str]) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "outdoorkoi-weather/1"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        url = self._url(product, params)
        last: Optional[NeaFetchError] = None
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                self.retries += 1
            self._wait_turn()
            self.requests += 1
            try:
                status, response_headers, body = self._transport(url, headers, self.timeout_seconds)
            except OSError as exc:
                last = NeaFetchError(product, "network", str(exc) or exc.__class__.__name__)
                self._sleep(self._backoff(attempt))
                continue
            if status == 429:
                last = NeaFetchError(product, "rate_limited", "HTTP 429")
                self._sleep(_retry_after(response_headers) or self._backoff(attempt))
                continue
            if 500 <= status < 600:
                last = NeaFetchError(product, "http", f"HTTP {status}")
                self._sleep(self._backoff(attempt))
                continue
            if status != 200:
                raise NeaFetchError(product, "http", f"HTTP {status}")
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                last = NeaFetchError(product, "malformed", "response is not JSON")
                self._sleep(self._backoff(attempt))
                continue
            if not isinstance(payload, dict):
                raise NeaFetchError(product, "malformed", "response is not a JSON object")
            if payload.get("code") != 0:
                raise NeaFetchError(product, "api", str(payload.get("errorMsg") or f"code={payload.get('code')}"))
            data = payload.get("data")
            if not isinstance(data, dict):
                raise NeaFetchError(product, "malformed", "response has no data object")
            return data
        assert last is not None
        raise last


def _retry_after(headers: dict) -> Optional[float]:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return max(float(value), 0.0)
            except (TypeError, ValueError):
                return None
    return None


# ---------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------
@dataclass
class Parsed:
    """What one product's pages produced. Each record that could not be
    used is counted in malformed with a reason (the first few kept)."""

    product: str
    observations: list[dict] = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)
    stations: list[dict] = field(default_factory=list)
    records: int = 0
    malformed: int = 0
    reasons: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.malformed += 1
        if len(self.reasons) < 5:
            self.reasons.append(reason)


def parse_time(value: Any) -> Optional[datetime]:
    """An ISO 8601 instant as an aware datetime, or None. A value without
    an offset is Singapore local time, as NEA reports it."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=zone(SINGAPORE))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def instant(value: object) -> datetime:
    """A stored timestamp (ISO string or datetime) as an aware datetime; a
    value without a zone is UTC, as koi.storage.base.parse_timestamp reads
    database values. (Kept here so koi.weather does not import storage.)"""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def normalise_unit(unit: Any) -> Optional[str]:
    if not isinstance(unit, str):
        return None
    return _UNITS.get(unit.strip().lower())


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def rainfall_semantics(reading_type: Any) -> Optional[tuple[str, timedelta]]:
    """(semantics, interval) from the rainfall readingType: an N-minute
    total, a cumulative total (stored, never summed), or None when the
    readingType does not say (the rows are then rejected rather than
    risk adding up a cumulative series)."""
    if not isinstance(reading_type, str):
        return None
    text = reading_type.lower()
    if "cumulative" in text or "accumulated" in text:
        return "cumulative_total", timedelta(0)
    match = re.search(r"(\d+)\s*min(?:ute)?s?\s+total", text)
    if match and int(match.group(1)) > 0:
        return "interval_total", timedelta(minutes=int(match.group(1)))
    return None


def parse_station_readings(product: str, pages: list[dict], fetched_at: datetime) -> Parsed:
    spec = OBSERVATION_PRODUCTS[product]
    out = Parsed(product)
    seen_stations: set[str] = set()
    for data in pages:
        unit = normalise_unit(data.get("readingUnit"))
        semantics, interval = "instantaneous", timedelta(0)
        if spec.metric == "rainfall":
            found = rainfall_semantics(data.get("readingType"))
            if found is None:
                n = sum(len(r.get("data") or []) for r in data.get("readings") or [] if isinstance(r, dict))
                out.records += n
                for _ in range(n):
                    out.reject(f"rainfall readingType {data.get('readingType')!r} is not a known total")
                continue
            semantics, interval = found
        for station in data.get("stations") or []:
            if not isinstance(station, dict) or not station.get("id"):
                continue
            sid = str(station["id"])
            if sid in seen_stations or spec.tag is None:
                continue
            seen_stations.add(sid)
            location = station.get("location") or {}
            out.stations.append({"station_id": sid, "station_name": station.get("name"),
                                 "latitude": _number(location.get("latitude")),
                                 "longitude": _number(location.get("longitude")), "tag": spec.tag})
        for reading in data.get("readings") or []:
            entries = reading.get("data") if isinstance(reading, dict) else None
            ts = parse_time(reading.get("timestamp")) if isinstance(reading, dict) else None
            for entry in entries or []:
                out.records += 1
                if ts is None:
                    out.reject("reading without a valid timestamp")
                    continue
                if unit != spec.unit:
                    out.reject(f"unit {data.get('readingUnit')!r} is not {spec.unit}")
                    continue
                value = _number(entry.get("value")) if isinstance(entry, dict) else None
                reading_station = entry.get("stationId") if isinstance(entry, dict) else None
                if value is None or not reading_station:
                    out.reject("reading without a numeric value and station")
                    continue
                out.observations.append({
                    "source": SOURCE, "series": "station", "station_id": str(reading_station), "metric": spec.metric,
                    "value": value, "unit": unit, "semantics": semantics,
                    "observed_from": iso(ts - interval), "observed_to": iso(ts),
                    "fetched_at": iso(fetched_at), "regime_id": None,
                    "provenance": {"product": product, "reading_type": data.get("readingType"),
                                   "reading_unit": data.get("readingUnit")},
                })
    return out


def parse_uv(pages: list[dict], fetched_at: datetime) -> Parsed:
    out = Parsed(UV_PRODUCT)
    for data in pages:
        for record in data.get("records") or []:
            for item in (record.get("index") if isinstance(record, dict) else None) or []:
                out.records += 1
                hour = parse_time(item.get("hour")) if isinstance(item, dict) else None
                value = _number(item.get("value")) if isinstance(item, dict) else None
                if hour is None or value is None:
                    out.reject("UV entry without an hour and a numeric value")
                    continue
                out.observations.append({
                    "source": SOURCE, "series": "station", "station_id": UV_STATION, "metric": "uv_index",
                    "value": value, "unit": "UV index", "semantics": "interval_mean",
                    "observed_from": iso(hour - timedelta(hours=1)), "observed_to": iso(hour),
                    "fetched_at": iso(fetched_at), "regime_id": None,
                    "provenance": {"product": UV_PRODUCT, "record_timestamp": record.get("timestamp"),
                                   "record_updated": record.get("updatedTimestamp")},
                })
    return out


def _issuance(product: str, slot: str, issued: Optional[datetime], updated: Optional[datetime],
              valid_from: datetime, valid_to: datetime, payload: dict, fetched_at: datetime,
              provenance: dict) -> dict:
    return {"source": SOURCE, "product": product, "slot_id": slot,
            "issued_at": iso(issued) if issued else None, "source_updated_at": iso(updated) if updated else None,
            "valid_from": iso(valid_from), "valid_to": iso(valid_to), "payload": payload,
            "fetched_at": iso(fetched_at), "provenance": provenance}


def parse_two_hour(pages: list[dict], fetched_at: datetime) -> Parsed:
    out = Parsed("two-hr-forecast")
    for data in pages:
        for area in data.get("area_metadata") or []:
            if isinstance(area, dict) and area.get("name"):
                loc = area.get("label_location") or {}
                out.stations.append({"station_id": None, "station_name": area["name"],
                                     "latitude": _number(loc.get("latitude")),
                                     "longitude": _number(loc.get("longitude")), "tag": AREA_TAG})
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            valid = item.get("valid_period") or {}
            start, end = parse_time(valid.get("start")), parse_time(valid.get("end"))
            issued, updated = parse_time(item.get("timestamp")), parse_time(item.get("update_timestamp"))
            for fc in item.get("forecasts") or []:
                out.records += 1
                area_name = fc.get("area") if isinstance(fc, dict) else None
                text = fc.get("forecast") if isinstance(fc, dict) else None
                if isinstance(text, dict):
                    text = text.get("text")
                if not area_name or not isinstance(text, str) or start is None or end is None or start >= end:
                    out.reject("2-hour forecast without an area, text and validity window")
                    continue
                out.forecasts.append(_issuance(
                    "2hr", area_name, issued, updated, start, end, {"forecast": text}, fetched_at,
                    {"product": "two-hr-forecast", "valid_text": valid.get("text")}))
    return out


def parse_twenty_four_hour(pages: list[dict], fetched_at: datetime) -> Parsed:
    out = Parsed("twenty-four-hr-forecast")
    for data in pages:
        for record in data.get("records") or []:
            if not isinstance(record, dict):
                continue
            issued, updated = parse_time(record.get("timestamp")), parse_time(record.get("updatedTimestamp"))
            general = record.get("general")
            out.records += 1
            if isinstance(general, dict):
                valid = general.get("validPeriod") or {}
                start, end = parse_time(valid.get("start")), parse_time(valid.get("end"))
                if start is not None and end is not None and start < end:
                    payload = {k: v for k, v in general.items() if k != "validPeriod"}
                    out.forecasts.append(_issuance("24hr", "GENERAL", issued, updated, start, end, payload,
                                                   fetched_at, {"product": "twenty-four-hr-forecast",
                                                                "valid_text": valid.get("text")}))
                else:
                    out.reject("24-hour general forecast without a validity window")
            else:
                out.reject("24-hour record without a general forecast")
            for period in record.get("periods") or []:
                tp = (period.get("timePeriod") if isinstance(period, dict) else None) or {}
                start, end = parse_time(tp.get("start")), parse_time(tp.get("end"))
                regions = (period.get("regions") if isinstance(period, dict) else None) or {}
                for region, block in regions.items():
                    out.records += 1
                    if isinstance(block, str):
                        block = {"text": block}
                    if start is None or end is None or start >= end or not isinstance(block, dict):
                        out.reject("24-hour regional forecast without a validity window")
                        continue
                    out.forecasts.append(_issuance(
                        "24hr", f"REG_{str(region).upper()}", issued, updated, start, end,
                        {k: block.get(k) for k in ("code", "text") if k in block}, fetched_at,
                        {"product": "twenty-four-hr-forecast", "valid_text": tp.get("text")}))
    return out


def parse_four_day(pages: list[dict], fetched_at: datetime) -> Parsed:
    """Each outlook day is one issuance row, slot = its Singapore date and
    validity that whole local day."""
    out = Parsed("four-day-outlook")
    sg = zone(SINGAPORE)
    for data in pages:
        for record in data.get("records") or []:
            if not isinstance(record, dict):
                continue
            issued, updated = parse_time(record.get("timestamp")), parse_time(record.get("updatedTimestamp"))
            for entry in record.get("forecasts") or []:
                out.records += 1
                when = parse_time(entry.get("timestamp")) if isinstance(entry, dict) else None
                if when is None:
                    out.reject("4-day outlook day without a timestamp")
                    continue
                day = when.astimezone(sg).date()
                start = datetime(day.year, day.month, day.day, tzinfo=sg)
                out.forecasts.append(_issuance(
                    "4day", day.isoformat(), issued, updated, start, start + timedelta(days=1), dict(entry),
                    fetched_at, {"product": "four-day-outlook"}))
    return out


def parse(product: str, pages: list[dict], fetched_at: datetime) -> Parsed:
    if product in OBSERVATION_PRODUCTS:
        return parse_station_readings(product, pages, fetched_at)
    if product == UV_PRODUCT:
        return parse_uv(pages, fetched_at)
    if product == "two-hr-forecast":
        return parse_two_hour(pages, fetched_at)
    if product == "twenty-four-hr-forecast":
        return parse_twenty_four_hour(pages, fetched_at)
    if product == "four-day-outlook":
        return parse_four_day(pages, fetched_at)
    raise ValueError(f"unknown NEA product {product!r}")
