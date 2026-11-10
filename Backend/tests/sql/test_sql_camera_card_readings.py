"""Local-only checks for migrations 0033 to 0035."""
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


def _pond_image(db):
    from tests.sql.test_sql_camera_card_masks import POND

    db.execute('insert into public."imageTable" ("user_ID") values (%s) returning id', (POND,))
    return POND, db.fetchone()["id"]


def test_card_reading_without_white_reference_is_stored(db):
    """Migration 0035: a covered card has no white_rgb; older rows keep theirs."""
    pond, image_id = _pond_image(db)
    db.execute("""insert into public.camera_card_readings
        (image_id, pond_id, card_masks_version, white_rgb, patches, usable_dots, total_dots, quality)
        values (%s, %s, 1, null, %s, 0, 199, 'unusable') returning white_rgb, quality""",
               (image_id, pond, Jsonb({})))
    assert db.fetchone() == {"white_rgb": None, "quality": "unusable"}
    db.execute("select is_nullable from information_schema.columns where table_schema = 'public' "
               "and table_name = 'camera_card_readings' and column_name = 'white_rgb'")
    assert db.fetchone()["is_nullable"] == "YES"


def test_row_the_upload_writes_for_a_covered_card_fits_the_table(db):
    """The camera's own row for a frame whose white reference is unreadable
    (real reader, in-memory storage) inserts into the migrated table."""
    import cv2
    import numpy as np

    from conftest import make_settings
    from koi.camera import create_app
    from koi.camera.card_masks import home_placements
    from koi.storage import MemoryStorage

    storage = MemoryStorage()
    placements = home_placements().to_json()
    placements["white"]["cx"] = 5.0  # the white card is out of the frame
    storage.save_camera_card_masks(15, placements)
    ok, jpeg = cv2.imencode(".jpg", np.full((48, 64, 3), (0, 200, 0), dtype=np.uint8))
    assert ok
    app = create_app(make_settings(), storage=storage)
    assert app.test_client().post("/upload", data=jpeg.tobytes(), headers={"X-User-ID": "15"}).status_code == 200
    [row] = storage.rows("camera_card_readings")
    assert row["white_rgb"] is None and row["quality"] == "unusable"

    pond, image_id = _pond_image(db)
    row = {**{k: v for k, v in row.items() if k != "created_at"}, "image_id": image_id, "pond_id": pond}
    values = [Jsonb(v) if isinstance(v, (dict, list)) else v for v in row.values()]
    db.execute(f"insert into public.camera_card_readings ({', '.join(row)}) "
               f"values ({', '.join(['%s'] * len(row))}) returning white_rgb, turbidity_reason",
               values)
    assert db.fetchone() == {"white_rgb": None, "turbidity_reason": "no_reference_frame"}
