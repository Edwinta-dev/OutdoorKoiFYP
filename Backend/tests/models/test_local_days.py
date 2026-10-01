"""Issue #15: the chemistry engine's calendar days run from local midnight
to local midnight in the pond's time zone (Asia/Singapore by default), as
aggregate_daily_sensor_data() does in SQL. Parity with the SQL aggregate on
the same samples is in tests/sql/test_sql_local_days.py.

Run from Backend/: python -m pytest tests/models/test_local_days.py
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from koi.models.engine import (
    DAILY_SNAPSHOT_VERSION,
    DAY_BASIS_LEGACY,
    DAY_BASIS_LOCAL,
    EventKind,
    PondConfig,
    PondEvent,
    RawSample,
    WaterChemistryEngine,
)
from koi.models.local_time import local_day_key, zone
from koi.models.pond_twin import PondTwin

CONFIG = PondConfig(volume_litres=5000, estimated_biomass_grams=8000)
SGT = zone("Asia/Singapore")
OLD_SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_v2_utc_days.json"


def sgt(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=SGT)


def sample_at(t, ph=7.2, tds=200.0, temp=28.0, lux=15000.0):
    return RawSample(time=t, ph=ph, tds=tds, temp_c=temp, lux=lux)


def keys_for(times):
    engine = WaterChemistryEngine(CONFIG)
    for i, t in enumerate(times):
        engine.ingest_sensor_sample(sample_at(t, ph=7.2 + 0.01 * (i % 3), tds=200.0 + i))
    return engine


# ---------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------
@pytest.mark.parametrize("before, after, before_key, after_key", [
    (sgt(2026, 3, 1, 23, 59), sgt(2026, 3, 2, 0, 1), "2026-03-01", "2026-03-02"),     # local midnight
    (sgt(2026, 2, 28, 23, 59), sgt(2026, 3, 1, 0, 1), "2026-02-28", "2026-03-01"),    # month
    (sgt(2026, 12, 31, 23, 59), sgt(2027, 1, 1, 0, 1), "2026-12-31", "2027-01-01"),   # year
], ids=["midnight", "month", "year"])
def test_day_changes_at_local_midnight(before, after, before_key, after_key):
    engine = keys_for([before, after])
    assert sorted(engine._daily) == [before_key, after_key]
    assert all(d.basis == DAY_BASIS_LOCAL for d in engine._daily.values())


@pytest.mark.parametrize("day", [date(2026, 3, 2), date(2026, 12, 31), date(2027, 1, 1)])
def test_utc_midnight_at_0800_sgt_is_not_a_day_boundary(day):
    # 07:59 and 08:01 SGT straddle UTC midnight; they are one local day.
    engine = keys_for([sgt(day.year, day.month, day.day, 7, 59), sgt(day.year, day.month, day.day, 8, 1)])
    assert list(engine._daily) == [day.isoformat()]
    assert len(engine._daily[day.isoformat()].trusted_ph) == 2


def test_evening_and_next_morning_are_different_calendar_dates():
    # 20:00 on day D and 06:00 on D+1 are one night but two calendar dates.
    # Calendar keys do not model overnight episodes; none of the models
    # needs one (night_ph is per calendar day and is not read).
    engine = keys_for([sgt(2026, 5, 10, 20, 0), sgt(2026, 5, 11, 6, 0)])
    assert sorted(engine._daily) == ["2026-05-10", "2026-05-11"]


def test_the_same_instant_gets_the_same_day_whatever_offset_it_carries():
    instant = datetime(2026, 4, 30, 17, 30, tzinfo=timezone.utc)  # 01:30 SGT on 1 May
    assert local_day_key(instant) == local_day_key(instant.astimezone(SGT)) == "2026-05-01"
    assert local_day_key(instant.replace(tzinfo=None)) == "2026-05-01", "naive is taken as UTC"


def test_volume_event_marks_its_local_day():
    # 07:00 SGT on 3 June is still 2 June in UTC.
    engine = WaterChemistryEngine(CONFIG)
    engine.apply_event(PondEvent(kind=EventKind.WATER_CHANGE, time=sgt(2026, 6, 3, 7, 0), volume_percent=20))
    assert engine._daily["2026-06-03"].had_volume_event
    assert "2026-06-02" not in engine._daily


def test_pond_time_zone_is_configurable():
    engine = WaterChemistryEngine(PondConfig(volume_litres=5000, estimated_biomass_grams=8000, time_zone="UTC"))
    engine.ingest_sensor_sample(sample_at(sgt(2026, 3, 2, 7, 59)))
    assert list(engine._daily) == ["2026-03-01"]


# ---------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------
@pytest.mark.parametrize("hour", [0, 12, 23])
def test_retention_keeps_exactly_the_window_of_local_dates(hour):
    # Independent of the time of day the reference sample arrives.
    n = WaterChemistryEngine.DAILY_RETENTION_DAYS
    times = [sgt(2026, 1, 1, 12) + timedelta(days=i) for i in range(n + 5)]
    times.append(sgt(2026, 1, 1, hour) + timedelta(days=n + 5))
    engine = keys_for(times)
    last = (date(2026, 1, 1) + timedelta(days=n + 5))
    assert sorted(engine._daily) == [(last - timedelta(days=k)).isoformat() for k in range(n - 1, -1, -1)]


# ---------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------
def test_new_snapshot_round_trips_local_days():
    engine = keys_for([sgt(2026, 3, 1, 23, 59), sgt(2026, 3, 2, 0, 1)])
    snap = json.loads(json.dumps(engine.to_snapshot()))
    assert snap["daily_version"] == DAILY_SNAPSHOT_VERSION
    assert snap["config"]["time_zone"] == "Asia/Singapore"
    restored = WaterChemistryEngine._load_daily(snap)
    assert sorted(restored) == ["2026-03-01", "2026-03-02"]
    assert [d.calendar_day for d in restored.values()] == [date(2026, 3, 1), date(2026, 3, 2)]
    assert all(d.basis == DAY_BASIS_LOCAL for d in restored.values())


def test_recorded_old_snapshot_loads():
    """snapshot_v2_utc_days.json was written by the engine before #15 (its
    daily keys are unpadded UTC dates, no daily_version, no time_zone)."""
    snap = json.loads(OLD_SNAPSHOT.read_text(encoding="utf-8"))
    chem = snap["chemistry"]
    assert "daily_version" not in chem and "time_zone" not in chem["config"]

    twin = PondTwin.from_snapshot(snap)
    assert twin.chemistry.config.time_zone == "Asia/Singapore"
    assert twin.chemistry._tan_mg == chem["tan_mg"]
    assert twin.chemistry._no3_mg == chem["no3_mg"]
    assert twin.chemistry._last_ingest_time == datetime.fromisoformat(chem["last_ingest_time"])
    # Whatever survived the now-relative retention prune is legacy-keyed.
    assert all(k.startswith("legacy:") for k in twin.chemistry._daily)
    twin.to_snapshot()  # and it saves in the new shape


def test_old_utc_day_buckets_are_kept_as_legacy_not_rebucketed():
    snap = json.loads(OLD_SNAPSHOT.read_text(encoding="utf-8"))["chemistry"]
    daily = WaterChemistryEngine._load_daily(snap)
    assert sorted(daily) == ["legacy:2026-09-28", "legacy:2026-09-29", "legacy:2026-09-30", "legacy:2026-10-01"]
    for key, old_key in (("legacy:2026-09-30", "2026-9-30"), ("legacy:2026-10-01", "2026-10-1")):
        assert daily[key].basis == DAY_BASIS_LEGACY
        # Samples kept exactly as stored: no re-bucketing, no merged means.
        assert daily[key].trusted_ph == snap["daily"][old_key]["trusted_ph"]
        assert daily[key].trusted_tds == snap["daily"][old_key]["trusted_tds"]


def test_legacy_and_local_buckets_for_the_same_date_stay_separate():
    snap = json.loads(OLD_SNAPSHOT.read_text(encoding="utf-8"))["chemistry"]
    engine = WaterChemistryEngine(CONFIG)
    engine._daily = WaterChemistryEngine._load_daily(snap)
    before = list(engine._daily["legacy:2026-10-01"].trusted_ph)
    # 2 Oct 07:00 SGT is still 1 Oct in UTC: the old engine would have
    # added it to its "2026-10-1" bucket; now it starts local 2 Oct.
    engine.ingest_sensor_sample(sample_at(sgt(2026, 10, 2, 7, 0)))
    engine.ingest_sensor_sample(sample_at(sgt(2026, 10, 1, 23, 0), ph=7.21))
    assert engine._daily["legacy:2026-10-01"].trusted_ph == before
    assert engine._daily["2026-10-02"].basis == DAY_BASIS_LOCAL
    assert engine._daily["2026-10-01"].basis == DAY_BASIS_LOCAL

    report = engine.daily_provenance()
    assert report["legacy_days"] == ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]
    assert report["local_days"] == 2
    assert engine.assess(rain_incoming=False) is not None


def test_legacy_buckets_age_out_of_the_window():
    snap = json.loads(OLD_SNAPSHOT.read_text(encoding="utf-8"))["chemistry"]
    engine = WaterChemistryEngine(CONFIG)
    engine._daily = WaterChemistryEngine._load_daily(snap)
    engine.ingest_sensor_sample(sample_at(sgt(2026, 10, 30, 12)))  # 30 days after 30 Sep
    assert engine.daily_provenance()["legacy_days"] == ["2026-10-01"]
    engine.ingest_sensor_sample(sample_at(sgt(2026, 10, 31, 12)))
    assert engine.daily_provenance()["legacy_days"] == []
