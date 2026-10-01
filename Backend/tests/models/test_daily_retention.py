"""Round 2 regression tests: WaterChemistryEngine._daily retention.

Not part of the original deliverable - added because the existing suite
never exercises _daily's size, reactivity_trend, or tds_trend at all (no
hits for any of those across test_*.py). That gap is exactly where an
unbounded-growth bug (and, while fixing it, a day-key string-comparison
bug) could hide.
Run from Backend/: python -m pytest tests/models/test_daily_retention.py
"""
from datetime import datetime, timedelta, timezone

from koi.models.engine import (
    DailyAggregate,
    PondConfig,
    RawSample,
    WaterChemistryEngine,
)

CONFIG = PondConfig(volume_litres=5000, estimated_biomass_grams=8000)


def sample_at(t, ph=7.2, tds=200.0, temp=28.0, lux=15000.0):
    return RawSample(time=t, ph=ph, tds=tds, temp_c=temp, lux=lux)


def test_daily_stays_bounded_across_90_days_of_polling():
    engine = WaterChemistryEngine(CONFIG)
    t0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    for day in range(90):
        t = t0 + timedelta(days=day)
        # Small deterministic wobble so the sensor gate's stale-run check
        # (4 identical readings in a row) never suppresses these as "trusted".
        engine.ingest_sensor_sample(sample_at(t, ph=7.0 + 0.01 * (day % 5)))

    assert len(engine._daily) <= WaterChemistryEngine.DAILY_RETENTION_DAYS, \
        f"_daily never exceeds DAILY_RETENTION_DAYS regardless of pond age: " \
        f"{len(engine._daily)} entries (cap {WaterChemistryEngine.DAILY_RETENTION_DAYS})"

    snap = engine.to_snapshot()
    assert len(snap["daily"]) <= WaterChemistryEngine.DAILY_RETENTION_DAYS, \
        "snapshot 'daily' payload does not grow with pond age either"


def test_month_boundary_pruning_compares_dates_not_key_strings():
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

    # Keys are now zero-padded local (SGT) dates; 08:00 UTC is 16:00 SGT on
    # the same date. Legacy unpadded keys are covered in test_local_days.py.
    keys = set(engine2._daily.keys())
    assert "2026-09-10" in keys, f"Sept 10 (25 days old, inside window) survives pruning: {sorted(keys)}"
    assert "2026-09-01" not in keys, f"Sept 1 (34 days old, outside window) is pruned: {sorted(keys)}"
    assert "2026-10-05" in keys, f"the reference day itself is present: {sorted(keys)}"


def test_from_snapshot_self_heals_an_unbounded_snapshot():
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
    restored = WaterChemistryEngine.from_snapshot(legacy_snapshot)
    assert len(restored._daily) <= WaterChemistryEngine.DAILY_RETENTION_DAYS, \
        f"loading a legacy unbounded snapshot prunes it immediately, no migration needed: " \
        f"{len(restored._daily)} entries"


def test_assess_produces_a_trend_from_the_rolling_window():
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
    assert len(engine3._daily) <= WaterChemistryEngine.DAILY_RETENTION_DAYS, \
        "_daily is bounded even with 45 days of dense (6/day) sensor history"
    assert assessment is not None, "assess() still runs without error on the pruned history"
    assert assessment.reactivity_trend is not None, \
        "reactivity_trend is computed (not None) from the retained window"
