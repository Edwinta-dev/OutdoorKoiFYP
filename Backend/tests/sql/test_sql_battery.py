"""SQL tests for migration 0028_battery_low.sql (issue #42): the
battery_low flag (16) through ingest_readings and the SensorData check,
the outbox's battery_low kind, and pond_device_activity reading the
payload v2 battery_mv and reset_reason columns (one battery reading per
cycle, the reset dated from its boot's first cycle), with the same answer
from MemoryStorage.

Local Supabase stack only; skipped when no local database answers, as in
CI. Each case runs in a rolled-back transaction.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402
from test_sql_device_health import _storage  # noqa: E402
from test_sql_sensor_payload_v2 import (  # noqa: E402
    BOOT_1,
    BOOT_2,
    _connect,
    _ingest,
    _issue,
    _now,
    _pond,
    _stored,
    _v2,
)

from koi.models import device_health  # noqa: E402
from koi.models.sensor_inputs import parse_timestamp  # noqa: E402
from koi.storage.memory import MemoryStorage  # noqa: E402

POND_A, POND_B = 9421, 9422
LOW = device_health.FLAG_BATTERY_LOW


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    c = _connect()
    with c.cursor() as cur:
        cur.execute("select pg_get_constraintdef(oid) as d from pg_constraint where conname = 'sensordata_flags_range'")
        row = cur.fetchone()
    c.rollback()
    if row is None or "31" not in row["d"]:
        c.close()
        pytest.skip("local database is not at the current migrations; run `supabase db reset`")
    yield c
    c.close()


@pytest.fixture
def db(conn: Any) -> Iterator[Any]:
    with conn.cursor() as cur:
        yield cur
    conn.rollback()


def _cycle(boot: str, seq: int, sampled: Any, mv: int, flags: int = 0, reset: str = "power_on") -> list[dict]:
    return [_v2(channel, value, boot, seq, sampled, flags=flags, reset_reason=reset, battery_mv=mv)
            for channel, value in (("temp", 28.0), ("pH", 7.2))]


def test_battery_low_flag_is_accepted_and_stored(db):
    _pond(db, POND_A)
    token = _issue(db, POND_A)
    now = _now(db)
    reply = _ingest(db, token, _cycle(BOOT_1, 1, now - timedelta(minutes=1), 4310, flags=LOW | 4)
                    + [_v2("LUX", 900, BOOT_1, 1, None, flags=31, battery_mv=4310),
                       _v2("TDS", 200, BOOT_1, 1, None, flags=32, battery_mv=4310)])
    assert reply["accepted"] == 3
    assert [(r["sensor_type"], r["reason"]) for r in reply["rejected"]] == [("TDS", "invalid_flags")]
    assert [(r["sensor_type"], r["flags"], r["battery_mv"]) for r in _stored(db, POND_A)] == [
        ("temp", 20, 4310), ("pH", 20, 4310), ("LUX", 31, 4310)]


def test_battery_flags_check_allows_16_and_refuses_32(db):
    _pond(db, POND_A)
    db.execute('insert into public."SensorData" (sensor_type, data1, "userID", flags) values (%s, 7, %s, 16)',
               ("pH", POND_A))
    with pytest.raises(errors.CheckViolation), db.connection.transaction():
        db.execute('insert into public."SensorData" (sensor_type, data1, "userID", flags) values (%s, 7, %s, 32)',
                   ("pH", POND_A))


def test_battery_low_outbox_kind_is_accepted(db):
    _pond(db, POND_A)
    db.execute("select public.enqueue_notification(%s, 'battery_low', 'warning', 'Sensor node battery is low', "
               "'Replace the batteries.', '{}'::jsonb, 'battery_low:sensor', now(), 86400) as r", (POND_A,))
    assert db.fetchone()["r"]["kind"] == "battery_low"


def _rows(db: Any) -> tuple[Any, list[dict]]:
    """Ponds A and B, two boots of v2 cycles on A, a legacy battery row and
    a legacy reset row on A, one cycle on B. Returns (now, A's rows)."""
    _pond(db, POND_A)
    _pond(db, POND_B)
    token_a, token_b = _issue(db, POND_A), _issue(db, POND_B)
    now = _now(db)
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") values '
               "(%s, 'battery_mv', 4800, %s), (%s, 'reset_reason', 4, %s), (%s, 'battery_mv', 5100, %s)",
               (now - timedelta(hours=8), POND_A, now - timedelta(hours=7), POND_A,
                now - timedelta(hours=30), POND_A))
    reply = _ingest(db, token_a, _cycle(BOOT_1, 1, now - timedelta(hours=6), 4700)
                    + _cycle(BOOT_2, 1, now - timedelta(hours=3), 4450, reset="brownout")
                    + _cycle(BOOT_2, 2, now - timedelta(hours=2), 4390, flags=LOW, reset="brownout")
                    + [_v2("pH", 7.1, BOOT_2, 3, None, flags=LOW | 1, reset_reason="brownout", battery_mv=4380)])
    assert reply["accepted"] == 7
    assert _ingest(db, token_b, _cycle(BOOT_1, 1, now - timedelta(minutes=5), 3900, flags=LOW))["accepted"] == 2
    return now, _stored(db, POND_A)


def test_battery_device_activity_reads_one_reading_per_v2_cycle(db):
    now, _ = _rows(db)
    activity = _storage(db).fetch_device_activity(POND_A, now - timedelta(hours=24))
    battery = [(parse_timestamp(b["at"]), b["mv"], b["low"]) for b in activity["battery"]]
    assert battery == [
        (now - timedelta(hours=8), 4800, None),           # legacy row: no flag
        (now - timedelta(hours=6), 4700, False),
        (now - timedelta(hours=3), 4450, False),
        (now - timedelta(hours=2), 4390, True),
        (now, 4380, True)]                                # no sample time: timed by receipt
    # The newest reset is boot 2's, dated from that boot's first cycle.
    reset = activity["reset"]
    assert (reset["reason"], reset["code"]) == ("brownout", None)
    assert parse_timestamp(reset["at"]) == now - timedelta(hours=3)
    entry = device_health.summarize_devices(activity, {}, [], timedelta(minutes=15), now)[0]
    assert entry["battery"]["samples"] == 5 and entry["battery"]["trend"] == "falling"
    assert entry["last_reset"]["reason"] == "brownout" and entry["last_reset"]["code"] == 9


def test_battery_device_activity_newer_legacy_reset_wins(db):
    now, _ = _rows(db)
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") values '
               "(%s, 'reset_reason', 3, %s)", (now + timedelta(minutes=1), POND_A))
    reset = _storage(db).fetch_device_activity(POND_A, now - timedelta(hours=24))["reset"]
    assert (reset["code"], reset["reason"]) == (3, None)


def test_battery_device_activity_without_v2_rows_is_unchanged(db):
    _pond(db, POND_A)
    now = _now(db)
    db.execute('insert into public."SensorData" (created_at, sensor_type, data1, "userID") values '
               "(%s, 'battery_mv', 12400, %s), (%s, 'reset_reason', 8, %s)",
               (now - timedelta(hours=3), POND_A, now - timedelta(hours=1), POND_A))
    activity = _storage(db).fetch_device_activity(POND_A, now - timedelta(hours=24))
    assert [(parse_timestamp(b["at"]), b["mv"], b["low"]) for b in activity["battery"]] == [
        (now - timedelta(hours=3), 12400, None)]
    assert activity["reset"]["code"] == 8 and parse_timestamp(activity["reset"]["at"]) == now - timedelta(hours=1)
    assert _storage(db).fetch_device_activity(9429, now)["battery"] == []
    assert _storage(db).fetch_device_activity(9429, now)["reset"] is None


def test_battery_device_activity_matches_memory_storage(db):
    now, rows = _rows(db)
    memory = MemoryStorage(clock=lambda: now)
    memory.add_rows("SensorData", [
        {**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()},
         "device_id": str(r["device_id"]) if r["device_id"] else None, "userID": POND_A} for r in rows])
    since = now - timedelta(hours=24)
    a, b = _storage(db).fetch_device_activity(POND_A, since), memory.fetch_device_activity(POND_A, since)

    def norm(activity: dict) -> dict:
        reset = activity["reset"]
        return {"battery": [(parse_timestamp(x["at"]), float(x["mv"]), x["low"]) for x in activity["battery"]],
                "reset": (parse_timestamp(reset["at"]), reset["code"] and float(reset["code"]), reset["reason"])}
    assert norm(a) == norm(b)
    assert device_health.summarize_devices(a, {}, [], timedelta(minutes=15), now) == \
        device_health.summarize_devices(b, {}, [], timedelta(minutes=15), now)
