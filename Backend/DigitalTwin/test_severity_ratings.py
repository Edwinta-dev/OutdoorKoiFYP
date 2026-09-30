"""Verification for human severity rating assimilation.

The central claim this must prove: a rating overrides a lying camera.
Everything else (calibration, drift detection, obstruction, undo) supports
that.

Run from Backend/: python -m pytest DigitalTwin/test_severity_ratings.py
"""
import json
from datetime import datetime, timedelta, timezone

import algae_engine as ae

T0 = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
CLEAN_PAIRS = [(T0 - timedelta(days=i), round(0.03 * (1.15 ** (6 - i)), 5))
               for i in range(6, 0, -1)]
SNAPSHOT_LABELS = [("none", 0.01), ("none", 0.011), ("minor", 0.04), ("minor", 0.042),
                   ("moderate", 0.10), ("moderate", 0.11)]


def frames(pairs):
    return [ae.GreenSample(time=t, green_ratio=v, smoothed_green=v, state="base")
            for t, v in pairs]


def engine_with(pairs, lux=18000.0, temp=29.0, no3=10.0):
    e = ae.AlgaeGrowthEngine()
    e.ingest_camera_samples(frames(pairs))
    e.refit_growth_rate(lux, temp, no3)
    return e


def fully_labelled_engine():
    """Eight camera frames plus two labels in each severity class."""
    e = engine_with([(T0 - timedelta(days=i), 0.02 + i * 0.01) for i in range(8, 0, -1)])
    for i, (sev, g) in enumerate([("none", 0.01), ("none", 0.012),
                                  ("minor", 0.05), ("minor", 0.055),
                                  ("moderate", 0.12), ("moderate", 0.13),
                                  ("severe", 0.30), ("severe", 0.33)]):
        e.apply_severity_rating(time=T0 + timedelta(hours=i), severity=sev,
                                green_ratio_at_rating=g)
    return e


def snapshot_engine():
    """Six camera frames and six labels (none/minor/moderate)."""
    s = engine_with([(T0 - timedelta(days=i), 0.03 + i * 0.006) for i in range(6, 0, -1)])
    for sev, g in SNAPSHOT_LABELS:
        s.apply_severity_rating(time=T0, severity=sev, green_ratio_at_rating=g)
    return s


def test_human_rating_outranks_the_camera():
    # Camera reads high (0.30). User says there is no algae - e.g. the lens
    # has fouled, or the frame caught a green reflection.
    e = engine_with([(T0 - timedelta(days=2), 0.28),
                     (T0 - timedelta(days=1), 0.29),
                     (T0, 0.30)])
    camera_level = e.green_ratio
    res = e.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=0.30)
    assert e.green_ratio < camera_level * 0.5, \
        f"rating moved the level down decisively: {camera_level:.4f} -> {e.green_ratio:.4f}"
    assert res["green_ratio_before"] is not None and res["green_ratio_after"] is not None, \
        "summary reports before and after"

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
    assert e_hum.green_ratio < e_cam.green_ratio, \
        f"human rating pulls harder than a camera frame: " \
        f"human={e_hum.green_ratio:.4f} camera={e_cam.green_ratio:.4f}"
    assert ae.AlgaeGrowthEngine.HUMAN_TRUST > ae.AlgaeGrowthEngine.CAMERA_TRUST, \
        "HUMAN_TRUST exceeds CAMERA_TRUST"


def test_class_targets_quantile_fallback_then_measured():
    e = engine_with([(T0 - timedelta(days=i), 0.02 + i * 0.01) for i in range(8, 0, -1)])
    t_fallback = e._class_targets()
    assert all(t_fallback[a] <= t_fallback[b] for a, b in
               zip(ae.SEVERITY_LEVELS, ae.SEVERITY_LEVELS[1:], strict=False)), \
        "fallback targets are monotonic across severity"
    assert t_fallback["severe"] <= 0.12, \
        f"fallback anchored to this pond's own range: {round(t_fallback['severe'], 4)}"

    # Feed real labels and the targets should become measured.
    t_measured = fully_labelled_engine()._class_targets()
    assert abs(t_measured["none"] - 0.011) < 0.005, \
        f"none target near labelled median 0.011: {round(t_measured['none'], 4)}"
    assert abs(t_measured["severe"] - 0.315) < 0.02, \
        f"severe target near labelled median 0.315: {round(t_measured['severe'], 4)}"
    assert all(t_measured[a] <= t_measured[b] for a, b in
               zip(ae.SEVERITY_LEVELS, ae.SEVERITY_LEVELS[1:], strict=False)), \
        "measured targets monotonic"


def test_thresholds_calibrate_from_labels():
    th = fully_labelled_engine().thresholds
    assert th["mode"] == "label_calibrated", f"threshold mode is label_calibrated: {th['mode']}"
    # watch = midpoint(minor 0.0525, moderate 0.125) = 0.08875
    assert abs(th["watch"] - (0.0525 + 0.125) / 2) < 0.01, \
        f"watch sits between the minor and moderate medians: {round(th['watch'], 4)}"
    # action = midpoint(moderate 0.125, severe 0.315) = 0.22
    assert abs(th["action"] - (0.125 + 0.315) / 2) < 0.02, \
        f"action sits between the moderate and severe medians: {round(th['action'], 4)}"
    assert th["watch"] < th["action"], "watch below action"

    # Non-monotonic labels must not produce inverted thresholds.
    bad = engine_with([(T0 - timedelta(days=i), 0.05) for i in range(6, 0, -1)])
    for sev, g in [("minor", 0.30), ("minor", 0.32), ("moderate", 0.02), ("moderate", 0.03),
                   ("severe", 0.10), ("severe", 0.11)]:
        bad.apply_severity_rating(time=T0, severity=sev, green_ratio_at_rating=g)
    bt = bad.thresholds
    assert bt["watch"] < bt["action"], "contradictory labels still yield ordered thresholds"


def test_partial_calibration_degrades_gracefully():
    p = engine_with([(T0 - timedelta(days=i), 0.03 + i * 0.005) for i in range(6, 0, -1)])
    p.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=0.01)
    pt = p.thresholds
    assert pt["mode"] in ("absolute", "baseline_relative"), \
        f"one label alone does not claim calibration: {pt['mode']}"
    assert p.labels_needed()["moderate"] == 2, \
        f"labels_needed reports what is missing: {p.labels_needed()}"
    for sev, g in [("minor", 0.04), ("minor", 0.045), ("moderate", 0.09), ("moderate", 0.095)]:
        p.apply_severity_rating(time=T0, severity=sev, green_ratio_at_rating=g)
    pt = p.thresholds
    assert pt.get("watch_calibrated") is True, "watch calibrates before severe labels exist"
    assert pt.get("action_calibrated") is False, "action stays uncalibrated without severe labels"
    assert pt["mode"] == "partially_calibrated", f"mode reports partial: {pt['mode']}"
    assert pt["watch"] < pt["action"], "partial calibration keeps thresholds ordered"


def test_camera_drift_detection():
    d = engine_with([(T0 - timedelta(days=i), 0.02) for i in range(10, 0, -1)])
    # Six "no algae" ratings where the camera reading creeps up - the pond is
    # clean every time but the lens is fouling.
    for i, g in enumerate([0.010, 0.011, 0.012, 0.030, 0.034, 0.038]):
        d.apply_severity_rating(time=T0 + timedelta(days=i), severity="none",
                                green_ratio_at_rating=g)
    drift = d.detect_camera_drift()
    assert drift["verdict"] == "drift_suspected", \
        f"rising 'no algae' readings flagged as drift: {drift['verdict']}"
    assert "lens" in drift["detail"].lower(), "drift verdict carries an actionable explanation"

    stable = engine_with([(T0 - timedelta(days=i), 0.02) for i in range(10, 0, -1)])
    for i, g in enumerate([0.010, 0.011, 0.0105, 0.0108, 0.0102, 0.011]):
        stable.apply_severity_rating(time=T0 + timedelta(days=i), severity="none",
                                     green_ratio_at_rating=g)
    assert stable.detect_camera_drift()["verdict"] == "stable", \
        f"flat 'no algae' readings report stable: {stable.detect_camera_drift()['verdict']}"

    few = engine_with([(T0 - timedelta(days=i), 0.02) for i in range(6, 0, -1)])
    few.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=0.01)
    assert few.detect_camera_drift()["verdict"] == "insufficient_data", \
        "too few labels reports insufficient_data, not a false verdict"


def test_obstruction_retroactively_removes_the_poisoned_frame():
    o = engine_with(CLEAN_PAIRS)
    hist_before = len(o._camera_history)

    # A leaf lands on the lens: a very green frame that is not algae.
    o.ingest_camera_samples(frames([(T0, 0.62)]))
    level_poisoned = o.green_ratio

    res = o.apply_severity_rating(time=T0, severity="obstruction", green_ratio_at_rating=0.62)
    assert res["removed_frames"] == 1, "obstructed frame removed from history"
    assert len(o._camera_history) == hist_before, "history length restored"
    assert o.green_ratio < level_poisoned, \
        f"level rolled back off the bad frame: {level_poisoned:.4f} -> {o.green_ratio:.4f}"
    assert o._obstructed_count == 1, "obstruction counted separately"
    assert o.label_counts()["none"] == 0 and o.label_counts()["obstruction"] == 1, \
        f"obstruction rating does not join the severity calibration set: {o.label_counts()}"


def test_undo_restores_state_exactly():
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
    assert abs(mistap_green - before["green"]) > 1e-6, "mis-tap visibly moved the level"

    ok = u.undo_last_rating(rating_id=99)
    assert ok, "undo reports success"
    assert u.green_ratio == before["green"], \
        f"level restored exactly: {u.green_ratio} vs {before['green']}"
    assert u.thresholds == before["thresholds"], "thresholds restored exactly"
    assert u._intrinsic_rate == before["rate"], "growth rate restored exactly"
    assert u.label_counts() == before["counts"], f"label counts restored: {u.label_counts()}"
    assert len(u._camera_history) == before["history"], "camera history restored"

    assert u.undo_last_rating(rating_id=99) is False, "second undo of the same id is refused"
    assert engine_with([(T0, 0.05)]).undo_last_rating(rating_id=12345) is False, \
        "undo with a mismatched id is refused"

    # Undo must also work for an obstruction rating.
    o2 = engine_with(CLEAN_PAIRS)
    o2.ingest_camera_samples(frames([(T0, 0.62)]))
    lvl = o2.green_ratio
    hist = len(o2._camera_history)
    o2.apply_severity_rating(time=T0, severity="obstruction", green_ratio_at_rating=0.62,
                             rating_id=7)
    o2.undo_last_rating(rating_id=7)
    assert len(o2._camera_history) == hist, "undoing an obstruction restores the frame"
    assert o2.green_ratio == lvl, "undoing an obstruction restores the level"


def test_validation_and_edge_cases():
    v = engine_with([(T0, 0.05)])
    raised = None
    try:
        v.apply_severity_rating(time=T0, severity="catastrophic")
    except ValueError as exc:
        raised = exc
    assert raised is not None, "invalid severity rejected: no exception raised"

    assert engine_with([(T0, 0.05)]).apply_severity_rating(
        time=T0, severity="  MODERATE ")["green_ratio_after"] is not None, \
        "severity is case-insensitive"

    # A rating on a pond with no camera history at all must still work.
    blank = ae.AlgaeGrowthEngine()
    assert blank.has_measurement is False, "no measurement before any input"
    blank.apply_severity_rating(time=T0, severity="moderate")
    assert blank.has_measurement is True, "a rating alone establishes a level"
    assert abs(blank.green_ratio - ae._SEVERITY_DEFAULT["moderate"]) < 1e-9, \
        f"level adopted from the default target: {blank.green_ratio}"
    assert blank.assess() is not None, "assessable from a rating alone"

    # Rating without green_ratio still corrects, just cannot calibrate.
    n = engine_with([(T0 - timedelta(days=1), 0.20), (T0, 0.22)])
    lvl_before = n.green_ratio
    n.apply_severity_rating(time=T0, severity="none", green_ratio_at_rating=None)
    assert n.green_ratio < lvl_before, "rating without a measurement still corrects the level"
    assert n._class_targets()["none"] != 0.0, \
        "rating without a measurement is skipped by calibration"


def test_snapshot_round_trip_carries_ratings():
    s = snapshot_engine()
    snap = json.loads(json.dumps(s.to_snapshot()))
    r = ae.AlgaeGrowthEngine.from_snapshot(snap)
    assert r.label_counts() == s.label_counts(), \
        f"ratings survive the round-trip: {r.label_counts()}"
    assert r.thresholds == s.thresholds, "calibrated thresholds survive"
    assert r._class_targets() == s._class_targets(), "class targets reproduce"
    assert r.green_ratio == s.green_ratio, "green level survives"
    assert r._undo_stack == [], "undo stack is NOT persisted (deliberate)"

    # load_ratings from the DB shape must reproduce the same calibration.
    rows = [
        {"id": i, "rated_at": (T0 + timedelta(hours=i)).isoformat(),
         "severity": sev, "is_obstructed": False, "green_ratio_at_rating": g,
         "image_id": None}
        for i, (sev, g) in enumerate(SNAPSHOT_LABELS)
    ]
    loaded = engine_with([(T0 - timedelta(days=i), 0.03 + i * 0.006) for i in range(6, 0, -1)])
    loaded.load_ratings(rows)
    assert loaded.label_counts() == s.label_counts(), \
        f"load_ratings reproduces the label counts: {loaded.label_counts()}"
    assert abs(loaded.thresholds["watch"] - s.thresholds["watch"]) < 1e-9, \
        "load_ratings reproduces the thresholds"


def test_assessment_surfaces_calibration_state():
    s = snapshot_engine()
    a = s.assess()
    assert a.label_count == 6, f"assessment reports the label count: {a.label_count}"
    assert isinstance(a.calibrated, bool), "assessment reports calibration state"
    assert a.camera_drift in ("stable", "drift_possible", "drift_suspected", "insufficient_data"), \
        "assessment reports drift verdict"
    assert isinstance(json.dumps(a.to_dict()), str), "assessment still JSON-serialisable"

    proj = s.project_forward(
        daily_environment=[ae.AlgaeDayEnvironment(lux=20000, temp_c=30.0, no3_ppm=12.0)],
        horizon_days=14)
    # Key name unified with the /ratings/algae endpoint - both now emit
    # "labels_needed" rather than two spellings of the same thing.
    for key in ("label_counts", "labels_needed", "camera_drift", "class_targets"):
        assert key in proj, f"projection exposes {key}"
