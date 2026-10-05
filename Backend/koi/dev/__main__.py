"""python -m koi.dev: the local development stack (docs/dev.md).

Profiles:
  memory    (default) MemoryStorage seeded from the demo seed. Nothing
            leaves the process; the data is lost on exit.
  supabase  the same seed written into the local Supabase stack
            (`supabase start`) and served through SupabaseStorage. Needs
            every migration applied (`supabase db reset`).

Either way the seed is moved to the current time, the worker is run over
its 14 days of history (koi/dev/replay.py), and then the API, the camera
service and the worker start, with KOI_ENV=development, KOI_AUTH=disabled
(the pond in the path is trusted) and error reporting off. --seed-only
(supabase profile) stops after the fast-forward, for running the three
services as separate processes (docker-compose.yml).

--check starts the stack on free local ports, waits for the worker's
first cycle, requests every pond's dashboard, assessments, profile and
forecasts, validates each response against the OpenAPI document, checks
the demo pond's dashboard has its three assessments and fresh readings,
stops and exits 0 when everything passed, 1 otherwise. With the supabase
profile it also checks every migration is applied, compares each pond's
dashboard with the memory profile's on the same seed, and removes the
seeded rows afterwards. When the local stack is not available the check
prints SKIP and exits 2.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from koi.dev import seed as seed_data
from koi.dev.replay import fast_forward
from koi.dev.stack import (
    Outcome,
    Stack,
    check_demo_dashboard,
    check_routes,
    comparable,
    dashboard_body,
    differences,
    wait_for_worker,
)
from koi.logs import log_event
from koi.settings import Settings
from koi.storage import MemoryStorage, Storage

log = logging.getLogger("koi.dev")

EXIT_SKIP = 2


def dev_settings(profile: str, **overrides: Any) -> Settings:
    """Settings for the stack: environment and Backend/.env, with the
    values the stack depends on fixed."""
    values: dict[str, Any] = {"env": "development", "auth": "disabled", "sentry_dsn": "",
                              "storage": "supabase" if profile == "supabase" else "memory"}
    if profile == "memory":
        values.update(supabase_url="", supabase_servicerole_key="")
    values.update(overrides)
    return Settings(**values)


def memory_storage(tables: dict[str, list[dict]], ponds: list[int], now: datetime) -> MemoryStorage:
    """MemoryStorage holding the shifted seed, with each pond's bundled
    payload (get_bundled_dashboard_payload) as of now."""
    storage = MemoryStorage(seed={"tables": tables})
    history = seed_data.SeedHistory(tables)
    for pond in ponds:
        storage.set_dashboard_payload(pond, history.payload(pond, now))
    return storage


def _write(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m koi.dev", description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", choices=("memory", "supabase"), default="memory")
    parser.add_argument("--check", action="store_true", help="start, check the API, stop; exit 0 when it passed")
    parser.add_argument("--seed", type=Path, default=seed_data.SEED_PATH, help="seed file (default: the demo pond)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to serve on (default 0.0.0.0, so a phone on the same network can reach it)")
    parser.add_argument("--api-port", type=int, default=8080)
    parser.add_argument("--camera-port", type=int, default=5000)
    parser.add_argument("--seed-only", action="store_true",
                        help="supabase profile: write the seed and fast-forward, then exit without serving")
    parser.add_argument("--keep", action="store_true",
                        help="supabase profile with --check: leave the seeded rows in the local database")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.seed_only and (args.profile != "supabase" or args.check):
        parser.error("--seed-only needs --profile supabase and no --check (memory data ends with the process)")
    now = datetime.now(timezone.utc)
    seed = seed_data.shift(seed_data.load(args.seed), now)
    tables, ponds = seed["tables"], [int(p) for p in seed["ponds"]]
    outcomes: list[Outcome] = []
    cleanup: Optional[Callable[[Stack], None]] = None

    if args.profile == "supabase":
        from koi.dev import local_db

        try:
            stack_info = local_db.discover(dev_settings("supabase"))
            conn = local_db.connect(stack_info)
        except local_db.LocalStackUnavailable as exc:
            _write(f"SKIP local Supabase stack not available: {exc}")
            return EXIT_SKIP if args.check else 1
        except ValueError as exc:  # not the local stack: refused
            _write(f"FAIL {exc}")
            return 1
        missing = local_db.missing_migrations(conn)
        outcomes.append(Outcome("local database at every migration", not missing,
                                f"not applied: {', '.join(missing)}; run `supabase db reset`" if missing
                                else f"{len(list(local_db.MIGRATIONS.glob('*.sql')))} migrations applied"))
        if missing:
            _write(outcomes[-1].line())
            return 1
        settings = dev_settings("supabase", supabase_url=stack_info.api_url,
                                supabase_servicerole_key=stack_info.service_role_key)
        counts = local_db.seed(conn, tables, ponds)
        outcomes.append(Outcome("seed written to the local database", True,
                                ", ".join(f"{t} {n}" for t, n in counts.items())))
        from koi.storage import SupabaseStorage

        storage: Storage = SupabaseStorage(settings)
        if args.check and not args.keep:
            def cleanup(stack: Stack) -> None:
                local_db.remove(conn, tables, ponds, worker_holder=stack.worker.holder)
    else:
        settings = dev_settings("memory")
        storage = memory_storage(tables, ponds, now)

    replays = fast_forward(storage, tables, settings, ponds)
    for r in replays:
        detail = f"{r.polls_ok} polls, {r.events_applied} interventions, {r.polls_skipped} skipped"
        if r.skip_reasons:
            detail += f" ({'; '.join(r.skip_reasons)})"
        outcomes.append(Outcome(f"fast-forward pond {r.pond_id}", True, detail))

    if args.profile == "supabase" and args.check:
        mirror = memory_storage(tables, ponds, now)
        fast_forward(mirror, tables, settings, ponds)
        for pond in ponds:
            diff = differences(comparable(dashboard_body(storage, pond, now, settings)),
                               comparable(dashboard_body(mirror, pond, now, settings)))
            outcomes.append(Outcome(f"pond {pond} dashboard: local database equals memory profile", not diff,
                                    "; ".join(diff[:5])))

    if args.seed_only:
        for outcome in outcomes:
            _write(outcome.line())
        return 0

    host = "127.0.0.1" if args.check else args.host
    ports = (0, 0) if args.check else (args.api_port, args.camera_port)
    stack = Stack(settings, storage, host, *ports).start()
    try:
        if not args.check:
            return _serve(stack, args)
        outcomes.append(wait_for_worker(stack))
        outcomes += check_routes(stack, ponds)
        outcomes.append(check_demo_dashboard(stack, ponds[0]))
    finally:
        stack.stop()
        if cleanup is not None:
            cleanup(stack)
    for outcome in outcomes:
        _write(outcome.line())
    failed = [o for o in outcomes if not o.ok]
    _write(f"{len(outcomes) - len(failed)} passed, {len(failed)} failed ({args.profile} profile)")
    return 1 if failed else 0


def _serve(stack: Stack, args: argparse.Namespace) -> int:
    log_event(log, "dev_stack_started", profile=args.profile, api=f"{args.host}:{args.api_port}",
              camera=f"{args.host}:{args.camera_port}")
    _write(f"Digital twin API  http://{args.host}:{args.api_port}/v1/ponds/1/dashboard\n"
           f"Camera service    http://{args.host}:{args.camera_port}/\n"
           f"Worker            polling every {stack.settings.poll_interval_minutes} min\n"
           "Ctrl-C to stop. Point the app at it with env/dev.json (docs/dev.md).")
    try:
        while True:
            # time.sleep, unlike a bare Event.wait, wakes for Ctrl-C on Windows.
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
