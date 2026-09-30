"""Round 2 regression tests: WaterChemistryEngine._daily retention.

Not part of the original deliverable - added because the existing suite
never exercises _daily's size, reactivity_trend, or tds_trend at all (no
hits for any of those across test_*.py). That gap is exactly where an
unbounded-growth bug (and, while fixing it, a day-key string-comparison
bug) could hide. Run: python3 test_daily_retention.py
"""
from datetime import datetime, timedelta, timezone

from engine import (
    DailyAggregate,
    PondConfig,
    RawSample,
    WaterChemistryEngine,
)

FAIL = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


CONFIG = PondConfig(volume_litres=5000, estimated_biomass_grams=8000)


def sample_at(t, ph=7.2, tds=200.0, temp=28.0, lux=15000.0):
    return RawSample(time=t, ph=ph, tds=tds, temp_c=temp, lux=lux)


# =====================================================================
print("=== 1. _daily stays bounded across 90 days of continuous polling ===")
engine = WaterChemistryEngine(CONFIG)
t0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
for day in range(90):
    t = t0 + timedelta(days=day)
    # Small deterministic wobble so the sensor gate's stale-run check
    # (4 identical readings in a row) never suppresses these as "trusted".
    engine.ingest_sensor_sample(sample_at(t, ph=7.0 + 0.01 * (day % 5)))

print(f"  _daily entries after 90 days of samples: {len(engine._daily)}")
check(
    "_daily never exceeds DAILY_RETENTION_DAYS regardless of pond age",
    len(engine._daily) <= WaterChemistryEngine.DAILY_RETENTION_DAYS,
    f"{len(engine._daily)} entries (cap {WaterChemistryEngine.DAILY_RETENTION_DAYS})",
)

snap = engine.to_snapshot()
print(f"  serialized 'daily' payload: {len(snap['daily'])} day-entries")
check(
    "snapshot 'daily' payload does not grow with pond age either",
    len(snap["daily"]) <= WaterChemistryEngine.DAILY_RETENTION_DAYS,
)

# =====================================================================
print("\n=== 2. Month-boundary correctness (the day-key string-sort trap) ===")
# _day_key() produces "{year}-{month}-{day}" with NO zero-padding, so as a
# STRING, "2026-9-10" sorts before "2026-9-5" ('1' < '5' at the first
# differing character) even though Sept 10 is chronologically later than
# Sept 5. A cutoff comparison done on the string keys would therefore
# wrongly drop days that are actually still inside the retention window
# whenever a day-of-month >= 10 is compared against one < 10 in the same
# month (or a month >= 10 against one < 10 in the same year). This test
# pins the CORRECT behavior: compare by the real datetime, not the key.
engine2 = WaterChemistryEngine(CONFIG)
reference = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)  # cutoff = Sept 5

# Sept 10 is 25 days before Oct 5 - inside the 30-day window, MUST survive.
# A string-key comparison would place "2026-9-10" before "2026-9-5" and
# incorrectly prune it.
engine2.ingest_sensor_sample(sample_at(datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)))
# Sept 1 is 34 days before Oct 5 - outside the window, MUST be pruned.
engine2.ingest_sensor_sample(sample_at(datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)))
# The reference day itself, so pruning has something recent to anchor to.
engine2.ingest_sensor_sample(sample_at(reference))

keys = set(engine2._daily.keys())
print(f"  retained day-keys: {sorted(keys)}")
check("Sept 10 (25 days old, inside window) survives pruning", "2026-9-10" in keys)
check("Sept 1 (34 days old, outside window) is pruned", "2026-9-1" not in keys)
check("the reference day itself is present", "2026-10-5" in keys)

# =====================================================================
print("\n=== 3. from_snapshot self-heals a pre-existing unbounded snapshot ===")
# Simulate a snapshot written before DAILY_RETENTION_DAYS existed: 120 days
# of daily entries, no cap, exactly the shape to_snapshot() would have
# produced under the old code.
now = datetime.now(timezone.utc)
legacy_daily = {}
for day in range(120):
    d = now - timedelta(days=day)
    key = f"{d.year}-{d.month}-{d.day}"
    legacy_daily[key] = DailyAggregate(day=d).to_dict()

legacy_snapshot = {
    "config": CONFIG.__dict__,
    "events": [],
    "daily": legacy_daily,
    "tan_mg": 0.0, "no2_mg": 0.0, "no3_mg": 0.0,
    "algae_suppression_days_remaining": 0,
    "last_tds_ppm": 0.0,
    "last_ingest_time": None,
    "gate": {},
}
print(f"  legacy snapshot carried {len(legacy_daily)} day-entries")
restored = WaterChemistryEngine.from_snapshot(legacy_snapshot)
print(f"  after from_snapshot(): {len(restored._daily)} day-entries")
check(
    "loading a legacy unbounded snapshot prunes it immediately, no migration needed",
    len(restored._daily) <= WaterChemistryEngine.DAILY_RETENTION_DAYS,
    f"{len(restored._daily)} entries",
)

# =====================================================================
print("\n=== 4. assess() still produces a valid trend from the rolling window ===")
engine3 = WaterChemistryEngine(CONFIG)
t0b = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
for day in range(45):
    # 6 samples/day (has_enough_data needs >=6 trusted ph AND lux readings),
    # spaced 4h apart, with a slow upward ph drift so reactivity_trend has
    # something non-trivial to measure.
    for slot in range(6):
        t = t0b + timedelta(days=day, hours=4 * slot)
        ph = 7.0 + 0.002 * day + 0.03 * (slot % 2)
        engine3.ingest_sensor_sample(sample_at(t, ph=ph, lux=5000.0 + 500.0 * (slot % 2)))

assessment = engine3.assess(rain_incoming=False)
print(f"  usable _daily entries: {len(engine3._daily)}")
print(f"  reactivity_trend: {assessment.reactivity_trend}")
check(
    "_daily is bounded even with 45 days of dense (6/day) sensor history",
    len(engine3._daily) <= WaterChemistryEngine.DAILY_RETENTION_DAYS,
)
check("assess() still runs without error on the pruned history", assessment is not None)
check(
    "reactivity_trend is computed (not None) from the retained window",
    assessment.reactivity_trend is not None,
)

# =====================================================================
if FAIL:
    print(f"\n{len(FAIL)} CHECK(S) FAILED: {FAIL}")
    raise SystemExit(1)
print("\nALL DAILY-RETENTION CHECKS PASSED")
