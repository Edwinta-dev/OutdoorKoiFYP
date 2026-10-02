"""SQL tests for migration 0008_pond_profile.sql (issue #16): the
pond_profile table, its checks and unique effective time, its access
rules, and the trigger that records legacy UserData volume and biomass
writes as new profile rows. The seed itself is checked by
tools/db_rehearsal.py, which runs the migration over legacy rows.

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

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND, OTHER = 9201, 9202


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
        cur.execute("select to_regclass('public.pond_profile') is not null as ok")
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
        # Opens the test's transaction now, so a test whose first statement
        # is a nested transaction() gets a savepoint (rolled back below)
        # rather than a transaction of its own that commits.
        cur.execute("select 1")
        yield cur
    conn.rollback()


def _pond(db: Any, user_id: int, volume: Any = 2500, biomass: Any = 55.05) -> None:
    # No coordinates or postal code, so the station-lookup triggers do nothing.
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, %s, %s)',
               (user_id, volume, biomass))


def _profiles(db: Any, pond: int) -> list[dict]:
    db.execute("select volume_l::float8, depth_m::float8, biomass_g::float8, fish_type, fish_count, aeration, "
               "source, effective_from from public.pond_profile where pond_id = %s order by effective_from, id",
               (pond,))
    return db.fetchall()


def _add(db: Any, pond: int, at: str, **values: Any) -> None:
    row = {"volume_l": 1000, "biomass_g": 2000, **values}
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    db.execute(f"insert into public.pond_profile (pond_id, effective_from, {cols}) "
               f"values (%s, %s::timestamptz, {marks})", (pond, at, *row.values()))


def _as_role(db: Any, role: str, sql: str, params: tuple = ()) -> None:
    with db.connection.transaction():
        db.execute(f"set local role {role}")
        db.execute(sql, params)
        db.execute("reset role")


def _new_transaction_time(db: Any) -> None:
    """now() is fixed for the whole test transaction, so before a second
    trigger write the first row is moved an hour back to give the two rows
    different effective times."""
    db.execute("update public.pond_profile set effective_from = effective_from - interval '1 hour' "
               "where pond_id in (%s, %s)", (POND, OTHER))


# --- table ----------------------------------------------------------------------

def test_profile_columns_and_unknown_depth_is_null(db):
    db.execute("select column_name, data_type, is_nullable from information_schema.columns "
               "where table_schema = 'public' and table_name = 'pond_profile'")
    cols = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in db.fetchall()}
    assert cols["effective_from"] == ("timestamp with time zone", "NO")
    assert cols["volume_l"][1] == "NO" and cols["biomass_g"][1] == "NO"
    for nullable in ("depth_m", "fish_type", "fish_count", "tap_tds_ppm", "tap_nitrate_ppm", "aeration"):
        assert cols[nullable][1] == "YES", nullable
    assert cols["aeration"][0] == "boolean"


@pytest.mark.parametrize("values", [{"volume_l": 0}, {"volume_l": -5}, {"depth_m": 0}, {"depth_m": -1},
                                    {"biomass_g": -1}, {"fish_count": -1}, {"tap_tds_ppm": -1},
                                    {"tap_nitrate_ppm": -0.5}, {"source": "phone"}])
def test_profile_rejects_impossible_values(db, values):
    _pond(db, POND)
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        _add(db, POND, "2026-09-01T00:00:00Z", **values)


def test_profile_effective_time_is_unique_per_pond_as_an_instant(db):
    _pond(db, POND)
    _pond(db, OTHER)
    _add(db, POND, "2026-09-01T06:00:00Z")
    with pytest.raises(errors.UniqueViolation), db.connection.transaction():
        _add(db, POND, "2026-09-01T14:00:00+08:00")
    _add(db, OTHER, "2026-09-01T06:00:00Z")


def test_profile_needs_an_existing_pond_and_keeps_history_when_it_is_deleted(db):
    with pytest.raises(errors.ForeignKeyViolation), db.connection.transaction():
        _add(db, 9299, "2026-09-01T00:00:00Z")
    _pond(db, POND)
    with pytest.raises(errors.ForeignKeyViolation), db.connection.transaction():
        db.execute('delete from public."UserData" where "userID" = %s', (POND,))


def test_profile_latest_effective_row_lookup_is_deterministic(db):
    _pond(db, POND)
    _add(db, POND, "2026-09-03T00:00:00Z", volume_l=3000)
    _add(db, POND, "2026-09-02T00:00:00Z", volume_l=2000)
    db.execute("select volume_l::float8 as v from public.pond_profile where pond_id = %s "
               "and effective_from <= '2026-09-02T12:00:00Z' order by effective_from desc limit 1", (POND,))
    assert db.fetchone()["v"] == 2000.0


# --- access -----------------------------------------------------------------------

def test_profile_rls_on_and_closed_to_the_app_roles(db):
    db.execute("select relrowsecurity from pg_class where oid = 'public.pond_profile'::regclass")
    assert db.fetchone()["relrowsecurity"] is True
    for role in ("anon", "authenticated"):
        db.execute("select has_table_privilege(%s, 'public.pond_profile', 'select') as s, "
                   "has_table_privilege(%s, 'public.pond_profile', 'insert') as i", (role, role))
        assert db.fetchone() == {"s": False, "i": False}
        db.execute("select has_function_privilege(%s, 'public.record_userdata_profile_change()', 'execute') as x",
                   (role,))
        assert db.fetchone()["x"] is False
    db.execute("select has_table_privilege('service_role', 'public.pond_profile', 'insert') as i")
    assert db.fetchone()["i"] is True


# --- legacy UserData writes ------------------------------------------------------

def test_profile_userdata_insert_records_a_profile_in_grams(db):
    _pond(db, POND, 2500, 55.05)
    [row] = _profiles(db, POND)
    assert (row["volume_l"], row["biomass_g"], row["source"]) == (2500.0, 55050.0, "userdata")
    assert row["depth_m"] is None and row["fish_type"] is None
    db.execute("select now() as t")
    assert row["effective_from"] == db.fetchone()["t"]


def test_profile_userdata_change_adds_a_row_and_keeps_the_rest_of_the_profile(db):
    _pond(db, POND, 2500, 55)
    db.execute("update public.pond_profile set depth_m = 0.9, fish_type = 'Koi', fish_count = 5, aeration = true "
               "where pond_id = %s", (POND,))
    _new_transaction_time(db)
    db.execute('update public."UserData" set volume = 3000 where "userID" = %s', (POND,))
    old, new = _profiles(db, POND)
    assert old["volume_l"] == 2500.0 and old["effective_from"] < new["effective_from"]
    assert (new["volume_l"], new["biomass_g"], new["depth_m"], new["fish_type"], new["fish_count"],
            new["aeration"], new["source"]) == (3000.0, 55000.0, 0.9, "Koi", 5, True, "userdata")


def test_profile_userdata_write_without_a_change_adds_nothing(db):
    _pond(db, POND)
    db.execute('update public."UserData" set volume = volume, biomass = biomass, region = %s '
               'where "userID" = %s', ("Central", POND))
    assert len(_profiles(db, POND)) == 1


@pytest.mark.parametrize("volume, biomass", [(None, 5), (2500, None), (0, 5)])
def test_profile_userdata_without_a_usable_volume_and_biomass_records_nothing(db, volume, biomass):
    _pond(db, POND, volume, biomass)
    assert _profiles(db, POND) == []


def test_profile_app_onboarding_upsert_still_works_as_anon(db):
    """The app's onboarding upsert runs with the publishable key (anon);
    the profile trigger must not make it fail."""
    _as_role(db, "anon", 'insert into public."UserData" ("userID", volume, biomass) values (%s, 800, 1.5) '
                         'on conflict ("userID") do update set volume = excluded.volume, biomass = excluded.biomass',
             (POND,))
    [row] = _profiles(db, POND)
    assert (row["volume_l"], row["biomass_g"]) == (800.0, 1500.0)
