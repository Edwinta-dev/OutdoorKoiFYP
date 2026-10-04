"""The three services of the local stack in one process, and the checks
`python -m koi.dev --check` runs against them.

Stack starts the digital twin API and the camera service on their own
HTTP servers (werkzeug, threaded) and the worker on a background thread
that runs a poll cycle at once and then every poll_interval_minutes. All
three share one Storage, and the API and the worker share one
EngineRegistry, as `python -m koi.api` does with KOI_ENV=development.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from werkzeug.serving import BaseWSGIServer, make_server

from koi.api import create_app as create_api
from koi.api.dashboard import build_dashboard
from koi.camera import create_app as create_camera
from koi.logs import configure_logging
from koi.registry import EngineRegistry
from koi.settings import Settings
from koi.storage import Storage, fail_soft
from koi.worker import poller


class Stack:
    def __init__(self, settings: Settings, storage: Storage, host: str = "127.0.0.1", api_port: int = 8080,
                 camera_port: int = 5000):
        self.settings = settings
        self.storage = storage
        self.api_app = create_api(settings, storage=storage)
        self.camera_app = create_camera(settings, storage=storage)
        # Each app set the process's log to its own service name; one
        # process serves all three here, so the lines say "dev".
        configure_logging(settings, "dev")
        self.registry: EngineRegistry = self.api_app.extensions["koi_registry"]
        self.worker = poller.Worker(settings, self.registry)
        self.first_cycle = threading.Event()
        self.first_cycle_polled: Optional[bool] = None
        self._stop = threading.Event()
        self._servers: list[BaseWSGIServer] = [make_server(host, api_port, self.api_app, threaded=True),
                                               make_server(host, camera_port, self.camera_app, threaded=True)]
        self._threads: list[threading.Thread] = []

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self._servers[0].server_port}"

    @property
    def camera_url(self) -> str:
        return f"http://127.0.0.1:{self._servers[1].server_port}"

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            polled = self.worker.run_cycle()
            if not self.first_cycle.is_set():
                self.first_cycle_polled = polled
                self.first_cycle.set()
            self._stop.wait(self.settings.poll_interval_minutes * 60)

    def start(self) -> "Stack":
        targets = [server.serve_forever for server in self._servers] + [self._run_worker]
        for name, target in zip(("koi-dev-api", "koi-dev-camera", "koi-dev-worker"), targets, strict=True):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def stop(self) -> None:
        self._stop.set()
        for server in self._servers:
            server.shutdown()
        for thread in self._threads:
            thread.join(timeout=30)
        self.worker.stop()


# ---------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------
@dataclass
class Outcome:
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'} {self.name}" + (f" ({self.detail})" if self.detail else "")


def http_get(url: str) -> tuple[int, Any]:
    """(status, JSON body or None). Only used against the local stack."""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - local URL
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def contract_errors(path: str, status: int, body: Any) -> list[str]:
    """Where a GET response breaks the OpenAPI document (koi.api.openapi):
    the status must be documented for the route and the body must match
    its schema. path is the documented path, e.g. /v1/ponds/{pond}/dashboard."""
    from jsonschema import Draft202012Validator

    from koi.api.openapi import build_document

    document = build_document()
    responses = document["paths"][path]["get"]["responses"]
    if str(status) not in responses:
        return [f"status {status} is not documented"]
    content = responses[str(status)].get("content", {}).get("application/json")
    if content is None:
        return [] if body is None else ["a body where none is documented"]
    ref = content["schema"]["$ref"]
    validator = Draft202012Validator({"$ref": ref, "components": document["components"]})
    errors = sorted(validator.iter_errors(body), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(body)'}: {e.message}" for e in errors[:5]]


# (documented path, statuses accepted). 422: not enough history to project.
POND_ROUTES = (
    ("/v1/ponds/{pond}/dashboard", (200,)),
    ("/v1/ponds/{pond}/assessments", (200, 404)),
    ("/v1/ponds/{pond}/profile", (200, 404)),
    ("/v1/ponds/{pond}/forecasts/chemistry", (200, 422)),
    ("/v1/ponds/{pond}/forecasts/evaporation", (200, 422)),
    ("/v1/ponds/{pond}/forecasts/algae", (200, 422)),
)


def check_routes(stack: Stack, ponds: list[int]) -> list[Outcome]:
    outcomes = []
    for pond in ponds:
        for path, accepted in POND_ROUTES:
            status, body = http_get(stack.api_url + path.replace("{pond}", str(pond)))
            errors = contract_errors(path, status, body)
            if status not in accepted:
                errors.insert(0, f"status {status}, expected {' or '.join(map(str, accepted))}")
            name = f"GET {path.replace('{pond}', str(pond))}"
            outcomes.append(Outcome(name, not errors, "; ".join(errors) if errors else f"{status}, matches the "
                                    "OpenAPI document"))
    status, _ = http_get(stack.camera_url + "/")
    outcomes.append(Outcome("GET camera /", status == 200, f"{status}"))
    return outcomes


def check_demo_dashboard(stack: Stack, pond: int) -> Outcome:
    """The fast-forward left assessments, and the newest readings are fresh."""
    status, body = http_get(f"{stack.api_url}/v1/ponds/{pond}/dashboard")
    problems = []
    if status != 200 or not isinstance(body, dict):
        return Outcome(f"pond {pond} dashboard content", False, f"status {status}")
    for domain in ("chemistry", "evaporation", "algae"):
        if not (body.get("assessments") or {}).get(domain):
            problems.append(f"no {domain} assessment")
    stale = [c for c in ("ph", "tds", "water_temp", "lux") if body["readings"][c]["status"] != "ok"]
    if stale:
        problems.append(f"readings not fresh: {', '.join(stale)}")
    return Outcome(f"pond {pond} dashboard content", not problems,
                   "; ".join(problems) or "three assessments, every reading fresh")


def wait_for_worker(stack: Stack, timeout: float = 120.0) -> Outcome:
    if not stack.first_cycle.wait(timeout):
        return Outcome("worker first cycle", False, f"no cycle within {timeout:.0f} s")
    report = fail_soft(lambda: stack.storage.fetch_worker_status(poller.LEASE_NAME), None) or {}
    if not stack.first_cycle_polled:
        return Outcome("worker first cycle", True, "on standby: another worker holds the poller lease")
    ponds = report.get("ponds") or {}
    results = ", ".join(f"pond {p} {r.get('result')}" for p, r in sorted(ponds.items()))
    return Outcome("worker first cycle", True, results or "no ponds")


def dashboard_body(storage: Storage, pond: int, now: datetime, settings: Settings) -> dict:
    """The dashboard the API would send at `now`, built the way the route
    builds it (koi.api.routes.get_dashboard), without an HTTP request."""
    registry = EngineRegistry(storage, settings.hypoxia_thresholds, settings.sensor_ingest)
    profiles = registry.profile_history(pond)
    return build_dashboard(
        pond_id=pond, sources=storage.fetch_dashboard_sources(pond),
        chemistry=storage.fetch_latest_evaluation(pond),
        evaporation=fail_soft(lambda: storage.fetch_latest_evaporation_evaluation(pond), None),
        algae=fail_soft(lambda: storage.fetch_latest_algae_evaluation(pond), None),
        aeration=profiles.at(now).aeration if profiles is not None else None,
        hypoxia_thresholds=settings.hypoxia_thresholds, now=now,
    ).model_dump(mode="json")


def comparable(body: dict) -> dict:
    """A dashboard without the evaluation rows' own id and write time,
    which differ between two stores holding the same evaluation."""
    body = json.loads(json.dumps(body))
    for assessment in (body.get("assessments") or {}).values():
        if isinstance(assessment, dict):
            for key in ("id", "evaluated_at"):
                assessment.pop(key, None)
    return body


def differences(a: Any, b: Any, path: str = "") -> list[str]:
    """Paths at which two JSON values differ, with both values."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            out += differences(a.get(key), b.get(key), f"{path}/{key}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in differences(x, y, f"{path}/{i}")]
    return [] if a == b else [f"{path or '(body)'}: {a!r} != {b!r}"]
