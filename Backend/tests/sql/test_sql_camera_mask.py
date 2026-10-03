"""SQL tests for migration 0010_camera_mask.sql (issue #39): the
camera_config and camera_mask_version tables, polygon validation in the
database, save_camera_mask's version numbering (also under concurrent
saves), immutable mask versions, access rules, and imageTable's new
nullable columns next to rows written before them.

Same rules as test_sql_rpcs.py: local database only, every test rolled
back, skipped when no local database answers. The one exception is the
concurrent-save test, which must commit to be seen by a second
connection; it uses its own synthetic pond and deletes it afterwards.
"""
from __future__ import annotations

import math
import os
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND, OTHER, RACE_POND = 9391, 9392, 9393
RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
TOP_HALF = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]


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
        cur.execute("select to_regclass('public.camera_config') is not null as ok")
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
        cur.execute("select 1")
        _pond(cur, POND)
        _pond(cur, OTHER)
        yield cur
    conn.rollback()


def _pond(db: Any, user_id: int) -> None:
    # No coordinates or postal code, so the station-lookup triggers do nothing.
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 1000, 1)', (user_id,))


def _save(db: Any, pond: int, mask: Any) -> dict:
    db.execute("select * from public.save_camera_mask(%s, %s)", (pond, Jsonb(mask)))
    return db.fetchone()


def _as_role(db: Any, role: str, sql: str, params: tuple = ()) -> list:
    with db.connection.transaction():
        db.execute(f"set local role {role}")
        db.execute(sql, params)
        rows = db.fetchall() if db.description else []
        db.execute("reset role")
    return rows


def _circle(points: int) -> list:
    return [[round(0.5 + 0.4 * math.cos(2 * math.pi * i / points), 6),
             round(0.5 + 0.4 * math.sin(2 * math.pi * i / points), 6)] for i in range(points)]


# --- validation ---------------------------------------------------------------

@pytest.mark.parametrize("mask, valid", [
    (RIGHT_HALF, True),
    ([[0, 0], [1, 0], [1, 1]], True),
    (_circle(64), True),
    (_circle(65), False),
    ([[0, 0], [1, 0]], False),
    ([[0, 0], [1.5, 0], [1, 1]], False),
    ([[0, -0.1], [1, 0], [1, 1]], False),
    ([[0, 0], [1, 0], ["1", 1]], False),
    ([[0, 0], [1, 0], [1]], False),
    ([[0, 0], [0.05, 0], [0.05, 0.05]], False),  # area 0.00125
    ({"points": RIGHT_HALF}, False),
    (None, False),
])
def test_mask_sql_validity_matches_the_backend_rules(db, mask, valid):
    from koi.camera import mask as camera_mask

    db.execute("select public.camera_mask_is_valid(%s) as ok", (Jsonb(mask) if mask is not None else None,))
    assert db.fetchone()["ok"] is valid
    try:
        camera_mask.validate_polygon(mask)
        backend_valid = True
    except ValueError:
        backend_valid = False
    assert backend_valid is valid


def test_mask_sql_save_rejects_an_invalid_polygon_and_an_unknown_pond(db):
    with pytest.raises(errors.InvalidParameterValue), db.connection.transaction():
        _save(db, POND, [[0, 0], [1, 0]])
    with pytest.raises(errors.ForeignKeyViolation), db.connection.transaction():
        _save(db, 9399, RIGHT_HALF)
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        db.execute("insert into public.camera_mask_version (pond_id, mask_version, mask) values (%s, 1, %s)",
                   (POND, Jsonb([[0, 0], [2, 0], [1, 1]])))


# --- versions -------------------------------------------------------------------

def test_mask_sql_save_numbers_versions_per_pond_and_keeps_every_version(db):
    first = _save(db, POND, RIGHT_HALF)
    assert first["pond_id"] == POND and first["mask_version"] == 1 and first["mask"] == RIGHT_HALF
    assert _save(db, OTHER, TOP_HALF)["mask_version"] == 1
    second = _save(db, POND, TOP_HALF)
    assert second["mask_version"] == 2 and second["mask"] == TOP_HALF
    db.execute("select pond_id, mask, mask_version from public.camera_config where pond_id = %s", (POND,))
    assert db.fetchall() == [{"pond_id": POND, "mask": TOP_HALF, "mask_version": 2}]
    db.execute("select mask_version, mask from public.camera_mask_version where pond_id = %s order by 1", (POND,))
    assert db.fetchall() == [{"mask_version": 1, "mask": RIGHT_HALF}, {"mask_version": 2, "mask": TOP_HALF}]


def test_mask_sql_saved_versions_are_immutable(db):
    _save(db, POND, RIGHT_HALF)
    with pytest.raises(errors.CheckViolation, match="immutable"), db.connection.transaction():
        db.execute("update public.camera_mask_version set mask = %s where pond_id = %s", (Jsonb(TOP_HALF), POND))
    with pytest.raises(errors.CheckViolation, match="immutable"), db.connection.transaction():
        db.execute("delete from public.camera_mask_version where pond_id = %s", (POND,))
    with pytest.raises(errors.ForeignKeyViolation), db.connection.transaction():
        db.execute("update public.camera_config set mask_version = 5 where pond_id = %s", (POND,))


def test_mask_sql_concurrent_saves_get_distinct_versions(conn):
    """Two connections save the first mask of one pond at the same time:
    the second waits for the first and gets version 2."""
    second_conn = _connect()
    results: dict[str, Any] = {}
    try:
        with conn.cursor() as cur:
            _pond(cur, RACE_POND)
        conn.commit()
        with conn.cursor() as cur:
            assert _save(cur, RACE_POND, RIGHT_HALF)["mask_version"] == 1  # holds the pond's lock, uncommitted

            def other_save() -> None:
                try:
                    with second_conn.cursor() as cur2:
                        results["row"] = _save(cur2, RACE_POND, TOP_HALF)
                    second_conn.commit()
                except Exception as exc:  # noqa: BLE001 - reported below
                    results["error"] = exc

            worker = threading.Thread(target=other_save)
            worker.start()
            worker.join(timeout=1)
            assert worker.is_alive(), "the second save should wait for the first transaction"
        conn.commit()
        worker.join(timeout=10)
        assert "error" not in results, results.get("error")
        assert results["row"]["mask_version"] == 2
        with conn.cursor() as cur:
            cur.execute("select mask_version from public.camera_mask_version where pond_id = %s order by 1",
                        (RACE_POND,))
            assert [r["mask_version"] for r in cur.fetchall()] == [1, 2]
    finally:
        second_conn.close()
        conn.rollback()
        with conn.cursor() as cur:
            # Mask versions refuse deletes; replica mode skips that trigger
            # (and foreign-key checks) for this clean-up only.
            cur.execute("set local session_replication_role = replica")
            cur.execute("delete from public.camera_config where pond_id = %s", (RACE_POND,))
            cur.execute("delete from public.camera_mask_version where pond_id = %s", (RACE_POND,))
            cur.execute('delete from public."UserData" where "userID" = %s', (RACE_POND,))
        conn.commit()


# --- imageTable ------------------------------------------------------------------

def test_mask_sql_image_rows_old_and_new_shapes(db):
    db.execute('insert into public."imageTable" ("user_ID", green_ratio, current_state, "imageURL") '
               "values (%s, 0.2, '[\"base\", 0.2]', 'old') returning mask_version, baseline_reset", (POND,))
    assert db.fetchone() == {"mask_version": None, "baseline_reset": None}
    db.execute('insert into public."imageTable" ("user_ID", green_ratio, current_state, "imageURL", mask_version, '
               "baseline_reset) values (%s, 0.1, '[\"base\", 0.1, 0, 0]', 'new', 1, 'mask_changed') "
               "returning mask_version, baseline_reset", (POND,))
    assert db.fetchone() == {"mask_version": 1, "baseline_reset": "mask_changed"}


def test_mask_sql_image_table_grants_are_unchanged(db):
    """The app reads imageTable directly; the new columns do not change who can."""
    rows = _as_role(db, "anon", 'select id, mask_version from public."imageTable" limit 1')
    assert isinstance(rows, list)


# --- access -------------------------------------------------------------------------

def test_mask_sql_rls_on_and_closed_to_the_app_roles(db):
    for table in ("camera_config", "camera_mask_version"):
        db.execute("select relrowsecurity from pg_class where oid = %s::regclass", (f"public.{table}",))
        assert db.fetchone()["relrowsecurity"] is True
        for role in ("anon", "authenticated"):
            db.execute("select has_table_privilege(%s, %s, 'select') as s, has_table_privilege(%s, %s, 'insert') as i",
                       (role, f"public.{table}", role, f"public.{table}"))
            assert db.fetchone() == {"s": False, "i": False}
        db.execute("select has_table_privilege('service_role', %s, 'select') as s", (f"public.{table}",))
        assert db.fetchone()["s"] is True
    for function in ("public.save_camera_mask(bigint, jsonb)", "public.camera_mask_is_valid(jsonb)",
                     "public.refuse_camera_mask_version_change()"):
        for role in ("anon", "authenticated"):
            db.execute("select has_function_privilege(%s, %s, 'execute') as x", (role, function))
            assert db.fetchone()["x"] is False, (role, function)
    db.execute("select has_function_privilege('service_role', 'public.save_camera_mask(bigint, jsonb)', "
               "'execute') as x")
    assert db.fetchone()["x"] is True


def test_mask_sql_service_role_can_save(db):
    rows = _as_role(db, "service_role", "select mask_version from public.save_camera_mask(%s, %s)",
                    (POND, Jsonb(RIGHT_HALF)))
    assert rows == [{"mask_version": 1}]
    with pytest.raises(errors.InsufficientPrivilege), db.connection.transaction():
        db.execute("set local role authenticated")
        db.execute("select * from public.save_camera_mask(%s, %s)", (POND, Jsonb(TOP_HALF)))
