"""Digital twin HTTP API: chemistry, evaporation and algae per pond.

    python -m koi.api          # local run on port 8080
    koi.api.create_app(...)    # WSGI (PythonAnywhere, gunicorn)

The app holds one Storage (from settings.storage unless one is passed in)
and one EngineRegistry over it, in app.extensions["koi_storage"] and
app.extensions["koi_registry"]. Every error response uses the envelope
in koi.errors.

GET /health is liveness only. GET /ready checks storage and the poller
(koi/api/health.py) and returns 503 when not ready. GET /metrics is
Prometheus text: request counts and latency by route, and the poller's
cycle, failure, sensor-age and snapshot-age gauges. Logs are JSON lines
(koi.logs) at settings.log_level. Errors go to Sentry when SENTRY_DSN is
set (koi/error_tracking.py).

Every pond route needs the caller's Supabase access token (koi/api/auth.py).
create_app raises koi.api.auth.AuthConfigError when auth is required but
the settings cannot verify a token; signing_keys is an offline JWKS for
tests.
"""
from __future__ import annotations

from typing import Optional

from flask import Flask
from flask_cors import CORS

from koi.api.auth import SigningKeys
from koi.api.auth import init_app as init_auth
from koi.error_tracking import init_error_tracking
from koi.errors import register_error_handlers
from koi.logs import configure_logging
from koi.observability import instrument_app
from koi.registry import EngineRegistry
from koi.settings import Settings, get_settings
from koi.storage import Storage, build_storage


def create_app(settings: Optional[Settings] = None, storage: Optional[Storage] = None,
               signing_keys: Optional[SigningKeys] = None) -> Flask:
    settings = settings or get_settings()
    configure_logging(settings, "api")
    init_error_tracking(settings, "api")
    storage = storage if storage is not None else build_storage(settings)
    app = Flask(__name__)
    app.config["KOI_SETTINGS"] = settings
    app.extensions["koi_storage"] = storage
    app.extensions["koi_registry"] = EngineRegistry(storage, settings.hypoxia_thresholds, settings.sensor_ingest,
                                                   settings.tds_prompt_config)
    CORS(app, origins=list(settings.cors_origins))
    init_auth(app, settings, signing_keys)

    from koi.api.health import poller_metrics
    from koi.api.routes import bp

    bp.register(app, {})  # what Flask.register_blueprint does
    register_error_handlers(app)
    instrument_app(app, "api", extra_metrics=lambda: poller_metrics(storage))
    return app


__all__ = ["create_app"]
