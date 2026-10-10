"""Migration 0024 (notification_outbox, enqueue_notification) against a
disposable local database, transactions rolled back. Skips without a
local stack (`supabase start`)."""
import pytest
from test_sql_event_ledger import _connect

POND = 9436
ENQUEUE = ("select public.enqueue_notification(%s, %s, %s, %s, %s, %s::jsonb, %s, %s::timestamptz, %s) as r")


@pytest.fixture(scope="module")
def conn():
    connection = _connect()
    with connection.cursor() as cur:
        cur.execute("select to_regclass('public.notification_outbox') is not null as ok")
        ok = cur.fetchone()["ok"]
    connection.rollback()
    if not ok:
        connection.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield connection
    connection.close()


@pytest.fixture
def db(conn):
    with conn.cursor() as cur:
        cur.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 1000, 1)', (POND,))
        yield cur
    conn.rollback()


def enqueue(db, at="2026-10-09T02:00:00Z", key="status_red:chemistry", cooldown=43200, kind="status_red",
            severity="critical", title="Water chemistry: High Risk", body="Consider a partial water change.",
            data='{"domain": "chemistry"}', pond=POND):
    db.execute(ENQUEUE, (pond, kind, severity, title, body, data, key, at, cooldown))
    return db.fetchone()["r"]


def test_outbox_sql_enqueue_stores_and_returns_the_row(db):
    row = enqueue(db)
    assert row["pond"] == POND and row["kind"] == "status_red" and row["data"] == {"domain": "chemistry"}
    assert row["created_at"].startswith("2026-10-09T02:00:00") and row["sent_at"] is None and row["error"] is None
    db.execute("select count(*) as n from public.notification_outbox where pond = %s", (POND,))
    assert db.fetchone()["n"] == 1


def test_outbox_sql_dedupe_cool_down_per_key(db):
    assert enqueue(db) is not None
    assert enqueue(db, at="2026-10-09T13:59:00Z") is None, "inside the 12-hour cool-down"
    assert enqueue(db, at="2026-10-09T03:00:00Z", key="status_red:algae") is not None
    assert enqueue(db, at="2026-10-09T14:00:00Z") is not None, "one cool-down later"
    db.execute("select dedupe_key from public.notification_outbox where pond = %s order by created_at, id", (POND,))
    assert [r["dedupe_key"] for r in db.fetchall()] == [
        "status_red:chemistry", "status_red:algae", "status_red:chemistry"]


def test_outbox_sql_old_rows_without_results_still_read(db):
    db.execute("insert into public.notification_outbox (pond, kind, severity, title, body, dedupe_key) "
               "values (%s, 'node_silent', 'warning', 't', 'b', 'node_silent:sensor') returning *", (POND,))
    row = db.fetchone()
    assert row["data"] == {} and row["created_at"] is not None and row["sent_at"] is None


@pytest.mark.parametrize("values", [
    {"kind": "email"}, {"severity": "urgent"}, {"title": " "}, {"body": ""}, {"key": ""}, {"data": "[1]"}])
def test_outbox_sql_rejects_invalid_rows(db, values):
    from psycopg.errors import CheckViolation

    with pytest.raises(CheckViolation):
        enqueue(db, **values)


def test_outbox_sql_rejects_a_negative_cool_down_and_an_unknown_pond(db):
    from psycopg.errors import ForeignKeyViolation, InvalidParameterValue

    with pytest.raises(InvalidParameterValue):
        enqueue(db, cooldown=-1)
    db.connection.rollback()
    with pytest.raises(ForeignKeyViolation):
        enqueue(db, pond=-9436)


def test_outbox_sql_is_backend_only(db):
    db.execute("select relrowsecurity from pg_class where oid = 'public.notification_outbox'::regclass")
    assert db.fetchone()["relrowsecurity"] is True
    db.execute("select grantee from information_schema.role_table_grants "
               "where table_schema = 'public' and table_name = 'notification_outbox'")
    grantees = {r["grantee"] for r in db.fetchall()}
    assert "service_role" in grantees and not grantees & {"anon", "authenticated"}
    signature = "public.enqueue_notification(bigint, text, text, text, text, jsonb, text, timestamptz, integer)"
    for role, allowed in (("anon", False), ("authenticated", False), ("service_role", True)):
        db.execute("select has_function_privilege(%s, %s, 'execute') as ok", (role, signature))
        assert db.fetchone()["ok"] is allowed, role
