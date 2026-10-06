"""SQL tests for migration 0011_frame_quality.sql (issue #40): the nullable
imageTable.quality and thumbnail_path columns next to rows written before
them, their shape checks (a thumbnail is stored as a path, never a URL),
image_quality_status agreeing with the backend's reading, and access.

Same rules as test_sql_rpcs.py: local database only, every test rolled
back, skipped when no local database answers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import numpy as np
import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from koi.camera import quality  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND = 9401
INSERT = 'insert into public."imageTable" ("user_ID", green_ratio, current_state, "imageURL"'


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
        cur.execute("""
            select to_regprocedure('public.image_quality_status(jsonb)') is not null
                   and exists (select 1 from information_schema.columns
                               where table_schema = 'public' and table_name = 'imageTable'
                                 and column_name in ('gcc', 'colour')
                               having count(distinct column_name) = 2)
                   and public.image_quality_status('{"version":2,"status":"pass"}'::jsonb) = 'pass'
                   as ok
        """)
        row = cur.fetchone()
    c.rollback()
    if not row["ok"]:
        c.close()
        pytest.skip("local database is not at migration 0021; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        yield cur
    conn.rollback()


def _as_role(db: Any, role: str, sql: str, params: tuple = ()) -> list:
    with db.connection.transaction():
        db.execute(f"set local role {role}")
        db.execute(sql, params)
        rows = db.fetchall() if db.description else []
        db.execute("reset role")
    return rows


def _measured() -> dict:
    rng = np.random.default_rng(3)
    img = np.clip(rng.normal(120, 20, (120, 160, 3)), 0, 255).astype(np.uint8)
    return quality.assess(img)


def test_quality_sql_old_rows_read_as_unknown(db):
    """A row inserted the pre-0011 way keeps loading, with no result and no
    thumbnail, and reads as unknown rather than passed."""
    db.execute(INSERT + ") values (%s, 0.2, '[\"base\", 0.2]', 'old') "
               "returning quality, thumbnail_path, public.image_quality_status(quality) as status", (POND,))
    assert db.fetchone() == {"quality": None, "thumbnail_path": None, "status": "unknown"}


def test_quality_sql_stores_the_backend_result_and_thumbnail_path(db):
    result = _measured()
    db.execute(INSERT + ", quality, thumbnail_path) values (%s, 0.1, '[\"base\", 0.1, 0, 0]', 'new', %s, %s) "
               "returning quality, thumbnail_path, public.image_quality_status(quality) as status",
               (POND, Jsonb(result), "9401/1700000000_photo_thumb.jpg"))
    row = db.fetchone()
    assert row["quality"] == result and row["status"] == result["status"] == "pass"
    assert row["thumbnail_path"] == "9401/1700000000_photo_thumb.jpg"


@pytest.mark.parametrize("value", [
    None,
    {},
    {"version": 2, "status": "pass"},
    {"version": 2, "status": "fail", "reasons": ["colour_cast"]},
    {"version": 3, "status": "pass"},
    {"version": 1, "status": "unknown"},
    {"version": 1, "status": "pass"},
    {"version": 1, "status": "fail", "reasons": ["blurred"]},
])
def test_quality_sql_status_matches_the_backend(db, value):
    db.execute("select public.image_quality_status(%s) as status", (Jsonb(value) if value is not None else None,))
    assert db.fetchone()["status"] == quality.status_of({"quality": value})


@pytest.mark.parametrize("value", [
    "pass",
    [1, "pass"],
    {"status": "pass"},
    {"version": "1", "status": "pass"},
    {"version": 1, "status": "ok"},
])
def test_quality_sql_refuses_a_malformed_result(db, value):
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        db.execute(INSERT + ", quality) values (%s, 0.1, 'base', 'x', %s)", (POND, Jsonb(value)))


@pytest.mark.parametrize("path", [
    "https://example.supabase.co/storage/v1/object/public/imageAnalysisBucket/15/1_photo_thumb.jpg",
    "/15/1_photo_thumb.jpg",
    "15/1_photo_thumb.jpg?token=abc",
    "",
])
def test_thumbnail_sql_refuses_a_url_instead_of_a_path(db, path):
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        db.execute(INSERT + ", thumbnail_path) values (%s, 0.1, 'base', 'x', %s)", (POND, path))


def test_quality_sql_image_table_grants_are_unchanged(db):
    """The app reads imageTable directly; the new columns do not change who can."""
    rows = _as_role(db, "anon", 'select id, quality, thumbnail_path from public."imageTable" limit 1')
    assert isinstance(rows, list)


def test_quality_sql_status_function_is_backend_only(db):
    for role in ("anon", "authenticated"):
        db.execute("select has_function_privilege(%s, 'public.image_quality_status(jsonb)', 'execute') as x",
                   (role,))
        assert db.fetchone()["x"] is False, role
    assert _as_role(db, "service_role", "select public.image_quality_status(null) as s") == [{"s": "unknown"}]


def test_quality_sql_adds_no_index(db):
    """Reads stay on idx_imagetable_user_time; 0011 adds no index."""
    db.execute("select indexname from pg_indexes where schemaname = 'public' and tablename = 'imageTable' "
               "order by indexname")
    assert [r["indexname"] for r in db.fetchall()] == ["idx_imagetable_user_time", "imageTable_pkey"]


def test_colour_sql_legacy_insert_has_null_observations(db):
    db.execute(INSERT + ") values (%s, 0.2, 'base', 'old') returning gcc, colour", (POND,))
    assert db.fetchone() == {"gcc": None, "colour": None}


def test_colour_sql_stores_valid_backend_metrics(db):
    img = np.full((12, 12, 3), 120, dtype=np.uint8)
    gcc, colour = quality.colour_metrics(img)
    result = quality.assess(img)
    db.execute(INSERT + ", gcc, colour, quality) values (%s, 0.1, 'base', 'new', %s, %s, %s) "
               "returning gcc, colour, public.image_quality_status(quality) as status",
               (POND, gcc, Jsonb(colour), Jsonb(result)))
    row = db.fetchone()
    assert row["gcc"] == pytest.approx(1 / 3)
    assert row["colour"] == colour and row["status"] == "pass"
    rows = _as_role(db, "anon", 'select gcc, colour from public."imageTable" where "user_ID" = %s', (POND,))
    assert any(r["colour"] == colour for r in rows)
