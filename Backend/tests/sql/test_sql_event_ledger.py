"""SQL tests for migration 0015 (issue #19): pondInterventions.event_id,
its deterministic backfill, pond_interventions_since,
sensor_ingest_history and the event_id key of get_historical_graph_payload,
through SupabaseStorage's RPC calls run against the local database.

Each case runs in a rolled-back transaction. Local Supabase stack only;
skipped when no local database answers, as in CI.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from conftest import make_settings  # noqa: E402
from koi.models.event_ledger import derived_event_id, event_from_row  # noqa: E402
from koi.models.sensor_inputs import SENSOR_TYPES  # noqa: E402
from koi.storage.supabase_storage import SupabaseStorage  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND_A, POND_B = 9191, 9192
T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
FUNCTIONS = ("public.intervention_event_id(bigint)", "public.backfill_intervention_event_ids(bigint)",
             "public.pond_interventions_since(bigint, timestamptz)",
             "public.sensor_ingest_history(bigint, text[], timestamptz, timestamptz)")


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
        cur.execute("select to_regprocedure('public.pond_interventions_since(bigint, timestamptz)') is not null as ok")
        row = cur.fetchone()
    c.rollback()
    if not row["ok"]:
        c.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        yield cur
    conn.rollback()


class _Rpc:
    SQL = {
        "pond_interventions_since":
            "select public.pond_interventions_since(%(p_pond_id)s, %(p_since)s::timestamptz) as r",
        "backfill_intervention_event_ids": "select public.backfill_intervention_event_ids(%(p_pond_id)s) as r",
        "sensor_ingest_history":
            "select public.sensor_ingest_history(%(p_pond_id)s, %(p_sensor_types)s, %(p_after)s::timestamptz, "
            "%(p_until)s::timestamptz) as r",
        "save_pond_snapshot_with_ingest":
            "select public.save_pond_snapshot_with_ingest(%(p_user_id)s, %(p_snapshot)s, %(p_base_version)s, "
            "%(p_ingest)s) as r",
    }

    def __init__(self, cur: Any, name: str, params: dict):
        self.cur, self.name, self.params = cur, name, params

    def execute(self) -> Any:
        params = {k: Jsonb(v) if k in ("p_snapshot", "p_ingest") else v for k, v in self.params.items()}
        self.cur.execute(self.SQL[self.name], params)
        return type("Result", (), {"data": self.cur.fetchone()["r"]})()


class _LocalClient:
    def __init__(self, cur: Any):
        self.cur = cur

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self.cur, name, params)


def _storage(cur: Any) -> SupabaseStorage:
    return SupabaseStorage(make_settings(storage="supabase"), client=_LocalClient(cur))  # type: ignore[arg-type]


def _log(db: Any, pond: int, at: datetime, *, event_id: Any = "default", grams: float = 50.0,
         event_type: str = "FEEDING") -> dict:
    """An app insert. event_id "default" leaves the column out; None sends
    an explicit null, as a row from before 0015 had."""
    if event_id == "default":
        db.execute('insert into public."pondInterventions" ("userID", event_type, food_grams, protein_percentage, '
                   "event_timestamp) values (%s, %s, %s, 40, %s) returning *", (pond, event_type, grams, at))
    else:
        db.execute('insert into public."pondInterventions" ("userID", event_type, food_grams, protein_percentage, '
                   "event_timestamp, event_id) values (%s, %s, %s, 40, %s, %s) returning *",
                   (pond, event_type, grams, at, event_id))
    return db.fetchone()


def _event_ids(db: Any, ids: list[int]) -> list[Any]:
    db.execute('select event_id from public."pondInterventions" where id = any(%s) order by id', (ids,))
    return [r["event_id"] and str(r["event_id"]) for r in db.fetchall()]


# ---------------------------------------------------------------------
# Schema and access
# ---------------------------------------------------------------------
def test_sql_ledger_event_id_column_is_nullable_unique_and_defaulted(db):
    db.execute("select data_type, is_nullable, column_default from information_schema.columns "
               "where table_schema = 'public' and table_name = 'pondInterventions' and column_name = 'event_id'")
    col = db.fetchone()
    assert (col["data_type"], col["is_nullable"]) == ("uuid", "YES")
    assert "gen_random_uuid()" in col["column_default"]
    db.execute("select indexdef from pg_indexes where indexname = 'pondinterventions_event_id_key'")
    assert "UNIQUE" in db.fetchone()["indexdef"]


def test_sql_ledger_functions_are_backend_only_and_the_app_insert_is_unchanged(db):
    for function in FUNCTIONS:
        for role, allowed in (("anon", False), ("authenticated", False), ("service_role", True)):
            db.execute("select has_function_privilege(%s, %s, 'execute') as ok", (role, function))
            assert db.fetchone()["ok"] is allowed, (role, function)
    for role in ("anon", "authenticated"):
        db.execute("select has_table_privilege(%s, 'public.\"pondInterventions\"', 'insert') as ok", (role,))
        assert db.fetchone()["ok"] is True, "the app still logs events with its current payload"


def test_sql_ledger_derived_event_id_matches_the_backend(db):
    db.execute("select public.intervention_event_id(1)::text as a, public.intervention_event_id(987654)::text as b")
    row = db.fetchone()
    assert (row["a"], row["b"]) == (derived_event_id(1), derived_event_id(987654))


# ---------------------------------------------------------------------
# New and old rows
# ---------------------------------------------------------------------
def test_sql_ledger_new_rows_get_a_uuid_and_an_id_cannot_be_reused(db):
    row = _log(db, POND_A, T0)
    assert row["event_id"] is not None and str(row["event_id"]) != derived_event_id(row["id"])
    assert isinstance(row["id"], int), "the integer row id stays separate from the event id"
    mine = _log(db, POND_A, T0, event_id="3f2b8c1e-7d4a-4e5b-9c6d-0a1b2c3d4e5f")
    assert str(mine["event_id"]) == "3f2b8c1e-7d4a-4e5b-9c6d-0a1b2c3d4e5f"
    db.execute("savepoint dup")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _log(db, POND_B, T0, event_id="3f2b8c1e-7d4a-4e5b-9c6d-0a1b2c3d4e5f")
    db.execute("rollback to savepoint dup")


def test_sql_ledger_backfill_is_deterministic_and_resumes_a_partial_run(db):
    rows = [_log(db, POND_A, T0 + timedelta(hours=h), event_id=None) for h in range(3)]
    other = _log(db, POND_B, T0, event_id=None)
    ids = [r["id"] for r in rows]
    # A first run that stopped after one row.
    db.execute('update public."pondInterventions" set event_id = public.intervention_event_id(id) where id = %s',
               (ids[0],))
    storage = _storage(db)
    assert storage.backfill_intervention_event_ids(POND_A) == 2
    assert _event_ids(db, ids) == [derived_event_id(i) for i in ids]
    assert storage.backfill_intervention_event_ids(POND_A) == 0
    assert _event_ids(db, ids) == [derived_event_id(i) for i in ids]
    assert _event_ids(db, [other["id"]]) == [None], "only the given pond's rows"


def test_sql_ledger_interventions_since_is_scoped_to_the_pond(db):
    a1 = _log(db, POND_A, T0)
    a2 = _log(db, POND_A, T0 + timedelta(hours=5), grams=10.0)
    _log(db, POND_A, T0 - timedelta(days=40))
    _log(db, POND_B, T0 + timedelta(hours=1))
    rows = _storage(db).fetch_interventions(POND_A, since=T0 - timedelta(days=30))
    assert [r["id"] for r in rows] == [a1["id"], a2["id"]]
    assert set(rows[0]) == {"id", "event_id", "event_type", "event_timestamp", "volume_percentage", "volume_litres",
                            "food_grams", "protein_percentage", "algae_method", "created_at", "salt_grams", "notes"}
    assert rows[1]["event_id"] == str(a2["event_id"])
    event = event_from_row(rows[1])
    assert event is not None and event.food_grams == 10.0 and event.time == T0 + timedelta(hours=5)
    assert len(_storage(db).fetch_interventions(POND_A, since=None)) == 3


# ---------------------------------------------------------------------
# Replay reads
# ---------------------------------------------------------------------
def _upload(db: Any, pond: int, at: datetime) -> list[int]:
    ids = []
    for sensor_type, value in (("temp", 28.0), ("pH", 7.5)):
        db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") '
                   "values (%s, %s, %s, %s) returning id", (at, sensor_type, value, pond))
        ids.append(db.fetchone()["id"])
    return ids


def test_sql_ledger_sensor_history_returns_only_ledgered_rows_in_the_window(db):
    for pond in (POND_A, POND_B):
        db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 5000, 8.0)', (pond,))
    applied = [_upload(db, POND_A, T0 + timedelta(minutes=15 * n)) for n in range(4)]
    pending = _upload(db, POND_A, T0 + timedelta(minutes=60))  # not yet ingested
    _upload(db, POND_B, T0 + timedelta(minutes=30))
    ledger = [{"sensor_row_id": i, "sensor_type": t, "time_basis": "ingestion", "disposition": "applied",
               "effective_sample_time": (T0 + timedelta(minutes=15 * n)).isoformat()}
              for n, ids in enumerate(applied) for i, t in zip(ids, ("temp", "pH"), strict=True)]
    storage = _storage(db)
    storage.save_engine_snapshot(POND_A, {"version": 4}, base_version=0, ingest={"rows": ledger, "watermark": None})
    rows = storage.fetch_ingested_sensor_rows(POND_A, SENSOR_TYPES, T0, T0 + timedelta(minutes=45))
    assert [r["id"] for r in rows] == applied[1] + applied[2] + applied[3]
    assert set(rows[0]) == {"id", "sensor_type", "value", "created_at"}
    everything = storage.fetch_ingested_sensor_rows(POND_A, SENSOR_TYPES, None, T0 + timedelta(days=1))
    assert [r["id"] for r in everything] == [i for ids in applied for i in ids]
    assert not set(pending) & {r["id"] for r in everything}
    assert storage.fetch_ingested_sensor_rows(POND_B, SENSOR_TYPES, None, T0 + timedelta(days=1)) == []


# ---------------------------------------------------------------------
# History payload
# ---------------------------------------------------------------------
def test_sql_ledger_historical_payload_adds_event_id_and_keeps_the_old_keys(db):
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 5000, 8.0)', (POND_A,))
    row = _log(db, POND_A, datetime.now(timezone.utc) - timedelta(days=1), event_type="WATER_CHANGE")
    db.execute("select public.get_historical_graph_payload(%s, %s) as p", (POND_A, 7))
    [item] = db.fetchone()["p"]["interventions"]
    assert set(item) == {"id", "event_id", "event_type", "timestamp", "pct", "litres", "food_grams",
                         "is_major_reset", "salt_grams", "notes"}
    assert item["id"] == row["id"] and item["event_id"] == str(row["event_id"])
