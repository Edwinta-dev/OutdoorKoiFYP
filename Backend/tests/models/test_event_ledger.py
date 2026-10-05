"""Issue #19: the twin's event ledger (koi/models/event_ledger.py and
PondTwin.record_event / reconcile_events): every event applied once by
event_id, a backdated event replayed into its place, edits, deletions and
tombstones, checkpoints and the old snapshot shapes.

Times are recent so the chemistry engine's 30-day day buckets, pruned
against the wall clock when a snapshot loads, are all kept.

Run from Backend/: python -m pytest -q -k "ledger or replay or reconcile"
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from koi.models import event_ledger as el
from koi.models.engine import EventKind, PondConfig, PondEvent
from koi.models.pond_twin import PondTwin
from koi.models.sensor_inputs import SensorInput

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
T0 = (datetime.now(timezone.utc) - timedelta(days=4)).replace(hour=0, minute=0, second=0, microsecond=0)
CONFIG = PondConfig(volume_litres=5000.0, estimated_biomass_grams=8000.0)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def reading(hours: float, temp: float = 28.0, lux=None) -> SensorInput:
    """One upload. No light by default: the algae nitrate uptake stays off,
    so total nitrogen (TAN + NO2 + NO3) is conserved between events."""
    return SensorInput(at(hours), "ingestion", {"ph": 7.5, "tds": 200.0, "temp": temp, "lux": lux},
                       {"ph": int(hours * 10), "tds": int(hours * 10) + 1, "temp": int(hours * 10) + 2})


def series(start: float, end: float, step: float = 1.0) -> list[SensorInput]:
    out, h = [], start
    while h <= end:
        out.append(reading(h))
        h += step
    return out


def history_of(inputs):
    """What EngineRegistry.sensor_history returns, over a fixed list."""
    def history(after, until):
        return [i for i in inputs if (after is None or i.time > after) and i.time <= until]
    return history


def feed(hours: float, grams: float = 50.0) -> PondEvent:
    return PondEvent(kind=EventKind.FEEDING, time=at(hours), food_grams=grams, protein_percent=40.0)


def water_change(hours: float, pct: float = 25.0) -> PondEvent:
    return PondEvent(kind=EventKind.WATER_CHANGE, time=at(hours), volume_percent=pct)


def new_id() -> str:
    return str(uuid.uuid4())


def chemistry(twin: PondTwin) -> dict:
    """The chemistry state a replay must reproduce. The engine's event
    list is compared as a set: its order is application order."""
    snap = json.loads(json.dumps(twin.chemistry.to_snapshot()))
    snap["events"] = sorted(json.dumps(e, sort_keys=True) for e in snap["events"])
    return snap


def nitrogen(twin: PondTwin) -> float:
    c = twin.chemistry
    return c._tan_mg + c._no2_mg + c._no3_mg


def in_order(steps) -> PondTwin:
    """A twin given readings and events in time order, as if every post
    had arrived on time. steps: SensorInput or (event_id, PondEvent)."""
    twin = PondTwin.create(CONFIG)
    for step in steps:
        if isinstance(step, SensorInput):
            twin.ingest_sensor_inputs([step])
        else:
            twin.record_event(step[0], step[1], now=step[1].time)
    return twin


# ---------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------
def test_ledger_duplicate_post_is_a_noop_that_reports_duplicate():
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(series(0, 3))
    event_id = new_id()
    first = twin.record_event(event_id, feed(3.5), now=at(3.5))
    tan = twin.chemistry._tan_mg
    again = twin.record_event(event_id, feed(3.5), now=at(3.6))
    assert first["status"] == "applied" and again["status"] == "duplicate"
    assert again["event_id"] == event_id and again["replayed"] is False
    assert twin.chemistry._tan_mg == tan
    assert [e.event_id for e in twin.ledger.live()] == [event_id]


def test_ledger_same_id_with_other_values_is_still_a_duplicate():
    # The id decides; a retried request whose body changed does not apply.
    twin = PondTwin.create(CONFIG)
    event_id = new_id()
    twin.record_event(event_id, feed(1, grams=50.0), now=at(1))
    tan = twin.chemistry._tan_mg
    assert twin.record_event(event_id, feed(1, grams=500.0), now=at(1))["status"] == "duplicate"
    assert twin.chemistry._tan_mg == tan


# ---------------------------------------------------------------------
# Time order and replay
# ---------------------------------------------------------------------
def test_ledger_backdated_feed_changes_the_tan_trajectory():
    """Fed at hour 2 but logged at hour 10: the ammonia has been
    nitrifying for 8 hours, so TAN is lower and NO2 + NO3 higher than if
    it had been added now, with the same total nitrogen."""
    readings = series(0, 10)
    backdated = PondTwin.create(CONFIG)
    backdated.ingest_sensor_inputs(readings)
    outcome = backdated.record_event(new_id(), feed(2.5), now=at(10.2), history=history_of(readings))
    assert outcome["replayed"] is True and outcome["placed_late"] is False

    added_now = PondTwin.create(CONFIG)
    added_now.ingest_sensor_inputs(readings)
    added_now.apply_event(feed(10.2), now=at(10.2))

    assert backdated.chemistry._tan_mg < added_now.chemistry._tan_mg * 0.95
    assert backdated.chemistry._no2_mg + backdated.chemistry._no3_mg > \
        added_now.chemistry._no2_mg + added_now.chemistry._no3_mg
    assert nitrogen(backdated) == pytest.approx(nitrogen(added_now))

    # And it is exactly the trajectory of a feed logged on time.
    on_time = in_order(readings[:3] + [("x", feed(2.5))] + readings[3:])
    assert chemistry(backdated) == chemistry(on_time)


def test_ledger_replay_equals_applying_in_order_across_days():
    """Three days of readings, events on each day, all posted late and out
    of order: the result equals applying everything in time order."""
    readings = series(0, 70, step=2.0)
    # Distinct times: events at one instant apply in the order recorded.
    events = [(new_id(), feed(5)), (new_id(), water_change(27, 30.0)), (new_id(), feed(27.5)),
              (new_id(), PondEvent(kind=EventKind.TOP_UP, time=at(50), volume_percent=5.0)),
              (new_id(), PondEvent(kind=EventKind.ALGAL_SCRUB, time=at(51), scrub_type="brush"))]
    steps = sorted(readings + events, key=lambda s: (s.time, 0) if isinstance(s, SensorInput) else (s[1].time, 1))
    expected = in_order(steps)

    replayed = PondTwin.create(CONFIG)
    replayed.ingest_sensor_inputs(readings)
    history = history_of(readings)
    for event_id, event in reversed(events):  # newest first: every post is backdated
        out = replayed.record_event(event_id, event, now=at(71), history=history)
        assert out["replayed"] is True
    assert chemistry(replayed) == chemistry(expected)
    assert [e.event_id for e in replayed.ledger.live()] == [e for e, _ in events]


def test_ledger_replay_starts_at_the_checkpoint_before_the_event():
    readings = series(0, 70, step=2.0)
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(readings)
    throughs = [c.through for c in twin.ledger.checkpoints]
    assert throughs[0] is None and len(throughs) >= 3  # genesis plus one per local day crossed
    out = twin.record_event(new_id(), feed(60), now=at(71), history=history_of(readings))
    used = el.Checkpoint.from_dict({"through": out["replayed_from"], "chemistry": {}}).through
    assert used is not None and used < at(60)
    assert max(t for t in throughs if t is not None and t < at(60)) == used


def test_ledger_checkpoint_is_not_changed_by_later_readings():
    """A checkpoint holds the day buckets from its own local day on. One
    that holds a bucket the engine is still filling must keep its copy as
    taken: the lists are copied, not shared with the live engine (found by
    the snapshot round-trip property test, issue #25)."""
    def varied(hours: float) -> SensorInput:
        return SensorInput(at(hours), "ingestion", {"ph": 7.4 + 0.05 * (hours % 4), "tds": 200.0 + hours % 3,
                                                    "temp": 28.0 + 0.1 * (hours % 5), "lux": 1000.0 + 10 * hours},
                           {})

    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs([varied(h) for h in range(21)])
    # An event applied outside the ledger opens the next local day's bucket
    # (local midnight is hour 40), so the checkpoint the 43:00 reading takes
    # at hour 20 holds that bucket.
    twin.apply_event(water_change(42), now=at(42))
    twin.ingest_sensor_inputs([varied(43)])
    taken = [json.dumps(c.to_dict(), sort_keys=True) for c in twin.ledger.checkpoints]
    twin.ingest_sensor_inputs([varied(h) for h in range(44, 48)])
    assert [json.dumps(c.to_dict(), sort_keys=True) for c in twin.ledger.checkpoints] == taken


def test_replay_of_a_sensor_input_older_than_the_newest_event():
    """An event logged at 10:05 is applied before the poll ingests the
    10:00 reading: the reading is replayed in before the event."""
    readings = series(0, 10)
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(readings[:-1])
    twin.record_event("e1", feed(10.1), now=at(10.1))
    provenance, _ = twin.ingest_sensor_inputs(readings[-1:], history=history_of(readings[:-1]))
    assert provenance[0]["replayed"] is True and provenance[0]["late"] is False
    assert chemistry(twin) == chemistry(in_order(readings + [("e1", feed(10.1))]))
    assert twin.last_input_at == at(10)


def test_ledger_event_before_every_checkpoint_is_placed_late_once():
    """A twin upgraded from a v3 snapshot can only replay after its newest
    input; an older event is applied at the current state, once."""
    twin = PondTwin.from_snapshot(json.loads((FIXTURES / "snapshot_v3_sensor_inputs.json").read_text()))
    newest = twin.ledger.newest_input_at
    old = PondEvent(kind=EventKind.FEEDING, time=newest - timedelta(hours=5), food_grams=40.0, protein_percent=40.0)
    tan = twin.chemistry._tan_mg
    out = twin.record_event("late-1", old, now=newest + timedelta(hours=1))
    assert out["status"] == "applied" and out["placed_late"] is True and out["replayed"] is False
    assert twin.ledger.get("late-1").model_time == newest + el.LATE_STEP
    assert twin.chemistry._tan_mg > tan
    after = twin.chemistry._tan_mg
    assert twin.record_event("late-1", old, now=newest + timedelta(hours=2))["status"] == "duplicate"
    assert twin.chemistry._tan_mg == after


def test_ledger_checkpoints_and_entries_are_kept_for_the_window():
    twin = PondTwin.create(CONFIG)
    start = datetime.now(timezone.utc) - timedelta(days=45)
    for day in range(45):
        t = start + timedelta(days=day)
        twin.ingest_sensor_inputs([SensorInput(t, "ingestion", {"ph": 7.5, "tds": 200.0, "temp": 28.0,
                                                                "lux": None}, {})])
        twin.record_event(f"day-{day}", PondEvent(kind=EventKind.FEEDING, time=t + timedelta(minutes=1),
                                                 food_grams=10.0, protein_percent=40.0), now=t)
    newest = twin.ledger.newest_input_at
    throughs = [c.through for c in twin.ledger.checkpoints]
    assert None not in throughs, "the genesis checkpoint goes once the window is covered"
    assert throughs[0] <= newest - el.WINDOW
    assert throughs[1] > newest - el.WINDOW - timedelta(days=1)
    assert len(throughs) <= 32
    kept = {int(k.split("-")[1]) for k in twin.ledger.entries}
    # The window plus ENTRY_GRACE, pruned at each day's checkpoint: entries
    # older than 32 days are gone, the last 30 days are all there.
    assert 44 - 32 <= min(kept) <= 44 - 30 and max(kept) == 44
    # The whole window can still be replayed.
    assert twin.ledger.checkpoint_before(newest - el.WINDOW + timedelta(hours=1)) is not None


# ---------------------------------------------------------------------
# Reconciliation with pondInterventions
# ---------------------------------------------------------------------
def row(row_id: int, event: PondEvent, event_id=None, created_at=None) -> dict:
    kinds = {v: k for k, v in el.ROW_KINDS.items()}
    return {"id": row_id, "event_id": event_id or el.derived_event_id(row_id),
            "event_type": kinds[event.kind], "event_timestamp": event.time.isoformat(),
            "volume_percentage": event.volume_percent, "volume_litres": event.volume_litres,
            "food_grams": event.food_grams, "protein_percentage": event.protein_percent,
            "algae_method": event.scrub_type, "created_at": (created_at or event.time).isoformat()}


def test_reconcile_applies_a_row_the_twin_never_saw():
    readings = series(0, 10)
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(readings)
    report = twin.reconcile_events([row(1, feed(4))], now=at(10.5), history=history_of(readings))
    assert report["applied"] == [el.derived_event_id(1)] and report["replayed"] is True
    assert chemistry(twin) == chemistry(in_order(readings[:5] + [("x", feed(4))] + readings[5:]))
    again = twin.reconcile_events([row(1, feed(4))], now=at(11), history=history_of(readings))
    assert again["applied"] == [] and again["replayed"] is False


def test_reconcile_does_not_reapply_a_posted_event_and_trusts_the_post():
    """The row of a posted event can hold phone-local wall time (8 hours
    off): first sight records the row without changing the event."""
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(series(0, 3))
    event_id = new_id()
    twin.record_event(event_id, feed(3.5), now=at(3.5))
    state = chemistry(twin)
    shifted = row(9, feed(11.5), event_id=event_id)
    report = twin.reconcile_events([shifted], now=at(4))
    assert report["applied"] == [] and report["revised"] == []
    assert chemistry(twin) == state
    assert twin.ledger.get(event_id).row_id == 9 and twin.ledger.get(event_id).event.time == at(3.5)


def test_reconcile_lets_a_legacy_post_claim_its_row():
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(series(0, 3))
    out = twin.record_event(None, feed(3.5), now=at(3.5))
    assert out["event_id"].startswith(el.LEGACY_PREFIX)
    state = chemistry(twin)
    report = twin.reconcile_events([row(5, feed(11.5))], now=at(4))  # same feed, local wall time
    assert report["claimed"] == [el.derived_event_id(5)] and report["applied"] == []
    assert chemistry(twin) == state
    assert twin.ledger.get(el.derived_event_id(5)).legacy is True


def test_reconcile_edit_replays_with_the_new_values():
    readings = series(0, 12)
    history = history_of(readings)
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(readings[:4])
    twin.reconcile_events([row(1, feed(3.5, grams=50.0))], now=at(3.6), history=history)
    twin.ingest_sensor_inputs(readings[4:])
    edited = twin.reconcile_events([row(1, feed(6.5, grams=20.0))], now=at(12.5), history=history)
    assert edited["revised"] == [el.derived_event_id(1)] and edited["replayed"] is True
    assert twin.ledger.get(el.derived_event_id(1)).revision == 1
    assert chemistry(twin) == chemistry(in_order(readings[:7] + [("x", feed(6.5, grams=20.0))] + readings[7:]))


def test_reconcile_delete_replays_without_the_event_and_leaves_a_tombstone():
    readings = series(0, 12)
    history = history_of(readings)
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(readings[:4])
    twin.reconcile_events([row(1, feed(3.5))], now=at(3.6), history=history)
    twin.ingest_sensor_inputs(readings[4:])
    report = twin.reconcile_events([], now=at(12.5), history=history)
    event_id = el.derived_event_id(1)
    assert report["deleted"] == [event_id] and report["replayed"] is True
    assert chemistry(twin) == chemistry(in_order(readings))
    # A late retry of the original post cannot bring it back.
    assert twin.record_event(event_id, feed(3.5), now=at(13))["status"] == "deleted"
    assert chemistry(twin) == chemistry(in_order(readings))
    # The row coming back (an undone delete) applies it again.
    restored = twin.reconcile_events([row(1, feed(3.5))], now=at(13), history=history)
    assert restored["restored"] == [event_id]
    assert chemistry(twin) == chemistry(in_order(readings[:4] + [("x", feed(3.5))] + readings[4:]))


def test_reconcile_edit_that_cannot_be_replayed_is_reported_not_applied():
    twin = PondTwin.from_snapshot(json.loads((FIXTURES / "snapshot_v3_sensor_inputs.json").read_text()))
    newest = twin.ledger.newest_input_at
    old_feed = PondEvent(kind=EventKind.FEEDING, time=newest - timedelta(hours=2), food_grams=40.0,
                         protein_percent=40.0)
    now = newest + timedelta(hours=1)
    first = twin.reconcile_events([row(3, old_feed, created_at=now)], now=now)
    assert first["applied"] == [el.derived_event_id(3)]
    state = chemistry(twin)
    edited = PondEvent(kind=EventKind.FEEDING, time=old_feed.time, food_grams=5.0, protein_percent=40.0)
    report = twin.reconcile_events([row(3, edited, created_at=now)], now=now)
    assert report["not_replayable"] == [el.derived_event_id(3)] and report["revised"] == []
    assert chemistry(twin) == state
    assert twin.ledger.get(el.derived_event_id(3)).event.food_grams == 40.0


def test_reconcile_skips_rows_from_before_the_ledger_and_outside_the_window():
    twin = PondTwin.from_snapshot(json.loads((FIXTURES / "snapshot_v3_sensor_inputs.json").read_text()))
    since = twin.ledger.since
    before = row(1, PondEvent(kind=EventKind.FEEDING, time=since - timedelta(hours=1), food_grams=9.0,
                              protein_percent=40.0), created_at=since - timedelta(minutes=1))
    ancient = row(2, PondEvent(kind=EventKind.FEEDING, time=since - timedelta(days=40), food_grams=9.0,
                               protein_percent=40.0), created_at=since + timedelta(minutes=1))
    unknown = {**row(4, feed(1)), "event_type": "UNKNOWN_KIND"}
    report = twin.reconcile_events([before, ancient, unknown], now=since + timedelta(hours=1))
    assert report["applied"] == [] and len(report["skipped"]) == 2
    assert twin.ledger.entries == {}


# ---------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------
def test_ledger_loads_a_recorded_v3_snapshot():
    snap = json.loads((FIXTURES / "snapshot_v3_sensor_inputs.json").read_text())
    assert snap["version"] == 3 and "event_ledger" not in snap
    twin = PondTwin.from_snapshot(snap)
    assert twin.chemistry._tan_mg == snap["chemistry"]["tan_mg"]
    assert twin.ledger.since == datetime.fromisoformat(snap["saved_at"])
    assert twin.ledger.entries == {}
    newest = datetime.fromisoformat(snap["sensor_inputs"]["last_input_at"])
    assert twin.ledger.newest_input_at == newest
    assert [c.through for c in twin.ledger.checkpoints] == [newest]
    assert twin.to_snapshot()["version"] == 4


def test_ledger_survives_a_snapshot_round_trip():
    readings = series(0, 30, step=3.0)
    twin = PondTwin.create(CONFIG)
    twin.ingest_sensor_inputs(readings)
    twin.record_event("a", feed(10), now=at(31), history=history_of(readings))
    twin.record_event(None, water_change(31), now=at(31))
    restored = PondTwin.from_snapshot(json.loads(json.dumps(twin.to_snapshot())))
    assert restored.ledger.to_dict() == twin.ledger.to_dict()
    assert restored.record_event("a", feed(10), now=at(32))["status"] == "duplicate"
    # And it still replays: same result as the live twin.
    for t in (twin, restored):
        t.record_event("b", feed(20), now=at(32), history=history_of(readings))
    assert chemistry(restored) == chemistry(twin)


def test_ledger_event_ids_are_canonical_and_derived_ids_match_the_migration():
    assert el.normalise_event_id("3F2B8C1E-7D4A-4E5B-9C6D-0A1B2C3D4E5F") == "3f2b8c1e-7d4a-4e5b-9c6d-0a1b2c3d4e5f"
    with pytest.raises(ValueError):
        el.normalise_event_id("not-a-uuid")
    # md5('pondInterventions:1')::uuid, as 0015's intervention_event_id
    # computes it (checked against the database in tests/sql).
    assert el.derived_event_id(1) == "fa611248-9531-655e-f2b5-7b5177785a3a"
    assert el.derived_event_id(2) != el.derived_event_id(1)
