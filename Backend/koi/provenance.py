"""Provenance stamped on every evaluation row (issue #21, migration 0016).

Each push to pond_chemistry_evaluations, pond_evaporation_evaluations or
pond_algae_evaluations carries four values besides the assessment:

  model_version       koi.__version__, plus "+g<commit>" when the backend
                      runs from a git checkout that tracks this package
                      (".dirty" appended when tracked files have
                      uncommitted changes). An installed copy without git
                      reports the package version alone.
  input_cutoff        PondTwin.input_cutoff(): the newest sensor sample,
                      event or camera frame time the twin had consumed.
                      Input time, not computation time (evaluated_at).
  forecast_issued_at  the provider's issue time shared by every forecast
                      record the assessment used, or None when any of
                      them is unknown or they differ. Never the cache's
                      updated_at.
  inputs              {"run", "sensor_groups", "events", "camera_frames",
                      "ratings", "forecasts"}: what the run applied to
                      the twin, and per forecast product the cache records
                      read with their own issue times.

Which forecast products each assessment uses (DOMAIN_FORECASTS):

  chemistry    the 2-hour nowcast and the 4-day outlook (rain context)
  evaporation  the 24-hour general forecast (humidity) and the 4-day
               outlook (days to top-up)
  algae        the 4-day outlook (days to scrub)

The services read forecasts from the bundled dashboard payload, which has
no issue times. A record's issue time comes from the pond's forecast
cache rows (Storage.fetch_dashboard_sources, migration 0012, which has
weather_forecasts.source_issued_at from 0009), and only from a row whose
data is the data the run read: a cache row rewritten since the payload
was read gives an unknown issue time, never the newer issuance's.
"""
from __future__ import annotations

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from koi import __version__
from koi.storage.base import parse_timestamp

PACKAGE_DIR = Path(__file__).resolve().parent

CHEMISTRY = "chemistry"
EVAPORATION = "evaporation"
ALGAE = "algae"
DOMAIN_FORECASTS: dict[str, tuple[str, ...]] = {
    CHEMISTRY: ("forecast_2hr", "outlook_4day"),
    EVAPORATION: ("forecast_24hr_general", "outlook_4day"),
    ALGAE: ("outlook_4day",),
}

_SHA = re.compile(r"[0-9a-f]{7,40}")


# ---------------------------------------------------------------------
# Model version
# ---------------------------------------------------------------------
def _git(*args: str) -> Optional[str]:
    try:
        done = subprocess.run(["git", *args], cwd=PACKAGE_DIR, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_commit() -> Optional[str]:
    """The commit this package's files come from, with ".dirty" when
    tracked files differ from it; None without git or outside a checkout
    that tracks the package (an installed copy in site-packages)."""
    if _git("ls-files", "--error-unmatch", "__init__.py") is None:
        return None
    head = (_git("rev-parse", "--short=12", "HEAD") or "").strip()
    if not _SHA.fullmatch(head):
        return None
    status = _git("status", "--porcelain", "--untracked-files=no")
    return f"{head}.dirty" if status else head


@functools.lru_cache(maxsize=1)
def model_version() -> str:
    """koi.__version__ plus "+g<commit>" when the commit is known (a PEP
    440 local version label). Worked out once per process."""
    commit = git_commit()
    return f"{__version__}+g{commit}" if commit else __version__


# ---------------------------------------------------------------------
# Forecast records
# ---------------------------------------------------------------------
def _iso(value: object) -> Optional[str]:
    if value is None:
        return None
    try:
        return parse_timestamp(value).isoformat()
    except (TypeError, ValueError):
        return None


def _common_issue(records: list[dict]) -> Optional[str]:
    """The issue time every record shares; None when there are none, any
    is unknown, or they differ."""
    issued = {r["issued_at"] for r in records}
    if len(issued) != 1:
        return None
    return issued.pop()


def _product(records: list[dict]) -> dict:
    return {"available": bool(records), "records": records, "issued_at": _common_issue(records)}


def forecast_provenance(payload: Optional[dict], sources: Optional[dict]) -> dict[str, dict]:
    """Per forecast product the bundled payload carried: the cache records
    read ({forecast_type, slot_id, issued_at, cache_updated_at}) and their
    common issue time. sources is fetch_dashboard_sources' result, or None
    when it could not be read (every issue time is then unknown)."""
    nea = (payload or {}).get("nea_forecasts") or {}
    rows = [r for r in ((sources or {}).get("forecasts") or []) if isinstance(r, dict)]
    stations = (sources or {}).get("stations")
    stations = stations if isinstance(stations, dict) else {}

    def record(kind: str, slot: Optional[str], data: Any) -> dict:
        row = next((r for r in rows if r.get("forecast_type") == kind
                    and (slot is None or r.get("slot_id") == slot) and r.get("data") == data), None)
        return {"forecast_type": kind, "slot_id": row.get("slot_id") if row else slot,
                "issued_at": _iso(row.get("source_issued_at")) if row else None,
                "cache_updated_at": _iso(row.get("updated_at")) if row else None}

    two_hour = nea.get("forecast_2hr") or {}
    area = stations.get("two-hr-forecast")
    general = (nea.get("forecast_24hr") or {}).get("general") or {}
    outlook = [e for e in (nea.get("outlook_4day") or []) if isinstance(e, dict)]
    return {
        "forecast_2hr": _product([record("2hr", str(area) if area else None, two_hour)] if two_hour else []),
        "forecast_24hr_general": _product([record("24hr", "GENERAL", general)] if general else []),
        "outlook_4day": _product([record("4day", e.get("slot_id"), e.get("data")) for e in outlook]),
    }


# ---------------------------------------------------------------------
# One run's provenance
# ---------------------------------------------------------------------
@dataclass
class RunProvenance:
    """What one poll or event run consumed. stamp() adds the four
    provenance values to an assessment dict before it is pushed."""

    run: str
    input_cutoff: Optional[datetime]
    forecasts: dict[str, dict]
    sensor_groups: int = 0
    events: int = 0
    camera_frames: int = 0
    ratings: int = 0
    model_version: str = field(default_factory=model_version)

    def values(self, domain: str) -> dict:
        products = DOMAIN_FORECASTS[domain]
        used = {p: self.forecasts.get(p) or _product([]) for p in products}
        records = [r for p in used.values() for r in p["records"]]
        return {
            "model_version": self.model_version,
            "input_cutoff": self.input_cutoff.isoformat() if self.input_cutoff else None,
            "forecast_issued_at": _common_issue(records),
            "inputs": {"run": self.run, "sensor_groups": self.sensor_groups, "events": self.events,
                       "camera_frames": self.camera_frames, "ratings": self.ratings, "forecasts": used},
        }

    def stamp(self, domain: str, assessment: dict) -> dict:
        return {**assessment, **self.values(domain)}


__all__ = ["ALGAE", "CHEMISTRY", "DOMAIN_FORECASTS", "EVAPORATION", "RunProvenance", "forecast_provenance",
           "git_commit", "model_version"]
