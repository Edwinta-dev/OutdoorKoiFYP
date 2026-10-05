"""python -m koi.tools.rebuild --pond N [--since DATE] [--dry-run]

Rebuilds one pond's twin from its history (issue #20) and compares it
with the stored snapshot.

--------------------------------------------------------------------
WHAT THE REBUILD READS
--------------------------------------------------------------------
A fresh twin is created from the pond profile (pond_profile, or the
UserData volume and biomass of a pond with none) and advanced by the
poller's own pond step (koi.worker.poller._poll_user) at each time the
pond's node reported, oldest first, then once more at the input cutoff
(the time the rebuild started). At each of those times HistoryStorage
answers the step's reads as they stood then:

  sensor rows     the pond's SensorData rows in the ingest ledger
                  (sensor_ingest_history) with a sample time up to the
                  cutoff. Rows not yet ingested are left to the poller.
  interventions   pondInterventions rows created by then, applied by the
                  step's own reconciliation (issue #19).
  camera frames   imageTable rows created by then (the newest
                  FRAME_LIMIT of the pond).
  ratings         algae_severity_ratings rows rated by then, applied
                  between polls as the rating endpoint applies them.
  weather         the weather history of migration 0009 as of then
                  (weather_payload_as_of below), never the latest caches.

The weather replay contract (#24): the step's payload carries, for the
pond's assigned stations and areas, the newest air temperature, rainfall
and wind speed observed in the TELEMETRY_REACH before the poll, and the
2-hour, 24-hour general and regional and 4-day forecasts usable then
(weather_forecast_as_of). A part with no history is left out, so the
step uses the same fallbacks the live poller uses for an empty cache;
the report lists those intervals as incomplete with the outputs they
affect. No past condition is taken from today's cache rows.

Times the live poller ran are not recorded, so the rebuild polls at the
node's upload times. Evaporation and algae are integrated over those
steps, which can differ slightly from the live cadence.

--since DATE starts the fresh twin at local midnight of DATE (or at an
ISO 8601 time) and leaves out every input before it.

--------------------------------------------------------------------
WHAT IT WRITES
--------------------------------------------------------------------
--dry-run writes nothing. Without it, the rebuilt snapshot is saved as
the pond's next snapshot_version against the version read at the start;
if the worker saved in between, the save is refused and nothing changes.
The sensor ingest ledger is left as it is (the rebuild applied the rows
it already holds), and the evaluation rows are left to the next poll.
The rebuild is logged as one pond_rebuilt event with its report.
Overwriting a live snapshot is an owner maintenance action.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from koi.dev.replay import ReplayStorage
from koi.dev.seed import SeedHistory
from koi.error_tracking import MODEL_VERSION, package_version
from koi.logs import configure_logging, log_event
from koi.models.event_ledger import derived_event_id
from koi.models.local_time import DEFAULT_POND_TIME_ZONE, zone
from koi.models.pond_twin import PondTwin
from koi.models.profile import ProfileHistory, profile_from_userdata_config
from koi.models.sensor_inputs import SENSOR_TYPES
from koi.registry import EngineRegistry
from koi.settings import Settings, get_settings
from koi.storage import StaleSnapshotError, Storage, StorageError, build_storage
from koi.storage.base import INTERVENTION_COLUMNS, parse_timestamp
from koi.weather.history import local_day_window
from koi.worker import poller

log = logging.getLogger("koi.tools.rebuild")

# How old the newest station observation may be and still stand for the
# conditions at a poll.
TELEMETRY_REACH = timedelta(hours=1)
# Camera frames read for the rebuild (the newest of the pond).
FRAME_LIMIT = 1000
RATING_LIMIT = 1000
# Days of 4-day outlook looked up from a poll's local date.
OUTLOOK_DAYS = 5

# (payload key, ClosestStations slot, weather_observation metric)
TELEMETRY = (("air_temp", "air-temperature", "air_temperature"),
             ("rainfall", "rainfall", "rainfall"),
             ("wind_speed", "wind-speed", "wind_speed"))

# What each missing weather part changes in the rebuilt twin.
AFFECTED = {
    "air_temp": ("evaporation loss", "algae growth"),
    "rainfall": ("evaporation loss",),
    "wind_speed": ("evaporation loss",),
    "humidity": ("evaporation loss",),
    "forecast_2hr": ("chemistry rain context",),
    "outlook_4day": ("days to top-up", "days to scrub", "chemistry rain context"),
}
WEATHER_LABELS = {
    "air_temp": "air temperature", "rainfall": "rainfall", "wind_speed": "wind speed",
    "humidity": "humidity (24-hour forecast)", "forecast_2hr": "2-hour forecast", "outlook_4day": "4-day outlook",
}

_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


# ---------------------------------------------------------------------
# Weather as of a past time
# ---------------------------------------------------------------------
def weather_payload_as_of(storage: Storage, stations: dict, at: datetime,
                          time_zone: str = DEFAULT_POND_TIME_ZONE) -> tuple[dict, list[str]]:
    """The nea_telemetry and nea_forecasts parts of the poller's payload
    as they could have been known at `at`, from weather history, and the
    parts that had no history then (keys of AFFECTED)."""
    missing: list[str] = []
    telemetry: dict[str, dict] = {}
    for key, slot, metric in TELEMETRY:
        station = stations.get(slot)
        rows = [] if not station else [
            o for o in storage.fetch_weather_observations(str(station), metric, at - TELEMETRY_REACH, at, at)
            if o.get("semantics") != "cumulative_total" and o.get("value") is not None]
        if rows:
            newest = max(rows, key=lambda o: (parse_timestamp(o["observed_to"]), o.get("id") or 0))
            telemetry[key] = {"value": newest["value"]}
        else:
            telemetry[key] = {}
            missing.append(key)

    area, region = stations.get("two-hr-forecast"), stations.get("twenty-four-hr-forecast")
    two_hour = storage.fetch_forecast_as_of("2hr", str(area), at) if area else None
    general = storage.fetch_forecast_as_of("24hr", "GENERAL", at)
    regional = storage.fetch_forecast_as_of("24hr", str(region), at, valid_at=at) if region else None
    outlook = _outlook_as_of(storage, at, time_zone)
    if two_hour is None:
        missing.append("forecast_2hr")
    if general is None or not (general.get("payload") or {}).get("relativeHumidity"):
        missing.append("humidity")
    if not outlook:
        missing.append("outlook_4day")
    return {
        "nea_telemetry": telemetry,
        "nea_forecasts": {
            "forecast_2hr": (two_hour or {}).get("payload") or {},
            "forecast_24hr": {"regional": (regional or {}).get("payload") or {},
                              "general": (general or {}).get("payload") or {}},
            "outlook_4day": outlook,
        },
    }, missing


def _outlook_as_of(storage: Storage, at: datetime, time_zone: str) -> list[dict]:
    """The days of the newest 4-day outlook issuance usable at `at`, in
    the cache's shape ({slot_id, data, valid_period}), oldest day first."""
    first = at.astimezone(zone(time_zone)).date()
    found = [row for row in (storage.fetch_forecast_as_of("4day", (first + timedelta(days=n)).isoformat(), at)
                             for n in range(OUTLOOK_DAYS)) if row is not None]
    if not found:
        return []
    newest = max(parse_timestamp(r["available_at"]) for r in found)
    days = sorted((r for r in found if parse_timestamp(r["available_at"]) == newest),
                  key=lambda r: parse_timestamp(r["valid_from"]))[:4]
    out = []
    for r in days:
        payload = r.get("payload") or {}
        day = parse_timestamp(r["valid_from"]).astimezone(zone(time_zone))
        out.append({"slot_id": _WEEKDAYS[day.weekday()], "data": payload,
                    "valid_period": {"timestamp": payload.get("timestamp") or day.isoformat()}})
    return out


# ---------------------------------------------------------------------
# The storage the poller step sees during a rebuild
# ---------------------------------------------------------------------
class HistoryStorage(ReplayStorage):
    """Answers the poller step's reads from the pond's history as of
    `at` (module docstring) and keeps the twin's snapshot, the run's
    sensor ingest ledger and the evaluations to itself. Nothing is
    written to the real storage."""

    def __init__(self, storage: Storage, tables: dict[str, list[dict]], stations: dict,
                 time_zone: str = DEFAULT_POND_TIME_ZONE):
        super().__init__(storage, SeedHistory(tables))
        self._tables = tables
        self._stations = stations
        self._time_zone = time_zone
        # (poll time, missing weather parts) of every payload read.
        self.weather: list[tuple[datetime, list[str]]] = []

    def _created_by(self, table: str, at: datetime, time_column: str) -> list[dict]:
        return [r for r in self._tables.get(table, [])
                if parse_timestamp(r.get("created_at") or r[time_column]) <= at]

    def fetch_dashboard_payload(self, user_id: int) -> Optional[dict]:
        at = self._now()
        latest: dict[str, dict] = {}
        for row in self._tables.get("SensorData", []):
            created = parse_timestamp(row["created_at"])
            current = latest.get(row["sensor_type"])
            if created <= at and (current is None or (created, int(row["id"])) >
                                  (parse_timestamp(current["created_at"]), int(current["id"]))):
                latest[row["sensor_type"]] = row
        weather, missing = weather_payload_as_of(self._storage, self._stations, at, self._time_zone)
        self.weather.append((at, missing))
        return {"raw_sensor": {t: r.get("data1") for t, r in sorted(latest.items())},
                "telemetry_history": [], **weather}

    def fetch_interventions(self, user_id: int, since: Optional[datetime]) -> list[dict]:
        """pond_interventions_since over the rows that existed at `at`."""
        rows = [r for r in self._created_by("pondInterventions", self._now(), "event_timestamp")
                if since is None or parse_timestamp(r["event_timestamp"]) >= since]
        rows.sort(key=lambda r: (parse_timestamp(r["event_timestamp"]), int(r["id"])))
        return [{**{c: r.get(c) for c in INTERVENTION_COLUMNS},
                 "event_id": r.get("event_id") or derived_event_id(r["id"])} for r in rows]

    def fetch_recent_feeding_events(self, user_id: int, limit: int = 30) -> list[dict]:
        rows = [r for r in self._created_by("pondInterventions", self._now(), "event_timestamp")
                if r.get("event_type") == "FEEDING"]
        rows.sort(key=lambda r: parse_timestamp(r["event_timestamp"]), reverse=True)
        return [{c: r.get(c) for c in ("food_grams", "protein_percentage", "event_timestamp")} for r in rows[:limit]]

    def fetch_algae_ratings(self, user_id: int, limit: int = 200) -> list[dict]:
        return []  # applied at their own times by rebuild()

    # --- a fresh twin: the stored snapshot is never read ----------------
    def _version(self, user_id: int) -> int:
        return self._snapshots[user_id][1] if user_id in self._snapshots else 0

    def load_engine_state(self, user_id: int) -> Optional[tuple[dict, int]]:
        if user_id not in self._snapshots:
            return None
        snapshot, version = self._snapshots[user_id]
        return json.loads(json.dumps(snapshot)), version

    def fetch_snapshot_version(self, user_id: int) -> int:
        return self._version(user_id)

    def snapshot(self, user_id: int) -> Optional[dict]:
        state = self.load_engine_state(user_id)
        return state[0] if state is not None else None

    def evaluation(self, kind: str, user_id: int) -> Optional[dict]:
        return self._evaluations.get((kind, user_id))


# ---------------------------------------------------------------------
# The rebuild
# ---------------------------------------------------------------------
@dataclass
class RebuildReport:
    pond_id: int
    dry_run: bool
    since: Optional[str]
    input_cutoff: str
    versions: dict
    inputs: dict
    weather: dict
    comparison: list[dict] = field(default_factory=list)
    stored_version: int = 0
    written_version: Optional[int] = None
    rebuilt_snapshot: Optional[dict] = None
    stored_snapshot: Optional[dict] = None

    def to_dict(self) -> dict:
        out = asdict(self)
        out.pop("rebuilt_snapshot")
        out.pop("stored_snapshot")
        return out


def parse_since(value: Optional[str], time_zone: str = DEFAULT_POND_TIME_ZONE) -> Optional[datetime]:
    """--since: a date (local midnight) or an ISO 8601 time (UTC when it
    has no offset)."""
    if not value:
        return None
    try:
        return local_day_window(date.fromisoformat(value), time_zone)[0].astimezone(timezone.utc)
    except ValueError:
        return parse_timestamp(value)


def _after(row: dict, column: str, since: Optional[datetime]) -> bool:
    return since is None or parse_timestamp(row[column]) >= since


def load_history(storage: Storage, pond: int, since: Optional[datetime], cutoff: datetime) -> dict[str, list[dict]]:
    """The pond's rows the rebuild reads, as tables in the seed's shape
    (koi.dev.seed.SeedHistory)."""
    after = since - timedelta(microseconds=1) if since is not None else None
    sensor = [{"id": r["id"], "userID": pond, "sensor_type": r["sensor_type"], "data1": r.get("value"),
               "created_at": r["created_at"]}
              for r in storage.fetch_ingested_sensor_rows(pond, SENSOR_TYPES, after, cutoff)]
    interventions = [{**r, "userID": pond} for r in storage.fetch_interventions(pond, since)
                     if parse_timestamp(r.get("created_at") or r["event_timestamp"]) <= cutoff]
    images = [{**r, "user_ID": pond} for r in storage.fetch_image_history(pond, limit=FRAME_LIMIT)
              if _after(r, "created_at", since) and parse_timestamp(r["created_at"]) <= cutoff]
    ratings = [r for r in storage.fetch_algae_ratings(pond, limit=RATING_LIMIT)
               if r.get("rated_at") is not None and _after(r, "rated_at", since)
               and parse_timestamp(r["rated_at"]) <= cutoff]
    return {"SensorData": sensor, "pondInterventions": interventions, "imageTable": images,
            "algae_severity_ratings": ratings}


def _poll_times(tables: dict[str, list[dict]], since: Optional[datetime], cutoff: datetime) -> list[datetime]:
    times = sorted({parse_timestamp(r["created_at"]) for r in tables["SensorData"]})
    times = [t for t in times if (since is None or t >= since) and t <= cutoff]
    if not times or times[-1] < cutoff:
        times.append(cutoff)
    return times


def _apply_rating(registry: EngineRegistry, pond: int, rating: dict, profiles: Optional[ProfileHistory]) -> None:
    """One stored rating into the twin, as the rating endpoint applies it."""
    obstructed = bool(rating.get("is_obstructed"))
    green = rating.get("green_ratio_at_rating")
    registry.with_twin(pond, lambda twin: twin.apply_severity_rating(
        time=parse_timestamp(rating["rated_at"]),
        severity="obstruction" if obstructed else rating.get("severity"),
        green_ratio_at_rating=float(green) if green is not None else None,
        image_id=rating.get("image_id"),
        rating_id=rating.get("id"),
    ), profiles=profiles)


def rebuild(storage: Storage, settings: Settings, pond: int, since: Optional[datetime] = None,
            now: Optional[datetime] = None, dry_run: bool = True) -> RebuildReport:
    """Rebuilds pond's twin from its history up to now (the input cutoff,
    default: the current time) and compares it with the stored snapshot;
    without dry_run, saves it as the next snapshot version."""
    cutoff = now or datetime.now(timezone.utc)
    sources = storage.fetch_dashboard_sources(pond)
    if not sources.get("pond_exists"):
        raise LookupError(f"pond {pond} has no UserData row")
    found = sources.get("stations")
    stations: dict = found if isinstance(found, dict) else {}
    stored_state = storage.load_engine_state(pond)
    stored_snapshot, stored_version = stored_state if stored_state is not None else (None, 0)

    tables = load_history(storage, pond, since, cutoff)
    history = HistoryStorage(storage, tables, stations)
    registry = EngineRegistry(history, settings.hypoxia_thresholds, settings.sensor_ingest)  # type: ignore[arg-type]
    config = storage.fetch_pond_config(pond) or {}
    profiles = registry.profile_history(pond)
    if profiles is None and config:
        profiles = ProfileHistory([profile_from_userdata_config(config)])
    if profiles is None:
        raise LookupError(f"pond {pond} has no profile and no UserData volume and biomass")
    config_row = {**config, "user_id": pond}

    ratings = sorted(tables["algae_severity_ratings"], key=lambda r: (parse_timestamp(r["rated_at"]), r.get("id") or 0))
    polls = _poll_times(tables, since, cutoff)
    ok = skipped = 0
    previous: Optional[datetime] = None
    for at in polls:
        history.at = at
        for rating in [r for r in ratings if (previous is None or parse_timestamp(r["rated_at"]) > previous)
                       and parse_timestamp(r["rated_at"]) <= at]:
            _apply_rating(registry, pond, rating, profiles)
        try:
            poller._poll_user(registry, pond, config_row, now=at)
            ok += 1
        except poller.PondSkipped:
            skipped += 1
        previous = at

    rebuilt = history.snapshot(pond)
    report = RebuildReport(
        pond_id=pond, dry_run=dry_run, since=since.isoformat() if since else None, input_cutoff=cutoff.isoformat(),
        versions=_versions(storage, pond, tables),
        inputs={"polls": ok, "polls_skipped": skipped, "sensor_rows": len(tables["SensorData"]),
                "interventions": len(tables["pondInterventions"]), "camera_frames": len(tables["imageTable"]),
                "camera_frames_capped": len(tables["imageTable"]) >= FRAME_LIMIT,
                "ratings": len(ratings), "first_poll": polls[0].isoformat(), "last_poll": polls[-1].isoformat()},
        weather=_weather_report(history.weather),
        stored_version=stored_version, rebuilt_snapshot=rebuilt, stored_snapshot=stored_snapshot)
    report.comparison = compare(stored_snapshot, rebuilt, _stored_assessments(storage, pond),
                                {k: history.evaluation(k, pond) for k in _ASSESSMENTS})

    if not dry_run:
        if rebuilt is None:
            raise LookupError(f"pond {pond} has no sensor reading to rebuild from")
        report.written_version = storage.save_engine_snapshot(pond, rebuilt, base_version=stored_version)
    log_event(log, "pond_rebuilt", **report.to_dict())
    return report


def _versions(storage: Storage, pond: int, tables: dict[str, list[dict]]) -> dict:
    """Model, profile and camera mask versions the rebuild ran with."""
    profiles = storage.fetch_pond_profiles(pond)
    mask = storage.fetch_camera_mask(pond)
    frame_masks = sorted({r["mask_version"] for r in tables["imageTable"] if r.get("mask_version") is not None})
    return {
        "package": package_version(), "model": MODEL_VERSION,
        "profile": ({"source": "pond_profile", "rows": [r["id"] for r in profiles],
                     "newest_effective_from": str(profiles[-1]["effective_from"])} if profiles
                    else {"source": "UserData", "rows": [], "newest_effective_from": None}),
        "camera_mask": {"in_force": mask.get("mask_version") if mask else None, "on_frames": frame_masks,
                        "frames_without_mask": sum(1 for r in tables["imageTable"] if r.get("mask_version") is None)},
    }


def _weather_report(polls: list[tuple[datetime, list[str]]]) -> dict:
    """Consecutive polls with the same missing weather parts, as
    intervals; status complete when no poll missed any."""
    intervals: list[dict] = []
    for at, missing in polls:
        if intervals and intervals[-1]["missing"] == missing:
            intervals[-1]["to"] = at.isoformat()
            intervals[-1]["polls"] += 1
        else:
            intervals.append({"from": at.isoformat(), "to": at.isoformat(), "polls": 1, "missing": missing})
    incomplete = [dict(i, affected=sorted({a for m in i["missing"] for a in AFFECTED[m]}))
                  for i in intervals if i["missing"]]
    return {"status": "incomplete" if incomplete else "complete", "incomplete": incomplete}


# ---------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------
_ASSESSMENTS = ("push_evaluation", "push_evaporation_evaluation", "push_algae_evaluation")
_ASSESSMENT_LABELS = {"push_evaluation": "chemistry", "push_evaporation_evaluation": "evaporation",
                      "push_algae_evaluation": "algae"}
_ASSESSMENT_KEYS = ("status", "category", "loss_litres", "days_to_topup", "green_ratio", "days_to_scrub")

# (label, unit, path in the snapshot)
_FIELDS = (
    ("chemistry TAN", "mg", ("chemistry", "tan_mg")),
    ("chemistry NO2", "mg", ("chemistry", "no2_mg")),
    ("chemistry NO3", "mg", ("chemistry", "no3_mg")),
    ("chemistry last TDS", "ppm", ("chemistry", "last_tds_ppm")),
    ("evaporation loss", "L", ("evaporation", "cumulative_loss_litres")),
    ("evaporation advanced to", "", ("evaporation", "last_advance_time")),
    ("algae level (green ratio)", "", ("algae", "green_ratio")),
    ("algae growth rate", "/day", ("algae", "intrinsic_rate")),
    ("algae frames assimilated", "", ("algae", "sample_count")),
    ("last sensor input", "", ("sensor_inputs", "last_input_at")),
)


def _stored_assessments(storage: Storage, pond: int) -> dict[str, Optional[dict]]:
    reads = {"push_evaluation": storage.fetch_latest_evaluation,
             "push_evaporation_evaluation": storage.fetch_latest_evaporation_evaluation,
             "push_algae_evaluation": storage.fetch_latest_algae_evaluation}
    out: dict[str, Optional[dict]] = {}
    for kind, read in reads.items():
        try:
            out[kind] = read(pond)
        except StorageError:
            out[kind] = None
    return out


def _at(snapshot: Optional[dict], path: tuple[str, ...]) -> Any:
    value: Any = snapshot
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _normalised(snapshot: Optional[dict]) -> Optional[dict]:
    """The stored snapshot in the current namespaced shape (a v1 bare
    chemistry snapshot loads through PondTwin.from_snapshot)."""
    if snapshot is None or "chemistry" in snapshot:
        return snapshot
    try:
        return PondTwin.from_snapshot(snapshot).to_snapshot()
    except (KeyError, TypeError, ValueError):
        return None


def compare(stored: Optional[dict], rebuilt: Optional[dict], stored_assessments: dict,
            rebuilt_assessments: dict) -> list[dict]:
    """One row per compared value: {item, unit, stored, rebuilt,
    difference (rebuilt - stored, numbers only), same}."""
    stored = _normalised(stored)
    rows = []

    def row(item: str, unit: str, a: Any, b: Any) -> None:
        numeric = isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool)
        rows.append({"item": item, "unit": unit, "stored": a, "rebuilt": b,
                     "difference": (b - a) if numeric else None,
                     "same": abs(b - a) <= 1e-9 if numeric else a == b})

    for label, unit, path in _FIELDS:
        row(label, unit, _at(stored, path), _at(rebuilt, path))
    row("events applied", "", _applied(stored), _applied(rebuilt))
    for kind in _ASSESSMENTS:
        a, b = stored_assessments.get(kind) or {}, rebuilt_assessments.get(kind) or {}
        for key in _ASSESSMENT_KEYS:
            if key in a or key in b:
                row(f"last {_ASSESSMENT_LABELS[kind]} assessment {key}", "", a.get(key), b.get(key))
    return rows


def _applied(snapshot: Optional[dict]) -> Optional[int]:
    entries = _at(snapshot, ("event_ledger", "entries"))
    if not isinstance(entries, list):
        return None
    return sum(1 for e in entries if e.get("status") == "applied")


# ---------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------
def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def format_report(report: RebuildReport) -> str:
    v, i, w = report.versions, report.inputs, report.weather
    profile = v["profile"]
    mask = v["camera_mask"]
    lines = [
        f"Rebuild of pond {report.pond_id} ({'dry run, nothing written' if report.dry_run else 'write'})",
        f"  inputs from {report.since or 'the first reading'} up to {report.input_cutoff}",
        f"  model {v['model']}, package {v['package']}",
        ("  profile: pond_profile rows " + ", ".join(str(r) for r in profile["rows"])
         + f" (newest effective {profile['newest_effective_from']})") if profile["rows"]
        else "  profile: UserData volume and biomass (no pond_profile rows)",
        ("  camera mask: " + (f"version {mask['in_force']} in force" if mask["in_force"] is not None
                              else "none saved")
         + "; frames used carry mask versions " + (", ".join(str(m) for m in mask["on_frames"]) or "none")
         + f" ({mask['frames_without_mask']} frames without one)"),
        f"  {i['polls']} polls ({i['polls_skipped']} skipped before the first reading) "
        f"from {i['first_poll']} to {i['last_poll']}",
        f"  {i['sensor_rows']} sensor rows, {i['interventions']} interventions, {i['camera_frames']} camera frames"
        + (f" (capped at {FRAME_LIMIT}: older frames left out)" if i["camera_frames_capped"] else "")
        + f", {i['ratings']} algae ratings",
    ]
    if w["status"] == "complete":
        lines.append("  weather: complete for every poll")
    else:
        lines.append("  weather: INCOMPLETE. The model's fallbacks were used for these intervals:")
        for gap in w["incomplete"]:
            lines.append(f"    {gap['from']} to {gap['to']} ({gap['polls']} polls): no "
                         + ", ".join(WEATHER_LABELS[m] for m in gap["missing"])
                         + "; affects " + ", ".join(gap["affected"]))
    lines.append("")
    lines.append(f"Compared with the stored snapshot (version {report.stored_version}):"
                 if report.stored_snapshot is not None else "No stored snapshot to compare with.")
    width = max(len(r["item"]) for r in report.comparison) if report.comparison else 10
    lines.append(f"  {'':{width}}  {'stored':>26}  {'rebuilt':>26}  {'difference':>12}")
    for r in report.comparison:
        mark = "" if r["same"] else "  *"
        unit = f" {r['unit']}" if r["unit"] else ""
        lines.append(f"  {r['item']:{width}}  {_fmt(r['stored']):>26}  {_fmt(r['rebuilt']):>26}  "
                     f"{_fmt(r['difference']):>12}{unit}{mark}")
    lines.append("  (* differs)")
    lines.append("")
    if report.written_version is not None:
        lines.append(f"Wrote the rebuilt snapshot as version {report.written_version}. The next poll moves it to now "
                     "and writes fresh assessments.")
    elif report.dry_run:
        lines.append("Nothing was written. Run again without --dry-run to replace the stored snapshot.")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None, storage: Optional[Storage] = None,
         settings: Optional[Settings] = None, now: Optional[datetime] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m koi.tools.rebuild",
                                     description="Rebuild a pond's twin from its history and compare it with "
                                                 "the stored snapshot.")
    parser.add_argument("--pond", type=int, required=True, help="the pond's userID")
    parser.add_argument("--since", help="start the rebuild at this date (local midnight) or ISO 8601 time")
    parser.add_argument("--dry-run", action="store_true", help="compare only; write nothing")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    settings = settings or get_settings()
    if storage is None:
        configure_logging(settings, "rebuild", stream=sys.stderr)
        storage = build_storage(settings)
    try:
        since = parse_since(args.since)
    except ValueError:
        sys.stderr.write(f"--since {args.since!r} is not a date (YYYY-MM-DD) or an ISO 8601 time\n")
        return 2
    try:
        report = rebuild(storage, settings, args.pond, since=since, now=now, dry_run=args.dry_run)
    except StaleSnapshotError:
        sys.stderr.write(f"Pond {args.pond}'s snapshot was saved by another process during the rebuild; "
                         "nothing was written. Run it again.\n")
        return 1
    except (LookupError, StorageError) as exc:
        sys.stderr.write(f"Rebuild of pond {args.pond} failed: {exc}\n")
        return 1
    sys.stdout.write((json.dumps(report.to_dict(), indent=2, default=str) if args.json
                      else format_report(report)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
