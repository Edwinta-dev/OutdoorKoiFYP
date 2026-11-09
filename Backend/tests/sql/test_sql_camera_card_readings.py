"""Local-only checks for migrations 0033 and 0034."""
from __future__ import annotations

import uuid

import pytest
from psycopg.types.json import Jsonb

pytest_plugins = ("tests.sql.test_sql_camera_card_masks",)


def test_card_reading_table_has_expected_columns(conn, db):
    db.execute("select to_regclass('public.camera_card_readings') as table_name")
    if db.fetchone()['table_name'] is None:
        pytest.skip("local database is not at migration 0034; run `supabase db reset`")
    db.execute("select column_name from information_schema.columns where table_schema='public' "
               "and table_name='camera_card_readings'")
    columns = {row['column_name'] for row in db.fetchall()}
    if 'turbidity_index' not in columns:
        pytest.skip("local database is before migration 0034; apply the repository migrations locally")
    assert {'image_id', 'pond_id', 'card_masks_version', 'white_rgb', 'patches', 'ph', 'nh3',
            'usable_dots', 'total_dots', 'quality', 'created_at', 'turbidity_index', 'turbidity_channels',
            'turbidity_a',
            'turbidity_b', 'turbidity_r2', 'turbidity_reason', 'turbidity_patches_used', 'agreement',
            'frame_agreement', 'unusable_streak', 'unusable_alerted'} <= columns


def test_card_reading_owner_rls_limits_authenticated_reads_to_own_pond(db):
    from tests.sql.test_sql_camera_card_masks import POND

    uid = str(uuid.uuid4())
    db.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{uid}@card-reading.test"))
    db.execute('update public."UserData" set auth_uid = %s where "userID" = %s', (uid, POND))
    other_pond = POND + 1
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 1000, 1)', (other_pond,))
    image_ids = []
    for pond_id in (POND, other_pond):
        db.execute('insert into public."imageTable" ("user_ID") values (%s) returning id', (pond_id,))
        image_id = db.fetchone()["id"]
        image_ids.append(image_id)
        db.execute("""insert into public.camera_card_readings
            (image_id, pond_id, card_masks_version, white_rgb, patches, usable_dots, total_dots, quality)
            values (%s, %s, 1, %s, %s, 0, 0, 'unusable')""",
                   (image_id, pond_id, Jsonb([200, 200, 200]), Jsonb({})))

    with db.connection.transaction():
        db.execute("set local role authenticated")
        db.execute("select set_config('request.jwt.claim.sub', %s, true)", (uid,))
        db.execute("select image_id from public.camera_card_readings where pond_id = any(%s)",
                   ([POND, other_pond],))
        assert [row["image_id"] for row in db.fetchall()] == [image_ids[0]]
