"""Local SQL checks for migration 0032; never connects to a hosted project."""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND = 9411
VALID = {m: {"cx": 0.5, "cy": 0.5, "scale": 1, "angle": 0} for m in ("white", "nh3", "ph")}


def _connect() -> Any:
    if urlparse(DB_URL).hostname not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail("KOI_TEST_DB_URL must point at a local database")
    try:
        return psycopg.connect(DB_URL, connect_timeout=2, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no local database ({exc.__class__.__name__}); run `supabase start`")


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    c = _connect()
    with c.cursor() as cur:
        cur.execute("select to_regclass('public.camera_card_masks') is not null as ok")
        ok = cur.fetchone()["ok"]
    c.rollback()
    if not ok:
        c.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        cur.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 1000, 1)', (POND,))
        yield cur
    conn.rollback()


def _save(db: Any, placements: dict = VALID, reference_image_id: int | None = None) -> dict:
    db.execute("select * from public.save_camera_card_masks(%s, %s, %s)",
               (POND, Jsonb(placements), reference_image_id))
    return db.fetchone()


def test_card_mask_sql_validation_and_immutable_versions(db):
    assert _save(db)["version"] == 1
    assert _save(db)["version"] == 2
    bad_values = [
        {**VALID, "ph": {**VALID["ph"], "scale": 3.1}},
        {**VALID, "x": {}},
        {**VALID, "ph": {**VALID["ph"], "angle": "0"}},
        {**VALID, "white": {k: v for k, v in VALID["white"].items() if k != "cx"}},
    ]
    for bad in bad_values:
        with pytest.raises(errors.CheckViolation), db.connection.transaction():
            db.execute("insert into public.camera_card_masks (pond_id, version, placements) values (%s, 3, %s)",
                       (POND, Jsonb(bad)))
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        db.execute("delete from public.camera_card_masks where pond_id = %s and version = 1", (POND,))
    db.execute("select count(*) as n from public.camera_card_masks where pond_id = %s", (POND,))
    assert db.fetchone()["n"] == 2


def test_card_mask_owner_rls_reads_and_inserts_only_own_pond(db):
    uid = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid}@card-mask.test"))
    db.execute('update public."UserData" set auth_uid = %s where "userID" = %s', (uid, POND))
    with db.connection.transaction():
        db.execute("set local role authenticated")
        db.execute("select set_config('request.jwt.claim.sub', %s, true)", (uid,))
        db.execute("select count(*) as n from public.camera_card_masks where pond_id = %s", (POND,))
        assert db.fetchone()["n"] == 0
        db.execute("insert into public.camera_card_masks (pond_id, version, placements) values (%s, 1, %s)",
                   (POND, Jsonb(VALID)))
        with pytest.raises(errors.InsufficientPrivilege):
            with db.connection.transaction():
                db.execute("insert into public.camera_card_masks (pond_id, version, placements) values (%s, 1, %s)",
                           (POND + 1, Jsonb(VALID)))
        db.execute("reset role")
