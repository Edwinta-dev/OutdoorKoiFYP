"""Error tracking with Sentry for the API, worker and camera service.

init_error_tracking(settings, service) turns it on when SENTRY_DSN is
set and does nothing otherwise. Each event carries

    environment  SENTRY_ENVIRONMENT, else KOI_ENV
    release      SENTRY_RELEASE, else koi@<package version>+twin.<snapshot version>
    tags         service (api, worker or camera) and model_version

Unhandled request errors and every log line at ERROR or above (the
catch-all error handler, poll_cycle_failed, pond_poll_failed) become
events; INFO and WARNING lines ride along as breadcrumbs.

Personal data is kept out: no IP addresses, cookies, request bodies or
local variables are sent, and scrub_event removes the rest before an
event leaves the process (see SENSITIVE_KEY_PARTS and the value patterns
below): location (latitude, longitude, postal code, station
assignment), email addresses, and tokens, keys and passwords.
"""
from __future__ import annotations

import logging
import re
from importlib import metadata
from typing import Any, Optional, cast

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.types import Breadcrumb, Event

from koi.logs import log_event
from koi.models.pond_twin import SNAPSHOT_VERSION
from koi.settings import Settings

FILTERED = "[Filtered]"

# The pond twin's snapshot version stands for the model version: it
# changes whenever the stored engine state does.
MODEL_VERSION = f"twin.{SNAPSHOT_VERSION}"

# A key containing any of these (case-insensitive, ignoring '_' and '-')
# has its value replaced, whatever the value is.
SENSITIVE_KEY_PARTS = (
    "latitude", "longitude", "location", "postal", "closeststations", "coord", "geo",
    "email", "ipaddress", "remoteaddr", "xforwardedfor", "xrealip",
    "token", "secret", "password", "passwd", "authorization", "cookie", "apikey", "servicerole",
    "jwt", "dsn", "session", "credential",
)
# Keys that are sensitive only as a whole word ("lat" but not "latency").
SENSITIVE_KEYS = {"lat", "lon", "lng", "long", "key", "auth", "ip"}

# Values scrubbed inside any string, whatever its key.
_VALUE_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?i)(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
]
_QUERY_PARAM = re.compile(r"(?i)\b([A-Za-z0-9_.-]+)=([^&\s]*)")

log = logging.getLogger(__name__)


def package_version() -> str:
    try:
        return metadata.version("koi")
    except metadata.PackageNotFoundError:
        return "0+unknown"


def release_name(settings: Settings) -> str:
    return settings.sentry_release or f"koi@{package_version()}+{MODEL_VERSION}"


def _sensitive_key(key: object) -> bool:
    name = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return name in SENSITIVE_KEYS or any(part in name for part in SENSITIVE_KEY_PARTS)


def scrub_text(text: str) -> str:
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(FILTERED, text)
    return _QUERY_PARAM.sub(lambda m: f"{m.group(1)}={FILTERED}" if _sensitive_key(m.group(1)) else m.group(0), text)


def scrub(value: Any) -> Any:
    """value with sensitive keys and values replaced, recursively."""
    if isinstance(value, dict):
        return {k: FILTERED if _sensitive_key(k) else scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # Sentry sends headers as [name, value] pairs in some places.
        if len(value) == 2 and isinstance(value[0], str) and _sensitive_key(value[0]):
            return [value[0], FILTERED]
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_event(event: Event, _hint: Optional[dict] = None) -> Event:
    """before_send hook: removes personal data from an event."""
    user = event.get("user")
    out: dict = scrub({k: v for k, v in event.items() if k != "user"})
    if isinstance(user, dict) and user.get("id") is not None:
        out["user"] = {"id": str(user["id"])}
    request = out.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)
        env = request.get("env")
        if isinstance(env, dict):
            env.pop("REMOTE_ADDR", None)
    return cast(Event, out)


def scrub_breadcrumb(crumb: Breadcrumb, _hint: Optional[dict] = None) -> Breadcrumb:
    """before_breadcrumb hook."""
    return cast(Breadcrumb, scrub(crumb))


def init_error_tracking(settings: Settings, service: str, transport: Any = None) -> bool:
    """Starts Sentry for this process when SENTRY_DSN is set. Returns
    whether it did. transport replaces the network sender (tests)."""
    dsn = settings.sentry_dsn.get_secret_value().strip()
    if not dsn:
        return False
    environment = settings.sentry_environment or settings.env
    release = release_name(settings)
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        transport=transport,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=None,
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )
    scope = sentry_sdk.get_global_scope()
    scope.set_tag("service", service)
    scope.set_tag("model_version", MODEL_VERSION)
    log_event(log, "error_tracking_enabled", service=service, environment=environment, release=release)
    return True


__all__ = ["MODEL_VERSION", "init_error_tracking", "release_name", "scrub", "scrub_breadcrumb", "scrub_event",
           "scrub_text"]
