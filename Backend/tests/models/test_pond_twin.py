"""Verification for the stateful multi-engine integration.

Covers the two things the refactor exists to guarantee:
  1. An environmental poll advances all three engines.
  2. A logged intervention resets the affected engines' state, and that
     reset survives a snapshot round-trip.

Run from Backend/: python -m pytest tests/models/test_pond_twin.py
conftest.py stubs Supabase so no credentials or network are needed.
"""
import json
from datetime import datetime, timedelta, timezone

from koi.models import algae_engine as ae
from koi.models import evaporation_engine as ev
from koi.models.engine import EventKind, PondConfig, PondEvent, RawSample, WaterChemistryEngine
from koi.models.pond_twin import PondTwin

T0 = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
CONFIG = PondConfig(volume_litres=5000, estimated_biomass_grams=8000)
ENV = ev.DayEnvironment(air_temp_c=31.0, relative_humidity_pct=70.0, wind_speed_ms=3.0)
AENV = ae.AlgaeDayEnvironment(lux=20000, temp_c=29.0, no3_ppm=15.0)
SAMPLE = RawSample(time=T0, ph=7.6, tds=220, temp_c=29.0, lux=20000)


def fresh_twin():
    return PondTwin.create(CONFIG)


def camera(times_values):
    return [
        ae.GreenSample(time=t, green_ratio=v, smoothed_green=v, state="base")
        for t, v in times_values
    ]


def ingest(twin, now, camera_samples=()):
    return twin.ingest_environment(now=now, sample=SAMPLE, evaporation_env=ENV,
                                   algae_env=AENV, camera_samples=list(camera_samples))


def snapshot_twin():
    """A twin with four days of state and a feeding event: the input to the
    snapshot round-trip and partial-snapshot checks."""
    twin = fresh_twin()
    ingest(twin, T0, camera([(T0 - timedelta(days=2), 0.05),
                             (T0 - timedelta(days=1), 0.06),
                             (T0, 0.075)]))
    for d in range(1, 5):
        ingest(twin, T0 + timedelta(days=d))
    twin.apply_event(PondEvent(kind=EventKind.FEEDING, time=T0,
                               food_grams=150.0, protein_percent=40.0))
    return twin


def test_poll_cycle_advances_all_three_engines():
    twin = fresh_twin()
    # First tick only establishes baselines.
    out = twin.ingest_environment(
        now=T0, sample=SAMPLE, evaporation_env=ENV, algae_env=AENV,
        camera_samples=camera([(T0 - timedelta(days=3), 0.02),
                               (T0 - timedelta(days=2), 0.025),
                               (T0 - timedelta(days=1), 0.031),
                               (T0, 0.039)]),
    )
    assert out["chemistry"] is not None, "chemistry assessment produced"
    assert out["evaporation"] is not None, "evaporation assessment produced"
    assert out["algae"] is not None, "algae assessment produced once camera reported"
    assert out["assimilated_camera_frames"] == 4, \
        f"4 camera frames assimilated: {out['assimilated_camera_frames']}"
    assert twin.evaporation.cumulative_loss_litres == 0.0, \
        "evaporation loss starts at zero on first tick"
    green_after_camera = twin.algae.green_ratio

    # Second tick a day later must integrate real loss and real growth.
    T1 = T0 + timedelta(days=1)
    sample1 = RawSample(time=T1, ph=7.6, tds=224, temp_c=29.2, lux=21000)
    out1 = twin.ingest_environment(
        now=T1, sample=sample1, evaporation_env=ENV, algae_env=AENV, camera_samples=[],
    )
    loss = twin.evaporation.cumulative_loss_litres
    assert loss > 0, f"evaporation integrated a day of loss: {loss:.2f} L"
    assert 5 < loss < 60, f"daily loss is physically plausible for 5000L pond: {loss:.2f} L"
    assert out1["assimilated_camera_frames"] == 0, "no new camera frames on second tick"
    assert twin.algae.green_ratio > green_after_camera, \
        f"algae grew without a camera frame: {green_after_camera:.5f} -> {twin.algae.green_ratio:.5f}"
    assert twin.algae._rate_source == "fitted_from_camera", \
        f"growth rate was fitted from camera, not fallback: {twin.algae._rate_source}"


def test_long_outage_does_not_dump_a_one_shot_loss():
    t_gap = fresh_twin()
    ingest(t_gap, T0)
    ingest(t_gap, T0 + timedelta(days=30))
    gap_loss = t_gap.evaporation.cumulative_loss_litres
    assert gap_loss < 60, f"outage integrates at most one day, not thirty: {gap_loss:.2f} L"


def test_water_topup_resets_evaporation_state():
    twin = fresh_twin()
    ingest(twin, T0)
    # Accrue several days of loss.
    for d in range(1, 9):
        ingest(twin, T0 + timedelta(days=d))
    before = twin.evaporation.cumulative_loss_litres
    assert before > 50, f"loss accumulated over 8 days: {before:.2f} L"

    # Real pondInterventions rows log top-ups as volume_percentage.
    twin.apply_event(PondEvent(kind=EventKind.TOP_UP,
                               time=T0 + timedelta(days=8), volume_percent=25.0))
    after = twin.evaporation.cumulative_loss_litres
    assert after == 0.0, f"top-up cleared the accrued loss: {after:.2f} L"
    assert twin.evaporation.assess().status == "Green", \
        f"assessment returns to Green after top-up: {twin.evaporation.assess().status}"

    # A partial top-up should only remove what it replaced.
    twin2 = fresh_twin()
    twin2.evaporation._cumulative_loss_litres = 400.0
    twin2.apply_event(PondEvent(kind=EventKind.TOP_UP, time=T0, volume_litres=150.0))
    assert abs(twin2.evaporation.cumulative_loss_litres - 250.0) < 1e-9, \
        f"partial top-up removes exactly the litres added: {twin2.evaporation.cumulative_loss_litres}"

    # An unquantified top-up assumes "topped back to full".
    twin3 = fresh_twin()
    twin3.evaporation._cumulative_loss_litres = 400.0
    twin3.apply_event(PondEvent(kind=EventKind.TOP_UP, time=T0))
    assert twin3.evaporation.cumulative_loss_litres == 0.0, "unquantified top-up clears to full"


def test_algae_scrub_resets_algae_state():
    twin = fresh_twin()
    ingest(twin, T0, camera([(T0 - timedelta(days=2), 0.20),
                             (T0 - timedelta(days=1), 0.24),
                             (T0, 0.30)]))
    before = twin.algae.green_ratio
    twin.apply_event(PondEvent(kind=EventKind.ALGAL_SCRUB, time=T0,
                               scrub_type="Manual Scrub"))
    after = twin.algae.green_ratio
    assert abs(after - before * (1 - ae.SCRUB_REMOVAL_EFFICIENCY)) < 1e-9, \
        f"scrub knocked green down by the removal efficiency: {after:.5f}"
    assert twin.algae._last_scrub_time == T0, "scrub time recorded"
    assert twin.chemistry._algae_suppression_days_remaining == 5, \
        f"chemistry also saw the scrub (NO3 uptake suppression): " \
        f"{twin.chemistry._algae_suppression_days_remaining}"


def test_water_change_fans_out_to_all_three_engines():
    twin = fresh_twin()
    ingest(twin, T0, camera([(T0 - timedelta(days=1), 0.20), (T0, 0.24)]))
    twin.chemistry._tan_mg = 5000.0
    twin.chemistry._no2_mg = 2000.0
    twin.evaporation._cumulative_loss_litres = 300.0
    green_before = twin.algae.green_ratio
    tan_before = twin.chemistry._tan_mg

    twin.apply_event(PondEvent(kind=EventKind.WATER_CHANGE, time=T0, volume_percent=40.0))

    assert abs(twin.chemistry._tan_mg - tan_before * 0.6) < 1e-6, "chemistry diluted TAN by 40%"
    assert twin.evaporation.cumulative_loss_litres == 0.0, \
        "evaporation loss cleared (40% of 5000L > 300L outstanding)"
    assert abs(twin.algae.green_ratio - green_before * (1 - 0.4 * 0.5)) < 1e-9, \
        f"algae diluted at half the replaced fraction: {twin.algae.green_ratio:.5f}"


def test_feeding_affects_chemistry_only():
    twin = fresh_twin()
    ingest(twin, T0, camera([(T0 - timedelta(days=1), 0.10), (T0, 0.12)]))
    green_before = twin.algae.green_ratio
    loss_before = twin.evaporation.cumulative_loss_litres
    tan_before = twin.chemistry._tan_mg
    twin.apply_event(PondEvent(kind=EventKind.FEEDING, time=T0,
                               food_grams=150.0, protein_percent=40.0))
    assert twin.chemistry._tan_mg > tan_before, \
        f"feeding raised TAN: {tan_before:.0f} -> {twin.chemistry._tan_mg:.0f} mg"
    assert twin.algae.green_ratio == green_before, "feeding left algae level untouched"
    assert twin.evaporation.cumulative_loss_litres == loss_before, \
        "feeding left evaporation untouched"


def test_snapshot_round_trip_preserves_all_three_engines():
    twin = snapshot_twin()
    snap = twin.to_snapshot()
    raw = json.dumps(snap)  # must be JSON-serialisable for Supabase JSONB
    restored = PondTwin.from_snapshot(json.loads(raw))

    assert isinstance(raw, str), "snapshot is JSON-serialisable"
    assert snap.get("version") == 2, f"snapshot is versioned: {snap.get('version')}"
    assert abs(restored.chemistry._tan_mg - twin.chemistry._tan_mg) < 1e-9, \
        "chemistry TAN preserved"
    assert abs(restored.chemistry._no3_mg - twin.chemistry._no3_mg) < 1e-9, \
        "chemistry NO3 preserved"
    assert abs(restored.evaporation.cumulative_loss_litres
               - twin.evaporation.cumulative_loss_litres) < 1e-9, \
        "evaporation accrued loss preserved"
    assert abs(restored.algae.green_ratio - twin.algae.green_ratio) < 1e-9, \
        "algae green level preserved"
    assert abs(restored.algae._intrinsic_rate - twin.algae._intrinsic_rate) < 1e-9, \
        "algae fitted rate preserved"
    assert restored.algae._last_camera_time == twin.algae._last_camera_time, \
        "algae camera watermark preserved"
    assert restored.algae.thresholds == twin.algae.thresholds, "algae thresholds preserved"
    assert restored.evaporation._water_air_offset_c == twin.evaporation._water_air_offset_c, \
        "evaporation water/air offset preserved"

    # Restored twin must continue advancing correctly, not restart.
    loss_before = restored.evaporation.cumulative_loss_litres
    ingest(restored, T0 + timedelta(days=5))
    assert restored.evaporation.cumulative_loss_litres > loss_before, \
        "restored twin keeps integrating from where it left off"


def test_legacy_bare_chemistry_snapshot_still_loads():
    legacy_engine = WaterChemistryEngine(CONFIG)
    legacy_engine._tan_mg = 1234.5
    legacy_engine._no3_mg = 987.6
    legacy_snap = legacy_engine.to_snapshot()
    assert "tan_mg" in legacy_snap and "chemistry" not in legacy_snap, \
        "legacy snapshot has the bare shape this detects"

    upgraded = PondTwin.from_snapshot(legacy_snap)
    assert upgraded.chemistry._tan_mg == 1234.5 and upgraded.chemistry._no3_mg == 987.6, \
        "legacy chemistry state preserved exactly"
    assert upgraded.evaporation.cumulative_loss_litres == 0.0, \
        "legacy upgrade creates a working evaporation engine"
    assert upgraded.algae.has_measurement is False, \
        "legacy upgrade creates an algae engine with no measurement"
    assert upgraded.evaporation.config.volume_litres == 5000, \
        "legacy upgrade carries pond volume across"
    # And it must re-serialise in the NEW shape.
    assert upgraded.to_snapshot().get("version") == 2, \
        "re-snapshot uses the new versioned shape"

    # Partial v2 snapshot (missing algae) must not explode.
    partial = snapshot_twin().to_snapshot()
    del partial["algae"]
    p = PondTwin.from_snapshot(partial)
    assert p.algae.has_measurement is False, "snapshot missing one engine still loads"


def test_algae_engine_refuses_to_invent_a_level_without_a_camera():
    blind = fresh_twin()
    out = ingest(blind, T0)
    assert out["algae"] is None, "no algae assessment without any camera frame"
    assert blind.algae.has_measurement is False, "has_measurement is False"
    proj = blind.algae.project_forward(daily_environment=[AENV], horizon_days=7)
    assert proj.get("error") == "no_camera_measurement", \
        f"projection returns a structured error, not a fabricated number: {proj.get('error')}"
    # But the other two domains must still work for that pond.
    assert out["evaporation"] is not None, "evaporation still assessable without a camera"
    assert out["chemistry"] is not None, "chemistry still assessable without a camera"


def test_camera_correction_pulls_the_model_back_to_reality():
    twin = fresh_twin()
    ingest(twin, T0, camera([(T0 - timedelta(days=2), 0.10),
                             (T0 - timedelta(days=1), 0.12),
                             (T0, 0.145)]))
    # Let the model run away for several days with no camera.
    for d in range(1, 6):
        ingest(twin, T0 + timedelta(days=d))
    modelled = twin.algae.green_ratio
    # Camera then reports a much LOWER real value (someone scrubbed and
    # didn't log it, or the model over-grew).
    T_cam = T0 + timedelta(days=5, hours=1)
    ingest(twin, T_cam, camera([(T_cam, 0.05)]))
    corrected = twin.algae.green_ratio
    assert corrected < modelled, "correction moved toward the measurement"
    assert corrected > 0.05, f"correction did not fully discard the model: {corrected:.5f}"
    expected = ae.AlgaeGrowthEngine.CAMERA_TRUST * 0.05 + \
        (1 - ae.AlgaeGrowthEngine.CAMERA_TRUST) * modelled
    assert abs(corrected - expected) < 1e-9, \
        f"correction matches the complementary filter weighting: {corrected:.5f} vs {expected:.5f}"

    # Re-presenting the SAME frame must not re-apply it.
    ingest(twin, T_cam + timedelta(hours=1), camera([(T_cam, 0.05)]))
    assert twin.algae._sample_count == 4, \
        f"an already-seen frame is not re-assimilated: {twin.algae._sample_count}"

    # Obstructed frames advance the watermark but are not assimilated.
    count_before = twin.algae._sample_count
    T_obs = T_cam + timedelta(hours=2)
    ingest(twin, T_obs, [ae.GreenSample(time=T_obs, green_ratio=0.95,
                                        smoothed_green=0.95, state="obstruction")])
    assert twin.algae._sample_count == count_before, "obstructed frame not counted as a sample"
    assert twin.algae._obstructed_count == 1, \
        f"obstructed frame counted separately: {twin.algae._obstructed_count}"
    assert twin.algae.green_ratio < 0.5, \
        f"obstructed frame did not corrupt the level: {twin.algae.green_ratio:.5f}"


def test_sync_config_propagates_a_resized_pond():
    twin = fresh_twin()
    assert twin.evaporation.config.volume_litres == 5000, "initial volume 5000"
    twin.sync_config(PondConfig(volume_litres=9000, estimated_biomass_grams=12000))
    assert twin.chemistry.config.volume_litres == 9000, "chemistry volume updated"
    assert twin.evaporation.config.volume_litres == 9000, "evaporation volume updated"
    assert twin.evaporation.config.estimated_biomass_grams == 12000, \
        "evaporation biomass updated"
    assert twin.evaporation.config.pond_depth_m == ev.DEFAULT_POND_DEPTH_M, \
        "depth preserved across a resize"


def test_projections_never_mutate_engine_state():
    twin = fresh_twin()
    ingest(twin, T0, camera([(T0 - timedelta(days=1), 0.10), (T0, 0.12)]))
    for d in range(1, 4):
        ingest(twin, T0 + timedelta(days=d))
    before = json.dumps(twin.to_snapshot(), sort_keys=True)
    twin.evaporation.project_forward(daily_environment=[ENV], horizon_days=21)
    twin.algae.project_forward(daily_environment=[AENV], horizon_days=21)
    twin.algae.project_scrub_benefit(daily_environment=[AENV], horizon_days=21)
    twin.no3_projection(avg_daily_tan_mg=2000.0, fallback_temp_c=29.0, horizon_days=21)
    after = json.dumps(twin.to_snapshot(), sort_keys=True)
    # saved_at changes every call by design, so compare everything else.
    b, a = json.loads(before), json.loads(after)
    b.pop("saved_at")
    a.pop("saved_at")
    assert b == a, "no projection mutated any engine state"
