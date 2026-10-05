"""Issue #18: SensorData rows -> the model's sensor inputs, and channel
freshness (koi/models/sensor_inputs.py). Pure; no storage.

Run from Backend/: python -m pytest tests/models/test_sensor_inputs.py
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from koi.models.engine import RawSample, SampleVerdict, SensorChannel, SensorGate
from koi.models.pond_twin import SNAPSHOT_VERSION, PondTwin
from koi.models.sensor_inputs import (
    TIME_BASIS_INGESTION,
    TIME_BASIS_SAMPLE,
    channel_freshness,
    complete_groups,
    fresh_value,
    group_rows,
)

T0 = datetime(2026, 10, 4, tzinfo=timezone.utc)
OLD_SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_v2_utc_days.json"


def row(id, sensor_type, value, at, **extra):
    return {"id": id, "sensor_type": sensor_type, "value": value, "created_at": at.isoformat(), **extra}


def upload(first_id, at, **values):
    return [row(first_id + i, t, v, at) for i, (t, v) in enumerate(values.items())]


def test_rows_at_one_time_form_one_input_with_every_channel():
    rows = upload(1, T0, temp=29.0, TDS=220.0, pH=7.6, LUX=20000.0)
    d = group_rows(rows)
    [only] = d.inputs
    assert only.time == T0 and only.time_basis == TIME_BASIS_INGESTION
    assert only.channels == {"ph": 7.6, "tds": 220.0, "temp": 29.0, "lux": 20000.0}
    assert only.row_ids == {"temp": 1, "tds": 2, "ph": 3, "lux": 4} and only.missing == []
    assert [e["disposition"] for e in d.ledger] == ["applied"] * 4
    assert d.watermark == T0


def test_inputs_are_ordered_by_time_then_id_whatever_order_rows_arrive_in():
    late_id_early_time = upload(50, T0, pH=7.1)
    early_id_late_time = upload(10, T0 + timedelta(minutes=15), pH=7.2)
    d = group_rows(early_id_late_time + late_id_early_time)
    assert [i.time for i in d.inputs] == [T0, T0 + timedelta(minutes=15)]
    assert [i.row_ids["ph"] for i in d.inputs] == [50, 10]
    # The watermark is ingestion progress: the newest insert time read.
    assert d.watermark == T0 + timedelta(minutes=15)


def test_a_channel_not_sent_is_missing_and_the_others_kept():
    d = group_rows(upload(1, T0, TDS=220.0, pH=7.6, LUX=-1.0))
    [only] = d.inputs
    assert only.channels["temp"] is None and only.missing == ["temp"]
    # -1 is a number: passed on, for the sensor gate to reject as out of range.
    assert only.channels["lux"] == -1.0


def test_unusable_values_are_missing_and_recorded_as_unusable():
    rows = [row(1, "pH", None, T0), row(2, "TDS", float("nan"), T0), row(3, "temp", "28", T0),
            row(4, "LUX", True, T0)]
    d = group_rows(rows)
    assert d.inputs[0].missing == ["ph", "tds", "temp", "lux"]
    assert [e["disposition"] for e in d.ledger] == ["unusable"] * 4


def test_a_channel_twice_at_one_time_uses_the_higher_id():
    rows = [row(7, "pH", 6.5, T0), row(6, "pH", 6.75, T0), row(8, "lux", 5.0, T0)]
    d = group_rows(rows)
    assert d.inputs[0].channels["ph"] == 6.5 and d.inputs[0].row_ids["ph"] == 7
    assert d.inputs[0].channels["lux"] == 5.0  # the lower-case spelling is light too
    assert {e["sensor_row_id"]: e["disposition"] for e in d.ledger} == {6: "superseded", 7: "applied",
                                                                        8: "applied"}


def test_unknown_sensor_types_are_not_read():
    d = group_rows([row(1, "humidity", 80.0, T0)])
    assert d.inputs == [] and d.ledger == [] and d.commit() is None


def test_a_sample_time_is_used_once_rows_carry_one():
    """#17 adds sampled_at; nothing else here changes when it does."""
    rows = [row(1, "pH", 7.0, T0, sampled_at=(T0 - timedelta(seconds=40)).isoformat())]
    d = group_rows(rows)
    assert d.inputs[0].time == T0 - timedelta(seconds=40) and d.inputs[0].time_basis == TIME_BASIS_SAMPLE
    assert d.ledger[0]["time_basis"] == TIME_BASIS_SAMPLE
    assert d.watermark == T0, "discovery progress stays on the insert time"


def test_commit_carries_ledger_rows_and_watermark():
    d = group_rows(upload(1, T0, pH=7.6))
    assert d.commit() == {"rows": [{"sensor_row_id": 1, "sensor_type": "pH", "effective_sample_time": T0.isoformat(),
                                    "time_basis": "ingestion", "disposition": "applied"}],
                          "watermark": T0.isoformat()}


def test_a_scan_cut_by_its_limit_never_splits_an_upload():
    rows = upload(1, T0, pH=7.0, TDS=200.0) + upload(3, T0 + timedelta(minutes=15), pH=7.1, TDS=201.0)
    assert complete_groups(rows[:3], 3) == rows[:2]
    assert complete_groups(rows, 5) == rows, "below the limit: whole"
    assert complete_groups(rows[:2], 2) == rows[:2], "one insert time: kept"


def test_freshness_follows_the_reading_time_and_the_cadence():
    channels = {"ph": {"value": 7.6, "reading_at": T0.isoformat(), "time_basis": "ingestion"}}
    cadence = timedelta(minutes=15)
    fresh = channel_freshness(channels, T0 + timedelta(minutes=30), cadence)
    assert fresh["ph"] == {"value": 7.6, "reading_at": T0.isoformat(), "time_basis": "ingestion",
                           "age_minutes": 30.0, "fresh": True}
    assert fresh_value(fresh, "ph") == 7.6
    stale = channel_freshness(channels, T0 + timedelta(minutes=31), cadence)
    assert stale["ph"]["fresh"] is False and fresh_value(stale, "ph") is None
    assert stale["tds"] == {"value": None, "reading_at": None, "time_basis": None, "age_minutes": None,
                            "fresh": False}


def test_gate_passes_a_missing_channel_without_touching_its_history():
    gate = SensorGate(lambda t: False)
    gate.validate(RawSample(T0, ph=7.6, tds=220.0, temp_c=29.0, lux=1000.0))
    gated = gate.validate(RawSample(T0 + timedelta(minutes=15), ph=7.6, tds=None, temp_c=None, lux=1000.0))
    assert gated.tds.verdict == SampleVerdict.MISSING and gated.tds.value == 220.0 and gated.tds.raw_value is None
    assert gated.temp.verdict == SampleVerdict.MISSING and not gated.temp.is_trusted
    assert gate._run_length[SensorChannel.TDS] == 1, "a missing reading is not a repeat"
    assert gated.ph.verdict == SampleVerdict.OK


def test_chemistry_without_any_temperature_waits_to_step_the_pools():
    twin = PondTwin.create(_config())
    twin.chemistry._tan_mg = 100.0
    twin.chemistry.ingest_sensor_sample(RawSample(T0, ph=7.6, tds=220.0, temp_c=None, lux=1000.0))
    warnings = twin.chemistry.ingest_sensor_sample(
        RawSample(T0 + timedelta(hours=1), ph=7.6, tds=220.0, temp_c=None, lux=1000.0))
    assert twin.chemistry._tan_mg == 100.0 and twin.chemistry._last_ingest_time == T0
    assert any("No water temperature yet" in w for w in warnings)
    twin.chemistry.ingest_sensor_sample(RawSample(T0 + timedelta(hours=2), ph=7.6, tds=220.0, temp_c=28.0, lux=1000.0))
    assert twin.chemistry._tan_mg < 100.0 and twin.chemistry._last_ingest_time == T0 + timedelta(hours=2)


def test_twin_snapshot_keeps_channel_readings_and_v2_loads_without_them():
    twin = PondTwin.create(_config())
    twin.ingest_sensor_inputs(group_rows(upload(1, T0, pH=7.6, temp=29.0)).inputs)
    snap = json.loads(json.dumps(twin.to_snapshot()))
    assert snap["version"] == SNAPSHOT_VERSION == 5
    again = PondTwin.from_snapshot(snap)
    assert again.sensor_channels == twin.sensor_channels and again.last_input_at == T0
    assert again.sensor_channels["ph"] == {"value": 7.6, "reading_at": T0.isoformat(), "time_basis": "ingestion",
                                           "row_id": 1}

    old = json.loads(OLD_SNAPSHOT.read_text(encoding="utf-8"))
    assert old["version"] == 2 and "sensor_inputs" not in old
    loaded = PondTwin.from_snapshot(old)
    assert loaded.sensor_channels == {} and loaded.last_input_at is None
    assert loaded.to_snapshot()["sensor_inputs"] == {"channels": {}, "last_input_at": None}


def test_a_late_input_is_applied_at_the_last_input_time_and_marked():
    twin = PondTwin.create(_config())
    twin.ingest_sensor_inputs(group_rows(upload(20, T0 + timedelta(minutes=15), pH=7.2, temp=29.0)).inputs)
    provenance, _ = twin.ingest_sensor_inputs(group_rows(upload(10, T0, pH=7.1, temp=28.0)).inputs)
    assert provenance == [{"time": T0.isoformat(), "time_basis": "ingestion",
                           "model_time": (T0 + timedelta(minutes=15)).isoformat(), "late": True,
                           "row_ids": {"ph": 10, "temp": 11}, "missing": ["tds", "lux"]}]
    assert twin.last_input_at == T0 + timedelta(minutes=15)
    # The older reading does not replace the newer one as the channel's latest.
    assert twin.sensor_channels["ph"]["value"] == 7.2


def _config():
    from koi.models.engine import PondConfig
    return PondConfig(volume_litres=5000.0, estimated_biomass_grams=8000.0)
