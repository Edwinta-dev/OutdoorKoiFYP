"""Verification for the stateful multi-engine integration.

Covers the two things the refactor exists to guarantee:
  1. An environmental poll advances all three engines.
  2. A logged intervention resets the affected engines' state, and that
     reset survives a snapshot round-trip.

Run: python3 test_pond_twin.py
Stubs Supabase/Flask so no credentials or network are needed.
"""
import sys
import types
import os
from datetime import datetime, timedelta, timezone

# --- stub external deps before importing the server modules ---
for name in ["supabase", "dotenv", "apscheduler",
             "apscheduler.schedulers", "apscheduler.schedulers.background"]:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["apscheduler.schedulers.background"].BackgroundScheduler = object
os.environ.setdefault("SUPABASE_URL", "x")
os.environ.setdefault("SUPABASE_SERVICEROLE_KEY", "y")

import algae_engine as ae            # noqa: E402
import evaporation_engine as ev      # noqa: E402
from engine import EventKind, PondConfig, PondEvent, RawSample  # noqa: E402
from pond_twin import PondTwin       # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


T0 = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
CONFIG = PondConfig(volume_litres=5000, estimated_biomass_grams=8000)
ENV = ev.DayEnvironment(air_temp_c=31.0, relative_humidity_pct=70.0, wind_speed_ms=3.0)
AENV = ae.AlgaeDayEnvironment(lux=20000, temp_c=29.0, no3_ppm=15.0)


def fresh_twin():
    return PondTwin.create(CONFIG)


def camera(times_values):
    return [
        ae.GreenSample(time=t, green_ratio=v, smoothed_green=v, state="base")
        for t, v in times_values
    ]


# =====================================================================
print("=== 1. Poll cycle advances all three engines ===")
twin = fresh_twin()
sample = RawSample(time=T0, ph=7.6, tds=220, temp_c=29.0, lux=20000)

# First tick only establishes baselines.
out = twin.ingest_environment(
    now=T0, sample=sample, evaporation_env=ENV, algae_env=AENV,
    camera_samples=camera([(T0 - timedelta(days=3), 0.02),
                           (T0 - timedelta(days=2), 0.025),
                           (T0 - timedelta(days=1), 0.031),
                           (T0, 0.039)]),
)
check("chemistry assessment produced", out["chemistry"] is not None)
check("evaporation assessment produced", out["evaporation"] is not None)
check("algae assessment produced once camera reported", out["algae"] is not None)
check("4 camera frames assimilated", out["assimilated_camera_frames"] == 4,
      str(out["assimilated_camera_frames"]))
check("evaporation loss starts at zero on first tick",
      twin.evaporation.cumulative_loss_litres == 0.0)
green_after_camera = twin.algae.green_ratio
print(f"  green after camera assimilation: {green_after_camera:.5f}")

# Second tick a day later must integrate real loss and real growth.
T1 = T0 + timedelta(days=1)
sample1 = RawSample(time=T1, ph=7.6, tds=224, temp_c=29.2, lux=21000)
out1 = twin.ingest_environment(
    now=T1, sample=sample1, evaporation_env=ENV, algae_env=AENV, camera_samples=[],
)
loss = twin.evaporation.cumulative_loss_litres
print(f"  loss after 1 day: {loss:.2f} L ({twin.evaporation.loss_pct:.2f}%)")
check("evaporation integrated a day of loss", loss > 0, f"{loss:.2f} L")
check("daily loss is physically plausible for 5000L pond", 5 < loss < 60, f"{loss:.2f} L")
check("no new camera frames on second tick", out1["assimilated_camera_frames"] == 0)
check("algae grew without a camera frame",
      twin.algae.green_ratio > green_after_camera,
      f"{green_after_camera:.5f} -> {twin.algae.green_ratio:.5f}")
check("growth rate was fitted from camera, not fallback",
      twin.algae._rate_source == "fitted_from_camera", twin.algae._rate_source)

# =====================================================================
print("\n=== 2. Long outage does not dump a huge one-shot loss ===")
t_gap = fresh_twin()
t_gap.ingest_environment(now=T0, sample=sample, evaporation_env=ENV,
                         algae_env=AENV, camera_samples=[])
t_gap.ingest_environment(now=T0 + timedelta(days=30), sample=sample,
                         evaporation_env=ENV, algae_env=AENV, camera_samples=[])
gap_loss = t_gap.evaporation.cumulative_loss_litres
print(f"  loss integrated across a 30-day outage: {gap_loss:.2f} L")
check("outage integrates at most one day, not thirty", gap_loss < 60, f"{gap_loss:.2f} L")

# =====================================================================
print("\n=== 3. WATER_TOPUP resets evaporation state ===")
twin = fresh_twin()
twin.ingest_environment(now=T0, sample=sample, evaporation_env=ENV,
                        algae_env=AENV, camera_samples=[])
# Accrue several days of loss.
for d in range(1, 9):
    twin.ingest_environment(now=T0 + timedelta(days=d), sample=sample,
                            evaporation_env=ENV, algae_env=AENV, camera_samples=[])
before = twin.evaporation.cumulative_loss_litres
pct_before = twin.evaporation.loss_pct
print(f"  loss before top-up: {before:.2f} L ({pct_before:.2f}%)")
check("loss accumulated over 8 days", before > 50, f"{before:.2f} L")

# Real pondInterventions rows log top-ups as volume_percentage.
twin.apply_event(PondEvent(kind=EventKind.TOP_UP,
                           time=T0 + timedelta(days=8), volume_percent=25.0))
after = twin.evaporation.cumulative_loss_litres
print(f"  loss after 25% top-up (1250 L): {after:.2f} L")
check("top-up cleared the accrued loss", after == 0.0, f"{after:.2f} L")
check("assessment returns to Green after top-up",
      twin.evaporation.assess().status == "Green",
      twin.evaporation.assess().status)

# A partial top-up should only remove what it replaced.
twin2 = fresh_twin()
twin2.evaporation._cumulative_loss_litres = 400.0
twin2.apply_event(PondEvent(kind=EventKind.TOP_UP, time=T0, volume_litres=150.0))
check("partial top-up removes exactly the litres added",
      abs(twin2.evaporation.cumulative_loss_litres - 250.0) < 1e-9,
      str(twin2.evaporation.cumulative_loss_litres))

# An unquantified top-up assumes "topped back to full".
twin3 = fresh_twin()
twin3.evaporation._cumulative_loss_litres = 400.0
twin3.apply_event(PondEvent(kind=EventKind.TOP_UP, time=T0))
check("unquantified top-up clears to full",
      twin3.evaporation.cumulative_loss_litres == 0.0)

# =====================================================================
print("\n=== 4. ALGAE_SCRUB resets algae state ===")
twin = fresh_twin()
twin.ingest_environment(now=T0, sample=sample, evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T0 - timedelta(days=2), 0.20),
                                               (T0 - timedelta(days=1), 0.24),
                                               (T0, 0.30)]))
before = twin.algae.green_ratio
twin.apply_event(PondEvent(kind=EventKind.ALGAL_SCRUB, time=T0,
                           scrub_type="Manual Scrub"))
after = twin.algae.green_ratio
print(f"  green {before:.5f} -> {after:.5f} after scrub")
check("scrub knocked green down by the removal efficiency",
      abs(after - before * (1 - ae.SCRUB_REMOVAL_EFFICIENCY)) < 1e-9,
      f"{after:.5f}")
check("scrub time recorded", twin.algae._last_scrub_time == T0)
check("chemistry also saw the scrub (NO3 uptake suppression)",
      twin.chemistry._algae_suppression_days_remaining == 5,
      str(twin.chemistry._algae_suppression_days_remaining))

# =====================================================================
print("\n=== 5. WATER_CHANGE fans out to all three engines ===")
twin = fresh_twin()
twin.ingest_environment(now=T0, sample=sample, evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T0 - timedelta(days=1), 0.20),
                                               (T0, 0.24)]))
twin.chemistry._tan_mg = 5000.0
twin.chemistry._no2_mg = 2000.0
twin.evaporation._cumulative_loss_litres = 300.0
green_before = twin.algae.green_ratio
tan_before = twin.chemistry._tan_mg

twin.apply_event(PondEvent(kind=EventKind.WATER_CHANGE, time=T0, volume_percent=40.0))

print(f"  TAN   {tan_before:.0f} -> {twin.chemistry._tan_mg:.0f} mg")
print(f"  loss  300.00 -> {twin.evaporation.cumulative_loss_litres:.2f} L")
print(f"  green {green_before:.5f} -> {twin.algae.green_ratio:.5f}")
check("chemistry diluted TAN by 40%",
      abs(twin.chemistry._tan_mg - tan_before * 0.6) < 1e-6)
check("evaporation loss cleared (40% of 5000L > 300L outstanding)",
      twin.evaporation.cumulative_loss_litres == 0.0)
check("algae diluted at half the replaced fraction",
      abs(twin.algae.green_ratio - green_before * (1 - 0.4 * 0.5)) < 1e-9,
      f"{twin.algae.green_ratio:.5f}")

# =====================================================================
print("\n=== 6. FEEDING affects chemistry only ===")
twin = fresh_twin()
twin.ingest_environment(now=T0, sample=sample, evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T0 - timedelta(days=1), 0.10),
                                               (T0, 0.12)]))
green_before = twin.algae.green_ratio
loss_before = twin.evaporation.cumulative_loss_litres
tan_before = twin.chemistry._tan_mg
twin.apply_event(PondEvent(kind=EventKind.FEEDING, time=T0,
                           food_grams=150.0, protein_percent=40.0))
check("feeding raised TAN", twin.chemistry._tan_mg > tan_before,
      f"{tan_before:.0f} -> {twin.chemistry._tan_mg:.0f} mg")
check("feeding left algae level untouched", twin.algae.green_ratio == green_before)
check("feeding left evaporation untouched",
      twin.evaporation.cumulative_loss_litres == loss_before)

# =====================================================================
print("\n=== 7. Snapshot round-trip preserves all three engines ===")
twin = fresh_twin()
twin.ingest_environment(now=T0, sample=sample, evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T0 - timedelta(days=2), 0.05),
                                               (T0 - timedelta(days=1), 0.06),
                                               (T0, 0.075)]))
for d in range(1, 5):
    twin.ingest_environment(now=T0 + timedelta(days=d), sample=sample,
                            evaporation_env=ENV, algae_env=AENV, camera_samples=[])
twin.apply_event(PondEvent(kind=EventKind.FEEDING, time=T0,
                           food_grams=150.0, protein_percent=40.0))

snap = twin.to_snapshot()
import json
raw = json.dumps(snap)  # must be JSON-serialisable for Supabase JSONB
restored = PondTwin.from_snapshot(json.loads(raw))

check("snapshot is JSON-serialisable", isinstance(raw, str))
check("snapshot is versioned", snap.get("version") == 2, str(snap.get("version")))
check("chemistry TAN preserved",
      abs(restored.chemistry._tan_mg - twin.chemistry._tan_mg) < 1e-9)
check("chemistry NO3 preserved",
      abs(restored.chemistry._no3_mg - twin.chemistry._no3_mg) < 1e-9)
check("evaporation accrued loss preserved",
      abs(restored.evaporation.cumulative_loss_litres
          - twin.evaporation.cumulative_loss_litres) < 1e-9)
check("algae green level preserved",
      abs(restored.algae.green_ratio - twin.algae.green_ratio) < 1e-9)
check("algae fitted rate preserved",
      abs(restored.algae._intrinsic_rate - twin.algae._intrinsic_rate) < 1e-9)
check("algae camera watermark preserved",
      restored.algae._last_camera_time == twin.algae._last_camera_time)
check("algae thresholds preserved",
      restored.algae.thresholds == twin.algae.thresholds)
check("evaporation water/air offset preserved",
      restored.evaporation._water_air_offset_c == twin.evaporation._water_air_offset_c)

# Restored twin must continue advancing correctly, not restart.
loss_before = restored.evaporation.cumulative_loss_litres
restored.ingest_environment(now=T0 + timedelta(days=5), sample=sample,
                            evaporation_env=ENV, algae_env=AENV, camera_samples=[])
check("restored twin keeps integrating from where it left off",
      restored.evaporation.cumulative_loss_litres > loss_before)

# =====================================================================
print("\n=== 8. Legacy bare-chemistry snapshot still loads ===")
from engine import WaterChemistryEngine  # noqa: E402
legacy_engine = WaterChemistryEngine(CONFIG)
legacy_engine._tan_mg = 1234.5
legacy_engine._no3_mg = 987.6
legacy_snap = legacy_engine.to_snapshot()
check("legacy snapshot has the bare shape this detects",
      "tan_mg" in legacy_snap and "chemistry" not in legacy_snap)

upgraded = PondTwin.from_snapshot(legacy_snap)
check("legacy chemistry state preserved exactly",
      upgraded.chemistry._tan_mg == 1234.5 and upgraded.chemistry._no3_mg == 987.6)
check("legacy upgrade creates a working evaporation engine",
      upgraded.evaporation.cumulative_loss_litres == 0.0)
check("legacy upgrade creates an algae engine with no measurement",
      upgraded.algae.has_measurement is False)
check("legacy upgrade carries pond volume across",
      upgraded.evaporation.config.volume_litres == 5000)
# And it must re-serialise in the NEW shape.
check("re-snapshot uses the new versioned shape",
      upgraded.to_snapshot().get("version") == 2)

# Partial v2 snapshot (missing algae) must not explode.
partial = twin.to_snapshot()
del partial["algae"]
p = PondTwin.from_snapshot(partial)
check("snapshot missing one engine still loads", p.algae.has_measurement is False)

# =====================================================================
print("\n=== 9. Algae engine refuses to invent a level without a camera ===")
blind = fresh_twin()
out = blind.ingest_environment(now=T0, sample=sample, evaporation_env=ENV,
                               algae_env=AENV, camera_samples=[])
check("no algae assessment without any camera frame", out["algae"] is None)
check("has_measurement is False", blind.algae.has_measurement is False)
proj = blind.algae.project_forward(daily_environment=[AENV], horizon_days=7)
check("projection returns a structured error, not a fabricated number",
      proj.get("error") == "no_camera_measurement", str(proj.get("error")))
# But the other two domains must still work for that pond.
check("evaporation still assessable without a camera", out["evaporation"] is not None)
check("chemistry still assessable without a camera", out["chemistry"] is not None)

# =====================================================================
print("\n=== 10. Camera correction pulls the model back to reality ===")
twin = fresh_twin()
twin.ingest_environment(now=T0, sample=sample, evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T0 - timedelta(days=2), 0.10),
                                               (T0 - timedelta(days=1), 0.12),
                                               (T0, 0.145)]))
# Let the model run away for several days with no camera.
for d in range(1, 6):
    twin.ingest_environment(now=T0 + timedelta(days=d), sample=sample,
                            evaporation_env=ENV, algae_env=AENV, camera_samples=[])
modelled = twin.algae.green_ratio
# Camera then reports a much LOWER real value (someone scrubbed and
# didn't log it, or the model over-grew).
T_cam = T0 + timedelta(days=5, hours=1)
twin.ingest_environment(now=T_cam, sample=sample, evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T_cam, 0.05)]))
corrected = twin.algae.green_ratio
print(f"  modelled {modelled:.5f} -> camera says 0.05000 -> corrected {corrected:.5f}")
check("correction moved toward the measurement", corrected < modelled)
check("correction did not fully discard the model",
      corrected > 0.05, f"{corrected:.5f}")
expected = ae.AlgaeGrowthEngine.CAMERA_TRUST * 0.05 + \
    (1 - ae.AlgaeGrowthEngine.CAMERA_TRUST) * modelled
check("correction matches the complementary filter weighting",
      abs(corrected - expected) < 1e-9, f"{corrected:.5f} vs {expected:.5f}")

# Re-presenting the SAME frame must not re-apply it.
again = twin.algae.green_ratio
twin.ingest_environment(now=T_cam + timedelta(hours=1), sample=sample,
                        evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T_cam, 0.05)]))
check("an already-seen frame is not re-assimilated",
      twin.algae._sample_count == 4, str(twin.algae._sample_count))

# Obstructed frames advance the watermark but are not assimilated.
count_before = twin.algae._sample_count
T_obs = T_cam + timedelta(hours=2)
twin.ingest_environment(
    now=T_obs, sample=sample, evaporation_env=ENV, algae_env=AENV,
    camera_samples=[ae.GreenSample(time=T_obs, green_ratio=0.95,
                                   smoothed_green=0.95, state="obstruction")])
check("obstructed frame not counted as a sample",
      twin.algae._sample_count == count_before)
check("obstructed frame counted separately",
      twin.algae._obstructed_count == 1, str(twin.algae._obstructed_count))
check("obstructed frame did not corrupt the level",
      twin.algae.green_ratio < 0.5, f"{twin.algae.green_ratio:.5f}")

# =====================================================================
print("\n=== 11. sync_config propagates a resized pond ===")
twin = fresh_twin()
check("initial volume 5000", twin.evaporation.config.volume_litres == 5000)
twin.sync_config(PondConfig(volume_litres=9000, estimated_biomass_grams=12000))
check("chemistry volume updated", twin.chemistry.config.volume_litres == 9000)
check("evaporation volume updated", twin.evaporation.config.volume_litres == 9000)
check("evaporation biomass updated",
      twin.evaporation.config.estimated_biomass_grams == 12000)
check("depth preserved across a resize",
      twin.evaporation.config.pond_depth_m == ev.DEFAULT_POND_DEPTH_M)

# =====================================================================
print("\n=== 12. Projections never mutate engine state ===")
twin = fresh_twin()
twin.ingest_environment(now=T0, sample=sample, evaporation_env=ENV, algae_env=AENV,
                        camera_samples=camera([(T0 - timedelta(days=1), 0.10),
                                               (T0, 0.12)]))
for d in range(1, 4):
    twin.ingest_environment(now=T0 + timedelta(days=d), sample=sample,
                            evaporation_env=ENV, algae_env=AENV, camera_samples=[])
before = json.dumps(twin.to_snapshot(), sort_keys=True)
twin.evaporation.project_forward(daily_environment=[ENV], horizon_days=21)
twin.algae.project_forward(daily_environment=[AENV], horizon_days=21)
twin.algae.project_scrub_benefit(daily_environment=[AENV], horizon_days=21)
twin.no3_projection(avg_daily_tan_mg=2000.0, fallback_temp_c=29.0, horizon_days=21)
after = json.dumps(twin.to_snapshot(), sort_keys=True)
# saved_at changes every call by design, so compare everything else.
b, a = json.loads(before), json.loads(after)
b.pop("saved_at"); a.pop("saved_at")
check("no projection mutated any engine state", b == a)

# =====================================================================
print("\n" + "=" * 62)
if FAIL:
    print(f"{len(FAIL)} CHECK(S) FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL POND TWIN CHECKS PASSED")
