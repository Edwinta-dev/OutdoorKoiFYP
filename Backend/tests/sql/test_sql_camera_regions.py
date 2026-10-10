"""SQL tests for migration 0023_camera_regions.sql (issue #91): regions
validation in the database matching koi/camera/mask.py::validate_regions,
save_camera_mask with regions (and its unchanged two-argument call),
regions in immutable mask versions, configs without a mask, access rules,
and imageTable.regions next to rows written before it.

Same rules as test_sql_camera_mask.py: local database only, every test
rolled back, skipped when no local database answers.
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
POND = 9394
RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
TOP_HALF = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]
GAP = [[0.3, 0.4], [0.45, 0.4], [0.45, 0.55], [0.3, 0.55]]
REGIONS = {"water_gap": GAP, "rim": RIGHT_HALF, "plants": TOP_HALF}


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    host = urlparse(DB_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        pytest.fail(f"KOI_TEST_DB_URL must point at a local database, not {host}")
    try:
        c = psycopg.connect(DB_URL, connect_timeout=2, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no local database at {DB_URL} ({exc.__class__.__name__}); run `supabase start`")
    with c.cursor() as cur:
        cur.execute("select to_regprocedure('public.camera_regions_are_valid(jsonb)') is not null as ok")
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
        cur.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 1000, 1)', (POND,))
        yield cur
    conn.rollback()


def _save(db: Any, mask: Any, regions: Any = None) -> dict:
    db.execute("select * from public.save_camera_mask(%s, %s, %s)",
               (POND, Jsonb(mask) if mask is not None else None, Jsonb(regions) if regions is not None else None))
    return db.fetchone()


@pytest.mark.parametrize("regions, valid", [
    (REGIONS, True),
    ({**REGIONS, "reference": GAP}, True),
    ({"water_gap": GAP, "rim": RIGHT_HALF}, False),
    ({**REGIONS, "bottle": GAP}, False),
    ({**REGIONS, "rim": [[0, 0], [1, 0]]}, False),
    ({**REGIONS, "reference": [[0, 0], [0.05, 0], [0.05, 0.05]]}, False),
    ([GAP], False),
    (None, False),
])
def test_regions_sql_validity_matches_the_backend_rules(db, regions, valid):
    from koi.camera import mask as camera_mask

    db.execute("select public.camera_regions_are_valid(%s) as ok",
               (Jsonb(regions) if regions is not None else None,))
    assert db.fetchone()["ok"] is valid
    try:
        camera_mask.validate_regions(regions)
        backend_valid = True
    except ValueError:
        backend_valid = False
    assert backend_valid is valid


def test_regions_sql_two_argument_save_is_unchanged(db):
    db.execute("select * from public.save_camera_mask(%s, %s)", (POND, Jsonb(RIGHT_HALF)))
    row = db.fetchone()
    assert row["mask"] == RIGHT_HALF and row["regions"] is None and row["mask_version"] == 1


def test_regions_sql_save_versions_regions_with_the_mask(db):
    assert _save(db, RIGHT_HALF)["mask_version"] == 1
    second = _save(db, RIGHT_HALF, REGIONS)
    assert second["mask_version"] == 2 and second["regions"] == REGIONS
    moved = {**REGIONS, "water_gap": [[0.1, 0.1], [0.25, 0.1], [0.25, 0.25], [0.1, 0.25]]}
    assert _save(db, RIGHT_HALF, moved)["mask_version"] == 3
    db.execute("select mask_version, mask, regions from public.camera_mask_version where pond_id = %s order by 1",
               (POND,))
    assert db.fetchall() == [{"mask_version": 1, "mask": RIGHT_HALF, "regions": None},
                             {"mask_version": 2, "mask": RIGHT_HALF, "regions": REGIONS},
                             {"mask_version": 3, "mask": RIGHT_HALF, "regions": moved}]
    db.execute("select mask, regions, mask_version from public.camera_config where pond_id = %s", (POND,))
    assert db.fetchone() == {"mask": RIGHT_HALF, "regions": moved, "mask_version": 3}


def test_regions_sql_config_without_a_mask(db):
    row = _save(db, None, REGIONS)
    assert row["mask"] is None and row["regions"] == REGIONS
    with pytest.raises(errors.InvalidParameterValue, match="mask, regions or both"), db.connection.transaction():
        _save(db, None, None)
    with pytest.raises(errors.InvalidParameterValue, match="regions must be"), db.connection.transaction():
        _save(db, RIGHT_HALF, {"water_gap": GAP})
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        db.execute("insert into public.camera_mask_version (pond_id, mask_version, mask, regions) "
                   "values (%s, 9, null, null)", (POND,))
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        db.execute("insert into public.camera_mask_version (pond_id, mask_version, mask, regions) "
                   "values (%s, 9, null, %s)", (POND, Jsonb({"water_gap": GAP})))


def test_regions_sql_saved_regions_are_immutable(db):
    _save(db, RIGHT_HALF, REGIONS)
    with pytest.raises(errors.CheckViolation, match="immutable"), db.connection.transaction():
        db.execute("update public.camera_mask_version set regions = null where pond_id = %s", (POND,))


def test_regions_sql_access_is_service_role_only(db):
    for function in ("public.save_camera_mask(bigint, jsonb, jsonb)", "public.camera_regions_are_valid(jsonb)"):
        for role in ("anon", "authenticated"):
            db.execute("select has_function_privilege(%s, %s, 'execute') as x", (role, function))
            assert db.fetchone()["x"] is False, (role, function)
        db.execute("select has_function_privilege('service_role', %s, 'execute') as x", (function,))
        assert db.fetchone()["x"] is True, function
    db.execute("select to_regprocedure('public.save_camera_mask(bigint, jsonb)') as old")
    assert db.fetchone()["old"] is None
    with db.connection.transaction():
        db.execute("set local role service_role")
        db.execute("select mask_version from public.save_camera_mask(%s, %s, %s)",
                   (POND, None, Jsonb(REGIONS)))
        assert db.fetchone() == {"mask_version": 1}
        db.execute("reset role")


def test_regions_sql_image_rows_old_and_new_shapes(db):
    db.execute('insert into public."imageTable" ("user_ID", green_ratio, current_state, "imageURL") '
               "values (%s, 0.2, '[\"base\", 0.2]', 'old') returning regions, mask_version", (POND,))
    assert db.fetchone() == {"regions": None, "mask_version": None}
    metrics = {"version": 1, "reference": None, "regions": {"water_gap": {"gcc": 0.4, "usable": True}}}
    db.execute('insert into public."imageTable" ("user_ID", green_ratio, current_state, "imageURL", mask_version, '
               "regions) values (%s, 0.1, '[\"base\", 0.1, 0, 0]', 'new', 1, %s) returning regions",
               (POND, Jsonb(metrics)))
    assert db.fetchone()["regions"] == metrics
    with db.connection.transaction():
        db.execute("set local role anon")
        db.execute('select id, regions from public."imageTable" limit 1')
        db.fetchall()
        db.execute("reset role")
