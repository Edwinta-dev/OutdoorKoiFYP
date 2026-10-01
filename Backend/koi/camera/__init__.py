"""Camera service: receives ESP32-CAM frames, runs the HSV green-ratio
analysis and tells the camera how long to sleep.

    python -m koi.camera       # local run on port 5000
    koi.camera.create_app()    # WSGI (PythonAnywhere)

Errors use the koi.errors envelope plus a top-level sleep_sec, which the
ESP32-CAM firmware reads from every reply.
"""
from __future__ import annotations

from typing import Optional

from flask import Flask
from flask_cors import CORS

from koi.errors import register_error_handlers
from koi.settings import Settings, get_settings
from koi.storage import Storage, build_storage


def create_app(settings: Optional[Settings] = None, storage: Optional[Storage] = None) -> Flask:
    settings = settings or get_settings()
    app = Flask(__name__)
    app.config["KOI_SETTINGS"] = settings
    app.extensions["koi_storage"] = storage if storage is not None else build_storage(settings)
    CORS(app, origins=list(settings.cors_origins))

    from koi.camera.camera import FALLBACK_SLEEP_SEC, bp

    app.register_blueprint(bp)
    register_error_handlers(app, extra=lambda: {"sleep_sec": FALLBACK_SLEEP_SEC})
    return app


__all__ = ["create_app"]
