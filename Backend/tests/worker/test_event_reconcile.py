"""Issue #19: each poll cycle reconciles the pond's pondInterventions rows
with the twin's event ledger (koi/worker/poller.py, PondTwin.
reconcile_events), here over MemoryStorage. tests/sql/test_sql_event_ledger.py
covers the database functions.

Run from Backend/: python -m pytest -q -k "ledger or replay or reconcile"
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from koi.models import event_ledger as el
from koi.models.engine import EventKind, PondEvent
from koi.registry import EngineRegistry
from koi.storage import MemoryStorage, StorageError
from koi.worker import poller

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
T0 = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
POND, OTHER = 7101, 7102
FULL = {"temp": 29.0, "TDS": 220.0, "pH": 7.6, "LUX": 0.0}


def minutes(n):
    return T0 + timedelta(minutes=n)


def make_storage():
    storage = MemoryStorage()
    storage.add_rows("UserData", [{"userID": p, "volume": 5000, "biomass": 8.0} for p in (POND, OTHER)])
    return storage


def upload(storage, at, pond=POND):
    storage.add_rows("SensorData", [{"userID": pond, "sensor_type": t, "data1": v, "created_at": at.isoformat()}
                                    for t, v in FULL.items()])


def log_feed(storage, at, grams=60.0, pond=POND, created=None, **extra):
    """The app's insert into pondInterventions (event_id by default)."""
    [row] = storage.add_rows("pondInterventions", [{
        "userID": pond, "event_type": "FEEDING", "event_timestamp": at.isoformat(), "food_grams": grams,
        "protein_percentage": 40.0, "created_at": (created or at).isoformat(), **extra}])
    return row


def poll(registry, at, pond=POND):
    config = next(c for c in registry.storage.fetch_active_pond_configs() if c["user_id"] == pond)
    return poller._poll_user(registry, pond, config, now=at)


def chemistry(storage, pond=POND):
    snap = storage.load_engine_snapshot(pond)["chemistry"]
    return {k: snap[k] for k in ("tan_mg", "no2_mg", "no3_mg", "last_ingest_time", "daily")}


def run(storage, steps):
    """Uploads every 15 minutes up to the last poll, polling at each
    minute in steps (a dict minute -> callable run before that poll)."""
    registry = EngineRegistry(storage)
    results = {}
    for minute in sorted(steps):
        steps[minute](storage, registry)
        results[minute] = poll(registry, minutes(minute))
    return registry, results


def uploads(*at):
    def step(storage, registry):
        for m in at:
            upload(storage, minutes(m))
    return step


def test_reconcile_applies_an_event_only_in_the_table():
    """The twin was down when the feed was logged: the row is applied by
    the next poll, in its place, and once."""
    late = make_storage()
    _, results = run(late, {
        31: uploads(0, 15, 30),
        46: lambda s, r: (log_feed(s, minutes(20), created=minutes(32)), upload(s, minutes(45))),
        61: uploads(60),
    })
    assert results[31].events["applied"] == 0
    assert results[46].events["applied"] == 1 and results[46].events["replayed"] is True
    assert results[61].events["applied"] == 0

    on_time = make_storage()
    run(on_time, {
        31: lambda s, r: (upload(s, minutes(0)), upload(s, minutes(15)), log_feed(s, minutes(20)),
                          upload(s, minutes(30))),
        46: uploads(45),
        61: uploads(60),
    })
    assert chemistry(late) == chemistry(on_time)
    assert chemistry(late)["tan_mg"] > 0


def test_reconcile_does_not_reapply_an_event_the_api_applied():
    storage = make_storage()
    registry = EngineRegistry(storage)
    upload(storage, minutes(0))
    poll(registry, minutes(1))
    row = log_feed(storage, minutes(5))
    feed = el.event_from_row(row)
    out = registry.with_twin(POND, lambda twin: twin.record_event(row["event_id"], feed, now=minutes(5)))
    assert out["status"] == "applied"
    tan = storage.load_engine_snapshot(POND)["chemistry"]["tan_mg"]
    upload(storage, minutes(15))
    result = poll(registry, minutes(16))
    assert result.events["applied"] == 0 and result.events["replayed"] is False
    snap = storage.load_engine_snapshot(POND)
    assert [e["event_id"] for e in snap["event_ledger"]["entries"]] == [row["event_id"]]
    assert snap["event_ledger"]["entries"][0]["row_id"] == row["id"]
    assert snap["chemistry"]["tan_mg"] < tan  # nitrified since, not added twice


def test_reconcile_backfills_rows_without_an_event_id():
    storage = make_storage()
    registry = EngineRegistry(storage)
    upload(storage, minutes(0))
    poll(registry, minutes(1))
    row = log_feed(storage, minutes(5), event_id=None)
    assert row["event_id"] is None
    upload(storage, minutes(15))
    assert poll(registry, minutes(16)).events["applied"] == 1
    [stored] = storage.rows("pondInterventions")
    assert stored["event_id"] == el.derived_event_id(row["id"])
    upload(storage, minutes(30))
    assert poll(registry, minutes(31)).events["applied"] == 0


def test_reconcile_restart_during_reconciliation_applies_once():
    """The cycle stops after reconciling but before the save (the worker
    died): nothing of it is kept but the deterministic backfill, and the
    next cycle applies the row exactly once."""
    interrupted = make_storage()
    registry = EngineRegistry(interrupted)
    upload(interrupted, minutes(0))
    poll(registry, minutes(1))
    log_feed(interrupted, minutes(5), event_id=None)
    upload(interrupted, minutes(15))
    interrupted.failing.add("save_engine_snapshot")
    with pytest.raises(StorageError):
        poll(registry, minutes(16))
    assert interrupted.load_engine_snapshot(POND)["event_ledger"]["entries"] == []
    backfilled = interrupted.rows("pondInterventions")[0]["event_id"]
    interrupted.failing.clear()
    restarted = EngineRegistry(interrupted)  # a new worker process
    assert poll(restarted, minutes(17)).events["applied"] == 1
    assert interrupted.rows("pondInterventions")[0]["event_id"] == backfilled

    straight = make_storage()
    registry = EngineRegistry(straight)
    upload(straight, minutes(0))
    poll(registry, minutes(1))
    log_feed(straight, minutes(5), event_id=None)
    upload(straight, minutes(15))
    poll(registry, minutes(17))
    assert chemistry(interrupted) == chemistry(straight)


def test_reconcile_replays_an_edit_and_a_delete():
    storage = make_storage()
    registry = EngineRegistry(storage)
    for m in (0, 15):
        upload(storage, minutes(m))
    poll(registry, minutes(16))
    row = log_feed(storage, minutes(20), grams=80.0)
    for m in (30, 45):
        upload(storage, minutes(m))
    poll(registry, minutes(46))

    # Edited in the app's event history (#57): less food, earlier.
    for r in storage._tables["pondInterventions"]:
        r.update(food_grams=20.0, event_timestamp=minutes(10).isoformat())
    upload(storage, minutes(60))
    edited = poll(registry, minutes(61))
    assert edited.events["revised"] == 1 and edited.events["replayed"] is True
    assert edited.events["revised_ids"] == [row["event_id"]]

    expected = make_storage()
    reg = EngineRegistry(expected)
    upload(expected, minutes(0))
    log_feed(expected, minutes(10), grams=20.0)
    upload(expected, minutes(15))
    poll(reg, minutes(16))
    for m in (30, 45):
        upload(expected, minutes(m))
    poll(reg, minutes(46))
    upload(expected, minutes(60))
    poll(reg, minutes(61))
    assert chemistry(storage) == chemistry(expected)

    # Deleted: replayed without it, and the tombstone stays.
    storage._tables["pondInterventions"].clear()
    upload(storage, minutes(75))
    deleted = poll(registry, minutes(76))
    assert deleted.events["deleted"] == 1 and deleted.events["replayed"] is True
    entry = storage.load_engine_snapshot(POND)["event_ledger"]["entries"][0]
    assert (entry["event_id"], entry["status"]) == (row["event_id"], "deleted")

    never = make_storage()
    reg = EngineRegistry(never)
    for at, ups in ((16, (0, 15)), (46, (30, 45)), (61, (60,)), (76, (75,))):
        for m in ups:
            upload(never, minutes(m))
        poll(reg, minutes(at))
    assert chemistry(storage)["tan_mg"] == pytest.approx(chemistry(never)["tan_mg"])
    assert chemistry(storage) == chemistry(never)


def test_reconcile_goes_on_without_rows_it_cannot_read():
    storage = make_storage()
    registry = EngineRegistry(storage)
    upload(storage, minutes(0))
    log_feed(storage, minutes(5))
    storage.failing.add("fetch_interventions")
    result = poll(registry, minutes(6))
    assert result.events == {"reconciled": False}
    storage.failing.clear()
    upload(storage, minutes(15))
    assert poll(registry, minutes(16)).events["applied"] == 1


def test_reconcile_identical_event_id_in_another_pond_changes_nothing_here():
    """Pond OTHER posts pond POND's event_id to its own twin. Each ledger
    is the pond's own: POND still applies its row once, OTHER never
    claims, edits or deletes it, and neither reads the other's rows."""
    storage = make_storage()
    registry = EngineRegistry(storage)
    for pond in (POND, OTHER):
        upload(storage, minutes(0), pond=pond)
        poll(registry, minutes(1), pond=pond)
    row = log_feed(storage, minutes(5), grams=60.0)
    stolen = PondEvent(kind=EventKind.FEEDING, time=minutes(5), food_grams=999.0, protein_percent=40.0)
    registry.with_twin(OTHER, lambda twin: twin.record_event(row["event_id"], stolen, now=minutes(5)))
    for pond in (POND, OTHER):
        upload(storage, minutes(15), pond=pond)
    assert poll(registry, minutes(16)).events["applied"] == 1
    other = poll(registry, minutes(16), pond=OTHER).events
    assert (other["applied"], other["claimed"], other["deleted"], other["revised"]) == (0, 0, 0, 0)
    mine = storage.load_engine_snapshot(POND)["event_ledger"]["entries"]
    assert [(e["event_id"], e["event"]["food_grams"]) for e in mine] == [(row["event_id"], 60.0)]
    theirs = storage.load_engine_snapshot(OTHER)["event_ledger"]["entries"]
    assert [(e["row_id"], e["event"]["food_grams"]) for e in theirs] == [(None, 999.0)]
    # The table row is untouched, and a second row cannot take the id.
    assert storage.rows("pondInterventions") == [row]
    with pytest.raises(ValueError):
        log_feed(storage, minutes(6), pond=OTHER, event_id=row["event_id"])


def test_reconcile_counts_rows_from_before_an_upgraded_snapshot_as_applied():
    """A v3 snapshot (before the ledger): rows created before it was saved
    were posted to the twin of that time; only later rows are applied."""
    snap = json.loads((FIXTURES / "snapshot_v3_sensor_inputs.json").read_text())
    saved_at = datetime.fromisoformat(snap["saved_at"])
    newest = datetime.fromisoformat(snap["sensor_inputs"]["last_input_at"])
    storage = make_storage()
    storage.add_rows("pond_chemistry_state", [{"user_id": POND, "snapshot": snap, "snapshot_version": 4}])
    log_feed(storage, newest - timedelta(hours=2), created=saved_at - timedelta(minutes=5))
    after = log_feed(storage, newest + timedelta(minutes=10), created=saved_at + timedelta(minutes=5))
    upload(storage, newest + timedelta(minutes=15))
    result = poll(EngineRegistry(storage), newest + timedelta(minutes=16))
    assert result.events["applied"] == 1
    entries = storage.load_engine_snapshot(POND)["event_ledger"]["entries"]
    assert [e["event_id"] for e in entries] == [after["event_id"]]
