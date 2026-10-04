"""SQL tests for migration 0016_evaluation_provenance.sql (issue #21): the
nullable provenance columns on the three evaluation tables, rows written
before them reading null (unknown), rows written with them, the shape
checks, and the tables' grants left as they were.

Same rules as test_sql_rpcs.py: local database only, every test rolled
back, skipped when no local database answers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND = 9421
TABLES = ("pond_chemistry_evaluations", "pond_evaporation_evaluations", "pond_algae_evaluations")
COLUMNS = {"model_version": "text", "input_cutoff": "timestamp with time zone",
           "forecast_issued_at": "timestamp with time zone", "inputs": "jsonb"}
# The insert each table took before 0016 (the backend's old column set, abridged).
LEGACY = {
    "pond_chemistry_evaluations": "(userid, status, category, tan_ppm, no2_ppm, no3_ppm, advisory) "
                                  "values (%s, 'Green', 'Normal', 0.1, 0.0, 5.0, 'ok')",
    "pond_evaporation_evaluations": "(userid, status, category, loss_litres) values (%s, 'Green', 'Normal', 1.0)",
    "pond_algae_evaluations": "(userid, status, category, green_ratio) values (%s, 'Green', 'clear', 0.03)",
}
INPUTS = {"run": "poll", "sensor_groups": 1, "events": 0, "camera_frames": 0, "ratings": 0,
          "forecasts": {"outlook_4day": {"available": True, "issued_at": "2026-08-19T20:00:00+00:00",
                                         "records": [{"forecast_type": "4day", "slot_id": "2026-08-20",
                                                      "issued_at": "2026-08-19T20:00:00+00:00",
                                                      "cache_updated_at": "2026-08-20T00:45:00+00:00"}]}}}


def _connect() -> Any:
    host = urlparse(DB_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail(f"KOI_TEST_DB_URL must point at a local database, not {host}")
    try:
        return psycopg.connect(DB_URL, connect_timeout=2, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no local database at {DB_URL} ({exc.__class__.__name__}); run `supabase start`")


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    c = _connect()
    with c.cursor() as cur:
        cur.execute("select count(*) as n from information_schema.columns where table_schema = 'public' "
                    "and table_name = 'pond_algae_evaluations' and column_name = 'model_version'")
        row = cur.fetchone()
    c.rollback()
    if not row["n"]:
        c.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        yield cur
    conn.rollback()


@pytest.mark.parametrize("table", TABLES)
def test_provenance_columns_are_nullable_without_defaults(db, table):
    db.execute("select column_name, data_type, is_nullable, column_default from information_schema.columns "
               "where table_schema = 'public' and table_name = %s and column_name = any(%s)",
               (table, list(COLUMNS)))
    found = {r["column_name"]: r for r in db.fetchall()}
    assert {c: r["data_type"] for c, r in found.items()} == COLUMNS
    assert all(r["is_nullable"] == "YES" and r["column_default"] is None for r in found.values())


@pytest.mark.parametrize("table", TABLES)
def test_provenance_legacy_insert_reads_unknown(db, table):
    """The insert the old backend made still works, and the row reads null
    provenance rather than being labelled with any version."""
    db.execute(f"insert into public.{table} {LEGACY[table]} returning id", (POND,))
    row_id = db.fetchone()["id"]
    db.execute(f"select model_version, input_cutoff, forecast_issued_at, inputs, evaluated_at "
               f"from public.{table} where id = %s", (row_id,))
    row = db.fetchone()
    assert row["evaluated_at"] is not None
    assert (row["model_version"], row["input_cutoff"], row["forecast_issued_at"], row["inputs"]) == (
        None, None, None, None)


@pytest.mark.parametrize("table", TABLES)
def test_provenance_insert_round_trips(db, table):
    columns = LEGACY[table].split(") values (")[0] + ", model_version, input_cutoff, forecast_issued_at, inputs)"
    values = "(" + LEGACY[table].split(") values (")[1][:-1] + ", %s, %s, %s, %s)"
    db.execute(f"insert into public.{table} {columns} values {values} returning *",
               (POND, "0.1.0+g1a2b3c4d5e6f", "2026-08-19T23:59:00+00:00", None, Jsonb(INPUTS)))
    row = db.fetchone()
    assert row["model_version"] == "0.1.0+g1a2b3c4d5e6f"
    assert row["input_cutoff"].isoformat() == "2026-08-19T23:59:00+00:00"
    assert row["forecast_issued_at"] is None
    assert row["inputs"] == INPUTS
    assert row["input_cutoff"] < row["evaluated_at"], "input time, not computation time"


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("column, value", [("model_version", ""), ("inputs", Jsonb([1, 2])),
                                           ("inputs", Jsonb("poll"))])
def test_provenance_shape_check_refuses_bad_values(db, table, column, value):
    db.execute(f"insert into public.{table} {LEGACY[table]} returning id", (POND,))
    row_id = db.fetchone()["id"]
    with pytest.raises(errors.CheckViolation):
        db.execute(f"update public.{table} set {column} = %s where id = %s", (value, row_id))


@pytest.mark.parametrize("table", TABLES)
def test_provenance_table_grants_are_unchanged(db, table):
    db.execute("select grantee, string_agg(privilege_type, ',' order by privilege_type) as p "
               "from information_schema.role_table_grants where table_schema = 'public' and table_name = %s "
               "and grantee in ('anon', 'authenticated', 'service_role') group by grantee", (table,))
    grants = {r["grantee"]: r["p"] for r in db.fetchall()}
    full = "DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE"
    assert grants == {"anon": full, "authenticated": full, "service_role": full}
