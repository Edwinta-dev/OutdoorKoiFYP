"""Shared pytest setup for the backend tests.

Tests run the services against koi.storage.MemoryStorage, seeded from
the JSON files in tests/fixtures. supabase and apscheduler are also
replaced with inert stubs before any test module is imported, so no test
can reach the live database or start a real scheduler. Settings are built
with _env_file=None so a developer's real Backend/.env is never read.
"""
import itertools
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_STUBBED_MODULES = ["supabase", "apscheduler", "apscheduler.schedulers",
                    "apscheduler.schedulers.background", "apscheduler.schedulers.blocking"]


def _install_dependency_stubs():
    for name in _STUBBED_MODULES:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["supabase"].create_client = lambda *a, **k: None
    sys.modules["supabase"].Client = object
    sys.modules["apscheduler.schedulers.background"].BackgroundScheduler = object
    sys.modules["apscheduler.schedulers.blocking"].BlockingScheduler = object


# Runs at import time: pytest imports the test modules (and through them
# the server modules) during collection, before any fixture could run.
_install_dependency_stubs()


# ---------------------------------------------------------------------
# Assertion counter, reported in the terminal summary so a run shows how
# many checks executed, not only how many test functions passed.
# ---------------------------------------------------------------------
_passed_assertions = 0


def pytest_assertion_pass(item, lineno, orig, expl):
    global _passed_assertions
    _passed_assertions += 1


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"{_passed_assertions} assertions passed")


# ---------------------------------------------------------------------
# In-memory storage seeded from a recorded pond.
# ---------------------------------------------------------------------
FIXTURES = Path(__file__).resolve().parent / "fixtures"
POND_FIXTURE = FIXTURES / "pond_455.json"
USER = 455

# The get_bundled_dashboard_payload result the fixture pond returns.
DASHBOARD_PAYLOAD = json.loads(POND_FIXTURE.read_text(encoding="utf-8"))["dashboard_payloads"][str(USER)]

# A complete algae assessment dict (AlgaeAssessment.to_dict() shape).
ALGAE_ASSESSMENT = {
    "status": "Green", "category": "clear", "green_ratio": 0.03, "watch_threshold": 0.06,
    "action_threshold": 0.12, "threshold_mode": "relative", "growth_rate_per_day": 0.1,
    "intrinsic_rate_per_day": 0.2, "rate_source": "fitted_from_camera", "confidence": "medium",
    "sample_count": 6, "days_to_scrub": 9, "advisory": "No scrub needed.", "scrub_now": False,
    "label_count": 3,  # in the API response, but no column in pond_algae_evaluations
}


def ticking_clock(start=datetime(2026, 8, 20, tzinfo=timezone.utc)):
    """A clock that moves one second per reading, so every stored
    timestamp is distinct and a run is repeatable."""
    ticks = itertools.count()
    return lambda: start + timedelta(seconds=next(ticks))


def make_storage():
    """A MemoryStorage holding the fixture pond: UserData, 10 days of
    daily temp, LUX and TDS averages, and the dashboard payload."""
    from koi.storage import MemoryStorage

    return MemoryStorage.from_json(POND_FIXTURE, clock=ticking_clock())


@pytest.fixture
def storage():
    return make_storage()


def make_settings(**overrides):
    """Settings that ignore Backend/.env and the process environment's
    Supabase credentials, with in-memory storage."""
    from koi.settings import Settings

    values = {"env": "test", "storage": "memory", "supabase_url": "", "supabase_servicerole_key": ""}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="module")
def pond_app():
    """The digital twin API over a fixture-pond MemoryStorage, shared by
    one test module so its tests can walk one pond through a sequence of
    polls and events. The app's storage and registry are in
    app.extensions["koi_storage"] and app.extensions["koi_registry"]."""
    from koi.api import create_app

    app = create_app(make_settings(), storage=make_storage())
    app.config["TESTING"] = True
    return app
