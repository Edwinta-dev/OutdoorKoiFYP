"""Issue #29: nominal salt mass, maintenance windows and ledger replay."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from koi.models.engine import EventKind, PondConfig, PondEvent, RawSample, WaterChemistryEngine, salt_tds_step
from koi.models.event_ledger import event_from_row
from koi.models.forecast_utils import slope_per_day, tds_series_after_events
from koi.models.pond_twin import PondTwin
from koi.models.sensor_inputs import SensorInput
from koi.storage.memory import MemoryStorage

T0 = (datetime.now(timezone.utc) - timedelta(days=4)).replace(hour=15, minute=0, second=0, microsecond=0)
CONFIG = PondConfig(volume_litres=100, estimated_biomass_grams=100)


def sample(at, tds=200):
    return RawSample(time=at, ph=7.5, temp_c=28, tds=tds, lux=1000)


def test_salt_grams_to_mg_per_litre_and_expected_tds():
    engine = WaterChemistryEngine(CONFIG)
    engine.ingest_sensor_sample(sample(T0))
    assert salt_tds_step(1, 100) == 10
    engine.apply_event(PondEvent(EventKind.SALT, T0 + timedelta(minutes=1), salt_grams=1))
    assert engine._last_tds_ppm == 210
    assert any(d.had_volume_event for d in engine._daily.values())


@pytest.mark.parametrize("volume", [0, -1, float("nan"), float("inf")])
def test_salt_invalid_volume_is_rejected_before_mutation(volume):
    engine = WaterChemistryEngine(PondConfig(volume_litres=volume, estimated_biomass_grams=100))
    before = engine.to_snapshot()
    with pytest.raises(ValueError, match="volume"):
        engine.apply_event(PondEvent(EventKind.SALT, T0, salt_grams=1))
    assert engine.to_snapshot() == before


@pytest.mark.parametrize("grams", [None, 0, -1, float("nan"), float("inf")])
def test_salt_invalid_mass(grams):
    with pytest.raises(ValueError, match="salt_grams"):
        salt_tds_step(grams, 100)


def test_salt_rate_gate_accepts_step_but_still_enforces_range():
    engine = WaterChemistryEngine(CONFIG)
    engine.ingest_sensor_sample(sample(T0))
    engine.apply_event(PondEvent(EventKind.SALT, T0 + timedelta(minutes=1), salt_grams=10))
    warnings = engine.ingest_sensor_sample(sample(T0 + timedelta(minutes=2), 300))
    assert not any("drift" in w for w in warnings)
    assert engine._last_tds_ppm == 300
    warnings = engine.ingest_sensor_sample(sample(T0 + timedelta(minutes=3), 4000))
    assert any("rejected" in w for w in warnings)
    engine.ingest_sensor_sample(sample(T0 + timedelta(hours=7), 300))
    warnings = engine.ingest_sensor_sample(sample(T0 + timedelta(hours=7, minutes=1), 500))
    assert any("drift" in w for w in warnings)


def test_filter_clean_exact_24_hours_and_snapshot_roundtrip():
    engine = WaterChemistryEngine(CONFIG)
    engine.apply_event(PondEvent(EventKind.FILTER_CLEAN, T0, notes="Rinsed media"))
    engine.ingest_sensor_sample(sample(T0 + timedelta(minutes=1)))
    day = engine._day_for(T0)
    assert day.ph_after_maintenance and day.tds_after_maintenance
    restored = WaterChemistryEngine.from_snapshot(engine.to_snapshot())
    assert restored._day_for(T0).ph_after_maintenance
    restored.ingest_sensor_sample(sample(T0 + timedelta(hours=23, minutes=59), 201))
    assert restored._day_for(T0 + timedelta(hours=23)).tds_after_maintenance
    # A separate engine avoids a bucket containing an earlier affected reading.
    boundary = WaterChemistryEngine(CONFIG)
    boundary.apply_event(PondEvent(EventKind.FILTER_CLEAN, T0))
    boundary.ingest_sensor_sample(sample(T0 + timedelta(hours=24)))
    assert not boundary._day_for(T0 + timedelta(hours=24)).ph_after_maintenance
    assert not boundary._day_for(T0 + timedelta(hours=24)).tds_after_maintenance


def test_filter_marks_only_channels_actually_read_and_does_not_change_pools():
    engine = WaterChemistryEngine(CONFIG)
    engine.apply_event(PondEvent(EventKind.FILTER_CLEAN, T0))
    engine.ingest_sensor_sample(RawSample(T0, 7.5, None, None, 1000))
    day = engine._day_for(T0)
    assert day.ph_after_maintenance and not day.tds_after_maintenance
    assert engine._tan_mg == engine._no2_mg == engine._no3_mg == 0


@pytest.mark.parametrize("kind,fields", [(EventKind.SALT, {"salt_grams": 10}),
                                        (EventKind.FILTER_CLEAN, {"notes": "Rinsed"})])
def test_salt_filter_duplicate_and_backdated_replay(kind, fields):
    inputs = [SensorInput(T0 + timedelta(hours=h), "ingestion",
                          {"ph": 7.5, "tds": 200 if h == 0 else 300, "temp": 28, "lux": 1000},
                          {"ph": h * 10 + 1, "tds": h * 10 + 2}) for h in (0, 2, 25)]
    def history(after, until):
        return [i for i in inputs if (after is None or i.time > after) and i.time <= until]
    event = PondEvent(kind, T0 + timedelta(hours=1), **fields)
    event_id = str(uuid.uuid4())
    ordered = PondTwin.create(CONFIG)
    ordered.ingest_sensor_inputs(inputs[:1])
    ordered.record_event(event_id, event, now=event.time)
    ordered.ingest_sensor_inputs(inputs[1:])
    late = PondTwin.create(CONFIG)
    late.ingest_sensor_inputs(inputs)
    result = late.record_event(event_id, event, now=inputs[-1].time, history=history)
    assert result["replayed"]
    assert late.chemistry.to_snapshot() == ordered.chemistry.to_snapshot()
    before = late.to_snapshot()
    before.pop("saved_at")
    assert late.record_event(event_id, event, history=history)["status"] == "duplicate"
    after = late.to_snapshot()
    after.pop("saved_at")
    assert after == before
    assert PondTwin.from_snapshot(late.to_snapshot()).ledger.get(event_id).event == event


def test_salt_filter_old_rows_and_old_snapshot_load():
    store = MemoryStorage()
    store.add_rows("pondInterventions", [{"userID": 1, "event_type": "FEEDING", "event_timestamp": T0.isoformat()},
        {"userID": 1, "event_type": "SALT", "event_timestamp": T0.isoformat(), "salt_grams": 1},
        {"userID": 1, "event_type": "FILTER_CLEAN", "event_timestamp": T0.isoformat(), "notes": "Media"}])
    old, salt, clean = store.fetch_interventions(1, None)
    assert old["salt_grams"] is old["notes"] is None
    assert event_from_row(old).salt_grams is None
    assert event_from_row(salt).salt_grams == 1
    assert event_from_row(clean).notes == "Media"
    snapshot = json.loads((Path(__file__).parents[1] / "fixtures/snapshot_v3_sensor_inputs.json").read_text())
    # Recorded older chemistry snapshots have no maintenance fields.
    daily = WaterChemistryEngine._load_daily(snapshot["chemistry"])
    assert daily
    engine = WaterChemistryEngine.from_snapshot(snapshot["chemistry"])
    assert all(not d.ph_after_maintenance and not d.tds_after_maintenance for d in daily.values())
    assert all(not d.ph_after_maintenance and not d.tds_after_maintenance for d in engine._daily.values())


def test_salt_cross_check_does_not_fit_across_added_mass():
    rows = [{"record_date": (T0 + timedelta(days=i)).date().isoformat(), "avg_value": 200 + (100 if i >= 2 else 0)}
            for i in range(6)]
    assert slope_per_day(rows) > 0
    event = PondEvent(EventKind.SALT, T0 + timedelta(days=2), salt_grams=10)
    kept = tds_series_after_events(rows, [event], "UTC")
    assert slope_per_day(kept) == 0
    assert slope_per_day(tds_series_after_events(rows[:4], [event], "UTC")) is None


def test_filter_cross_check_omits_both_overlapping_local_days():
    rows = [{"record_date": (T0 + timedelta(days=i)).date().isoformat(), "avg_value": 200} for i in range(4)]
    event = PondEvent(EventKind.FILTER_CLEAN, T0)
    assert tds_series_after_events(rows, [event], "UTC") == rows[2:]


def test_filter_cross_check_midnight_window_excludes_only_one_local_day():
    # Singapore midnight: the exclusive endpoint is the next midnight.
    midnight = datetime(2026, 9, 30, 16, tzinfo=timezone.utc)
    rows = [{"record_date": "2026-10-01", "avg_value": 200},
            {"record_date": "2026-10-02", "avg_value": 201}]
    event = PondEvent(EventKind.FILTER_CLEAN, midnight)
    assert tds_series_after_events(rows, [event], "Asia/Singapore") == rows[1:]


def test_filter_checkpoint_keeps_maintenance_window_for_replay():
    twin = PondTwin.create(CONFIG)
    twin.record_event(str(uuid.uuid4()), PondEvent(EventKind.FILTER_CLEAN, T0))
    through = T0 + timedelta(hours=10)
    checkpoint = twin._chemistry_checkpoint(through)
    assert checkpoint["events"][0]["kind"] == "filter_clean"
    restored = WaterChemistryEngine.from_snapshot(checkpoint)
    restored.ingest_sensor_sample(sample(T0 + timedelta(hours=12), 201))
    assert restored._day_for(T0 + timedelta(hours=12)).tds_after_maintenance


def test_salt_filter_legacy_fingerprint_null_fields_are_preserved():
    from koi.models.event_ledger import LedgerEntry, event_values

    event = PondEvent(EventKind.FEEDING, T0, food_grams=1)
    fingerprint = event_values(event)
    fingerprint.pop("salt_grams")
    fingerprint.pop("notes")
    entry = LedgerEntry("old", 1, event, T0, T0, "reconcile", row=fingerprint)
    restored = LedgerEntry.from_dict(entry.to_dict())
    assert restored.row == event_values(event)


def test_salt_invalid_volume_does_not_enter_ledger():
    twin = PondTwin.create(PondConfig(volume_litres=0, estimated_biomass_grams=100))
    with pytest.raises(ValueError, match="volume"):
        twin.record_event(str(uuid.uuid4()), PondEvent(EventKind.SALT, T0, salt_grams=1))
    assert not twin.ledger.entries


def test_salt_reconcile_invalid_old_mass_is_reported_and_not_applied():
    twin = PondTwin.create(CONFIG)
    event_id = str(uuid.uuid4())
    report = twin.reconcile_events([{"id": 1, "event_id": event_id, "event_type": "SALT",
                                    "event_timestamp": T0.isoformat(), "salt_grams": None}], now=T0)
    assert report["applied"] == []
    assert "salt_grams" in report["skipped"][0]["reason"]
    assert not twin.ledger.entries


def test_salt_uses_profile_volume_at_event_time():
    from koi.models.profile import PondProfile, ProfileHistory

    twin = PondTwin.create(CONFIG)
    twin.profiles = ProfileHistory([PondProfile(volume_l=100, biomass_g=100, effective_from=T0),
                                   PondProfile(volume_l=200, biomass_g=100,
                                               effective_from=T0 + timedelta(days=1))])
    twin.record_event(str(uuid.uuid4()), PondEvent(EventKind.SALT, T0, salt_grams=1),
                      now=T0 + timedelta(days=2))
    assert twin.chemistry._last_tds_ppm == 10
    assert twin.chemistry.config.volume_litres == 200
