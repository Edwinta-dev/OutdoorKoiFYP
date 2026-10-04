"""The local database profile of koi.dev: the same seed in the local
Supabase stack (`supabase start`), served through its REST API by
SupabaseStorage, as the deployed services use the live project.

Only a stack on this machine is used. The API URL, the service-role key
and the database URL come from KOI_DEV_SUPABASE_URL,
KOI_DEV_SUPABASE_SERVICE_ROLE_KEY and KOI_DEV_DB_URL (koi.settings) when
set, otherwise from `supabase status -o env`; anything not on this machine (a loopback
address, or host.docker.internal from a container) is refused. Nothing is
written to a file.

seed() first deletes the rows the seed would collide with: every row of
the seed's ponds, the seeded SensorData ids, and the weather cache and
station rows the seed carries. The local stack is disposable; `supabase
db reset` rebuilds it from supabase/migrations.

Needs psycopg (pip install -e "Backend[dev]").
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from koi.settings import Settings

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "supabase" / "migrations"
# This machine; host.docker.internal is it as seen from a container
# (docker-compose.yml, supabase profile).
LOOPBACK = ("127.0.0.1", "localhost", "::1", "host.docker.internal")

# Pond tables and their pond column, children before parents (foreign keys).
POND_TABLES = (
    ("algae_severity_ratings", "userid"),
    ("imageTable", "user_ID"),
    ("pondInterventions", "userID"),
    ("SensorData", "userID"),
    ("daily_sensor_averages", "userid"),
    ("pond_chemistry_evaluations", "userid"),
    ("pond_evaporation_evaluations", "userid"),
    ("pond_algae_evaluations", "userid"),
    ("pond_chemistry_state", "user_id"),
    ("camera_config", "pond_id"),
    ("camera_mask_version", "pond_id"),
    ("pond_profile", "pond_id"),
    ("UserData", "userID"),
)
# Insert order (parents first), and columns left to the database default.
INSERT_ORDER = ("WeatherStationLookup", "weather_telemetry", "weather_forecasts", "UserData", "pond_profile",
                "SensorData", "daily_sensor_averages", "imageTable", "pondInterventions", "pond_chemistry_state")
DATABASE_IDS = {"pond_profile": "id", "daily_sensor_averages": "id"}


class LocalStackUnavailable(RuntimeError):
    """No local Supabase stack answers, or it is not at the migrations."""


@dataclass(frozen=True)
class LocalStack:
    api_url: str
    service_role_key: str
    db_url: str


def _status_env() -> dict[str, str]:
    exe = shutil.which("supabase") or "supabase"
    proc = subprocess.run([exe, "status", "-o", "env"], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode != 0:
        raise LocalStackUnavailable("`supabase status` failed; run `supabase start` from the repository root")
    values = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().isupper():
            values[key.strip()] = value.strip().strip('"')
    return values


def _require_local(url: str, what: str) -> str:
    host = urlparse(url).hostname
    if host not in LOOPBACK:
        raise ValueError(f"{what} must be on this machine (127.0.0.1, localhost or host.docker.internal), "
                         f"not {host!r}")
    return url


def discover(settings: Settings) -> LocalStack:
    """The local stack's URLs and key (see the module docstring)."""
    api: Optional[str] = settings.dev_supabase_url
    key: Optional[str] = settings.dev_supabase_service_role_key.get_secret_value()
    db: Optional[str] = settings.dev_db_url
    if not (api and key and db):
        if shutil.which("supabase") is None:
            raise LocalStackUnavailable("set KOI_DEV_SUPABASE_URL, KOI_DEV_SUPABASE_SERVICE_ROLE_KEY and "
                                        "KOI_DEV_DB_URL, or install the Supabase CLI and run `supabase start`")
        status = _status_env()
        api = api or status.get("API_URL")
        key = key or status.get("SERVICE_ROLE_KEY")
        db = db or status.get("DB_URL")
    if not (api and key and db):
        raise LocalStackUnavailable("`supabase status` did not report API_URL, SERVICE_ROLE_KEY and DB_URL")
    return LocalStack(_require_local(api, "the Supabase API"), key, _require_local(db, "the database"))


def connect(stack: LocalStack) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # a dev dependency
        raise LocalStackUnavailable('psycopg is not installed; pip install -e "Backend[dev]"') from exc
    try:
        return psycopg.connect(stack.db_url, connect_timeout=3)
    except psycopg.OperationalError as exc:
        raise LocalStackUnavailable(f"no database at {stack.db_url}; run `supabase start`") from exc


def missing_migrations(conn: Any) -> list[str]:
    """Migration files in supabase/migrations that the local database has
    not applied (supabase_migrations.schema_migrations)."""
    wanted = {p.name.split("_", 1)[0]: p.name for p in MIGRATIONS.glob("*.sql")}
    try:
        applied = {row[0] for row in conn.execute("select version from supabase_migrations.schema_migrations")}
    except Exception:  # noqa: BLE001 - no migration table: nothing applied
        conn.rollback()
        applied = set()
    return sorted(name for version, name in wanted.items() if version not in applied)


def _value(value: Any) -> Any:
    # A dict or list goes as JSON text, which Postgres reads into the
    # json, jsonb or text column it is inserted into.
    return json.dumps(value) if isinstance(value, (dict, list)) else value


def _delete(cur: Any, tables: dict[str, list[dict]], ponds: list[int]) -> None:
    for table, column in POND_TABLES:
        cur.execute(f'delete from public."{table}" where "{column}" = any(%s)', (ponds,))
    sensor_ids = [r["id"] for r in tables.get("SensorData", []) if r.get("id") is not None]
    cur.execute('delete from public."SensorData" where id = any(%s)', (sensor_ids,))
    cur.execute('delete from public."WeatherStationLookup" where "Id" = any(%s)',
                ([r["Id"] for r in tables.get("WeatherStationLookup", [])],))
    for row in tables.get("weather_telemetry", []):
        cur.execute("delete from public.weather_telemetry where station_id = %s and metric_type = %s",
                    (row["station_id"], row["metric_type"]))
    for row in tables.get("weather_forecasts", []):
        cur.execute("delete from public.weather_forecasts where forecast_type = %s and slot_id = %s",
                    (row["forecast_type"], row["slot_id"]))


def seed(conn: Any, tables: dict[str, list[dict]], ponds: list[int]) -> dict[str, int]:
    """Replaces the seed's rows in the local database (one transaction).
    Returns the rows inserted per table."""
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        _delete(cur, tables, ponds)
        for table in INSERT_ORDER:
            rows = tables.get(table, [])
            for row in rows:
                row = {k: v for k, v in row.items() if k != DATABASE_IDS.get(table)}
                columns = ", ".join(f'"{c}"' for c in row)
                marks = ", ".join(["%s"] * len(row))
                cur.execute(f'insert into public."{table}" ({columns}) values ({marks})',
                            [_value(v) for v in row.values()])
            counts[table] = len(rows)
            if table == "UserData":
                # The UserData trigger (migration 0008) records each insert
                # as a profile row; the seed carries its own profiles.
                cur.execute("delete from public.pond_profile where pond_id = any(%s) and source = 'userdata'",
                            (ponds,))
    conn.commit()
    return counts


def remove(conn: Any, tables: dict[str, list[dict]], ponds: list[int], worker_holder: Optional[str] = None) -> None:
    """Deletes what seed() inserted, anything the services wrote for the
    seed's ponds since, and the cycle report of the given worker."""
    with conn.cursor() as cur:
        _delete(cur, tables, ponds)
        if worker_holder is not None:
            cur.execute("delete from public.worker_status where holder = %s", (worker_holder,))
    conn.commit()
