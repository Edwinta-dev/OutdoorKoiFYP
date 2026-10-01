"""python -m koi.worker: runs the poller in the foreground until stopped.

Start one per deployment. A second one is safe: it stays on standby
while the first holds the poller lease (see koi/worker/poller.py).
"""
from __future__ import annotations

from typing import Optional

from koi.registry import EngineRegistry
from koi.settings import Settings, get_settings
from koi.storage import Storage, build_storage
from koi.worker import poller


def main(settings: Optional[Settings] = None, storage: Optional[Storage] = None) -> None:
    settings = settings or get_settings()
    storage = storage if storage is not None else build_storage(settings)
    print(f"[worker] polling every {settings.poll_interval_minutes} min, {settings.worker_threads} ponds at a time "
          f"({settings.env}, {settings.storage} storage)")
    poller.start(settings, EngineRegistry(storage), blocking=True)


if __name__ == "__main__":
    main()
