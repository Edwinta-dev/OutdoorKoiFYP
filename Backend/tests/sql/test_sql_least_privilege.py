"""SQL tests for migrations 0025_device_tokens.sql and
0026_least_privilege.sql (issue #12), run as the roles that use the
database: anon (the app signed out, and the sensor node), authenticated
(the app signed in), service_role (the backend) and postgres (the owner,
and the migration creator).

Covers token ingest (valid token, wrong, expired, revoked token, revoked
device, forged pond id, disallowed fields, unknown channels, invalid
samples, oversized and empty batches, the rate limit and rotation), the
access matrix in docs/access-matrix.md (every anon and authenticated
grant in public, the app's signed-out calls, the shared weather read,
backend-only functions and the #8 lease and snapshot calls), the http
extension's removal from the API schema, fixed search paths on definer
functions, and the default privileges of objects created later.

Local Supabase stack only; skipped when no local database answers, as in
CI. Each case runs in a rolled-back transaction.
"""
from __future__ import annotations

import json
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
POND_A, POND_B = 9261, 9262
APP_ROLES = ("anon", "authenticated")


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
        cur.execute("select to_regprocedure('public.ingest_readings(text, jsonb)') is not null as ok")
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


def _as_role(db: Any, role: str, sql: str, params: tuple | dict = ()) -> list[dict]:
    """Runs sql as role inside a savepoint (an expected error leaves the
    test's transaction usable) and returns its rows."""
    with db.connection.transaction():
        db.execute(f"set local role {role}")
        db.execute(sql, params)
        rows = db.fetchall() if db.description else []
        db.execute("reset role")
    return rows


def _pond(db: Any, pond: int) -> None:
    # No coordinates or postal code, so the station-lookup triggers do nothing.
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 2500, 55)', (pond,))


def _issue(db: Any, pond: int, overlap: str = "24 hours") -> str:
    db.execute("select public.issue_device_token(%s, %s::interval) as t", (pond, overlap))
    return db.fetchone()["t"]


def _ingest(db: Any, token: str | None, rows: Any, role: str = "anon") -> dict:
    """The node's call: POST /rest/v1/rpc/ingest_readings with the
    publishable key, which PostgREST runs as anon."""
    out = _as_role(db, role, "select public.ingest_readings(%s, %s) as r", (token, Jsonb(rows)))
    return out[0]["r"]


def _ingest_error(db: Any, token: str | None, rows: Any) -> Any:
    with pytest.raises(psycopg.Error) as err:
        _ingest(db, token, rows)
    return err.value


def _sensor_rows(db: Any, pond: int) -> list[tuple]:
    db.execute('select sensor_type, data1, "userID" from public."SensorData" where "userID" = %s', (pond,))
    return sorted((r["sensor_type"], r["data1"], r["userID"]) for r in db.fetchall())


READING = [{"sensor_type": "temp", "data1": 27.5}, {"sensor_type": "TDS", "data1": 180},
           {"sensor_type": "pH", "data1": 7.2}, {"sensor_type": "LUX", "data1": 5400}]


# ---------------------------------------------------------------------
# ingest_readings: tokens
# ---------------------------------------------------------------------
def test_ingest_valid_token_stores_rows_for_the_devices_pond(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    assert len(token) == 64
    reply = _ingest(db, token, READING)
    assert reply["accepted"] == 4 and reply["rejected"] == [] and reply["format"] == "v1"
    assert _sensor_rows(db, POND_A) == [("LUX", 5400.0, POND_A), ("TDS", 180.0, POND_A),
                                        ("pH", pytest.approx(7.2), POND_A), ("temp", 27.5, POND_A)]
    db.execute("select count(distinct created_at) as n from public.\"SensorData\" where \"userID\" = %s", (POND_A,))
    assert db.fetchone()["n"] == 1, "one batch, one insert time"
    db.execute("select t.last_used_at is not null as used from public.device_tokens t "
               "join public.devices d using (device_id) where d.pond_id = %s", (POND_A,))
    assert db.fetchone()["used"]


def test_ingest_token_is_stored_only_as_a_hash(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    db.execute("select encode(token_hash, 'hex') as h from public.device_tokens t "
               "join public.devices d using (device_id) where d.pond_id = %s", (POND_A,))
    stored = db.fetchone()["h"]
    assert stored != token and len(stored) == 64
    db.execute("select encode(sha256(convert_to(%s, 'UTF8')), 'hex') as h", (token,))
    assert db.fetchone()["h"] == stored


@pytest.mark.parametrize("token", [None, "", "short", "f" * 64, "f" * 300])
def test_ingest_wrong_token_is_refused_and_stores_nothing(db, token):
    _pond(db, POND_A)
    _issue(db, POND_A)
    err = _ingest_error(db, token, READING)
    assert err.sqlstate == "PT401" and "device token not accepted" in str(err)
    assert _sensor_rows(db, POND_A) == []


def test_ingest_revoked_token_is_refused(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    db.execute("select id from public.device_tokens order by id desc limit 1")
    token_id = db.fetchone()["id"]
    db.execute("select public.revoke_device_token(%s) as r", (token_id,))
    assert db.fetchone()["r"] is True
    db.execute("select public.revoke_device_token(%s) as r", (token_id,))
    assert db.fetchone()["r"] is False, "already revoked"
    assert _ingest_error(db, token, READING).sqlstate == "PT401"
    assert _sensor_rows(db, POND_A) == []


def test_ingest_revoked_device_refuses_every_token_and_new_tokens(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    db.execute("update public.devices set revoked_at = now() where pond_id = %s and kind = 'sensor'", (POND_A,))
    assert _ingest_error(db, token, READING).sqlstate == "PT401"
    with pytest.raises(psycopg.Error) as err, db.connection.transaction():
        _issue(db, POND_A)
    assert "revoked" in str(err.value)


def test_ingest_token_for_an_unknown_pond_cannot_be_issued(db):
    with pytest.raises(errors.ForeignKeyViolation):
        _issue(db, 9269)


@pytest.mark.parametrize("overlap", ["-1 hour", "31 days"])
def test_ingest_rotation_overlap_is_bounded(db, overlap):
    _pond(db, POND_A)
    with pytest.raises(errors.InvalidParameterValue):
        _issue(db, POND_A, overlap)


def test_ingest_rotation_keeps_the_old_token_for_the_overlap_only(db):
    _pond(db, POND_A)
    old = _issue(db, POND_A)
    new = _issue(db, POND_A, "1 hour")
    assert old != new
    db.execute("select expires_at - now() as left from public.device_tokens order by id")
    assert [r["left"] for r in db.fetchall()][0].total_seconds() == 3600
    # Both work during the overlap.
    assert _ingest(db, old, READING[:1])["accepted"] == 1
    assert _ingest(db, new, READING[1:2])["accepted"] == 1
    # After it, only the new one.
    db.execute("update public.device_tokens set expires_at = now() - interval '1 second' "
               "where expires_at is not null")
    assert _ingest_error(db, old, READING).sqlstate == "PT401"
    assert _ingest(db, new, READING[2:3])["accepted"] == 1
    # A rotation with no overlap ends the previous token at once.
    newest = _issue(db, POND_A, "0")
    assert _ingest_error(db, new, READING).sqlstate == "PT401"
    assert _ingest(db, newest, READING[3:])["accepted"] == 1


def test_ingest_token_belongs_to_one_pond(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    token_a, token_b = _issue(db, POND_A), _issue(db, POND_B)
    _ingest(db, token_a, READING[:1])
    _ingest(db, token_b, READING[1:2])
    assert [r[0] for r in _sensor_rows(db, POND_A)] == ["temp"]
    assert [r[0] for r in _sensor_rows(db, POND_B)] == ["TDS"]


# ---------------------------------------------------------------------
# ingest_readings: the batch
# ---------------------------------------------------------------------
@pytest.mark.parametrize("field", ["userID", "pond_id", "id", "created_at", "user_id"])
def test_ingest_forged_pond_or_other_fields_refuse_the_batch(db, field):
    _pond(db, POND_A)
    _pond(db, POND_B)
    token = _issue(db, POND_A)
    rows = [{"sensor_type": "temp", "data1": 27.0}, {"sensor_type": "pH", "data1": 7.0, field: POND_B}]
    err = _ingest_error(db, token, rows)
    assert err.sqlstate == "22023" and f'"{field}"' in str(err)
    assert _sensor_rows(db, POND_A) == [] and _sensor_rows(db, POND_B) == []


def test_ingest_oversized_batch_is_refused(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    assert _ingest_error(db, token, [{"sensor_type": "temp", "data1": 27.0}] * 33).sqlstate == "PT413"
    # 32 is the limit; a full batch of the same channel is accepted once.
    reply = _ingest(db, token, [{"sensor_type": "temp", "data1": 27.0}] * 32)
    assert reply["accepted"] == 1 and len(reply["rejected"]) == 31
    assert {r["reason"] for r in reply["rejected"]} == {"duplicate"}


@pytest.mark.parametrize("rows", [[], {"sensor_type": "temp", "data1": 27.0}, "temp", None, [["temp", 27.0]]])
def test_ingest_malformed_batch_is_refused(db, rows):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    assert _ingest_error(db, token, rows).sqlstate == "22023"


def test_ingest_invalid_samples_are_rejected_and_the_rest_stored(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    rows = [
        {"sensor_type": "temp", "data1": -127.0},       # DS18B20 disconnected
        {"sensor_type": "temp", "data1": 26.0},
        {"sensor_type": "LUX", "data1": -1.0},          # old node's lux failure value
        {"sensor_type": "pH", "data1": None},           # ArduinoJson writes NaN as null
        {"sensor_type": "TDS", "data1": "180"},
        {"sensor_type": "GY33_R", "data1": 12.0},
        {"sensor_type": 7, "data1": 12.0},
        {"data1": 1.0},
        {"sensor_type": "reset_reason", "data1": 1.5},
        {"sensor_type": "battery_mv", "data1": 12450},
        {"sensor_type": "reset_reason", "data1": 8},
    ]
    reply = _ingest(db, token, rows)
    assert reply["accepted"] == 3
    assert [(r["index"], r["reason"]) for r in reply["rejected"]] == [
        (0, "out_of_range"), (2, "out_of_range"), (3, "not_a_number"), (4, "not_a_number"),
        (5, "unknown_sensor_type"), (6, "unknown_sensor_type"), (7, "unknown_sensor_type"),
        (8, "out_of_range")]
    assert _sensor_rows(db, POND_A) == [("battery_mv", 12450.0, POND_A), ("reset_reason", 8.0, POND_A),
                                        ("temp", 26.0, POND_A)]


def test_ingest_number_too_large_for_the_column_is_out_of_range(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    reply = _as_role(db, "anon", "select public.ingest_readings(%s, %s::jsonb) as r",
                     (token, '[{"sensor_type": "TDS", "data1": 1e400}, {"sensor_type": "pH", "data1": 7}]'))[0]["r"]
    assert reply["accepted"] == 1
    assert reply["rejected"] == [{"index": 0, "sensor_type": "TDS", "reason": "out_of_range"}]


@pytest.mark.parametrize("sensor_type, low, high", [
    ("temp", -10, 60), ("pH", 0, 14), ("TDS", 0, 5000), ("LUX", 0, 200000),
    ("battery_mv", 0, 20000), ("reset_reason", 0, 255)])
def test_ingest_channel_ranges_are_inclusive(db, sensor_type, low, high):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    for value, ok in ((low, True), (high, True), (low - 1, False), (high + 1, False)):
        reply = _ingest(db, token, [{"sensor_type": sensor_type, "data1": value}])
        assert reply["accepted"] == (1 if ok else 0), (sensor_type, value)
        db.execute("update public.devices set ingest_window_batches = 0")


def test_ingest_rate_limit_per_device(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    for _ in range(6):
        _ingest(db, token, READING[:1])
    err = _ingest_error(db, token, READING[:1])
    assert err.sqlstate == "PT429"
    # Rotating the token does not reset the device's window.
    new = _issue(db, POND_A, "0")
    assert _ingest_error(db, new, READING[:1]).sqlstate == "PT429"
    # Once the window has passed, batches are accepted again.
    db.execute("update public.devices set ingest_window_started_at = now() - interval '61 seconds' "
               "where pond_id = %s", (POND_A,))
    assert _ingest(db, new, READING[:1])["accepted"] == 1
    db.execute("select count(*) as n from public.\"SensorData\" where \"userID\" = %s", (POND_A,))
    assert db.fetchone()["n"] == 7


def test_ingest_device_columns_keep_the_devices_readers_working(db):
    """0025 only adds columns: record_device_contacts and
    pond_device_activity still answer for a device that has a token."""
    _pond(db, POND_A)
    _issue(db, POND_A)
    db.execute("select public.record_device_contacts(%s, 'sensor', %s) as r",
               (POND_A, Jsonb([{"received_at": "2099-01-01T00:00:00Z"}])))
    row = db.fetchone()["r"]
    assert row["pond_id"] == POND_A and row["kind"] == "sensor" and row["device_id"]
    db.execute("select public.pond_device_activity(%s, now() - interval '1 day') as r", (POND_A,))
    assert [d["kind"] for d in db.fetchone()["r"]["devices"]] == ["sensor"]


def test_ingest_token_functions_are_owner_only(db):
    for fn in ("public.issue_device_token(bigint, interval, text)", "public.revoke_device_token(bigint)"):
        for role in ("anon", "authenticated", "service_role"):
            db.execute("select has_function_privilege(%s, %s, 'execute') as ok", (role, fn))
            assert db.fetchone()["ok"] is False, (role, fn)
    _pond(db, POND_A)
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, "anon", "select public.issue_device_token(%s)", (POND_A,))
    for role in ("anon", "authenticated"):
        with pytest.raises(errors.InsufficientPrivilege):
            _as_role(db, role, "select * from public.device_tokens")
        with pytest.raises(errors.InsufficientPrivilege):
            _as_role(db, role, "select * from public.devices")


def test_ingest_backend_role_may_call_ingest(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    assert _ingest(db, token, READING, role="service_role")["accepted"] == 4


# ---------------------------------------------------------------------
# The access matrix (docs/access-matrix.md)
# ---------------------------------------------------------------------
APP_TABLE_GRANTS = {
    "UserData": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "pondInterventions": {"SELECT", "INSERT"},
    "Fish_Database": {"SELECT"},
    "imageTable": {"SELECT"},
    "weather_telemetry": {"SELECT"},
    "weather_forecasts": {"SELECT"},
}


@pytest.mark.parametrize("role", APP_ROLES)
def test_matrix_app_role_table_grants_are_exactly_the_apps_calls(db, role):
    db.execute("select table_name, privilege_type from information_schema.role_table_grants "
               "where table_schema = 'public' and grantee = %s", (role,))
    grants: dict[str, set[str]] = {}
    for r in db.fetchall():
        grants.setdefault(r["table_name"], set()).add(r["privilege_type"])
    assert grants == APP_TABLE_GRANTS
    db.execute("select table_name from information_schema.role_table_grants "
               "where table_schema = 'public' and grantee = 'PUBLIC'")
    assert db.fetchall() == []


@pytest.mark.parametrize("role", APP_ROLES)
def test_matrix_app_role_sequence_and_function_grants(db, role):
    db.execute("select c.relname from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'S' "
               "and (has_sequence_privilege(%s, c.oid, 'usage') or has_sequence_privilege(%s, c.oid, 'select') "
               "or has_sequence_privilege(%s, c.oid, 'update')) order by 1", (role, role, role))
    assert [r["relname"] for r in db.fetchall()] == ["UserData_id_seq", "pondinterventions_id_seq"]
    db.execute("select p.proname from pg_proc p where p.pronamespace = 'public'::regnamespace "
               "and has_function_privilege(%s, p.oid, 'execute') order by 1", (role,))
    expected = ["get_historical_graph_payload"] + (["ingest_readings"] if role == "anon" else [])
    assert [r["proname"] for r in db.fetchall()] == expected


def test_matrix_no_public_function_is_executable_by_public(db):
    db.execute("select p.oid::regprocedure::text as fn from pg_proc p where p.pronamespace = 'public'::regnamespace "
               "and exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
               "where a.grantee = 0)")
    assert db.fetchall() == []


def test_matrix_sensordata_is_closed_to_the_app_roles(db):
    db.execute('insert into public."SensorData" (sensor_type, data1, "userID") values (\'pH\', 7, %s)', (POND_A,))
    for role in APP_ROLES:
        for sql in ('insert into public."SensorData" (sensor_type, data1, "userID") values (\'pH\', 7, 1)',
                    'update public."SensorData" set data1 = 0',
                    'delete from public."SensorData"',
                    'truncate public."SensorData"',
                    'select * from public."SensorData"'):
            with pytest.raises(errors.InsufficientPrivilege):
                _as_role(db, role, sql)
    db.execute('select count(*) as n from public."SensorData" where "userID" = %s', (POND_A,))
    assert db.fetchone()["n"] == 1


@pytest.mark.parametrize("call", [
    "select public.aggregate_daily_sensor_data(current_date, current_date)",
    "select public.reconcile_daily_sensor_averages(1, 7)",
    "select public.sensor_channel('pH')",
    "select * from public.resolve_closest_station(1.3, 103.8, 'AirTemp')",
    "select public.get_bundled_dashboard_payload(1)",
    "select * from public.get_pond_telemetry_history(1, 7)",
    "select public.take_worker_lease('poller', 'x', 60)",
    "select public.save_pond_snapshot(1, '{}'::jsonb, 0)",
    "select public.summarize_evaluation_days(current_date, 'Asia/Singapore', 1)",
    "select public.link_pond_to_account(1, gen_random_uuid())",
])
@pytest.mark.parametrize("role", APP_ROLES)
def test_matrix_internal_functions_are_refused_to_the_app_roles(db, role, call):
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, role, call)


@pytest.mark.parametrize("table", ["daily_sensor_averages", "pond_chemistry_state", "pond_chemistry_evaluations",
                                   "WeatherStationLookup", "worker_lease", "worker_status", "devices"])
def test_matrix_backend_tables_are_refused_to_the_app_roles(db, table):
    for role in APP_ROLES:
        with pytest.raises(errors.InsufficientPrivilege):
            _as_role(db, role, f'select * from public."{table}"')


@pytest.mark.parametrize("role", APP_ROLES)
def test_matrix_app_signed_out_calls_still_work(db, role):
    """Every Supabase call the app makes directly (the list #116 will put
    behind sign-in), run as the app's roles."""
    db.execute('insert into public."WeatherStationLookup" ("Id", station__id, station__name, location__latitude, '
               'location__longitude, "Measurement") values (99261, \'S_T\', \'Test station\', 1.35, 103.82, '
               '\'["AirTemp", "Rainfall", "Wind", "2HourForecast"]\')')
    db.execute('insert into public."Fish_Database" ("Title", "Common Name") values (\'Koi test\', \'Koi\')')
    db.execute('insert into public."imageTable" ("user_ID", green_ratio, current_state) values (%s, 0.2, \'base\')',
               (POND_A,))

    # Onboarding: upsert with no id (identity sequence) and with coordinates
    # (the station-lookup triggers read WeatherStationLookup as definer).
    rows = _as_role(db, role, 'insert into public."UserData" (volume, biomass, latitude, longitude) '
                              'values (1200, 2.5, 1.35, 103.82) returning "userID", "ClosestStations"')
    new_id = rows[0]["userID"]
    assert rows[0]["ClosestStations"]["air-temperature"] == "S_T"
    rows = _as_role(db, role, 'insert into public."UserData" ("userID", volume, biomass) values (%s, 900, 1.5) '
                              'on conflict ("userID") do update set volume = excluded.volume, '
                              'biomass = excluded.biomass returning *', (new_id,))
    assert rows[0]["volume"] == 900
    # Event log, species list, camera history, history graph.
    _as_role(db, role, 'insert into public."pondInterventions" ("userID", event_type, food_grams) '
                       "values (%s, 'FEED', 20)", (new_id,))
    rows = _as_role(db, role, 'select "Title" from public."Fish_Database" where "Title" = any(%s)', (["Koi test"],))
    assert rows == [{"Title": "Koi test"}]
    rows = _as_role(db, role, 'select id, created_at, green_ratio, current_state, "imageURL", quality, thumbnail_path '
                              'from public."imageTable" where "user_ID" = %s order by created_at desc limit 1',
                    (POND_A,))
    assert len(rows) == 1
    rows = _as_role(db, role, "select public.get_historical_graph_payload(%s, 7) as p", (new_id,))
    assert [i["event_type"] for i in rows[0]["p"]["interventions"]] == ["FEED"]
    _as_role(db, role, "select public.get_historical_graph_payload(%s, 30)", (new_id,))
    # Pond deletion. The onboarding write recorded a pond_profile row
    # (0008), whose reference does not cascade; removing it is not an app
    # call, so it is done as the owner first.
    db.execute("delete from public.pond_profile where pond_id = %s", (new_id,))
    _as_role(db, role, 'delete from public."UserData" where "userID" = %s', (new_id,))
    db.execute('select count(*) as n from public."UserData" where "userID" = %s', (new_id,))
    assert db.fetchone()["n"] == 0


@pytest.mark.parametrize("days", [0, -1, 367, None])
def test_matrix_history_graph_input_is_bounded(db, days):
    with pytest.raises(errors.InvalidParameterValue):
        _as_role(db, "anon", "select public.get_historical_graph_payload(%s, %s)", (POND_A, days))


@pytest.mark.parametrize("role", APP_ROLES)
def test_matrix_shared_weather_caches_are_readable(db, role):
    db.execute("insert into public.weather_forecasts (forecast_type, slot_id, data, valid_period) values "
               "('2hr', 'Koi Test Area', %s, %s)", (json.dumps({"forecast": "Showers"}), json.dumps({})))
    rows = _as_role(db, role, "select data from public.weather_forecasts where slot_id = 'Koi Test Area'")
    assert rows == [{"data": {"forecast": "Showers"}}]
    _as_role(db, role, "select count(*) from public.weather_telemetry")
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, role, "update public.weather_forecasts set data = '{}'::jsonb")


def test_matrix_backend_role_keeps_the_8_lease_and_snapshot_calls(db):
    rows = _as_role(db, "service_role", "select public.take_worker_lease('poller-test', 'w1', 60) as ok")
    assert rows[0]["ok"] is True
    db.execute("insert into public.pond_chemistry_state (user_id, snapshot) values (%s, %s)",
               (POND_A, json.dumps({"version": 1})))
    rows = _as_role(db, "service_role", "select public.save_pond_snapshot(%s, %s, 1) as v",
                    (POND_A, json.dumps({"version": 2})))
    assert rows[0]["v"] == 2
    rows = _as_role(db, "service_role", "select public.save_pond_snapshot_with_ingest(%s, %s, 2, '[]'::jsonb) as v",
                    (POND_A, json.dumps({"version": 2})))
    assert rows[0]["v"] is not None
    rows = _as_role(db, "service_role", "select public.get_bundled_dashboard_payload(%s) as p", (POND_A,))
    assert set(rows[0]["p"]) >= {"raw_sensor", "telemetry_history", "nea_telemetry", "nea_forecasts"}
    _as_role(db, "service_role", 'insert into public."imageTable" ("user_ID", green_ratio) values (%s, 0.1)', (POND_A,))
    _as_role(db, "service_role", 'insert into public."SensorData" (sensor_type, data1, "userID") values '
                                 "('pH', 7.1, %s)", (POND_A,))


def test_matrix_backend_role_reads_every_public_table(db):
    db.execute("select c.relname from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' "
               "and not has_table_privilege('service_role', c.oid, 'select')")
    assert db.fetchall() == []


# ---------------------------------------------------------------------
# Functions: search paths, the http extension
# ---------------------------------------------------------------------
def test_definer_and_app_callable_functions_have_a_fixed_search_path(db):
    db.execute("select p.oid::regprocedure::text as fn, p.proconfig from pg_proc p "
               "where p.pronamespace = 'public'::regnamespace and (p.prosecdef "
               "or has_function_privilege('anon', p.oid, 'execute') "
               "or has_function_privilege('authenticated', p.oid, 'execute'))")
    rows = db.fetchall()
    assert rows, "no definer functions found"
    for r in rows:
        assert r["proconfig"] and 'search_path=""' in r["proconfig"], r["fn"]


def test_dashboard_reads_are_now_security_invoker(db):
    db.execute("select p.proname, p.prosecdef from pg_proc p where p.pronamespace = 'public'::regnamespace "
               "and p.proname in ('get_bundled_dashboard_payload', 'get_pond_telemetry_history', "
               "'get_historical_graph_payload', 'ingest_readings') order by 1")
    assert {r["proname"]: r["prosecdef"] for r in db.fetchall()} == {
        "get_bundled_dashboard_payload": False, "get_historical_graph_payload": True,
        "get_pond_telemetry_history": False, "ingest_readings": True}


def test_http_extension_is_out_of_the_api_schemas(db):
    db.execute("select n.nspname from pg_extension e join pg_namespace n on n.oid = e.extnamespace "
               "where e.extname = 'http'")
    assert db.fetchone()["nspname"] == "extensions"
    # graphql_public holds only the platform's graphql() endpoint.
    db.execute("select p.oid::regprocedure::text as fn from pg_proc p "
               "where p.pronamespace = 'public'::regnamespace and pg_get_userbyid(p.proowner) <> 'postgres'")
    assert db.fetchall() == [], "functions in public owned by another role (an extension)"
    # The postal-code trigger still finds it there (not called: no network).
    db.execute("select prosrc from pg_proc where oid = 'public.process_user_location_and_stations()'::regprocedure")
    assert "extensions.http_get(" in db.fetchone()["prosrc"]
    db.execute("select has_function_privilege('postgres', 'extensions.http_get(character varying)', 'execute') as ok")
    assert db.fetchone()["ok"]


# ---------------------------------------------------------------------
# Objects created later
# ---------------------------------------------------------------------
def test_new_objects_from_the_migration_creator_grant_nothing_to_the_app(db):
    db.execute("create table public.koi_test_future (id bigint generated by default as identity primary key)")
    db.execute("create function public.koi_test_future_fn() returns int language sql as 'select 1'")
    db.execute("create schema koi_test_other")
    db.execute("create function koi_test_other.f() returns int language sql as 'select 1'")
    for role in APP_ROLES:
        db.execute("select has_table_privilege(%s, 'public.koi_test_future', 'select') as t, "
                   "has_sequence_privilege(%s, 'public.koi_test_future_id_seq', 'usage') as s, "
                   "has_function_privilege(%s, 'public.koi_test_future_fn()', 'execute') as f",
                   (role, role, role))
        assert db.fetchone() == {"t": False, "s": False, "f": False}, role
    db.execute("select has_table_privilege('service_role', 'public.koi_test_future', 'select') as t, "
               "has_sequence_privilege('service_role', 'public.koi_test_future_id_seq', 'usage') as s, "
               "has_function_privilege('service_role', 'public.koi_test_future_fn()', 'execute') as f")
    assert db.fetchone() == {"t": True, "s": True, "f": True}
    # No PUBLIC execute on a new function in any schema.
    db.execute("select p.proname from pg_proc p where p.proname in ('koi_test_future_fn', 'f') "
               "and p.pronamespace in ('public'::regnamespace, 'koi_test_other'::regnamespace) "
               "and exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
               "where a.grantee = 0)")
    assert db.fetchall() == []
    with pytest.raises(errors.InsufficientPrivilege):
        _as_role(db, "anon", "select public.koi_test_future_fn()")
