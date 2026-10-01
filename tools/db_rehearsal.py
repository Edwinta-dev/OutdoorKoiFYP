"""Migration rehearsal on the local Supabase stack (issue #5).

Usage, from the repo root, with Docker Desktop and `supabase start` running:
    python tools/db_rehearsal.py

Builds the target schema two ways in the local database and checks they
converge:

  B  upgrade path: a clean Supabase database, the supplied dump
     (schema.sql) restored into it, synthetic legacy rows loaded
     (tools/rehearsal/legacy_rows.sql), then every migration after
     0001_baseline applied. This is what the live project goes through.
  A  fresh path: `supabase db reset`, which applies every migration to a
     clean database.

It compares the public schema (pg_dump -s, which includes definitions,
grants, policies and publication membership), the storage buckets and
storage policies, and checks that every legacy row survives path B
unchanged. SQL comment lines are ignored when comparing, because the dump
lacks one comment line the live dashboard function has (see
0001_baseline.sql).

Only the local stack is touched. The script refuses to run if the
container is not the local Supabase database. It ends with path A, so the
local database is left at the current migrations for tests/sql.
Exit code 0 when both paths converge and every row is kept.
"""
from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "supabase" / "migrations"
DUMP = REPO / "schema.sql"
LEGACY_ROWS = REPO / "tools" / "rehearsal" / "legacy_rows.sql"
CONTAINER = "supabase_db_OutdoorKoiFYP"

# Legacy tables and the columns they had before the later migrations, so
# rows can be compared even where a migration adds a column.
LEGACY_TABLES = {
    '"UserData"': ('"userID", volume, biomass, region, latitude, longitude, manualpostallocation, '
                   '"ClosestStations"::text'),
    '"SensorData"': 'id, created_at, sensor_type, data1, "userID"',
    '"imageTable"': 'id, created_at, green_ratio, current_state, "user_ID", "imageURL"',
    '"pondInterventions"': 'id, "userID", event_type, volume_percentage, volume_litres, food_grams, event_timestamp',
    "daily_sensor_averages": "id, userid, sensor_type, avg_value, min_value, max_value, record_date",
    "pond_chemistry_state": "user_id, snapshot, updated_at",
    "pond_chemistry_evaluations": "id, userid, status, tan_ppm, no2_ppm, no3_ppm, advisory",
    "pond_algae_evaluations": "id, userid, status, green_ratio",
    "pond_evaporation_evaluations": "id, userid, status, loss_litres",
    "weather_telemetry": "station_id, metric_type, data, valid_start, valid_end",
    "weather_forecasts": "forecast_type, slot_id, data, valid_period",
    '"WeatherStationLookup"': '"Id", station__id, station__name, "Measurement"',
    '"Fish_Database"': '"Title", "Common Name", "pH", "Temperature"',
}

STORAGE_QUERY = (
    "select 'bucket ' || id || ' public=' || public "
    "|| ' mime=' || coalesce(array_to_string(allowed_mime_types, ','), '-') "
    "from storage.buckets "
    "union all "
    "select 'policy ' || policyname || ' ' || cmd || ' ' || array_to_string(roles, ',') || ' using=' "
    "|| coalesce(qual, '-') || ' check=' || coalesce(with_check, '-') "
    "from pg_policies where schemaname = 'storage' "
    "order by 1"
)


def run(cmd: list[str], stdin: bytes | None = None, check: bool = True) -> str:
    proc = subprocess.run(cmd, input=stdin, capture_output=True, cwd=REPO)
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd[:4])} ...\n{out}\n{proc.stderr.decode('utf-8', 'replace')}")
    return out


def psql(sql: bytes, *, stop_on_error: bool = True) -> str:
    flags = ["-v", "ON_ERROR_STOP=1"] if stop_on_error else []
    return run(["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d", "postgres", "-q", "-tA", *flags],
               stdin=sql)


def query(sql: str) -> str:
    return psql(sql.encode())


def reset(workdir: Path | None = None) -> None:
    cmd = ["supabase", "db", "reset", "--no-seed"]
    if workdir is not None:
        cmd += ["--workdir", str(workdir)]
    run(cmd)


def snapshot_rows() -> dict[str, str]:
    return {t: query(f"select md5(coalesce(string_agg(r::text, '|' order by r::text), '')) "
                     f"from (select {cols} from public.{t}) r").strip()
            for t, cols in LEGACY_TABLES.items()}


def schema_text() -> str:
    dump = run(["docker", "exec", CONTAINER, "pg_dump", "-U", "postgres", "-d", "postgres", "-s", "-n", "public"])
    return dump + "\n-- storage --\n" + query(STORAGE_QUERY)


def normalise(text: str) -> list[str]:
    lines = []
    for line in text.replace("\r", "").splitlines():
        if not line.strip() or line.lstrip().startswith("--") or line.startswith(("\\restrict", "\\unrestrict")):
            continue
        lines.append(line.rstrip())
    return lines


def main() -> int:
    if CONTAINER not in run(["docker", "ps", "--format", "{{.Names}}"]):
        sys.exit(f"{CONTAINER} is not running; start the local stack with `supabase start`")

    later = sorted(p for p in MIGRATIONS.glob("*.sql") if p.name != "0001_baseline.sql")

    print("B  upgrade path: clean database, schema.sql, legacy rows, then", ", ".join(p.name for p in later))
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "supabase" / "migrations").mkdir(parents=True)
        shutil.copy(REPO / "supabase" / "config.toml", Path(tmp) / "supabase" / "config.toml")
        reset(Path(tmp))
    psql(DUMP.read_bytes())
    psql(LEGACY_ROWS.read_bytes())
    rows_before = snapshot_rows()
    for path in later:
        psql(path.read_bytes())
    rows_after = snapshot_rows()
    schema_b = schema_text()

    print("A  fresh path: supabase db reset")
    reset()
    schema_a = schema_text()

    ok = True
    changed = [t for t in LEGACY_TABLES if rows_before[t] != rows_after[t]]
    empty = [t for t in LEGACY_TABLES if rows_before[t] == "d41d8cd98f00b204e9800998ecf8427e"]
    if changed:
        ok = False
        print("FAIL rows changed by the upgrade:", ", ".join(changed))
    else:
        print(f"PASS every legacy row kept ({len(LEGACY_TABLES)} tables)")
    if empty:
        print("NOTE no legacy rows loaded for:", ", ".join(empty))

    diff = list(difflib.unified_diff(normalise(schema_a), normalise(schema_b), "fresh (A)", "upgrade (B)",
                                     lineterm="", n=2))
    if diff:
        ok = False
        print("FAIL schemas differ:")
        print("\n".join(diff[:200]))
    else:
        objects = len(re.findall(r"^CREATE ", schema_a, re.M))
        grants = len(re.findall(r"^GRANT ", schema_a, re.M))
        print(f"PASS fresh and upgrade paths converge ({objects} CREATE statements, {grants} grants, "
              "policies, publication membership, storage buckets and policies)")
    print("Local database left at the current migrations (path A).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
