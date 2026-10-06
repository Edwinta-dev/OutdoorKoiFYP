"""Migration 0019 on the disposable local database, never live."""
from datetime import datetime, timedelta, timezone

import pytest
from test_sql_event_ledger import POND_A, T0, _connect, _log, _storage


@pytest.fixture(scope="module")
def conn():
    connection = _connect()
    yield connection
    connection.close()


@pytest.fixture
def db(conn):
    with conn.cursor() as cur:
        yield cur
    conn.rollback()


def test_sql_salt_filter_nullable_columns_and_old_rows(db):
    row = _log(db, POND_A, T0)
    assert row["salt_grams"] is row["notes"] is None
    [loaded] = _storage(db).fetch_interventions(POND_A, T0)
    assert loaded["salt_grams"] is loaded["notes"] is None
    assert loaded["event_id"] == str(row["event_id"])


def test_sql_salt_filter_history_and_maintenance_window(db):
    # Singapore 23:00 yesterday; exactly two daily buckets overlap 24 h.
    now = datetime.now(timezone.utc)
    at = (now - timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    db.execute('insert into public."pondInterventions" ("userID", event_type, event_timestamp, salt_grams, notes) '
               "values (%s, 'SALT', %s, 1, 'Added'), (%s, 'FILTER_CLEAN', %s, null, 'Rinsed') returning *",
               (POND_A, at, POND_A, at))
    salt, clean = db.fetchall()
    rows = _storage(db).fetch_interventions(POND_A, at)
    assert rows[0]["salt_grams"] == 1 and rows[0]["notes"] == "Added"
    assert rows[1]["salt_grams"] is None and rows[1]["notes"] == "Rinsed"
    assert rows[0]["event_id"] == str(salt["event_id"])
    assert rows[1]["event_id"] == str(clean["event_id"])
    for instant in (at - timedelta(minutes=1), at + timedelta(hours=23), at + timedelta(hours=25)):
        for channel, value in (("TDS", 200), ("pH", 7.5), ("temp", 28)):
            db.execute('insert into public."SensorData" ("userID", sensor_type, data1, created_at) '
                       'values (%s, %s, %s, %s)', (POND_A, channel, value, instant))
    db.execute('select public.get_historical_graph_payload(%s, 30) as r', (POND_A,))
    payload = db.fetchone()["r"]
    assert {e["event_id"] for e in payload["interventions"]} == {str(salt["event_id"]), str(clean["event_id"])}
    assert payload["interventions"][0]["salt_grams"] == 1
    assert payload["interventions"][1]["notes"] == "Rinsed"
    for channel in ("TDS", "pH"):
        trends = [r for r in payload["daily_trends"] if r["sensor_type"] == channel]
        assert [r["after_maintenance"] for r in trends] == [True, True, False]
    assert not any(r["after_maintenance"] for r in payload["daily_trends"] if r["sensor_type"] == "temp")
    db.execute('delete from public."pondInterventions" where id = %s', (clean["id"],))
    db.execute('select public.get_historical_graph_payload(%s, 30) as r', (POND_A,))
    assert not any(r["after_maintenance"] for r in db.fetchone()["r"]["daily_trends"])


@pytest.mark.parametrize("grams", [None, 0, -1, "NaN", "Infinity"])
def test_sql_salt_invalid_mass_constraint(db, grams):
    # Isolated savepoint keeps the surrounding rollback fixture usable.
    db.execute("savepoint invalid_salt")
    with pytest.raises(Exception, match="salt_event_mass"):
        db.execute('insert into public."pondInterventions" ("userID", event_type, event_timestamp, salt_grams) '
                   "values (%s, 'SALT', %s, %s)", (POND_A, T0, grams))
    db.execute("rollback to savepoint invalid_salt")
