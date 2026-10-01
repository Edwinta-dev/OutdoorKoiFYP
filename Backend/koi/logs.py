"""Structured logging: one JSON object per line on stderr.

Every line has the fields

    time        UTC, ISO 8601 with milliseconds
    level       DEBUG, INFO, WARNING, ERROR or CRITICAL
    service     api, worker or camera
    pond_id     the pond (UserData userID) being worked on, or null
    request_id  the HTTP request's X-Request-ID, or null outside a request
    event       a short snake_case name ("poll_cycle_finished"), or the
                message text for records from libraries

plus any fields the call passes. Log values and identifiers, never whole
database rows or payloads.

    log = logging.getLogger(__name__)
    log_event(log, "pond_poll_failed", level=logging.WARNING, error=str(exc))

    with pond_context(user_id):   # stamps pond_id on every line inside
        ...

configure_logging(settings, service) installs the formatter on the root
logger at settings.log_level. The services call it at start-up.
"""
from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from koi.settings import Settings

_pond_id: ContextVar[Optional[str]] = ContextVar("koi_pond_id", default=None)
_request_id: ContextVar[Optional[str]] = ContextVar("koi_request_id", default=None)

# The koi subpackage that names the service a record came from, so the
# poller's lines say "worker" even when it runs inside the API process.
_SERVICE_PACKAGES = {"koi.api": "api", "koi.worker": "worker", "koi.camera": "camera"}

# LogRecord attributes that are not caller-supplied fields.
_RECORD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


def current_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(request_id: Optional[str]):
    """Sets the request id for this context; returns a token for reset_request_id."""
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def set_pond_id(pond_id: object):
    return _pond_id.set(None if pond_id is None else str(pond_id))


def reset_pond_id(token) -> None:
    _pond_id.reset(token)


@contextmanager
def pond_context(pond_id: object) -> Iterator[None]:
    token = set_pond_id(pond_id)
    try:
        yield
    finally:
        reset_pond_id(token)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, exc_info: Any = None,
              **fields: Any) -> None:
    """Logs one named event with extra fields."""
    logger.log(level, event, exc_info=exc_info, extra={"koi_event": event, "koi_fields": fields})


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def _service_for(self, logger_name: str) -> str:
        for package, service in _SERVICE_PACKAGES.items():
            if logger_name == package or logger_name.startswith(package + "."):
                return service
        return self.service

    def format(self, record: logging.LogRecord) -> str:
        # Fields passed as logging's own extra={...} are kept too.
        fields = {**{k: v for k, v in vars(record).items() if k not in _RECORD_ATTRS and not k.startswith("koi_")},
                  **(getattr(record, "koi_fields", None) or {})}
        # A pond_id field names the pond when no pond_context is active.
        pond_id = _pond_id.get() or fields.pop("pond_id", None)
        fields.pop("pond_id", None)
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self._service_for(record.name),
            "pond_id": None if pond_id is None else str(pond_id),
            "request_id": _request_id.get(),
            "event": getattr(record, "koi_event", None) or record.getMessage(),
        }
        if getattr(record, "koi_event", None) is None:
            entry["logger"] = record.name
        for key, value in fields.items():
            entry.setdefault(key, value)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class _KoiHandler(logging.StreamHandler):
    """Marks the handler configure_logging installed, so a second call
    replaces it instead of adding another."""


def configure_logging(settings: Settings, service: str, stream=None) -> logging.Handler:
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _KoiHandler)]:
        root.removeHandler(handler)
    handler = _KoiHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    # The request hook logs every request; werkzeug's own access line would
    # repeat it as unstructured text.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    return handler


__all__ = ["JsonFormatter", "configure_logging", "current_request_id", "log_event", "pond_context",
           "reset_pond_id", "reset_request_id", "set_pond_id", "set_request_id"]
