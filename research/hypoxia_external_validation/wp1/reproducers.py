"""Minimal standalone reproducers for the WP1 findings (production code imported read-only).
Run: python -I wp1/reproducers.py   (prints one block per finding)"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
from koi.models import event_ledger as el
from koi.models.engine import PondConfig, RawSample, SensorGate, WaterChemistryEngine
from koi.models.sensor_inputs import group_rows
from koi.models.tds_prompts import PromptConfig, TdsPrompts

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)

print("== F1  from_snapshot prunes daily buckets against the WALL CLOCK (data time is irrelevant)")
eng = WaterChemistryEngine(PondConfig(volume_litres=700.0, estimated_biomass_grams=0.0))
for k in range(8):
    eng.ingest_sensor_sample(RawSample(T0 + timedelta(hours=6 * k), 7.5 + 0.01 * k, 300.0 + k, 26.0 + 0.1 * k, None))
snap = eng.to_snapshot()
print("   buckets before:", len(snap["daily"]), " after from_snapshot(snapshot of Jan-2026 data):",
      len(WaterChemistryEngine.from_snapshot(snap).to_snapshot()["daily"]), "  (machine clock:", datetime.now(UTC).date(), ")")

print("== F2  rate check compares with the last TRUSTED value, which never updates: a sustained step latches")
g = SensorGate(lambda t: False)
seq = [7.00, 7.01, 7.02] + [7.60 + 0.002 * ((k % 7) - 3) for k in range(288)]
v = [g.validate(RawSample(T0 + timedelta(minutes=15 * k), x, None, None, None)).ph.verdict.value for k, x in enumerate(seq)]
print("   3 days at pH 7.60 after one 0.58 step (limit 0.25 per 15 min):", {x: v.count(x) for x in set(v)}, " last trusted:", g._last_trusted)
g2 = SensorGate(lambda t: abs((t - (T0 + timedelta(minutes=40))).total_seconds()) <= 6 * 3600)
v2 = [g2.validate(RawSample(T0 + timedelta(minutes=15 * k), x, None, None, None)).ph.verdict.value for k, x in enumerate(seq)]
print("   same series with a logged water change within 6 h:", {x: v2.count(x) for x in set(v2)})

print("== F2b the limit scales with the time since the last SAMPLE, so dense data is almost all rejected")
import numpy as np
x = list((7.5 + np.random.default_rng(_common.SEED).normal(0, 0.03, 600)).round(2))
for step in (1, 15):
    gg = SensorGate(lambda t: False)
    vv = [gg.validate(RawSample(T0 + timedelta(minutes=step * k), a, None, None, None)).ph.verdict.value for k, a in enumerate(x)]
    print(f"   noise sd 0.03 pH, one reading per {step:2d} min: ok fraction {vv.count('ok') / len(vv):.3f}")

print("== F4/F4b TdsPrompts.observe drops any reading with time <= last_reading_at")
r1 = {"id": 1, "sensor_type": "TDS", "value": 300.0, "created_at": T0, "sampled_at": None}
r2 = {"id": 2, "sensor_type": "TDS", "value": 400.0, "created_at": T0, "sampled_at": None}
a, b = TdsPrompts(), TdsPrompts()
cfg = PromptConfig(enabled=True)
a.observe(group_rows([r1, r2]).inputs, el.EventLedger(), cfg)
b.observe(group_rows([r1]).inputs, el.EventLedger(), cfg)
b.observe(group_rows([r2]).inputs, el.EventLedger(), cfg)
print("   one poll with both rows ->", a.recent, "; two polls ->", b.recent, "(PondTwin.sensor_channels uses the highest id: 400)")

print("== gate: 0 C is inside the temperature range, so a zeroed probe is only caught by the rate or stale rules")
g3 = SensorGate(lambda t: False)
out = [g3.validate(RawSample(T0 + timedelta(minutes=15 * k), None, None, t, None)).temp.verdict.value for k, t in enumerate([26.0, 26.1, 0.0, 0.0, 0.0, 0.0, 0.0, 26.2])]
print("   26.0 26.1 0 0 0 0 0 26.2 ->", out)
