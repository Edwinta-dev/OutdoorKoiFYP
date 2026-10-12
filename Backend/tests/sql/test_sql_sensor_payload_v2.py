"""SQL tests for migration 0027_sensor_payload_v2.sql (issue #17, and the
database half of #97): the payload v2 columns, ingest_readings' v2 rows
and retry key, the canonical channels, the bounded daily aggregation and
reconciliation, and the readers' time basis and provenance.

The acceptance cases are each named after what they cover: an old row, a
true sample delayed by several hours, equal-time channels, a repeated
upload, a reboot, a missing channel, two ponds and local midnight.

Local Supabase stack only; skipped when no local database answers, as in
CI. Each case runs in a rolled-back transaction. Inside one transaction
now() does not move, so every ingest_readings call in a test stores the
same created_at; rows that need another insert time are written directly
as the owner.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from koi.models.local_time import zone  # noqa: E402
from koi.models.sensor_inputs import SENSOR_TYPES, group_rows  # noqa: E402
from koi.storage.supabase_storage import SupabaseStorage  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND_A, POND_B = 9271, 9272
SGT = zone("Asia/Singapore")
BOOT_1, BOOT_2 = "0000000100a1b2c3", "0000000200d4e5f6"


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
        cur.execute("select to_regclass('public.daily_sensor_refresh') is not null as ok")
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


@pytest.fixture(params=["UTC", "Asia/Singapore"])
def session_zone(request: Any, db: Any) -> str:
    db.execute("select set_config('TimeZone', %s, true)", (request.param,))
    return request.param


def _pond(db: Any, pond: int) -> None:
    # No coordinates or postal code, so the station-lookup triggers do nothing.
    db.execute('insert into public."UserData" ("userID", volume, biomass) values (%s, 2500, 55)', (pond,))


def _issue(db: Any, pond: int) -> str:
    db.execute("select public.issue_device_token(%s) as t", (pond,))
    return db.fetchone()["t"]


def _as_role(db: Any, role: str, sql: str, params: tuple = ()) -> list[dict]:
    with db.connection.transaction():
        db.execute(f"set local role {role}")
        db.execute(sql, params)
        rows = db.fetchall() if db.description else []
        db.execute("reset role")
    return rows


def _ingest(db: Any, token: str, rows: Any) -> dict:
    """The node's call, run as anon as PostgREST does with the publishable key."""
    return _as_role(db, "anon", "select public.ingest_readings(%s, %s) as r", (token, Jsonb(rows)))[0]["r"]


def _now(db: Any) -> datetime:
    db.execute("select now() as t")
    return db.fetchone()["t"]


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _v2(sensor: str, value: float, boot: str, seq: int, sampled: datetime | None, flags: int = 0,
        **extra: Any) -> dict:
    row = {"sensor_type": sensor, "data1": value, "boot_id": boot, "cycle_seq": seq, "flags": flags,
           "reset_reason": "deep_sleep_wake", **extra}
    if sampled is not None:
        row["sampled_at"] = _iso(sampled)
    return row


def _row(db: Any, pond: int | None, sensor: str, value: float, created: datetime,
         sampled: datetime | None = None) -> int:
    """A row written directly (as the owner), for a chosen insert time."""
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID", sampled_at) '
               "values (%s, %s, %s, %s, %s) returning id", (created, sensor, value, pond, sampled))
    return db.fetchone()["id"]


def _stored(db: Any, pond: int) -> list[dict]:
    db.execute('select id, sensor_type, data1, boot_id, cycle_seq, sampled_at, flags, reset_reason, battery_mv, '
               'device_id, created_at from public."SensorData" where "userID" = %s order by id', (pond,))
    return db.fetchall()


def _daily(db: Any, pond: int) -> dict[tuple[str, str], dict]:
    db.execute("select sensor_type, record_date::text as d, avg_value, sample_count, sample_time_count "
               "from public.daily_sensor_averages where userid = %s", (pond,))
    return {(r["sensor_type"], r["d"]): r for r in db.fetchall()}


def _history(db: Any, pond: int, days: int = 7) -> list[dict]:
    db.execute("select * from public.get_pond_telemetry_history(%s, %s)", (pond, days))
    return db.fetchall()


def _bundled(db: Any, pond: int) -> dict:
    db.execute("select public.get_bundled_dashboard_payload(%s) as p", (pond,))
    return db.fetchone()["p"]


def _graph(db: Any, pond: int, days: int = 30) -> dict:
    return _as_role(db, "anon", "select public.get_historical_graph_payload(%s, %s) as p", (pond, days))[0]["p"]


def _storage(db: Any) -> SupabaseStorage:
    from test_sql_sensor_ingest import _storage as storage

    return storage(db)


# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------
def test_v2_columns_are_nullable_and_old_rows_load(db):
    db.execute("select column_name, is_nullable, data_type from information_schema.columns "
               "where table_schema = 'public' and table_name = 'SensorData' "
               "and column_name in ('device_id', 'boot_id', 'cycle_seq', 'sampled_at', 'flags', "
               "'reset_reason', 'battery_mv')")
    cols = {r["column_name"]: (r["is_nullable"], r["data_type"]) for r in db.fetchall()}
    assert cols == {"device_id": ("YES", "uuid"), "boot_id": ("YES", "text"), "cycle_seq": ("YES", "bigint"),
                    "sampled_at": ("YES", "timestamp with time zone"), "flags": ("YES", "smallint"),
                    "reset_reason": ("YES", "text"), "battery_mv": ("YES", "integer")}
    # The pre-v2 insert shape still works and reads as unknown.
    _pond(db, POND_A)
    db.execute('insert into public."SensorData" (sensor_type, data1, "userID") values (\'pH\', 7.1, %s) '
               "returning sampled_at, flags, boot_id", (POND_A,))
    assert db.fetchone() == {"sampled_at": None, "flags": None, "boot_id": None}


def test_v2_indexes_exist(db):
    db.execute("select indexname, indexdef from pg_indexes where schemaname = 'public' and tablename = 'SensorData'")
    defs = {r["indexname"]: r["indexdef"] for r in db.fetchall()}
    assert "UNIQUE" in defs["sensordata_cycle_key"]
    assert "(device_id, boot_id, cycle_seq, sensor_type) WHERE (boot_id IS NOT NULL)" in defs["sensordata_cycle_key"]
    assert '("userID", created_at, id)' in defs["idx_sensordata_pond_created"]
    assert "COALESCE(sampled_at, created_at)" in defs["idx_sensordata_pond_effective"]
    assert "COALESCE(sampled_at, created_at) DESC, id DESC" in defs["idx_sensordata_pond_type_effective"]


def test_cycle_key_constraints(db):
    _pond(db, POND_A)
    for bad in ("insert into public.\"SensorData\" (sensor_type, data1, \"userID\", boot_id) "
                "values ('pH', 7, %s, '0000000100a1b2c3')",
                "insert into public.\"SensorData\" (sensor_type, data1, \"userID\", boot_id, cycle_seq) "
                "values ('pH', 7, %s, 'ABC', 1)",
                "insert into public.\"SensorData\" (sensor_type, data1, \"userID\", flags) values ('pH', 7, %s, 32)"):
        with pytest.raises(errors.CheckViolation), db.connection.transaction():
            db.execute(bad, (POND_A,))


# ---------------------------------------------------------------------
# Canonical channels
# ---------------------------------------------------------------------
def test_sensor_channel_maps_only_the_observed_spellings(db):
    db.execute("select s, public.sensor_channel(s) as c from unnest(array['pH', 'TDS', 'temp', 'LUX', 'lux', "
               "'ph', 'tds', 'Temp', 'battery_mv', 'reset_reason', '']) s")
    assert {r["s"]: r["c"] for r in db.fetchall()} == {
        "pH": "pH", "TDS": "TDS", "temp": "temp", "LUX": "LUX", "lux": "LUX",
        "ph": None, "tds": None, "Temp": None, "battery_mv": None, "reset_reason": None, "": None}


def test_ingest_refuses_case_variants_instead_of_creating_channels(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    reply = _ingest(db, token, [{"sensor_type": "ph", "data1": 7.0}, {"sensor_type": "Temp", "data1": 27.0},
                                {"sensor_type": "lux", "data1": 900}, {"sensor_type": "pH", "data1": 7.0}])
    assert reply["accepted"] == 1
    assert [(r["sensor_type"], r["reason"]) for r in reply["rejected"]] == [
        ("ph", "unknown_sensor_type"), ("Temp", "unknown_sensor_type"), ("lux", "unknown_sensor_type")]


# ---------------------------------------------------------------------
# ingest_readings v2
# ---------------------------------------------------------------------
def test_ingest_v2_stores_the_cycle_with_server_derived_pond_and_device(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    now = _now(db)
    sampled = now - timedelta(seconds=20)
    reply = _ingest(db, token, [_v2("temp", 27.5, BOOT_1, 1, sampled, battery_mv=3900),
                                _v2("pH", 7.2, BOOT_1, 1, sampled, flags=4, battery_mv=3900)])
    assert reply["accepted"] == 2 and reply["stored"] == 2 and reply["already_stored"] == 0
    assert reply["rejected"] == [] and reply["format"] == "v2"
    db.execute("select device_id from public.devices where pond_id = %s and kind = 'sensor'", (POND_A,))
    device = db.fetchone()["device_id"]
    rows = _stored(db, POND_A)
    assert [(r["sensor_type"], r["boot_id"], r["cycle_seq"], r["flags"], r["reset_reason"], r["battery_mv"])
            for r in rows] == [("temp", BOOT_1, 1, 0, "deep_sleep_wake", 3900),
                               ("pH", BOOT_1, 1, 4, "deep_sleep_wake", 3900)]
    assert all(r["device_id"] == device for r in rows)
    assert all(abs((r["sampled_at"] - sampled).total_seconds()) < 0.001 for r in rows)
    assert all(r["created_at"] == now for r in rows)


def test_ingest_refuses_spoofed_ids(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    token = _issue(db, POND_A)
    for field, value in (("userID", POND_B), ("device_id", "00000000-0000-0000-0000-000000000000"),
                         ("pond_id", POND_B), ("created_at", "2026-01-01T00:00:00Z")):
        with pytest.raises(errors.InvalidParameterValue):
            _ingest(db, token, [{"sensor_type": "pH", "data1": 7.0, field: value}])
    assert _stored(db, POND_A) == [] and _stored(db, POND_B) == []


@pytest.mark.parametrize("extra, reason", [
    ({"boot_id": BOOT_1}, "invalid_cycle"),
    ({"cycle_seq": 3}, "invalid_cycle"),
    ({"boot_id": "0000000100A1B2C3", "cycle_seq": 3}, "invalid_cycle"),
    ({"boot_id": "short", "cycle_seq": 3}, "invalid_cycle"),
    ({"boot_id": BOOT_1, "cycle_seq": -1}, "invalid_cycle"),
    ({"boot_id": BOOT_1, "cycle_seq": 1.5}, "invalid_cycle"),
    ({"boot_id": BOOT_1, "cycle_seq": 1e30}, "invalid_cycle"),
    ({"boot_id": BOOT_1, "cycle_seq": "7"}, "invalid_cycle"),
    ({"flags": 32}, "invalid_flags"),   # 16 (battery_low) is valid since 0028
    ({"flags": -1}, "invalid_flags"),
    ({"flags": 2.5}, "invalid_flags"),
    ({"flags": None}, "invalid_flags"),
    ({"reset_reason": "POWERON"}, "invalid_reset_reason"),
    ({"reset_reason": 1}, "invalid_reset_reason"),
    ({"battery_mv": 20001}, "invalid_battery_mv"),
    ({"battery_mv": 3.3}, "invalid_battery_mv"),
    ({"sampled_at": "2026-10-11 08:00:00"}, "invalid_sampled_at"),
    ({"sampled_at": "2026-10-11T08:00:00"}, "invalid_sampled_at"),
    ({"sampled_at": "2026-02-30T08:00:00Z"}, "invalid_sampled_at"),
    ({"sampled_at": 1791705600}, "invalid_sampled_at"),
    ({"sampled_at": "1970-01-01T00:00:00Z"}, "sampled_at_too_old"),
])
def test_ingest_rejects_invalid_v2_fields_row_by_row(db, extra, reason):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    reply = _ingest(db, token, [{"sensor_type": "pH", "data1": 7.0, **extra}, {"sensor_type": "TDS", "data1": 180}])
    assert reply["accepted"] == 1
    assert reply["rejected"] == [{"index": 0, "sensor_type": "pH", "reason": reason}]
    assert [r["sensor_type"] for r in _stored(db, POND_A)] == ["TDS"]


def test_ingest_rejects_future_and_unsynced_sample_times(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    now = _now(db)
    reply = _ingest(db, token, [
        _v2("pH", 7.0, BOOT_1, 1, now + timedelta(minutes=6)),
        _v2("TDS", 180, BOOT_1, 1, now, flags=1),          # time_unsynced but a time sent
        _v2("temp", 27.0, BOOT_1, 1, now + timedelta(minutes=4)),
        _v2("LUX", 900, BOOT_1, 1, now - timedelta(days=29))])
    assert [(r["index"], r["reason"]) for r in reply["rejected"]] == [
        (0, "sampled_at_in_future"), (1, "invalid_sampled_at")]
    assert reply["accepted"] == 2


def test_ingest_never_substitutes_receipt_time_for_a_missing_sample_time(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    reply = _ingest(db, token, [_v2("pH", 7.0, BOOT_1, 1, None, flags=1)])
    assert reply["accepted"] == 1
    [row] = _stored(db, POND_A)
    assert row["sampled_at"] is None and row["flags"] == 1


def test_ingest_duplicate_key_in_one_batch(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    now = _now(db)
    reply = _ingest(db, token, [_v2("pH", 7.0, BOOT_1, 1, now), _v2("pH", 7.1, BOOT_1, 1, now),
                                _v2("pH", 7.2, BOOT_1, 2, now)])
    assert reply["accepted"] == 2
    assert reply["rejected"] == [{"index": 1, "sensor_type": "pH", "reason": "duplicate"}]


def test_repeated_upload_is_stored_once(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    now = _now(db)
    cycle = [_v2("temp", 27.0, BOOT_1, 5, now), _v2("pH", 7.0, BOOT_1, 5, now)]
    first = _ingest(db, token, cycle)
    # The node did not see the reply and re-sends the cycle with the next one.
    second = _ingest(db, token, cycle + [_v2("pH", 7.1, BOOT_1, 6, now + timedelta(seconds=15))])
    assert (first["stored"], first["already_stored"]) == (2, 0)
    assert (second["accepted"], second["stored"], second["already_stored"]) == (3, 1, 2)
    assert [(r["sensor_type"], r["cycle_seq"]) for r in _stored(db, POND_A)] == [
        ("temp", 5), ("pH", 5), ("pH", 6)]


def test_independent_cycles_with_equal_sample_times_are_both_stored(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    t = _now(db) - timedelta(minutes=1)
    reply = _ingest(db, token, [_v2("pH", 7.0, BOOT_1, 1, t), _v2("pH", 7.3, BOOT_1, 2, t)])
    assert reply["stored"] == 2


def test_reboot_restarts_cycle_seq_without_colliding(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    t = _now(db) - timedelta(minutes=1)
    _ingest(db, token, [_v2("pH", 7.0, BOOT_1, 1, t)])
    # Power loss: RTC memory gone, cycle_seq starts again at 1 with a new
    # boot_id, and the clock was not set yet.
    reply = _ingest(db, token, [_v2("pH", 7.1, BOOT_2, 1, None, flags=1, reset_reason="power_on")])
    assert reply["stored"] == 1 and reply["already_stored"] == 0
    rows = _stored(db, POND_A)
    assert [(r["boot_id"], r["cycle_seq"], r["reset_reason"]) for r in rows] == [
        (BOOT_1, 1, "deep_sleep_wake"), (BOOT_2, 1, "power_on")]


def test_legacy_rows_have_no_retry_key_and_are_never_deduplicated(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    v1 = [{"sensor_type": "pH", "data1": 7.0}]
    assert _ingest(db, token, v1)["format"] == "v1"
    assert _ingest(db, token, v1)["stored"] == 1
    mixed = _ingest(db, token, v1 + [_v2("TDS", 180, BOOT_1, 1, None)])
    assert mixed["format"] == "mixed"
    assert [r["sensor_type"] for r in _stored(db, POND_A)] == ["pH", "pH", "pH", "TDS"]


def test_the_same_cycle_key_on_two_devices_is_two_readings(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    t = _now(db)
    a = _ingest(db, _issue(db, POND_A), [_v2("pH", 7.0, BOOT_1, 1, t)])
    b = _ingest(db, _issue(db, POND_B), [_v2("pH", 7.4, BOOT_1, 1, t)])
    assert a["stored"] == 1 and b["stored"] == 1
    assert [r["data1"] for r in _stored(db, POND_A)] == [pytest.approx(7.0)]
    assert [r["data1"] for r in _stored(db, POND_B)] == [pytest.approx(7.4)]


# ---------------------------------------------------------------------
# Readers: the acceptance cases
# ---------------------------------------------------------------------
def test_old_row_reads_with_ingestion_basis_everywhere(db, session_zone):
    _pond(db, POND_A)
    now = _now(db)
    created = now - timedelta(hours=2)
    for sensor, value in (("pH", 7.1), ("TDS", 190.0), ("temp", 28.0), ("LUX", 800.0)):
        _row(db, POND_A, sensor, value, created)
    [hour] = _history(db, POND_A)
    assert hour["time_basis"] == "ingestion"
    assert hour["bucket_time"] == created.replace(minute=0, second=0, microsecond=0)
    payload = _bundled(db, POND_A)
    assert payload["latest_readings"]["pH"]["time_basis"] == "ingestion"
    assert payload["latest_readings"]["pH"]["sampled_at"] is None
    assert payload["latest_readings"]["pH"]["flags"] is None
    assert payload["telemetry_history"][0]["time_basis"] == "ingestion"
    graph = _graph(db, POND_A, 7)
    days = {d["sensor_type"]: d for d in graph["daily_trends"]}
    assert set(days) == {"pH", "TDS", "temp", "LUX"}
    assert all(d["time_basis"] == "ingestion" and d["sample_count"] == 1 for d in days.values())


def test_delayed_sample_is_timed_by_its_sample_not_its_upload(db, session_zone):
    _pond(db, POND_A)
    now = _now(db)
    sampled = now - timedelta(hours=5)
    _row(db, POND_A, "pH", 7.0, now, sampled)       # uploaded now, sampled 5 hours ago
    _row(db, POND_A, "TDS", 200.0, now, sampled)
    _row(db, POND_A, "pH", 7.6, now - timedelta(hours=1))  # an old-firmware row, stored an hour ago
    _row(db, POND_A, "TDS", 210.0, now - timedelta(hours=1))
    hours = {h["bucket_time"]: h for h in _history(db, POND_A)}
    sample_hour = sampled.replace(minute=0, second=0, microsecond=0)
    assert float(hours[sample_hour]["ph"]) == pytest.approx(7.0)
    assert hours[sample_hour]["time_basis"] == "sample"
    assert now.replace(minute=0, second=0, microsecond=0) not in hours or \
        float(hours[now.replace(minute=0, second=0, microsecond=0)]["ph"]) != pytest.approx(7.0)
    # The late upload is not the latest reading: the row stored an hour ago
    # describes the pond more recently.
    latest = _bundled(db, POND_A)["latest_readings"]["pH"]
    assert latest["value"] == pytest.approx(7.6) and latest["time_basis"] == "ingestion"
    assert _bundled(db, POND_A)["raw_sensor"]["pH"] == pytest.approx(7.6)
    db.execute("select public.pond_dashboard_sources(%s) as p", (POND_A,))
    readings = {r["sensor_type"]: r for r in db.fetchone()["p"]["readings"]}
    assert readings["pH"]["value"] == pytest.approx(7.6) and readings["pH"]["sampled_at"] is None


def test_newer_sample_wins_and_reports_its_own_time_and_flags(db):
    _pond(db, POND_A)
    now = _now(db)
    _row(db, POND_A, "pH", 7.6, now - timedelta(hours=1))
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID", sampled_at, flags) '
               "values (%s, 'pH', 7.2, %s, %s, 4) returning id", (now, POND_A, now - timedelta(minutes=10)))
    newest = db.fetchone()["id"]
    latest = _bundled(db, POND_A)["latest_readings"]["pH"]
    assert latest["id"] == newest and latest["value"] == pytest.approx(7.2)
    assert latest["time_basis"] == "sample" and latest["flags"] == 4
    assert latest["time"] == latest["sampled_at"] != latest["ingested_at"]


def test_equal_time_channels_share_one_hour_and_one_input(db):
    _pond(db, POND_A)
    now = _now(db)
    sampled = now - timedelta(minutes=30)
    ids = [_row(db, POND_A, s, v, now, sampled) for s, v in (("pH", 7.2), ("TDS", 190.0), ("temp", 28.5),
                                                             ("LUX", 900.0))]
    [hour] = [h for h in _history(db, POND_A) if h["time_basis"] == "sample"]
    assert (float(hour["ph"]), float(hour["tds"]), float(hour["temp_c"]), float(hour["lux"])) == pytest.approx(
        (7.2, 190.0, 28.5, 900.0))
    # The poller reads sampled_at and groups the four rows into one input
    # timed by the sample.
    found = _storage(db).fetch_pending_sensor_rows(POND_A, SENSOR_TYPES, None, None, 3600, 100)
    assert [r["id"] for r in found["rows"]] == ids
    [only] = group_rows(found["rows"]).inputs
    assert only.time_basis == "sample" and only.time == sampled
    assert only.channels == pytest.approx({"ph": 7.2, "tds": 190.0, "temp": 28.5, "lux": 900.0})


def test_equal_ingestion_times_tie_break_by_id(db):
    _pond(db, POND_A)
    now = _now(db)
    _row(db, POND_A, "pH", 7.0, now)
    last = _row(db, POND_A, "pH", 7.4, now)
    latest = _bundled(db, POND_A)["latest_readings"]["pH"]
    assert latest["id"] == last


def test_missing_channel_stays_null(db):
    _pond(db, POND_A)
    now = _now(db)
    sampled = now - timedelta(minutes=30)
    for sensor, value in (("pH", 7.1), ("TDS", 185.0)):   # temperature and light failed: omitted
        _row(db, POND_A, sensor, value, now, sampled)
    [hour] = _history(db, POND_A)
    assert hour["temp_c"] is None and hour["lux"] is None
    payload = _bundled(db, POND_A)
    assert set(payload["latest_readings"]) == {"pH", "TDS"}
    graph = _graph(db, POND_A, 7)
    assert {d["sensor_type"] for d in graph["daily_trends"]} == {"pH", "TDS"}


def test_two_ponds_never_mix_and_graph_aggregates_only_the_requested_pond(db):
    _pond(db, POND_A)
    _pond(db, POND_B)
    now = _now(db)
    t = now - timedelta(hours=1)
    for pond, ph in ((POND_A, 7.0), (POND_B, 8.0), (None, 6.0)):   # None: a row with no pond (#97)
        _row(db, pond, "pH", ph, t)
        _row(db, pond, "TDS", 180.0, t)
    graph = _graph(db, POND_A, 7)
    assert {d["sensor_type"]: float(d["avg_value"]) for d in graph["daily_trends"]} == {
        "TDS": pytest.approx(180.0), "pH": pytest.approx(7.0)}
    assert _daily(db, POND_B) == {}, "pond B's aggregates are not touched by pond A's graph"
    graph_b = _graph(db, POND_B, 7)
    assert [float(d["avg_value"]) for d in graph_b["daily_trends"] if d["sensor_type"] == "pH"] == [
        pytest.approx(8.0)]
    [hour_a] = _history(db, POND_A)
    [hour_b] = _history(db, POND_B)
    assert (float(hour_a["ph"]), float(hour_b["ph"])) == pytest.approx((7.0, 8.0))
    assert _bundled(db, POND_A)["latest_readings"]["pH"]["value"] == pytest.approx(7.0)


def test_pondless_row_no_longer_empties_every_graph(db):
    # #97: the old aggregate inserted userid null and failed the whole call.
    _pond(db, POND_A)
    now = _now(db)
    _row(db, None, "pH", 6.5, now - timedelta(hours=2))
    _row(db, POND_A, "pH", 7.0, now - timedelta(hours=2))
    graph = _graph(db, POND_A, 30)
    assert [d["sensor_type"] for d in graph["daily_trends"]] == ["pH"]
    db.execute("select count(*) as n from public.daily_sensor_averages where userid is null")
    assert db.fetchone()["n"] == 0


def test_local_midnight_splits_days_by_sample_time(db, session_zone):
    _pond(db, POND_A)
    before = datetime(2026, 9, 30, 23, 59, tzinfo=SGT)
    after = datetime(2026, 10, 1, 0, 1, tzinfo=SGT)
    uploaded = datetime(2026, 10, 1, 9, 0, tzinfo=SGT)   # both uploaded together, the next morning
    _row(db, POND_A, "pH", 7.0, uploaded, before)
    _row(db, POND_A, "pH", 8.0, uploaded, after)
    # An old-firmware row stored at 23:59 belongs to 30 September.
    _row(db, POND_A, "TDS", 180.0, before)
    db.execute("select public.aggregate_daily_sensor_data('2026-09-30', '2026-10-01', %s) as n", (POND_A,))
    assert db.fetchone()["n"] == 3
    daily = _daily(db, POND_A)
    assert float(daily[("pH", "2026-09-30")]["avg_value"]) == pytest.approx(7.0)
    assert float(daily[("pH", "2026-10-01")]["avg_value"]) == pytest.approx(8.0)
    assert daily[("pH", "2026-10-01")]["sample_time_count"] == 1
    assert daily[("TDS", "2026-09-30")]["sample_time_count"] == 0
    assert ("TDS", "2026-10-01") not in daily


def test_hour_buckets_are_utc_instants_of_the_sample(db, session_zone):
    _pond(db, POND_A)
    now = _now(db)
    sampled = (now - timedelta(hours=3)).replace(minute=59, second=30, microsecond=0)
    _row(db, POND_A, "pH", 7.0, now, sampled)
    _row(db, POND_A, "TDS", 180.0, now, sampled + timedelta(seconds=40))   # next hour
    _row(db, POND_A, "TDS", 181.0, now, sampled)
    [hour] = _history(db, POND_A)
    assert hour["bucket_time"] == sampled.replace(minute=0, second=0)
    assert hour["bucket_time"].utcoffset() is not None


def test_mixed_hour_reports_mixed(db):
    _pond(db, POND_A)
    now = _now(db)
    t = (now - timedelta(hours=2)).replace(minute=10, second=0, microsecond=0)
    _row(db, POND_A, "pH", 7.0, t)                                  # legacy
    _row(db, POND_A, "TDS", 180.0, now, t + timedelta(minutes=5))   # v2
    [hour] = _history(db, POND_A)
    assert hour["time_basis"] == "mixed"


# ---------------------------------------------------------------------
# Daily aggregation and reconciliation
# ---------------------------------------------------------------------
def test_aggregate_merges_spellings_and_rewrites_old_daily_rows(db):
    _pond(db, POND_A)
    day = datetime(2026, 9, 10, 12, 0, tzinfo=SGT)
    _row(db, POND_A, "LUX", 900.0, day)
    _row(db, POND_A, "lux", 1100.0, day + timedelta(minutes=1))
    _row(db, POND_A, "ph", 6.0, day)                 # not a canonical spelling: not aggregated
    _row(db, POND_A, "battery_mv", 3900.0, day)
    # Daily rows written by the old function: by spelling, with no counts.
    for sensor in ("lux", "LUX", "battery_mv"):
        db.execute("insert into public.daily_sensor_averages (userid, sensor_type, avg_value, min_value, "
                   "max_value, record_date) values (%s, %s, 1, 1, 1, '2026-09-10')", (POND_A, sensor))
    db.execute("insert into public.daily_sensor_averages (userid, sensor_type, avg_value, min_value, "
               "max_value, record_date) values (%s, 'pH', 7, 7, 7, '2026-09-01')", (POND_A,))
    db.execute("select public.aggregate_daily_sensor_data('2026-09-01', '2026-09-30', %s)", (POND_A,))
    daily = _daily(db, POND_A)
    assert set(daily) == {("LUX", "2026-09-10"), ("pH", "2026-09-01")}
    assert float(daily[("LUX", "2026-09-10")]["avg_value"]) == pytest.approx(1000.0)
    assert daily[("LUX", "2026-09-10")]["sample_count"] == 2
    # A day with no readings keeps its row.
    assert daily[("pH", "2026-09-01")]["sample_count"] is None


def test_aggregate_is_bounded(db):
    for args in (("2026-01-01", "2027-01-02"), ("2026-02-01", "2026-01-01"), (None, "2026-01-01")):
        with pytest.raises(errors.InvalidParameterValue), db.connection.transaction():
            db.execute("select public.aggregate_daily_sensor_data(%s::date, %s::date)", args)
    db.execute("select public.aggregate_daily_sensor_data('2026-01-01', '2027-01-01') as n")
    assert db.fetchone()["n"] >= 0
    for days in (0, 367, None):
        with pytest.raises(errors.InvalidParameterValue), db.connection.transaction():
            db.execute("select public.reconcile_daily_sensor_averages(1, %s)", (days,))


def test_reconcile_recomputes_only_new_and_late_days(db):
    _pond(db, POND_A)
    now = _now(db)
    today = now.astimezone(SGT).date()
    _row(db, POND_A, "pH", 7.0, now - timedelta(days=3))
    db.execute("select public.reconcile_daily_sensor_averages(%s, 7) as r", (POND_A,))
    first = db.fetchone()["r"]
    assert first["days_recomputed"] == 8 and first["from"] == (today - timedelta(days=7)).isoformat()
    db.execute("select covered_from, refreshed_at from public.daily_sensor_refresh where pond_id = %s", (POND_A,))
    state = db.fetchone()
    assert state["covered_from"] == today - timedelta(days=7) and state["refreshed_at"] == now

    # Pretend that run was an hour ago; since then a cycle sampled 5 days
    # ago arrived (a late upload). Only that day is recomputed.
    db.execute("update public.daily_sensor_refresh set refreshed_at = now() - interval '1 hour' "
               "where pond_id = %s", (POND_A,))
    _row(db, POND_A, "pH", 6.0, now, now - timedelta(days=5))
    _row(db, POND_A, "pH", 6.5, now - timedelta(hours=3))   # stored before the last run: already counted
    db.execute("select public.reconcile_daily_sensor_averages(%s, 7) as r", (POND_A,))
    second = db.fetchone()["r"]
    assert second["days_recomputed"] == 1
    late_day = (now - timedelta(days=5)).astimezone(SGT).date().isoformat()
    assert float(_daily(db, POND_A)[("pH", late_day)]["avg_value"]) == pytest.approx(6.0)

    # A longer window recomputes the days before covered_from once.
    db.execute("select public.reconcile_daily_sensor_averages(%s, 30) as r", (POND_A,))
    assert db.fetchone()["r"]["days_recomputed"] == 23 + 1   # 23 new days, plus the 6.0 row's day again


def test_graph_payload_reports_sample_count_and_time_basis(db):
    _pond(db, POND_A)
    now = _now(db)
    t = now - timedelta(hours=1)
    _row(db, POND_A, "pH", 7.0, t)
    _row(db, POND_A, "pH", 7.2, now, t)
    graph = _graph(db, POND_A, 7)
    [day] = [d for d in graph["daily_trends"] if d["sensor_type"] == "pH" and d["date"] == t.astimezone(SGT).date()
             .isoformat()]
    assert day["sample_count"] == 2 and day["time_basis"] == "mixed"
    assert {"date", "sensor_type", "avg_value", "min_value", "max_value", "after_maintenance"} <= set(day)


def test_new_objects_are_backend_only(db):
    for role in ("anon", "authenticated"):
        for sql in ("select public.aggregate_daily_sensor_data(current_date, current_date)",
                    "select public.reconcile_daily_sensor_averages(1, 7)",
                    "select public.sensor_channel('pH')",
                    "select * from public.daily_sensor_refresh",
                    "select * from public.get_pond_telemetry_history(1, 7)"):
            with pytest.raises(errors.InsufficientPrivilege):
                _as_role(db, role, sql)
    db.execute("select relrowsecurity from pg_class where oid = 'public.daily_sensor_refresh'::regclass")
    assert db.fetchone()["relrowsecurity"] is True
    for fn in ("public.sensor_channel(text)", "public.aggregate_daily_sensor_data(date, date, bigint)",
               "public.reconcile_daily_sensor_averages(bigint, integer)",
               "public.get_pond_telemetry_history(bigint, integer)"):
        db.execute("select has_function_privilege('service_role', %s, 'execute') as ok", (fn,))
        assert db.fetchone()["ok"] is True, fn
    db.execute("select proconfig from pg_proc where oid = 'public.get_pond_telemetry_history(bigint, integer)'"
               "::regprocedure")
    assert db.fetchone()["proconfig"] == ['search_path=""']
