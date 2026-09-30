"""The one Supabase client both services share.

The client is built on first use, not at import, so importing the package
needs no credentials and tests (which replace every state_store function
with an in-memory fake) never build one.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

from koi.settings import Settings, get_settings

if TYPE_CHECKING:
    from supabase import Client

_lock = threading.Lock()
_settings: Optional[Settings] = None
_client: Optional["Client"] = None


def configure(settings: Settings) -> None:
    """Points the storage layer at these settings. Called by each app
    factory; drops any client built from earlier settings."""
    global _settings, _client
    with _lock:
        _settings = settings
        _client = None


def get_client() -> "Client":
    global _client
    with _lock:
        if _client is None:
            settings = _settings or get_settings()
            key = settings.supabase_servicerole_key.get_secret_value()
            if not settings.supabase_url or not key:
                raise RuntimeError(
                    "SUPABASE_URL and SUPABASE_SERVICEROLE_KEY must be set "
                    "(copy Backend/.env.example to Backend/.env)")
            from supabase import create_client

            _client = create_client(settings.supabase_url, key)
        return _client
