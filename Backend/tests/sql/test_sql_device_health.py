"""SQL tests for migration 0018: record_device_contacts,
pond_device_activity and the devices and device_contact tables, through
SupabaseStorage's RPC calls run against the local database (issue #23).

Covers repeated and out-of-order contacts, the 30-day limit, the window
and the contact before it, battery and reset rows, two ponds, the same
results from MemoryStorage, and access (backend only). Each case runs in
a rolled-back transaction.

Local Supabase stack only; skipped when no local database answers, as in
CI.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from conftest import make_settings  # noqa: E402
from koi.models import device_health  # noqa: E402
from koi.storage.memory import MemoryStorage  # noqa: E402
from koi.storage.supabase_storage import SupabaseStorage  # noqa: E402

DB_URL = os.environ.get("KOI_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
POND_A, POND_B = 9231, 9232
# Contacts older than 30 days are dropped against the database clock, so
# the cases are placed relative to it.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
INTERVAL = timedelta(minutes=15)


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
        cur.execute("select to_regclass('public.device_contact') is not null as ok")
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


class _Rpc:
    SQL = {
        "record_device_contacts":
            "select public.record_device_contacts(%(p_pond_id)s, %(p_kind)s, %(p_contacts)s) as r",
        "pond_device_activity": "select public.pond_device_activity(%(p_pond_id)s, %(p_since)s::timestamptz) as r",
    }

    def __init__(self, cur: Any, name: str, params: dict):
        self.cur, self.name, self.params = cur, name, params

    def execute(self) -> Any:
        params = {k: Jsonb(v) if k == "p_contacts" else v for k, v in self.params.items()}
        self.cur.execute(self.SQL[self.name], params)
        return type("Result", (), {"data": self.cur.fetchone()["r"]})()


class _LocalClient:
    def __init__(self, cur: Any):
        self.cur = cur

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self.cur, name, params)


def _storage(cur: Any) -> SupabaseStorage:
    return SupabaseStorage(make_settings(storage="supabase"), client=_LocalClient(cur))  # type: ignore[arg-type]


def _contact(at: datetime, interval: timedelta = INTERVAL) -> dict:
    return {"received_at": at.isoformat(), "expected_next_at": (at + interval).isoformat()}


def _sensor_row(db: Any, pond: int, at: datetime, sensor_type: str, value: float) -> None:
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") values (%s, %s, %s, %s)',
               (at, sensor_type, value, pond))


def _t(value: Any) -> datetime:
    return device_health.parse_timestamp(value)


def test_device_contacts_are_recorded_once_and_last_seen_only_moves_forward(db):
    storage = _storage(db)
    first = [_contact(NOW - timedelta(minutes=45)), _contact(NOW - timedelta(minutes=30))]
    row = storage.record_device_contacts(POND_A, "sensor", first)
    assert _t(row["last_seen_at"]) == NOW - timedelta(minutes=30)
    assert _t(row["next_expected_at"]) == NOW - timedelta(minutes=15)
    assert row["expected_interval_seconds"] is None

    # A retried cycle repeats them: nothing changes.
    storage.record_device_contacts(POND_A, "sensor", first)
    db.execute("select count(*) as n from public.device_contact where pond_id = %s", (POND_A,))
    assert db.fetchone()["n"] == 2

    # An older contact found late is stored but does not move last_seen_at back.
    row = storage.record_device_contacts(POND_A, "sensor", [_contact(NOW - timedelta(hours=2))])
    assert _t(row["last_seen_at"]) == NOW - timedelta(minutes=30)
    assert _t(row["next_expected_at"]) == NOW - timedelta(minutes=15)
    db.execute("select count(*) as n from public.device_contact where pond_id = %s", (POND_A,))
    assert db.fetchone()["n"] == 3


def test_device_contacts_older_than_30_days_are_not_kept(db):
    storage = _storage(db)
    db.execute("insert into public.device_contact (pond_id, kind, received_at) values (%s, 'sensor', %s)",
               (POND_A, NOW - timedelta(days=40)))
    storage.record_device_contacts(POND_A, "sensor", [_contact(NOW - timedelta(days=31)), _contact(NOW)])
    db.execute("select received_at from public.device_contact where pond_id = %s", (POND_A,))
    assert [r["received_at"] for r in db.fetchall()] == [NOW]


def test_device_contact_with_no_usable_expected_time_keeps_null(db):
    storage = _storage(db)
    row = storage.record_device_contacts(POND_A, "camera", [
        {"received_at": NOW.isoformat(), "expected_next_at": (NOW - timedelta(minutes=1)).isoformat()}])
    assert row["next_expected_at"] is None


def test_device_unknown_kind_is_refused(db):
    with pytest.raises(Exception) as err:
        _storage(db).record_device_contacts(POND_A, "pump", [_contact(NOW)])
    assert "unknown device kind" in str(err.value)


def test_device_activity_window_battery_and_reset(db):
    storage = _storage(db)
    since = NOW - timedelta(hours=24)
    storage.record_device_contacts(POND_A, "sensor", [
        _contact(since - timedelta(hours=3)), _contact(since - timedelta(hours=1)),
        _contact(NOW - timedelta(hours=2)), _contact(NOW - timedelta(hours=1))])
    storage.record_device_contacts(POND_A, "camera", [_contact(NOW - timedelta(hours=5), timedelta(hours=2))])
    storage.record_device_contacts(POND_B, "sensor", [_contact(NOW)])
    _sensor_row(db, POND_A, since - timedelta(hours=1), "battery_mv", 12600)
    _sensor_row(db, POND_A, NOW - timedelta(hours=3), "battery_mv", 12400)
    _sensor_row(db, POND_A, NOW - timedelta(hours=2), "battery_mv", 12300)
    _sensor_row(db, POND_A, NOW - timedelta(hours=6), "reset_reason", 9)
    _sensor_row(db, POND_A, NOW - timedelta(hours=1), "reset_reason", 8)
    _sensor_row(db, POND_B, NOW, "reset_reason", 4)

    activity = storage.fetch_device_activity(POND_A, since)
    assert [d["kind"] for d in activity["devices"]] == ["camera", "sensor"]
    sensor = [_t(c["received_at"]) for c in activity["contacts"] if c["kind"] == "sensor"]
    # The newest contact before the window starts the first gap.
    assert sensor == [since - timedelta(hours=1), NOW - timedelta(hours=2), NOW - timedelta(hours=1)]
    assert [(_t(b["at"]), b["mv"]) for b in activity["battery"]] == [
        (NOW - timedelta(hours=3), 12400), (NOW - timedelta(hours=2), 12300)]
    assert activity["reset"]["code"] == 8
    assert _t(activity["reset"]["at"]) == NOW - timedelta(hours=1)

    empty = storage.fetch_device_activity(9239, since)
    assert empty == {"devices": [], "contacts": [], "battery": [], "reset": None}


def test_device_activity_matches_memory_storage(db):
    """MemoryStorage answers the same as the database for the same rows."""
    clock = NOW
    memory = MemoryStorage(clock=lambda: clock)
    sql = _storage(db)
    calls = [("sensor", [_contact(NOW - timedelta(hours=26)), _contact(NOW - timedelta(hours=3))]),
             ("sensor", [_contact(NOW - timedelta(hours=1)), _contact(NOW - timedelta(hours=3))]),
             ("camera", [_contact(NOW - timedelta(hours=4), timedelta(hours=3))])]
    for kind, contacts in calls:
        a = sql.record_device_contacts(POND_A, kind, contacts)
        b = memory.record_device_contacts(POND_A, kind, contacts)
        for key in ("pond_id", "kind", "expected_interval_seconds"):
            assert a[key] == b[key]
        for key in ("last_seen_at", "next_expected_at"):
            assert (a[key] and _t(a[key])) == (b[key] and _t(b[key]))
    for at, sensor_type, value in [(NOW - timedelta(hours=2), "battery_mv", 12500.0),
                                   (NOW - timedelta(hours=1), "reset_reason", 1.0)]:
        _sensor_row(db, POND_A, at, sensor_type, value)
        memory.add_rows("SensorData", [{"created_at": at.isoformat(), "sensor_type": sensor_type, "data1": value,
                                        "userID": POND_A}])

    since = NOW - timedelta(hours=24)
    a, b = sql.fetch_device_activity(POND_A, since), memory.fetch_device_activity(POND_A, since)

    def norm(activity: dict) -> dict:
        return {
            "contacts": [(c["kind"], _t(c["received_at"]), c["expected_next_at"] and _t(c["expected_next_at"]))
                         for c in activity["contacts"]],
            "battery": [(_t(x["at"]), float(x["mv"])) for x in activity["battery"]],
            "reset": (_t(activity["reset"]["at"]), float(activity["reset"]["code"])),
            "devices": [(d["kind"], _t(d["last_seen_at"])) for d in activity["devices"]],
        }
    assert norm(a) == norm(b)
    summary_a = device_health.summarize_devices(a, {}, [], INTERVAL, NOW)
    summary_b = device_health.summarize_devices(b, {}, [], INTERVAL, NOW)
    assert summary_a == summary_b


def test_device_tables_and_functions_are_backend_only(db):
    db.execute("""
        select c.relname, c.relrowsecurity,
               has_table_privilege('anon', c.oid, 'select') as anon_select,
               has_table_privilege('authenticated', c.oid, 'insert') as auth_insert,
               has_table_privilege('service_role', c.oid, 'select') as service_select
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relname in ('devices', 'device_contact') order by c.relname""")
    rows = db.fetchall()
    assert [r["relname"] for r in rows] == ["device_contact", "devices"]
    for r in rows:
        assert r["relrowsecurity"] and not r["anon_select"] and not r["auth_insert"] and r["service_select"]
    for fn in ("public.record_device_contacts(bigint, text, jsonb)",
               "public.pond_device_activity(bigint, timestamptz)"):
        db.execute("select has_function_privilege('anon', %s, 'execute') as anon, "
                   "has_function_privilege('authenticated', %s, 'execute') as auth, "
                   "has_function_privilege('service_role', %s, 'execute') as service", (fn, fn, fn))
        r = db.fetchone()
        assert not r["anon"] and not r["auth"] and r["service"]


def test_device_migration_leaves_sensordata_insert_open_to_the_node(db):
    """The node's anonymous insert path is unchanged: anon may still insert
    a battery_mv or reset_reason row."""
    db.execute("select has_table_privilege('anon', 'public.\"SensorData\"', 'insert') as ok")
    assert db.fetchone()["ok"]
