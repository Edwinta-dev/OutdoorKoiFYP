"""Digital twin HTTP API: chemistry, evaporation and algae per pond.

    python -m koi.api          # local run on port 8080
    koi.api.create_app(...)    # WSGI (PythonAnywhere, gunicorn)

The app holds one Storage (from settings.storage unless one is passed in)
and one EngineRegistry over it, in app.extensions["koi_storage"] and
app.extensions["koi_registry"]. Every error response uses the envelope
in koi.errors.
"""
from __future__ import annotations

from typing import Optional

from flask import Flask
from flask_cors import CORS

from koi.errors import register_error_handlers
from koi.registry import EngineRegistry
from koi.settings import Settings, get_settings
from koi.storage import Storage, build_storage


def create_app(settings: Optional[Settings] = None, storage: Optional[Storage] = None) -> Flask:
    settings = settings or get_settings()
    storage = storage if storage is not None else build_storage(settings)
    app = Flask(__name__)
    app.config["KOI_SETTINGS"] = settings
    app.extensions["koi_storage"] = storage
    app.extensions["koi_registry"] = EngineRegistry(storage)
    CORS(app, origins=list(settings.cors_origins))

    from koi.api.routes import bp

    app.register_blueprint(bp)
    register_error_handlers(app)
    return app


__all__ = ["create_app"]
