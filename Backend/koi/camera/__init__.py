"""Camera service: receives ESP32-CAM frames, runs the HSV green-ratio
analysis and tells the camera how long to sleep.

    python -m koi.camera       # local run on port 5000
    koi.camera.create_app()    # WSGI (PythonAnywhere)
"""
from __future__ import annotations

from typing import Optional

from flask import Flask
from flask_cors import CORS

from koi.settings import Settings, get_settings
from koi.storage import client


def create_app(settings: Optional[Settings] = None) -> Flask:
    settings = settings or get_settings()
    client.configure(settings)
    app = Flask(__name__)
    app.config["KOI_SETTINGS"] = settings
    CORS(app, origins=list(settings.cors_origins))

    from koi.camera.camera import bp

    app.register_blueprint(bp)
    return app


__all__ = ["create_app"]
