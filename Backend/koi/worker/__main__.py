"""python -m koi.worker: runs the poller in the foreground until stopped."""
from __future__ import annotations

from typing import Optional

from koi.settings import Settings, get_settings
from koi.storage import client
from koi.worker import poller


def main(settings: Optional[Settings] = None) -> None:
    settings = settings or get_settings()
    client.configure(settings)
    print(f"[worker] polling every {settings.poll_interval_minutes} min ({settings.env})")
    poller.start(settings, blocking=True)


if __name__ == "__main__":
    main()
