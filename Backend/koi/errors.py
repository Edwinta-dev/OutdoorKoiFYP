"""The one error format every HTTP service returns, and the exceptions
that map onto it.

Every non-2xx response from the digital twin API and the camera service
has this body:

    {"error": {"code": "validation_failed",
               "message": "The request has 2 invalid fields.",
               "details": {...}}}

code is a stable snake_case string a client can branch on; message is
plain text for a person; details holds machine-readable context (the
invalid fields, a retry delay) and is {} when there is none. A service
can add top-level keys next to "error": the camera service adds
sleep_sec, which the ESP32-CAM firmware reads from every reply.

Routes raise ApiError (or a subclass) instead of building error
responses; register_error_handlers turns those, pydantic validation
errors, StorageError, Flask's HTTP errors and any other exception into
the envelope.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from flask import Flask, current_app, jsonify
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from koi.storage.base import StorageError

# How long a client should wait before retrying after a storage failure.
STORAGE_RETRY_AFTER_SEC = 30


class ApiError(Exception):
    """An error with a known HTTP status, returned in the envelope."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, status: Optional[int] = None, code: Optional[str] = None,
                 details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.details = details or {}


class PondNotConfigured(ApiError):
    """The pond has no stored engine state and no volume and biomass in
    UserData, so there is nothing to model. 404."""

    status = 404
    code = "pond_not_configured"

    def __init__(self, user_id: int):
        super().__init__(
            f"Pond {user_id} is not set up. Complete onboarding (pond volume and fish biomass) first.",
            details={"user_id": user_id},
        )
        self.user_id = user_id


def error_body(code: str, message: str, details: Optional[dict] = None, **extra: Any) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}, **extra}


def error_response(status: int, code: str, message: str, details: Optional[dict] = None, **extra: Any):
    return jsonify(error_body(code, message, details, **extra)), status


def validation_details(exc: ValidationError) -> dict:
    """Field-level detail: one entry per invalid field, with the field's
    dotted path (None for a rule across several fields)."""
    return {"fields": [
        {"field": ".".join(str(p) for p in err["loc"]) or None, "type": err["type"], "message": err["msg"]}
        for err in exc.errors(include_url=False, include_input=False, include_context=False)
    ]}


def register_error_handlers(app: Flask, extra: Optional[Callable[[], dict]] = None) -> None:
    """Sends every error raised in app as the envelope. extra() returns
    top-level keys added to every error body (the camera's sleep_sec)."""
    # Flask's TESTING mode otherwise re-raises unexpected exceptions before
    # the catch-all handler can format them, unlike deployed requests.
    app.config["PROPAGATE_EXCEPTIONS"] = False

    def reply(status: int, code: str, message: str, details: Optional[dict] = None, headers=None):
        resp, status = error_response(status, code, message, details, **(extra() if extra else {}))
        if headers:
            resp.headers.update(headers)
        return resp, status

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return reply(exc.status, exc.code, exc.message, exc.details)

    @app.errorhandler(ValidationError)
    def _validation_error(exc: ValidationError):
        count = exc.error_count()
        return reply(400, "validation_failed",
                     f"The request has {count} invalid field{'s' if count != 1 else ''}.",
                     validation_details(exc))

    @app.errorhandler(StorageError)
    def _storage_error(exc: StorageError):
        current_app.logger.error("storage failure: %s", exc)
        return reply(503, "storage_unavailable",
                     f"The pond database could not be reached. Try again in {STORAGE_RETRY_AFTER_SEC} seconds.",
                     {"operation": exc.operation, "retry_after_sec": STORAGE_RETRY_AFTER_SEC},
                     headers={"Retry-After": str(STORAGE_RETRY_AFTER_SEC)})

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        if exc.code is None or exc.code < 400:
            return exc  # routing redirects are not errors
        code = (exc.name or "error").lower().replace(" ", "_").replace("'", "")
        return reply(exc.code, code, exc.description or exc.name)

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        current_app.logger.exception("unhandled error")
        return reply(500, "internal_error", "The server hit an unexpected error. It has been logged.")


__all__ = ["ApiError", "PondNotConfigured", "STORAGE_RETRY_AFTER_SEC", "error_body", "error_response",
           "register_error_handlers", "validation_details"]
