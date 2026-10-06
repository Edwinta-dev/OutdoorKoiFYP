"""Migration 0020 against a disposable local database, transactions rolled back."""
import pytest
from test_sql_event_ledger import _connect


@pytest.fixture(scope="module")
def conn():
    connection = _connect()
    yield connection
    connection.close()


@pytest.fixture
def db(conn):
    with conn.cursor() as cur:
        cur.execute('insert into public."UserData" ("userID", volume, biomass) values (9430, 1000, 1)')
        yield cur
    conn.rollback()


def test_kit_sql_nullable_values_and_comparison_round_trip(db):
    db.execute("insert into public.kit_readings (pond) values (9430) returning *")
    row = db.fetchone()
    assert row["id"] is not None and row["created_at"] is not None
    fields = ("taken_at", "kit", "ammonia_mg_l", "nitrite_mg_l", "nitrate_mg_l", "ph", "kh_dkh", "notes",
              "estimates", "differences", "comparison")
    assert all(row[field] is None for field in fields)
    db.execute("select column_name, is_nullable from information_schema.columns "
               "where table_schema = 'public' and table_name = 'kit_readings'")
    columns = {r["column_name"]: r["is_nullable"] for r in db.fetchall()}
    assert all(columns[field] == "YES" for field in fields)
    db.execute("insert into public.kit_readings (pond, taken_at, kit, ammonia_mg_l, nitrite_mg_l, nitrate_mg_l, "
               "ph, kh_dkh, notes, estimates, differences, comparison) values "
               "(9430, '2026-08-20T00:00:00Z', 'Liquid TAN kit', 0, 0.2, 10, 7.4, 4, 'Before feed', "
               "'{\"ammonia_mg_l\":0.5}', '{\"ammonia_mg_l\":0.5}', '{\"source\":\"evaluation\"}') returning *")
    result = db.fetchone()
    assert result["ammonia_mg_l"] == 0
    assert result["estimates"] == result["differences"] == {"ammonia_mg_l": 0.5}
    assert result["comparison"] == {"source": "evaluation"}
    assert result["notes"] == "Before feed"


@pytest.mark.parametrize("field,value", [("ammonia_mg_l", -1), ("nitrite_mg_l", "Infinity"),
                                         ("nitrate_mg_l", "NaN"), ("ph", 15), ("kh_dkh", -1)])
def test_kit_sql_rejects_invalid_values(db, field, value):
    from psycopg.errors import CheckViolation

    with pytest.raises(CheckViolation):
        db.execute(f"insert into public.kit_readings (pond, {field}) values (9430, %s)", (value,))


def test_kit_sql_readings_follow_a_renumbered_pond_and_block_its_deletion(db):
    """As pond_profile and camera_config: on update cascade, on delete no action."""
    from psycopg.errors import ForeignKeyViolation

    db.execute("insert into public.kit_readings (pond, ammonia_mg_l) values (9430, 0.25) returning id")
    reading = db.fetchone()["id"]
    db.execute('update public."UserData" set "userID" = 9431 where "userID" = 9430')
    db.execute("select pond from public.kit_readings where id = %s", (reading,))
    assert db.fetchone()["pond"] == 9431
    db.execute("savepoint before_delete")
    with pytest.raises(ForeignKeyViolation):
        db.execute('delete from public."UserData" where "userID" = 9431')
    db.execute("rollback to savepoint before_delete")
    db.execute("select count(*) as n from public.kit_readings where id = %s", (reading,))
    assert db.fetchone()["n"] == 1


def test_kit_sql_backend_only_access_and_index(db):
    db.execute("select relrowsecurity from pg_class where oid = 'public.kit_readings'::regclass")
    assert db.fetchone()["relrowsecurity"]
    for role in ("anon", "authenticated", "service_role"):
        for operation in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            db.execute("select has_table_privilege(%s, 'public.kit_readings', %s) as ok", (role, operation))
            assert db.fetchone()["ok"] == (role == "service_role")
        db.execute("select has_sequence_privilege(%s, 'public.kit_readings_id_seq', 'USAGE') as ok", (role,))
        assert db.fetchone()["ok"] == (role == "service_role")
    db.execute("select indexdef from pg_indexes where schemaname = 'public' and indexname = 'kit_readings_pond_time'")
    assert "(pond, taken_at, id)" in db.fetchone()["indexdef"]


def test_kit_sql_measurements_do_not_write_model_state(db):
    db.execute("select count(*) as n from public.pond_chemistry_state where user_id = 9430")
    before = db.fetchone()["n"]
    db.execute("insert into public.kit_readings (pond, ammonia_mg_l) values (9430, 1)")
    db.execute("select count(*) as n from public.pond_chemistry_state where user_id = 9430")
    assert db.fetchone()["n"] == before
