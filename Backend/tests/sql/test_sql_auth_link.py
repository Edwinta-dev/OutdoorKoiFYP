"""SQL tests for migration 0007_auth_link.sql (issue #11): UserData.auth_uid,
its guard trigger, auth_session_active and link_pond_to_account.

Same rules as test_sql_rpcs.py: local database only, every test rolled
back, skipped when no local database answers. Accounts and sessions are
synthetic rows in the local stack's auth schema.

The tests for direct table and RPC access by the app's roles (anon,
authenticated) to pond data belong to #12.
"""
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

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND_A, POND_B, POND_LEGACY = 9101, 9102, 9103


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
        cur.execute("select exists (select 1 from information_schema.columns where table_schema = 'public' "
                    "and table_name = 'UserData' and column_name = 'auth_uid') as ok")
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


def _account(db: Any) -> str:
    uid = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid}@koi-test.invalid"))
    return uid


def _session(db: Any, uid: str, not_after: str | None = None) -> str:
    sid = str(uuid.uuid4())
    db.execute("insert into auth.sessions (id, user_id, not_after) values (%s, %s, %s::timestamptz)",
               (sid, uid, not_after))
    return sid


def _pond(db: Any, user_id: int, auth_uid: str | None = None) -> None:
    # No coordinates or postal code, so the station-lookup triggers do nothing.
    db.execute('insert into public."UserData" ("userID", volume, biomass, auth_uid) values (%s, 2500, 55, %s)',
               (user_id, auth_uid))


def _auth_uid(db: Any, user_id: int) -> str | None:
    db.execute('select auth_uid from public."UserData" where "userID" = %s', (user_id,))
    value = db.fetchone()["auth_uid"]
    return str(value) if value is not None else None


def _as_role(db: Any, role: str, sql: str, params: tuple = ()) -> None:
    """Runs sql as role inside a savepoint, so an expected error leaves
    the test's transaction usable."""
    # On an error the savepoint's rollback also undoes the set local role.
    with db.connection.transaction():
        db.execute(f"set local role {role}")
        db.execute(sql, params)
        db.execute("reset role")


def _link(db: Any, user_id: int, auth_uid: str) -> int:
    db.execute("select public.link_pond_to_account(%s, %s) as pond", (user_id, auth_uid))
    return db.fetchone()["pond"]


# --- column and index ---------------------------------------------------

def test_auth_uid_is_nullable_and_legacy_ponds_stay_unlinked(db):
    db.execute("select is_nullable, data_type from information_schema.columns "
               "where table_schema = 'public' and table_name = 'UserData' and column_name = 'auth_uid'")
    assert db.fetchone() == {"is_nullable": "YES", "data_type": "uuid"}
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 2500, 55)', (POND_LEGACY,))
    assert _auth_uid(db, POND_LEGACY) is None


def test_auth_uid_is_unique_one_pond_per_account(db):
    uid = _account(db)
    _pond(db, POND_A, uid)
    with pytest.raises(errors.UniqueViolation), db.connection.transaction():
        _pond(db, POND_B, uid)
    # Many unlinked ponds are fine.
    _pond(db, POND_B)
    _pond(db, POND_LEGACY)


def test_auth_uid_is_cleared_when_the_account_is_deleted(db):
    uid = _account(db)
    _pond(db, POND_A, uid)
    db.execute("delete from auth.users where id = %s", (uid,))
    assert _auth_uid(db, POND_A) is None


def test_auth_uid_must_name_a_real_account(db):
    with pytest.raises(errors.ForeignKeyViolation), db.connection.transaction():
        _pond(db, POND_A, str(uuid.uuid4()))


# --- guard trigger ----------------------------------------------------------

@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_auth_app_roles_cannot_link_a_pond_by_update(db, role):
    uid = _account(db)
    _pond(db, POND_A)
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, role, 'update public."UserData" set auth_uid = %s where "userID" = %s', (uid, POND_A))
    assert _auth_uid(db, POND_A) is None


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_auth_app_roles_cannot_insert_a_linked_pond(db, role):
    uid = _account(db)
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, role, 'insert into public."UserData" ("userID", volume, biomass, auth_uid) '
                           "values (%s, 2500, 55, %s)", (POND_A, uid))


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_auth_app_roles_cannot_unlink_or_move_a_linked_pond(db, role):
    uid = _account(db)
    _pond(db, POND_A, uid)
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, role, 'update public."UserData" set auth_uid = null where "userID" = %s', (POND_A,))
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, role, 'update public."UserData" set "userID" = %s where "userID" = %s', (POND_B, POND_A))
    assert _auth_uid(db, POND_A) == uid


def test_auth_app_roles_keep_editing_other_pond_fields(db):
    """The guard only covers the link; the app's existing writes still work."""
    uid = _account(db)
    _pond(db, POND_A, uid)
    _as_role(db, "anon", 'update public."UserData" set volume = 3000 where "userID" = %s', (POND_A,))
    _as_role(db, "anon", 'insert into public."UserData" ("userID", volume, biomass) values (%s, 1, 1)', (POND_B,))
    db.execute('select volume from public."UserData" where "userID" = %s', (POND_A,))
    assert db.fetchone()["volume"] == 3000


def test_auth_service_role_is_not_blocked_by_the_guard(db):
    uid = _account(db)
    _pond(db, POND_A)
    _as_role(db, "service_role", 'update public."UserData" set auth_uid = %s where "userID" = %s', (uid, POND_A))
    assert _auth_uid(db, POND_A) == uid


# --- auth_session_active ----------------------------------------------------

def _active(db: Any, sid: str, uid: str) -> bool:
    db.execute("select public.auth_session_active(%s, %s) as ok", (sid, uid))
    return db.fetchone()["ok"]


def test_auth_session_active_for_a_live_session(db):
    uid = _account(db)
    assert _active(db, _session(db, uid), uid) is True
    assert _active(db, _session(db, uid, "2999-01-01T00:00:00Z"), uid) is True


def test_auth_session_inactive_once_signed_out_or_past_not_after(db):
    uid = _account(db)
    sid = _session(db, uid)
    db.execute("delete from auth.sessions where id = %s", (sid,))
    assert _active(db, sid, uid) is False
    assert _active(db, _session(db, uid, "2000-01-01T00:00:00Z"), uid) is False


def test_auth_session_of_another_account_is_inactive(db):
    mine, theirs = _account(db), _account(db)
    assert _active(db, _session(db, theirs), mine) is False
    assert _active(db, str(uuid.uuid4()), mine) is False


def test_auth_session_check_is_backend_only(db):
    for role, allowed in [("anon", False), ("authenticated", False), ("service_role", True)]:
        db.execute("select has_function_privilege(%s, 'public.auth_session_active(uuid, uuid)', 'execute') as ok",
                   (role,))
        assert db.fetchone()["ok"] is allowed, role


# --- link_pond_to_account ---------------------------------------------------

def test_auth_owner_links_a_legacy_pond(db):
    uid = _account(db)
    _pond(db, POND_LEGACY)
    assert _link(db, POND_LEGACY, uid) == POND_LEGACY
    assert _auth_uid(db, POND_LEGACY) == uid
    assert _link(db, POND_LEGACY, uid) == POND_LEGACY   # repeating it is harmless


def test_auth_link_refuses_a_pond_owned_by_another_account(db):
    owner, intruder = _account(db), _account(db)
    _pond(db, POND_A, owner)
    with pytest.raises(errors.UniqueViolation, match="already linked"), db.connection.transaction():
        _link(db, POND_A, intruder)
    assert _auth_uid(db, POND_A) == owner


def test_auth_link_refuses_a_second_pond_for_an_account(db):
    uid = _account(db)
    _pond(db, POND_A, uid)
    _pond(db, POND_B)
    with pytest.raises(errors.UniqueViolation, match="already owns a pond"), db.connection.transaction():
        _link(db, POND_B, uid)
    assert _auth_uid(db, POND_B) is None


def test_auth_link_refuses_unknown_ponds_and_accounts(db):
    uid = _account(db)
    _pond(db, POND_A)
    with pytest.raises(errors.NoDataFound, match="pond"), db.connection.transaction():
        _link(db, POND_B, uid)
    with pytest.raises(errors.NoDataFound, match="account"), db.connection.transaction():
        _link(db, POND_A, str(uuid.uuid4()))


def test_auth_link_is_owner_only(db):
    for role in ("anon", "authenticated", "service_role"):
        db.execute("select has_function_privilege(%s, 'public.link_pond_to_account(bigint, uuid)', 'execute') "
                   "as ok", (role,))
        assert db.fetchone()["ok"] is False, role
    uid = _account(db)
    _pond(db, POND_A)
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, "authenticated", "select public.link_pond_to_account(%s, %s)", (POND_A, uid))
    assert _auth_uid(db, POND_A) is None


def test_auth_guard_function_is_not_callable_by_app_roles(db):
    for role in ("anon", "authenticated"):
        db.execute("select has_function_privilege(%s, 'public.guard_userdata_auth_uid()', 'execute') as ok", (role,))
        assert db.fetchone()["ok"] is False, role
