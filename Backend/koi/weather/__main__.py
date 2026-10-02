"""python -m koi.weather: the NEA ingestion job.

    python -m koi.weather live [--products rainfall,uv,...]
    python -m koi.weather backfill --start 2026-09-01 --end 2026-09-07 [--products ...] [--force]

Exits 1 when any product or window failed, so a scheduler can alert.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import Optional, Sequence

from koi.logs import configure_logging, log_event
from koi.settings import Settings, get_settings
from koi.storage import Storage, build_storage
from koi.weather import job, nea


def _products(value: str) -> tuple[str, ...]:
    names = tuple(p.strip() for p in value.split(",") if p.strip())
    unknown = [p for p in names if p not in nea.PRODUCTS]
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"unknown product(s) {unknown}; choose from {', '.join(nea.PRODUCTS)}")
    return names


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m koi.weather", description="NEA weather ingestion")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("live", help="fetch the latest response of each product and update the caches")
    live.add_argument("--products", type=_products, default=nea.PRODUCTS)
    back = sub.add_parser("backfill", help="fetch whole Singapore days into history (resumable)")
    back.add_argument("--start", type=date.fromisoformat, required=True)
    back.add_argument("--end", type=date.fromisoformat, required=True)
    back.add_argument("--products", type=_products, default=nea.PRODUCTS)
    back.add_argument("--force", action="store_true", help="refetch windows already done")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, settings: Optional[Settings] = None,
         storage: Optional[Storage] = None, client: Optional[nea.NeaClient] = None) -> int:
    args = parse_args(argv)
    settings = settings or get_settings()
    configure_logging(settings, "weather")
    storage = storage if storage is not None else build_storage(settings)
    client = client or job.build_client(settings)
    if args.command == "live":
        runs = job.run_live(storage, client, settings, args.products)
    else:
        runs = job.run_backfill(storage, client, settings, args.start, args.end, args.products, force=args.force)
    failed = [r for r in runs if r.status == "failed"]
    log_event(logging.getLogger("koi.weather"), "weather_run_finished", command=args.command, runs=len(runs),
              failed=len(failed), requests=client.requests, retries=client.retries)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
