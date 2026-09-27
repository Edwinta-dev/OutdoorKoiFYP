"""Verification for human severity rating assimilation.

The central claim this must prove: a rating overrides a lying camera.
Everything else (calibration, drift detection, obstruction, undo) supports
that.

Run: python3 test_severity_ratings.py
"""
import json
import sys
import types
import os
from datetime import datetime, timedelta, timezone

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

import algae_engine as ae  # noqa: E402

FAIL = []
T0 = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


def frames(pairs):
    return [ae.GreenSample(time=t, green_ratio=v, smoothed_green=v, state="base")
            for t, v in pairs]


def engine_with(pairs, lux=18000.0, temp=29.0, no3=10.0):
    e = ae.AlgaeGrowthEngine()
    e.ingest_camera_samples(frames(pairs))
    e.refit_growth_rate(lux, temp, no3)
    return e


# =====================================================================
print("=== 1. Human rating outranks the camera ===")
# Camera reads high (0.30). User says there is no algae - e.g. the lens
# has fouled, or the frame caught a green reflection.
e = engine_with([(T0 - timedelta(days=2), 0.28),
                 (T0 - timedelta(days=1), 0.29),
                 (T0, 0.30)])
camera_level = e.green_ratio
res = e.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=0.30)
print(f"  camera said {camera_level:.4f} -> user said 'none' -> {e.green_ratio:.4f}")
check("rating moved the level down decisively", e.green_ratio < camera_level * 0.5,
      f"{camera_level:.4f} -> {e.green_ratio:.4f}")
check("summary reports before and after",
      res["green_ratio_before"] is not None and res["green_ratio_after"] is not None)

# A rating must move further than a camera frame of equal target would.
e_cam = engine_with([(T0 - timedelta(days=2), 0.28),
                     (T0 - timedelta(days=1), 0.29),
                     (T0, 0.30)])
e_cam.ingest_camera_samples(frames([(T0 + timedelta(hours=1), 0.01)]))
e_hum = engine_with([(T0 - timedelta(days=2), 0.28),
                     (T0 - timedelta(days=1), 0.29),
                     (T0, 0.30)])
e_hum._ratings = [{"t": T0.isoformat(), "severity": "none", "obstructed": False,
                   "green_at": 0.01, "image_id": None, "rating_id": None}] * 2
e_hum.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=0.01)
print(f"  camera frame @0.01 -> {e_cam.green_ratio:.4f}; "
      f"human 'none' -> {e_hum.green_ratio:.4f}")
check("human rating pulls harder than a camera frame",
      e_hum.green_ratio < e_cam.green_ratio,
      f"human={e_hum.green_ratio:.4f} camera={e_cam.green_ratio:.4f}")
check("HUMAN_TRUST exceeds CAMERA_TRUST",
      ae.AlgaeGrowthEngine.HUMAN_TRUST > ae.AlgaeGrowthEngine.CAMERA_TRUST)

# =====================================================================
print("\n=== 2. Class targets: quantile fallback then measured ===")
e = engine_with([(T0 - timedelta(days=i), 0.02 + i * 0.01) for i in range(8, 0, -1)])
t_fallback = e._class_targets()
print(f"  fallback targets: { {k: round(v,4) for k,v in t_fallback.items()} }")
check("fallback targets are monotonic across severity",
      all(t_fallback[a] <= t_fallback[b] for a, b in
          zip(ae.SEVERITY_LEVELS, ae.SEVERITY_LEVELS[1:])))
check("fallback anchored to this pond's own range",
      t_fallback["severe"] <= 0.12, str(round(t_fallback["severe"], 4)))

# Feed real labels and the targets should become measured.
for i, (sev, g) in enumerate([("none", 0.01), ("none", 0.012),
                              ("minor", 0.05), ("minor", 0.055),
                              ("moderate", 0.12), ("moderate", 0.13),
                              ("severe", 0.30), ("severe", 0.33)]):
    e.apply_severity_rating(time=T0 + timedelta(hours=i), severity=sev,
                            green_ratio_at_rating=g)
t_measured = e._class_targets()
print(f"  measured targets: { {k: round(v,4) for k,v in t_measured.items()} }")
check("none target near labelled median 0.011",
      abs(t_measured["none"] - 0.011) < 0.005, str(round(t_measured["none"], 4)))
check("severe target near labelled median 0.315",
      abs(t_measured["severe"] - 0.315) < 0.02, str(round(t_measured["severe"], 4)))
check("measured targets monotonic",
      all(t_measured[a] <= t_measured[b] for a, b in
          zip(ae.SEVERITY_LEVELS, ae.SEVERITY_LEVELS[1:])))

# =====================================================================
print("\n=== 3. Thresholds calibrate from labels ===")
th = e.thresholds
print(f"  thresholds: watch={th['watch']:.4f} action={th['action']:.4f} mode={th['mode']}")
check("threshold mode is label_calibrated", th["mode"] == "label_calibrated", th["mode"])
# watch = midpoint(minor 0.0525, moderate 0.125) = 0.08875
check("watch sits between the minor and moderate medians",
      abs(th["watch"] - (0.0525 + 0.125) / 2) < 0.01, str(round(th["watch"], 4)))
# action = midpoint(moderate 0.125, severe 0.315) = 0.22
check("action sits between the moderate and severe medians",
      abs(th["action"] - (0.125 + 0.315) / 2) < 0.02, str(round(th["action"], 4)))
check("watch below action", th["watch"] < th["action"])

# Non-monotonic labels must not produce inverted thresholds.
bad = engine_with([(T0 - timedelta(days=i), 0.05) for i in range(6, 0, -1)])
for sev, g in [("minor", 0.30), ("minor", 0.32), ("moderate", 0.02), ("moderate", 0.03),
               ("severe", 0.10), ("severe", 0.11)]:
    bad.apply_severity_rating(time=T0, severity=sev, green_ratio_at_rating=g)
bt = bad.thresholds
print(f"  from contradictory labels: watch={bt['watch']:.4f} action={bt['action']:.4f}")
check("contradictory labels still yield ordered thresholds", bt["watch"] < bt["action"])

# =====================================================================
print("\n=== 4. Partial calibration degrades gracefully ===")
p = engine_with([(T0 - timedelta(days=i), 0.03 + i * 0.005) for i in range(6, 0, -1)])
p.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=0.01)
pt = p.thresholds
check("one label alone does not claim calibration",
      pt["mode"] in ("absolute", "baseline_relative"), pt["mode"])
check("labels_needed reports what is missing",
      p.labels_needed()["moderate"] == 2, str(p.labels_needed()))
for sev, g in [("minor", 0.04), ("minor", 0.045), ("moderate", 0.09), ("moderate", 0.095)]:
    p.apply_severity_rating(time=T0, severity=sev, green_ratio_at_rating=g)
pt = p.thresholds
print(f"  with minor+moderate only: mode={pt['mode']} "
      f"watch_cal={pt.get('watch_calibrated')} action_cal={pt.get('action_calibrated')}")
check("watch calibrates before severe labels exist", pt.get("watch_calibrated") is True)
check("action stays uncalibrated without severe labels",
      pt.get("action_calibrated") is False)
check("mode reports partial", pt["mode"] == "partially_calibrated", pt["mode"])
check("partial calibration keeps thresholds ordered", pt["watch"] < pt["action"])

# =====================================================================
print("\n=== 5. Camera drift detection ===")
d = engine_with([(T0 - timedelta(days=i), 0.02) for i in range(10, 0, -1)])
# Six "no algae" ratings where the camera reading creeps up - the pond is
# clean every time but the lens is fouling.
for i, g in enumerate([0.010, 0.011, 0.012, 0.030, 0.034, 0.038]):
    d.apply_severity_rating(time=T0 + timedelta(days=i), severity="none",
                            green_ratio_at_rating=g)
drift = d.detect_camera_drift()
print(f"  {drift['verdict']}: early={drift['early_median']} late={drift['late_median']} "
      f"ratio={drift['ratio']}")
check("rising 'no algae' readings flagged as drift",
      drift["verdict"] == "drift_suspected", drift["verdict"])
check("drift verdict carries an actionable explanation",
      "lens" in drift["detail"].lower())

stable = engine_with([(T0 - timedelta(days=i), 0.02) for i in range(10, 0, -1)])
for i, g in enumerate([0.010, 0.011, 0.0105, 0.0108, 0.0102, 0.011]):
    stable.apply_severity_rating(time=T0 + timedelta(days=i), severity="none",
                                 green_ratio_at_rating=g)
check("flat 'no algae' readings report stable",
      stable.detect_camera_drift()["verdict"] == "stable",
      stable.detect_camera_drift()["verdict"])

few = engine_with([(T0 - timedelta(days=i), 0.02) for i in range(6, 0, -1)])
few.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=0.01)
check("too few labels reports insufficient_data, not a false verdict",
      few.detect_camera_drift()["verdict"] == "insufficient_data")

# =====================================================================
print("\n=== 6. Obstruction retroactively removes the poisoned frame ===")
clean_pairs = [(T0 - timedelta(days=i), round(0.03 * (1.15 ** (6 - i)), 5))
               for i in range(6, 0, -1)]
o = engine_with(clean_pairs)
rate_clean = o._intrinsic_rate
hist_before = len(o._camera_history)

# A leaf lands on the lens: a very green frame that is not algae.
o.ingest_camera_samples(frames([(T0, 0.62)]))
rate_poisoned = o._intrinsic_rate
level_poisoned = o.green_ratio
print(f"  history {hist_before} -> {len(o._camera_history)} frames after the bad frame")
print(f"  level poisoned to {level_poisoned:.4f}")

res = o.apply_severity_rating(time=T0, severity="obstruction", green_ratio_at_rating=0.62)
print(f"  after obstruction rating: {len(o._camera_history)} frames, "
      f"level {o.green_ratio:.4f}, removed={res['removed_frames']}")
check("obstructed frame removed from history", res["removed_frames"] == 1)
check("history length restored", len(o._camera_history) == hist_before)
check("level rolled back off the bad frame", o.green_ratio < level_poisoned,
      f"{level_poisoned:.4f} -> {o.green_ratio:.4f}")
check("obstruction counted separately", o._obstructed_count == 1)
check("obstruction rating does not join the severity calibration set",
      o.label_counts()["none"] == 0 and o.label_counts()["obstruction"] == 1,
      str(o.label_counts()))

# =====================================================================
print("\n=== 7. Undo restores state exactly ===")
u = engine_with([(T0 - timedelta(days=i), 0.04 + i * 0.004) for i in range(6, 0, -1)])
for sev, g in [("none", 0.01), ("none", 0.012), ("minor", 0.05), ("minor", 0.052)]:
    u.apply_severity_rating(time=T0, severity=sev, green_ratio_at_rating=g)

before = {
    "green": u.green_ratio,
    "thresholds": dict(u.thresholds),
    "rate": u._intrinsic_rate,
    "counts": dict(u.label_counts()),
    "history": len(u._camera_history),
}
# A mis-tap: user meant "minor", hit "severe".
u.apply_severity_rating(time=T0, severity="severe", green_ratio_at_rating=0.052,
                        rating_id=99)
mistap_green = u.green_ratio
print(f"  green {before['green']:.5f} -> {mistap_green:.5f} after mis-tap")
check("mis-tap visibly moved the level", abs(mistap_green - before["green"]) > 1e-6)

ok = u.undo_last_rating(rating_id=99)
check("undo reports success", ok)
check("level restored exactly", u.green_ratio == before["green"],
      f"{u.green_ratio} vs {before['green']}")
check("thresholds restored exactly", u.thresholds == before["thresholds"])
check("growth rate restored exactly", u._intrinsic_rate == before["rate"])
check("label counts restored", u.label_counts() == before["counts"], str(u.label_counts()))
check("camera history restored", len(u._camera_history) == before["history"])

check("second undo of the same id is refused", u.undo_last_rating(rating_id=99) is False)
check("undo with a mismatched id is refused",
      engine_with([(T0, 0.05)]).undo_last_rating(rating_id=12345) is False)

# Undo must also work for an obstruction rating.
o2 = engine_with(clean_pairs)
o2.ingest_camera_samples(frames([(T0, 0.62)]))
lvl = o2.green_ratio
hist = len(o2._camera_history)
o2.apply_severity_rating(time=T0, severity="obstruction", green_ratio_at_rating=0.62,
                         rating_id=7)
o2.undo_last_rating(rating_id=7)
check("undoing an obstruction restores the frame", len(o2._camera_history) == hist)
check("undoing an obstruction restores the level", o2.green_ratio == lvl)

# =====================================================================
print("\n=== 8. Validation and edge cases ===")
v = engine_with([(T0, 0.05)])
try:
    v.apply_severity_rating(time=T0, severity="catastrophic")
    check("invalid severity rejected", False, "no exception raised")
except ValueError as exc:
    check("invalid severity rejected", True, str(exc)[:50])

check("severity is case-insensitive",
      engine_with([(T0, 0.05)]).apply_severity_rating(
          time=T0, severity="  MODERATE ")["green_ratio_after"] is not None)

# A rating on a pond with no camera history at all must still work.
blank = ae.AlgaeGrowthEngine()
check("no measurement before any input", blank.has_measurement is False)
blank.apply_severity_rating(time=T0, severity="moderate")
check("a rating alone establishes a level", blank.has_measurement is True)
check("level adopted from the default target",
      abs(blank.green_ratio - ae._SEVERITY_DEFAULT["moderate"]) < 1e-9,
      str(blank.green_ratio))
a = blank.assess()
check("assessable from a rating alone", a is not None)

# Rating without green_ratio still corrects, just cannot calibrate.
n = engine_with([(T0 - timedelta(days=1), 0.20), (T0, 0.22)])
lvl_before = n.green_ratio
n.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=None)
check("rating without a measurement still corrects the level",
      n.green_ratio < lvl_before)
check("rating without a measurement is skipped by calibration",
      n._class_targets()["none"] != 0.0)

# =====================================================================
print("\n=== 9. Snapshot round-trip carries ratings ===")
s = engine_with([(T0 - timedelta(days=i), 0.03 + i * 0.006) for i in range(6, 0, -1)])
for sev, g in [("none", 0.01), ("none", 0.011), ("minor", 0.04), ("minor", 0.042),
               ("moderate", 0.10), ("moderate", 0.11)]:
    s.apply_severity_rating(time=T0, severity=sev, green_ratio_at_rating=g)

snap = json.loads(json.dumps(s.to_snapshot()))
r = ae.AlgaeGrowthEngine.from_snapshot(snap)
check("ratings survive the round-trip", r.label_counts() == s.label_counts(),
      str(r.label_counts()))
check("calibrated thresholds survive", r.thresholds == s.thresholds)
check("class targets reproduce", r._class_targets() == s._class_targets())
check("green level survives", r.green_ratio == s.green_ratio)
check("undo stack is NOT persisted (deliberate)", r._undo_stack == [])

# load_ratings from the DB shape must reproduce the same calibration.
rows = [
    {"id": i, "rated_at": (T0 + timedelta(hours=i)).isoformat(),
     "severity": sev, "is_obstructed": False, "green_ratio_at_rating": g,
     "image_id": None}
    for i, (sev, g) in enumerate([("none", 0.01), ("none", 0.011),
                                  ("minor", 0.04), ("minor", 0.042),
                                  ("moderate", 0.10), ("moderate", 0.11)])
]
l = engine_with([(T0 - timedelta(days=i), 0.03 + i * 0.006) for i in range(6, 0, -1)])
l.load_ratings(rows)
check("load_ratings reproduces the label counts",
      l.label_counts() == s.label_counts(), str(l.label_counts()))
check("load_ratings reproduces the thresholds",
      abs(l.thresholds["watch"] - s.thresholds["watch"]) < 1e-9)

# =====================================================================
print("\n=== 10. Assessment surfaces calibration state ===")
a = s.assess()
print(f"  status={a.status} labels={a.label_count} calibrated={a.calibrated} "
      f"drift={a.camera_drift}")
check("assessment reports the label count", a.label_count == 6, str(a.label_count))
check("assessment reports calibration state", isinstance(a.calibrated, bool))
check("assessment reports drift verdict", a.camera_drift in
      ("stable", "drift_possible", "drift_suspected", "insufficient_data"))
check("assessment still JSON-serialisable", isinstance(json.dumps(a.to_dict()), str))

proj = s.project_forward(
    daily_environment=[ae.AlgaeDayEnvironment(lux=20000, temp_c=30.0, no3_ppm=12.0)],
    horizon_days=14)
# Key name unified with the /ratings/algae endpoint - both now emit
# "labels_needed" rather than two spellings of the same thing.
for key in ("label_counts", "labels_needed", "camera_drift", "class_targets"):
    check(f"projection exposes {key}", key in proj)

print("\n" + "=" * 62)
if FAIL:
    print(f"{len(FAIL)} CHECK(S) FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL SEVERITY RATING CHECKS PASSED")
