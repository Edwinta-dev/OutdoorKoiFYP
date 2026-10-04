"""SQL tests for migration 0014: sensor_ingest_pending,
save_pond_snapshot_with_ingest and the two backend-only tables, through
SupabaseStorage's RPC calls run against the local database (issue #18).

Covers equal-time channels, partial uploads, two ponds, no new rows, a
lower id committed after a higher one (two real connections), and a
worker that stops between discovery and save. Each case except the
commit-order one runs in a rolled-back transaction; that one commits its
rows on a dedicated pond and deletes them afterwards.

Local Supabase stack only; skipped when no local database answers, as in
CI.
"""
from __future__ import annotations

import json
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
from koi.models.engine import PondConfig  # noqa: E402
from koi.models.pond_twin import PondTwin  # noqa: E402
from koi.models.sensor_inputs import SENSOR_TYPES, complete_groups, group_rows  # noqa: E402
from koi.storage.supabase_storage import SupabaseStorage  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND_A, POND_B, POND_COMMIT = 9181, 9182, 9189
T0 = datetime(2026, 10, 4, tzinfo=timezone.utc)
OVERLAP = 3600


def _connect(autocommit: bool = False) -> Any:
    host = urlparse(DB_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail(f"KOI_TEST_DB_URL must point at a local database, not {host}")
    try:
        return psycopg.connect(DB_URL, connect_timeout=2, row_factory=dict_row, autocommit=autocommit)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no local database at {DB_URL} ({exc.__class__.__name__}); run `supabase start`")


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    c = _connect()
    with c.cursor() as cur:
        cur.execute("select to_regclass('public.sensor_ingest_ledger') is not null as ok")
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
        "sensor_ingest_pending":
            "select public.sensor_ingest_pending(%(p_pond_id)s, %(p_sensor_types)s, %(p_start)s::timestamptz, "
            "%(p_until)s::timestamptz, %(p_overlap_seconds)s, %(p_limit)s) as r",
        "save_pond_snapshot_with_ingest":
            "select public.save_pond_snapshot_with_ingest(%(p_user_id)s, %(p_snapshot)s, %(p_base_version)s, "
            "%(p_ingest)s) as r",
        "save_pond_snapshot":
            "select public.save_pond_snapshot(%(p_user_id)s, %(p_snapshot)s, %(p_base_version)s) as r",
    }

    def __init__(self, cur: Any, name: str, params: dict):
        self.cur, self.name, self.params = cur, name, params

    def execute(self) -> Any:
        params = {k: Jsonb(v) if k in ("p_snapshot", "p_ingest") else v for k, v in self.params.items()}
        self.cur.execute(self.SQL[self.name], params)
        return type("Result", (), {"data": self.cur.fetchone()["r"]})()


class _LocalClient:
    """The supabase-py RPC calls of sensor ingestion, run through a local
    connection."""

    def __init__(self, cur: Any):
        self.cur = cur

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self.cur, name, params)


def _storage(cur: Any) -> SupabaseStorage:
    return SupabaseStorage(make_settings(storage="supabase"), client=_LocalClient(cur))  # type: ignore[arg-type]


def _pond(db: Any, pond: int) -> None:
    # No coordinates or postal code, so no station lookup runs.
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 5000, 8.0)', (pond,))


def _upload(db: Any, pond: int, at: datetime, reading: dict) -> list[int]:
    """One node upload: a single insert of one row per sensor_type, all at
    the time `at` (the database's now() for that insert)."""
    ids = []
    for sensor_type, value in reading.items():
        db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") '
                   "values (%s, %s, %s, %s) returning id", (at, sensor_type, value, pond))
        ids.append(db.fetchone()["id"])
    return ids


def _pending(storage: SupabaseStorage, pond: int, until: datetime | None = None, start: datetime | None = None,
             limit: int = 2000) -> dict:
    return storage.fetch_pending_sensor_rows(pond, SENSOR_TYPES, start=start, until=until,
                                             overlap_seconds=OVERLAP, limit=limit)


def _ledger(db: Any, pond: int) -> list[tuple]:
    db.execute("select sensor_row_id, disposition, time_basis, snapshot_version from public.sensor_ingest_ledger "
               "where pond_id = %s order by sensor_row_id", (pond,))
    return [(r["sensor_row_id"], r["disposition"], r["time_basis"], r["snapshot_version"]) for r in db.fetchall()]


def _snapshot(db: Any, pond: int) -> tuple[dict, int] | None:
    db.execute("select snapshot, snapshot_version from public.pond_chemistry_state where user_id = %s", (pond,))
    row = db.fetchone()
    return (row["snapshot"], row["snapshot_version"]) if row else None


def _cycle(db: Any, pond: int, now: datetime, crash: bool = False) -> int:
    """One poll's sensor half, as poller._poll_user and registry.with_twin
    run it: load the stored twin, discover, apply, save with the ingest.
    crash=True stops after applying, before the save. Returns the number
    of inputs applied."""
    storage = _storage(db)
    stored = _snapshot(db, pond)
    twin = (PondTwin.from_snapshot(stored[0]) if stored
            else PondTwin.create(PondConfig(volume_litres=5000.0, estimated_biomass_grams=8000.0)))
    found = _pending(storage, pond, until=now, start=twin.last_input_at)
    discovery = group_rows(complete_groups(found["rows"], 2000))
    twin.ingest_sensor_inputs(discovery.inputs)
    if crash:
        return len(discovery.inputs)
    commit = discovery.commit()
    if commit is not None:
        storage.save_engine_snapshot(pond, json.loads(json.dumps(twin.to_snapshot())),
                                     base_version=stored[1] if stored else 0, ingest=commit)
    return len(discovery.inputs)


# ---------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------
def test_tables_and_functions_are_closed_to_the_app_roles(db):
    for table in ("sensor_ingest_cursor", "sensor_ingest_ledger"):
        db.execute("select relrowsecurity as rls from pg_class where oid = %s::regclass", (f"public.{table}",))
        assert db.fetchone()["rls"] is True, table
        db.execute("select count(*) as n from pg_policies where tablename = %s", (table,))
        assert db.fetchone()["n"] == 0
        for role in ("anon", "authenticated"):
            for privilege in ("select", "insert", "update", "delete"):
                db.execute("select has_table_privilege(%s, %s, %s) as ok", (role, f"public.{table}", privilege))
                assert db.fetchone()["ok"] is False, (role, table, privilege)
        db.execute("select has_table_privilege('service_role', %s, 'insert') as ok", (f"public.{table}",))
        assert db.fetchone()["ok"] is True
    for function in ("public.sensor_ingest_pending(bigint, text[], timestamptz, timestamptz, integer, integer)",
                     "public.save_pond_snapshot_with_ingest(bigint, jsonb, bigint, jsonb)"):
        for role, allowed in (("anon", False), ("authenticated", False), ("service_role", True)):
            db.execute("select has_function_privilege(%s, %s, 'execute') as ok", (role, function))
            assert db.fetchone()["ok"] is allowed, (role, function)


def test_sensordata_is_left_as_it_was(db):
    db.execute("select has_table_privilege('anon', 'public.\"SensorData\"', 'insert') as ok")
    assert db.fetchone()["ok"] is True, "the sensor sketch still inserts with the publishable key"
    db.execute("select string_agg(column_name, ',' order by ordinal_position) as cols from information_schema.columns "
               "where table_schema = 'public' and table_name = 'SensorData'")
    assert db.fetchone()["cols"] == "id,created_at,sensor_type,data1,userID"
    db.execute("select count(*) as n from pg_trigger where tgrelid = 'public.\"SensorData\"'::regclass "
               "and not tgisinternal")
    assert db.fetchone()["n"] == 0


# ---------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------
def test_equal_time_channels_come_back_as_one_input(db):
    _pond(db, POND_A)
    ids = _upload(db, POND_A, T0, {"temp": 29.0, "TDS": 220.0, "pH": 7.6, "LUX": 20000.0})
    found = _pending(_storage(db), POND_A)
    assert found["cursor"] is None and found["scan_from"] is None
    assert [r["id"] for r in found["rows"]] == ids
    [only] = group_rows(found["rows"]).inputs
    assert only.channels == pytest.approx({"temp": 29.0, "tds": 220.0, "ph": 7.6, "lux": 20000.0})
    assert only.time == T0 and only.time_basis == "ingestion"


def test_partial_uploads_unknown_types_and_two_ponds(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    a1 = _upload(db, POND_A, T0, {"TDS": 220.0, "pH": 7.6, "LUX": -1.0})
    b1 = _upload(db, POND_B, T0, {"temp": 28.0, "pH": 8.0, "humidity": 80.0})
    a2 = _upload(db, POND_A, T0 + timedelta(minutes=15), {"temp": 29.0})
    found_a = _pending(_storage(db), POND_A)
    found_b = _pending(_storage(db), POND_B)
    assert [r["id"] for r in found_a["rows"]] == a1 + a2
    assert [r["id"] for r in found_b["rows"]] == b1[:2], "a type the model does not read is not returned"
    first, second = group_rows(found_a["rows"]).inputs
    assert first.missing == ["temp"] and second.missing == ["ph", "tds", "lux"]
    assert group_rows(found_b["rows"]).inputs[0].missing == ["tds", "lux"]


def test_until_and_start_bound_the_scan(db):
    _pond(db, POND_A)
    early = _upload(db, POND_A, T0, {"pH": 7.6})
    late = _upload(db, POND_A, T0 + timedelta(minutes=30), {"pH": 7.7})
    storage = _storage(db)
    assert [r["id"] for r in _pending(storage, POND_A, until=T0 + timedelta(minutes=10))["rows"]] == early
    assert [r["id"] for r in _pending(storage, POND_A, start=T0 + timedelta(minutes=10))["rows"]] == late
    assert [r["id"] for r in _pending(storage, POND_A, limit=1)["rows"]] == early


# ---------------------------------------------------------------------
# Saving the ingest with the snapshot
# ---------------------------------------------------------------------
def test_save_records_the_ledger_and_cursor_and_no_new_rows_means_nothing(db):
    _pond(db, POND_A)
    ids = _upload(db, POND_A, T0, {"temp": 29.0, "pH": 7.6})
    assert _cycle(db, POND_A, T0 + timedelta(minutes=1)) == 1
    assert _ledger(db, POND_A) == [(i, "applied", "ingestion", 1) for i in ids]
    db.execute("select watermark from public.sensor_ingest_cursor where pond_id = %s", (POND_A,))
    assert db.fetchone()["watermark"] == T0
    found = _pending(_storage(db), POND_A)
    assert found["rows"] == [] and found["cursor"] is not None
    assert found["scan_from"] is not None
    assert _cycle(db, POND_A, T0 + timedelta(minutes=2)) == 0
    assert _snapshot(db, POND_A)[1] == 1, "no new rows: nothing saved"


def test_stale_save_writes_nothing(db):
    _pond(db, POND_A)
    _upload(db, POND_A, T0, {"pH": 7.6})
    _cycle(db, POND_A, T0 + timedelta(minutes=1))
    ingest = {"rows": [], "watermark": (T0 + timedelta(hours=5)).isoformat()}
    db.execute("select public.save_pond_snapshot_with_ingest(%s, %s, 7, %s) as v",
               (POND_A, Jsonb({"version": 3}), Jsonb(ingest)))
    assert db.fetchone()["v"] is None
    db.execute("select watermark from public.sensor_ingest_cursor where pond_id = %s", (POND_A,))
    assert db.fetchone()["watermark"] == T0


def test_a_row_already_in_the_ledger_rolls_the_save_back(db):
    _pond(db, POND_A)
    [row_id] = _upload(db, POND_A, T0, {"pH": 7.6})
    _cycle(db, POND_A, T0 + timedelta(minutes=1))
    entry = {"sensor_row_id": row_id, "sensor_type": "pH", "effective_sample_time": T0.isoformat(),
             "time_basis": "ingestion", "disposition": "applied"}
    db.execute("savepoint dup")
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("select public.save_pond_snapshot_with_ingest(%s, %s, 1, %s)",
                   (POND_A, Jsonb({"version": 3, "x": 1}), Jsonb({"rows": [entry], "watermark": None})))
    db.execute("rollback to savepoint dup")
    assert _snapshot(db, POND_A)[1] == 1


def test_the_watermark_never_moves_back(db):
    _pond(db, POND_A)
    for version, mark in ((0, T0 + timedelta(hours=1)), (1, T0)):
        db.execute("select public.save_pond_snapshot_with_ingest(%s, %s, %s, %s) as v",
                   (POND_A, Jsonb({"version": 3}), version, Jsonb({"rows": [], "watermark": mark.isoformat()})))
        assert db.fetchone()["v"] == version + 1
    db.execute("select watermark from public.sensor_ingest_cursor where pond_id = %s", (POND_A,))
    assert db.fetchone()["watermark"] == T0 + timedelta(hours=1)


def test_a_crash_before_the_save_replays_the_same_input_as_an_in_order_run(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    uploads = [(0, {"temp": 29.0, "TDS": 220.0, "pH": 7.6, "LUX": 20000.0}),
               (15, {"temp": 29.5, "TDS": 221.0, "pH": 7.7, "LUX": 18000.0}),
               (30, {"TDS": 225.0, "pH": 7.8, "LUX": 100.0})]
    # A: polled after each upload. B: the poll after the second upload
    # stops before saving, and a restarted worker polls once at the end.
    for minute, reading in uploads:
        _upload(db, POND_A, T0 + timedelta(minutes=minute), reading)
        _cycle(db, POND_A, T0 + timedelta(minutes=minute + 1))
    _upload(db, POND_B, T0, uploads[0][1])
    _cycle(db, POND_B, T0 + timedelta(minutes=1))
    _upload(db, POND_B, T0 + timedelta(minutes=15), uploads[1][1])
    assert _cycle(db, POND_B, T0 + timedelta(minutes=16), crash=True) == 1
    _upload(db, POND_B, T0 + timedelta(minutes=30), uploads[2][1])
    assert _cycle(db, POND_B, T0 + timedelta(minutes=31)) == 2

    a, b = _snapshot(db, POND_A)[0], _snapshot(db, POND_B)[0]
    assert a["chemistry"] == b["chemistry"]
    # The ponds' rows have different ids; everything else matches.
    def readings(snapshot: dict) -> dict:
        channels = snapshot["sensor_inputs"]["channels"]
        return {c: {k: v for k, v in s.items() if k != "row_id"} for c, s in channels.items()}
    assert readings(a) == readings(b)
    assert a["sensor_inputs"]["last_input_at"] == b["sensor_inputs"]["last_input_at"]
    assert [r[1:3] for r in _ledger(db, POND_A)] == [r[1:3] for r in _ledger(db, POND_B)]
    assert len(_ledger(db, POND_B)) == 11


# ---------------------------------------------------------------------
# Commit order: two real transactions
# ---------------------------------------------------------------------
def _cleanup(pond: int) -> None:
    with _connect(autocommit=True) as c, c.cursor() as cur:
        cur.execute("delete from public.sensor_ingest_ledger where pond_id = %s", (pond,))
        cur.execute("delete from public.sensor_ingest_cursor where pond_id = %s", (pond,))
        cur.execute("delete from public.pond_chemistry_state where user_id = %s", (pond,))
        cur.execute('delete from public."SensorData" where "userID" = %s', (pond,))
        cur.execute("delete from public.pond_profile where pond_id = %s", (pond,))
        cur.execute('delete from public."UserData" where "userID" = %s', (pond,))


def test_a_lower_id_committed_after_a_higher_one_is_found(conn):
    """Upload A takes its ids and created_at first but commits after
    upload B. The worker polls between the two commits and sees only B.
    An id cursor would then start above every id of A; the overlap scan
    finds A on the next cycle, once."""
    _cleanup(POND_COMMIT)
    worker = _connect(autocommit=True)
    first = _connect()
    second = _connect()
    try:
        with worker.cursor() as w:
            _pond(w, POND_COMMIT)
        with first.cursor() as a, second.cursor() as b, worker.cursor() as w:
            ids_a = [_insert_now(a, "pH", 7.5), _insert_now(a, "temp", 28.5)]
            ids_b = [_insert_now(b, "pH", 7.6), _insert_now(b, "temp", 29.0)]
            second.commit()
            assert max(ids_a) < min(ids_b)
            storage = _storage(w)
            seen = _pending(storage, POND_COMMIT)
            assert [r["id"] for r in seen["rows"]] == ids_b, "A is not committed yet"
            assert _cycle(w, POND_COMMIT, datetime.now(timezone.utc)) == 1

            first.commit()
            found = _pending(storage, POND_COMMIT)
            assert [r["id"] for r in found["rows"]] == ids_a
            assert all(r["id"] < max(ids_b) for r in found["rows"]), "below an id cursor at B"
            assert _cycle(w, POND_COMMIT, datetime.now(timezone.utc)) == 1
            assert _pending(storage, POND_COMMIT)["rows"] == []
            assert sorted(r[0] for r in _ledger(w, POND_COMMIT)) == sorted(ids_a + ids_b)
            assert {r[3] for r in _ledger(w, POND_COMMIT) if r[0] in ids_a} == {2}
            twin = PondTwin.from_snapshot(_snapshot(w, POND_COMMIT)[0])
            assert twin.sensor_channels["ph"]["value"] == pytest.approx(7.6), "the newer reading stays newest"
    finally:
        first.rollback()
        second.rollback()
        for c in (worker, first, second):
            c.close()
        _cleanup(POND_COMMIT)


def _insert_now(cur: Any, sensor_type: str, value: float) -> int:
    cur.execute('insert into public."SensorData" (sensor_type, data1, "userID") values (%s, %s, %s) returning id',
                (sensor_type, value, POND_COMMIT))
    return cur.fetchone()["id"]
