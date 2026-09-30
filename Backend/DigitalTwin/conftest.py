"""Shared pytest setup for the DigitalTwin tests.

The server modules (state_store, poller, app) import supabase, dotenv and
apscheduler at module level, and state_store builds a Supabase client on
import. Those packages are replaced with inert stubs here, before any test
module is imported, so no test needs credentials or network access. The
in-memory FakeStore stands in for every state_store function the server
calls.
"""
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

_STUBBED_MODULES = ["supabase", "dotenv", "apscheduler",
                    "apscheduler.schedulers", "apscheduler.schedulers.background"]


def _install_dependency_stubs():
    for name in _STUBBED_MODULES:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["supabase"].create_client = lambda *a, **k: None
    sys.modules["supabase"].Client = object
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
    sys.modules["apscheduler.schedulers.background"].BackgroundScheduler = object
    os.environ.setdefault("SUPABASE_URL", "x")
    os.environ.setdefault("SUPABASE_SERVICEROLE_KEY", "y")


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
# In-memory Supabase replacement.
# ---------------------------------------------------------------------
USER = 455

DASHBOARD_PAYLOAD = {
    "raw_sensor": {"pH": 7.64, "TDS": 220, "temp": 29.2, "LUX": 22755},
    "nea_telemetry": {"air_temp": {"value": 30.8}, "rainfall": {"value": 0},
                      "wind_speed": {"value": 6.9}},
    "nea_forecasts": {
        "forecast_2hr": {"forecast": "Fair (Day)"},
        "forecast_24hr": {"general": {"relativeHumidity": {"low": 60, "high": 90},
                                      "temperature": {"low": 26, "high": 33}}},
        "outlook_4day": [
            {"data": {"day": "Wednesday", "wind": {"speed": {"low": 10, "high": 20}},
                      "forecast": {"code": "FA", "text": "Fair (Day)"},
                      "temperature": {"low": 27, "high": 34},
                      "relativeHumidity": {"low": 55, "high": 85}}},
            {"data": {"day": "Thursday", "wind": {"speed": {"low": 10, "high": 20}},
                      "forecast": {"code": "FA", "text": "Fair (Day)"},
                      "temperature": {"low": 27, "high": 34},
                      "relativeHumidity": {"low": 55, "high": 85}}},
        ],
    },
}

# Every state_store function the poller and app.py call.
STORE_FUNCTIONS = [
    "load_engine_snapshot", "save_engine_snapshot", "push_evaluation",
    "push_evaporation_evaluation", "push_algae_evaluation",
    "fetch_latest_evaluation", "fetch_latest_evaporation_evaluation",
    "fetch_latest_algae_evaluation", "fetch_pond_config",
    "fetch_active_pond_configs", "fetch_dashboard_payload",
    "fetch_image_history", "fetch_recent_feeding_events",
    "fetch_daily_sensor_stats", "fetch_daily_sensor_series",
    "slope_per_day", "fetch_last_intervention_time",
    "insert_algae_rating", "fetch_algae_ratings", "delete_algae_rating",
    "fetch_image_by_id",
]


class FakeStore:
    def __init__(self, user_id=USER, payload=DASHBOARD_PAYLOAD):
        self.user_id = user_id
        self.payload = payload
        self.snapshots = {}
        self.chem_evals = []
        self.evap_evals = []
        self.algae_evals = []
        self.camera_rows = []
        self.feeding_rows = []
        self.ratings = []
        self._rating_seq = 0

    # --- snapshot persistence ---
    def load_engine_snapshot(self, user_id):
        raw = self.snapshots.get(user_id)
        # Round-trip through JSON exactly like Supabase JSONB would, so a
        # non-serialisable field fails here rather than in production.
        return json.loads(raw) if raw else None

    def save_engine_snapshot(self, user_id, snapshot):
        self.snapshots[user_id] = json.dumps(snapshot)

    # --- evaluation pushes ---
    def push_evaluation(self, user_id, d):
        self.chem_evals.append(d)

    def push_evaporation_evaluation(self, user_id, d):
        self.evap_evals.append(d)

    def push_algae_evaluation(self, user_id, d):
        self.algae_evals.append(d)

    def fetch_latest_evaluation(self, user_id):
        return self.chem_evals[-1] if self.chem_evals else None

    def fetch_latest_evaporation_evaluation(self, user_id):
        return self.evap_evals[-1] if self.evap_evals else None

    def fetch_latest_algae_evaluation(self, user_id):
        return self.algae_evals[-1] if self.algae_evals else None

    # --- config ---
    def fetch_pond_config(self, user_id):
        return {"volume_litres": 5000.0, "estimated_biomass_grams": 8000.0}

    def fetch_active_pond_configs(self):
        return [{"user_id": self.user_id, "volume_litres": 5000.0,
                 "estimated_biomass_grams": 8000.0}]

    # --- telemetry / history ---
    def fetch_dashboard_payload(self, user_id):
        return self.payload

    def fetch_image_history(self, user_id, limit=200):
        return self.camera_rows[-limit:][::-1]  # newest first, as Supabase returns

    def fetch_recent_feeding_events(self, user_id, limit=30):
        return self.feeding_rows[:limit]

    def fetch_daily_sensor_stats(self, user_id, sensor_type, days=14):
        return {"avg": {"temp": 29.0, "TDS": 220.0, "LUX": 18000.0}.get(sensor_type, 20.0),
                "min": None, "max": None, "sample_days": 10}

    def fetch_daily_sensor_series(self, user_id, sensor_type, days=30):
        base = datetime(2026, 8, 1, tzinfo=timezone.utc)
        return [{"avg_value": 220.0 + i * 0.7, "min_value": None, "max_value": None,
                 "record_date": (base + timedelta(days=i)).isoformat()}
                for i in range(10)]

    def slope_per_day(self, rows, value_key="avg_value"):
        return 0.7

    def fetch_last_intervention_time(self, user_id, event_type):
        return None

    # --- algae severity ratings ---
    def insert_algae_rating(self, user_id, severity, is_obstructed, image_id,
                            image_url, green_ratio_at_rating,
                            image_captured_at=None, notes=None):
        self._rating_seq += 1
        row = {
            "id": self._rating_seq, "userid": user_id, "image_id": image_id,
            "image_url": image_url, "severity": severity,
            "is_obstructed": is_obstructed,
            "green_ratio_at_rating": green_ratio_at_rating,
            "image_captured_at": image_captured_at,
            "rated_at": datetime.now(timezone.utc).isoformat(), "notes": notes,
        }
        self.ratings.append(row)
        return row

    def fetch_algae_ratings(self, user_id, limit=200):
        return self.ratings[-limit:]

    def delete_algae_rating(self, user_id, rating_id):
        self.ratings = [r for r in self.ratings if r["id"] != rating_id]
        return True

    def fetch_image_by_id(self, user_id, image_id):
        for r in self.camera_rows:
            if r.get("id") == image_id:
                return r
        return None


@pytest.fixture(scope="module")
def fake_store():
    """A FakeStore patched over every state_store function for one test
    module. Module-scoped so a module's tests can walk one pond through a
    sequence of polls and events; the patch and the engine registry are
    both reset when the module finishes."""
    import state_store
    from registry import registry

    fake = FakeStore()
    with pytest.MonkeyPatch.context() as mp:
        for fn in STORE_FUNCTIONS:
            mp.setattr(state_store, fn, getattr(fake, fn))
        registry.evict(fake.user_id)
        yield fake
        registry.evict(fake.user_id)


@pytest.fixture(scope="module")
def flask_client(fake_store):
    """Flask test client for app.py, backed by the fake store."""
    import app as flask_app

    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()
