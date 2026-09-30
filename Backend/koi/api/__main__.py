"""python -m koi.api: runs the digital twin API on port 8080.

With KOI_ENV=development the poller also runs in this process, as the old
`python app.py` did. In any other environment run `python -m koi.worker`
separately.
"""
from __future__ import annotations

from typing import Optional

from koi.api import create_app
from koi.settings import Settings, get_settings


def main(settings: Optional[Settings] = None, run: bool = True):
    settings = settings or get_settings()
    app = create_app(settings)
    if settings.poller_in_api_process:
        # Single process only - see koi/registry.py for why multi-worker
        # deployment needs a different locking strategy first.
        from koi.worker import poller

        poller.start(settings)
    if run:
        app.run(host="0.0.0.0", port=8080, threaded=True)
    return app


if __name__ == "__main__":
    main()
