"""python -m koi.worker: runs the poller in the foreground until stopped.

Start one per deployment. A second one is safe: it stays on standby
while the first holds the poller lease (see koi/worker/poller.py).
"""
from __future__ import annotations

import logging
from typing import Optional

from koi.error_tracking import init_error_tracking
from koi.logs import configure_logging, log_event
from koi.registry import EngineRegistry
from koi.settings import Settings, get_settings
from koi.storage import Storage, build_storage
from koi.worker import poller


def main(settings: Optional[Settings] = None, storage: Optional[Storage] = None) -> None:
    settings = settings or get_settings()
    storage = storage if storage is not None else build_storage(settings)
    configure_logging(settings, "worker")
    init_error_tracking(settings, "worker")
    log_event(logging.getLogger("koi.worker"), "worker_started", poll_interval_minutes=settings.poll_interval_minutes,
              worker_threads=settings.worker_threads, env=settings.env, storage=settings.storage)
    poller.start(settings, EngineRegistry(storage, settings.hypoxia_thresholds), blocking=True)


if __name__ == "__main__":
    main()
