"""Migration 0022 (pond_calibration) against a disposable local database,
transactions rolled back. Skips without a local stack (`supabase start`)."""
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
        cur.execute('insert into public."UserData" ("userID", volume, biomass) values (9432, 1000, 1)')
        yield cur
    conn.rollback()


INSERT = ("insert into public.pond_calibration (pond_id, parameter, status, value, lag_hours, fitted_at, "
          "effective_from, training_from, training_to, evaluation_from, evaluation_to, error, details) "
          "values (9432, %s, %s, %s, %s, now(), now(), %s, %s, %s, %s, %s, %s) returning *")


def insert(db, parameter="water_air_temperature", status="fitted", value=-1.5, lag=2,
           training=("2026-09-01Z", "2026-09-10Z"), evaluation=("2026-09-10Z", "2026-09-14Z"),
           error='{"evaluation_rmse": 0.2}', details='{}'):
    db.execute(INSERT, (parameter, status, value, lag, *training, *evaluation, error, details))
    return db.fetchone()


def test_calibration_sql_fitted_and_pending_rows_round_trip(db):
    fitted = insert(db)
    assert fitted["id"] is not None and fitted["created_at"] is not None
    assert fitted["sample_count"] == 0 and fitted["method_version"] == 1
    assert fitted["error"] == {"evaluation_rmse": 0.2}
    pending = insert(db, parameter="nitrification_rate_scale", status="pending", value=None, lag=None,
                     training=(None, None), evaluation=(None, None), error=None,
                     details='{"reason": "not_enough_kit_readings"}')
    assert pending["value"] is None and pending["details"] == {"reason": "not_enough_kit_readings"}


@pytest.mark.parametrize("values", [
    {"parameter": "wind_function"},
    {"status": "maybe"},
    {"status": "fitted", "value": None},
    {"status": "pending", "value": 0.5},
    {"value": "Infinity"},
    {"lag": -1},
    {"training": ("2026-09-10Z", "2026-09-01Z")},
    {"evaluation": ("2026-09-05Z", "2026-09-14Z")},  # overlaps training
    {"error": "[1]"},
])
def test_calibration_sql_rejects_invalid_rows(db, values):
    from psycopg.errors import CheckViolation

    with pytest.raises(CheckViolation):
        insert(db, **values)


def test_calibration_sql_is_backend_only(db):
    db.execute("select relrowsecurity from pg_class where oid = 'public.pond_calibration'::regclass")
    assert db.fetchone()["relrowsecurity"] is True
    db.execute("select grantee, privilege_type from information_schema.role_table_grants "
               "where table_schema = 'public' and table_name = 'pond_calibration'")
    grantees = {r["grantee"] for r in db.fetchall()}
    assert "service_role" in grantees and not grantees & {"anon", "authenticated"}
