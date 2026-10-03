"""The effective-dated pond profile (issue #16): lookup rules, and the
engines reading the profile in force at each simulated time, so a
profile change mid-history applies from its effective time onward only.

Run from Backend/: python -m pytest -q -k profile
"""
from datetime import datetime, timedelta, timezone

import pytest

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models.engine import EventKind, PondConfig, PondEvent, RawSample
from koi.models.pond_twin import PondTwin
from koi.models.profile import PondProfile, ProfileHistory, profile_from_row, profile_from_userdata_config

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
CHANGE = T0 + timedelta(hours=6)
OLD = PondProfile(effective_from=T0 - timedelta(days=30), volume_l=1000, biomass_g=2000, depth_m=1.0)
NEW = PondProfile(effective_from=CHANGE, volume_l=2000, biomass_g=4000, depth_m=1.0, fish_type="Koi",
                  fish_count=6, tap_tds_ppm=120, tap_nitrate_ppm=2.0, aeration=True)
ENV = ev.DayEnvironment(air_temp_c=31.0, relative_humidity_pct=70.0, wind_speed_ms=3.0)
AENV = ae.AlgaeDayEnvironment(lux=20000, temp_c=29.0, no3_ppm=15.0)


def sample(t):
    return RawSample(time=t, ph=7.6, tds=220, temp_c=29.0, lux=20000)


def twin_with(history):
    twin = PondTwin.create(history.at(T0).pond_config())
    twin.profiles = history
    twin.use_profile(history.at(T0))
    return twin


def ingest(twin, now):
    return twin.ingest_environment(now=now, sample=sample(now), evaporation_env=ENV, algae_env=AENV)


# --- lookup ---------------------------------------------------------------

def test_profile_in_force_is_the_latest_effective_at_or_before_t():
    history = ProfileHistory([NEW, OLD])  # storage order does not matter
    assert history.at(CHANGE - timedelta(seconds=1)) is OLD
    assert history.at(CHANGE) is NEW
    assert history.at(CHANGE + timedelta(days=400)) is NEW
    # Before the first profile, the first is used.
    assert history.at(T0 - timedelta(days=365)) is OLD
    assert history.latest is NEW


def test_profile_changes_between_excludes_start_and_includes_end():
    history = ProfileHistory([OLD, NEW])
    assert history.changes_between(T0, CHANGE) == [(CHANGE, NEW)]
    assert history.changes_between(CHANGE, CHANGE + timedelta(hours=1)) == []
    assert history.changes_between(OLD.effective_from, T0) == []


def test_profile_from_userdata_is_undated_and_always_in_force():
    profile = profile_from_userdata_config({"volume_litres": 2500.0, "estimated_biomass_grams": 55050.0})
    history = ProfileHistory([profile])
    assert history.at(T0) is profile and profile.depth_m is None
    with pytest.raises(ValueError):
        ProfileHistory([profile, NEW])
    with pytest.raises(ValueError):
        ProfileHistory([])


def test_profile_row_parses_types_and_keeps_unknowns_unknown():
    profile = profile_from_row({"effective_from": "2026-09-01T06:00:00+08:00", "volume_l": "1500",
                                "biomass_g": 3000, "depth_m": None, "fish_count": None, "aeration": None})
    assert profile.effective_from == datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)
    assert profile.volume_l == 1500.0 and profile.depth_m is None and profile.fish_type is None
    naive = profile_from_row({"effective_from": "2026-09-01T06:00:00", "volume_l": 1, "biomass_g": 0})
    assert naive.effective_from == datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)


def test_profile_config_uses_model_defaults_for_unknown_fields_and_keeps_time_zone():
    config = PondProfile(volume_l=800, biomass_g=1500).pond_config(
        base=PondConfig(volume_litres=1, estimated_biomass_grams=1, time_zone="UTC"))
    defaults = PondConfig(volume_litres=800, estimated_biomass_grams=1500)
    assert (config.fish_type, config.fish_count, config.tap_tds_ppm, config.tap_nitrate_ppm) == (
        defaults.fish_type, defaults.fish_count, defaults.tap_tds_ppm, defaults.tap_nitrate_ppm)
    assert config.time_zone == "UTC"
    full = NEW.pond_config()
    assert (full.volume_litres, full.estimated_biomass_grams, full.fish_type, full.fish_count,
            full.tap_tds_ppm, full.tap_nitrate_ppm) == (2000, 4000, "Koi", 6, 120, 2.0)


def test_profile_unknown_depth_uses_the_default_depth():
    twin = twin_with(ProfileHistory([PondProfile(volume_l=1200, biomass_g=10)]))
    assert twin.evaporation.config.pond_depth_m == ev.DEFAULT_POND_DEPTH_M
    assert twin.evaporation.config.surface_area_m2 == pytest.approx(1.2 / ev.DEFAULT_POND_DEPTH_M)


# --- mid-history change: evaporation --------------------------------------

def _loss_over_cycle(history):
    twin = twin_with(history)
    ingest(twin, T0)
    ingest(twin, T0 + timedelta(hours=12))
    return twin


def test_profile_change_mid_cycle_splits_the_evaporation_integral_at_its_effective_time():
    unchanged = _loss_over_cycle(ProfileHistory([OLD]))
    changed = _loss_over_cycle(ProfileHistory([OLD, NEW]))
    old_loss = unchanged.evaporation._cumulative_loss_litres
    assert old_loss > 0
    # 6 h at the old surface area (1 m2), then 6 h at the new one (2 m2):
    # the half before the change is untouched, the half after doubles.
    assert changed.evaporation._cumulative_loss_litres == pytest.approx(old_loss * 1.5)
    # The engines end on the profile in force now.
    assert changed.evaporation.config.volume_litres == 2000
    assert changed.chemistry.config.fish_type == "Koi"


def test_profile_change_before_the_last_advance_does_not_rewrite_earlier_loss():
    history = ProfileHistory([OLD, NEW])
    twin = twin_with(history)
    ingest(twin, T0)
    ingest(twin, CHANGE)  # integrates T0..CHANGE, all at the old profile
    loss_before = twin.evaporation._cumulative_loss_litres
    reference = _loss_over_cycle(ProfileHistory([OLD]))  # T0..T0+12h at the old area
    assert loss_before == pytest.approx(reference.evaporation._cumulative_loss_litres / 2)
    ingest(twin, CHANGE + timedelta(hours=6))  # entirely at the new profile
    assert twin.evaporation._cumulative_loss_litres == pytest.approx(loss_before * 3)


def test_profile_effective_in_the_future_is_not_applied_yet():
    later = PondProfile(effective_from=T0 + timedelta(days=5), volume_l=9000, biomass_g=1, depth_m=3.0)
    twin = _loss_over_cycle(ProfileHistory([OLD, later]))
    reference = _loss_over_cycle(ProfileHistory([OLD]))
    assert twin.evaporation._cumulative_loss_litres == pytest.approx(reference.evaporation._cumulative_loss_litres)
    assert twin.evaporation.config.volume_litres == 1000


def test_profile_split_without_changes_matches_the_plain_engine_exactly():
    plain = ev.EvaporationFeedEngine(ev.EvaporationConfig(volume_litres=1000, estimated_biomass_grams=1,
                                                          pond_depth_m=1.0))
    split = ev.EvaporationFeedEngine(ev.EvaporationConfig(volume_litres=1000, estimated_biomass_grams=1,
                                                          pond_depth_m=1.0))
    for engine, changes in ((plain, None), (split, [])):
        engine.advance_state(T0, ENV)
        engine.advance_state(T0 + timedelta(hours=12), ENV, config_changes=changes)
    assert split._cumulative_loss_litres == plain._cumulative_loss_litres


def test_profile_change_inside_a_capped_outage_only_counts_the_integrated_day():
    # Two days since the last advance: only the final day is integrated,
    # and a change before that day applies to all of it.
    engine = ev.EvaporationFeedEngine(ev.EvaporationConfig(volume_litres=1000, estimated_biomass_grams=1,
                                                           pond_depth_m=1.0))
    engine.advance_state(T0, ENV)
    big = ev.EvaporationConfig(volume_litres=3000, estimated_biomass_grams=1, pond_depth_m=1.0)
    step = engine.advance_state(T0 + timedelta(days=2), ENV, config_changes=[(T0 + timedelta(hours=12), big)])
    reference = ev.EvaporationFeedEngine(big)
    reference.advance_state(T0, ENV)
    assert step == pytest.approx(reference.advance_state(T0 + timedelta(days=1), ENV))
    assert engine.config is big


# --- mid-history change: events -----------------------------------------------

def _tan_after_water_change(at):
    twin = twin_with(ProfileHistory([OLD, NEW]))
    twin.apply_event(PondEvent(kind=EventKind.FEEDING, time=T0, food_grams=100.0, protein_percent=40.0),
                     now=at)
    before = twin.chemistry._tan_mg
    twin.apply_event(PondEvent(kind=EventKind.WATER_CHANGE, time=at, volume_litres=500.0),
                     now=CHANGE + timedelta(days=1))
    return twin, twin.chemistry._tan_mg / before


def test_profile_change_applies_to_events_from_its_effective_time_onward():
    # 500 L out of the 1000 L pond before the change, out of 2000 L after.
    twin, kept_before = _tan_after_water_change(CHANGE - timedelta(minutes=1))
    assert kept_before == pytest.approx(0.5)
    _, kept_after = _tan_after_water_change(CHANGE)
    assert kept_after == pytest.approx(0.75)
    # Either way the engines are left on the profile in force at now.
    assert twin.chemistry.config.volume_litres == 2000
    assert twin.evaporation.config.volume_litres == 2000


def test_profile_tap_water_in_force_at_the_change_is_used_for_dilution():
    twin = twin_with(ProfileHistory([OLD, NEW]))
    twin.chemistry._last_tds_ppm = 300.0
    twin.apply_event(PondEvent(kind=EventKind.WATER_CHANGE, time=T0, volume_percent=50.0), now=T0)
    assert twin.chemistry._last_tds_ppm == pytest.approx(300 * 0.5 + 30.0 * 0.5)  # default tap TDS
    twin.apply_event(PondEvent(kind=EventKind.WATER_CHANGE, time=CHANGE, volume_percent=50.0), now=CHANGE)
    assert twin.chemistry._last_tds_ppm == pytest.approx(165 * 0.5 + 120 * 0.5)  # NEW's tap TDS


def test_profile_history_is_not_stored_in_the_snapshot():
    twin = twin_with(ProfileHistory([OLD, NEW]))
    ingest(twin, CHANGE)
    snap = twin.to_snapshot()
    assert "profiles" not in snap
    assert PondTwin.from_snapshot(snap).profiles is None
