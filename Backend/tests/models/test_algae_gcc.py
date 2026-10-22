"""The algae engine on GCC (issue #92): daily windows, d(GCC)/dt growth,
offset thresholds, the zone rule on the cell grid, and the restart on a
camera baseline reset. Frames are synthetic: a 3x3 grid of water cells
over a floor row (cells 6-8), with white-balance drift and per-cell noise.
Run from Backend/: python -m pytest tests/models/test_algae_gcc.py
"""
import json
import random
import statistics
from datetime import datetime, timedelta, timezone

import pytest

from conftest import USER
from koi.models import algae_engine as ae
from koi.models.algae_engine import AlgaeDayEnvironment, AlgaeGrowthEngine, GccSignalConfig, GreenSample
from koi.models.pond_twin import SNAPSHOT_VERSION, PondTwin
from koi.settings import Settings
from koi.worker import poller

# 08:00 in Singapore; HOURS are the daytime frame times after it.
DAY0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
HOURS = (0, 2, 4, 6, 8, 10)
FLOOR = (0.50, 0.50, 0.48)
WATER = (0.25, 0.35, 0.30)
ENV = [AlgaeDayEnvironment(lux=20000, temp_c=29, no3_ppm=10)]


def at(day, hour=0):
    return DAY0 + timedelta(days=day, hours=hour)


def cell_gcc(rgb, wb=0.0, tint=0.0):
    """GCC of a cell whose green channel is scaled by a white-balance shift
    and a tint (0.16 = 16% more green)."""
    r, g, b = rgb
    g = g * (1 + wb) * (1 + tint)
    return g / (r + g + b)


def scene(seed, days, *, tint_cell=None, tint_at=(), tint=0.16, rise=0.0, by_frame=False, sd=0.004):
    """Daytime frames ({"t", "v", "s", "c"}) for `days` days. tint_at holds
    day numbers (frame numbers with by_frame) when tint_cell is tinted.
    rise lifts every cell, floor included, by that much GCC a day."""
    rng = random.Random(seed)
    frames, n = [], 0
    for d in range(days):
        for h in HOURS:
            wb = rng.gauss(0, 0.03)
            cells = []
            for i in range(9):
                tinted = i == tint_cell and (n if by_frame else d) in tint_at
                base = FLOOR if i >= 6 else WATER
                cells.append(cell_gcc(base, wb, tint if tinted else 0.0) + rise * d + rng.gauss(0, sd))
            frames.append({"t": at(d, h).isoformat(), "v": sum(cells[:6]) / 6, "s": "frame", "c": cells})
            n += 1
    return frames


def daily(seed, days, *, level=0.38, sd=0.004, rise_from=None, rate=0.0):
    """Pond-wide frames only (no grid): flat with noise, rising by rate a
    day from day rise_from on."""
    rng = random.Random(seed)
    out = []
    for d in range(days):
        lift = rate * (d - rise_from + 1) if rise_from is not None and d >= rise_from else 0.0
        out += [{"t": at(d, h).isoformat(), "v": level + lift + rng.gauss(0, sd)} for h in HOURS]
    return out


def samples(frames, *, green=0.02, reset_at=None, reason="obstruction_persisted"):
    """GreenSamples for frames; the frame at index reset_at carries a reset."""
    return [GreenSample(time=datetime.fromisoformat(f["t"]), green_ratio=green, smoothed_green=green,
                        state="base", gcc=f["v"], gcc_source=f.get("s", "frame"), grid=f.get("c"),
                        baseline_reset=reason if i == reset_at else None)
            for i, f in enumerate(frames)]


def flat_frames(values, start=0):
    """One daytime frame a day per value."""
    return [{"t": at(start + i, 4).isoformat(), "v": v} for i, v in enumerate(values)]


# ---------------------------------------------------------------- parsing

def test_parse_prefers_water_gap_and_drops_an_unusable_gap():
    grid = {"rows": 3, "cols": 3, "gcc": [0.4] * 8 + [None]}
    rows = [
        {"created_at": at(0).isoformat(), "green_ratio": 0.02, "gcc": 0.36, "colour": {"version": 1, "grid": grid},
         "regions": {"version": 1, "regions": {"water_gap": {"usable": True, "gcc": 0.41}}},
         "baseline_reset": "mask_changed"},
        {"created_at": at(1).isoformat(), "green_ratio": 0.02, "gcc": 0.36,
         "regions": {"version": 1, "regions": {"water_gap": {"usable": False, "unusable_reason": "plants_over_gap",
                                                             "gcc": 0.5}}}},
        {"created_at": at(2).isoformat(), "green_ratio": 0.02, "gcc": 0.37, "colour": {"version": 1}},
        {"created_at": at(3).isoformat(), "green_ratio": 0.02},
        {"created_at": at(4).isoformat(), "green_ratio": 0.02, "gcc": 0.9,
         "quality": {"version": 2, "status": "fail"}},
    ]
    gap, unusable, frame, legacy = ae.parse_image_rows(rows)
    assert (gap.gcc, gap.gcc_source, gap.baseline_reset) == (0.41, "water_gap", "mask_changed")
    assert gap.grid == [0.4] * 8 + [None]
    assert unusable.gcc is None and unusable.gcc_source is None, "an unusable gap does not fall back to the frame"
    assert (frame.gcc, frame.gcc_source, frame.grid) == (0.37, "frame", None)
    assert legacy.gcc is None and legacy.baseline_reset is None


# ------------------------------------------------------- daily and window

def test_daily_p90_and_three_day_window():
    frames = [{"t": at(0, h).isoformat(), "v": v} for h, v in ((0, .30), (2, .32), (4, .40))]
    frames += flat_frames([.40, .50, .60], start=1)
    days = ae.gcc_daily_series(frames)
    assert days[0]["gcc"] == pytest.approx(.384), "linear p90 of .30/.32/.40"
    assert days[0]["window"] == pytest.approx(.384), "the first window holds one day"
    assert days[1]["window"] == pytest.approx((.384 + .40) / 2)
    assert days[3]["window"] == pytest.approx((.40 + .50 + .60) / 3)


def test_daily_series_uses_daytime_local_days_median_and_no_invented_days():
    frames = [
        {"t": at(0, -2).isoformat(), "v": .90},   # 06:00 local, before the day starts
        {"t": at(0, 0).isoformat(), "v": .20},
        {"t": at(0, 1).isoformat(), "v": .40},
        {"t": at(0, 12).isoformat(), "v": .90},   # 20:00 local, after it ends
        {"t": at(2, 0).isoformat(), "v": .50},
    ]
    days = ae.gcc_daily_series(frames, GccSignalConfig(daily_statistic="median"))
    assert [d["date"] for d in days] == ["2026-09-01", "2026-09-03"], "day 2 has no frames and is not invented"
    assert days[0]["gcc"] == pytest.approx(.30) and days[0]["frames"] == 2
    assert days[1]["window"] == pytest.approx((.30 + .50) / 2), "the window spans calendar days"


# ------------------------------------------------------------- growth

def test_a_noisy_flat_series_does_not_flag_growth():
    """Judged every day for 50 noisy flat ponds: a false growth flag is
    rare (k = 3 on a 7-difference noise estimate; about 0.3% when
    measured over 200 ponds, decision record 0092)."""
    flagged = judged = 0
    for seed in range(50):
        days = ae.gcc_daily_series(daily(seed, 30))
        for end in range(1, len(days) + 1):
            growth = ae.gcc_growth(days[:end])
            if growth["threshold"] is None:
                assert not growth["flagged"]
                continue
            judged += 1
            flagged += growth["flagged"]
    assert judged >= 1000 and flagged / judged < 0.01, (flagged, judged)
    assert growth["noise"] > 0 and growth["threshold"] == pytest.approx(3 * growth["noise"])


def test_a_series_rising_at_the_threshold_flags_within_the_window():
    config = GccSignalConfig()
    quiet = ae.gcc_growth(ae.gcc_daily_series(daily(3, 14)), config)
    assert not quiet["flagged"] and quiet["quiet_differences"] >= config.min_quiet_diffs
    frames = daily(3, 14 + config.growth_window_days, rise_from=14, rate=1.2 * quiet["threshold"])
    growth = ae.gcc_growth(ae.gcc_daily_series(frames), config)
    assert growth["flagged"], growth
    days = ae.gcc_daily_series(frames)
    before_rise = [b["window"] - a["window"] for a, b in zip(days[:14], days[1:14], strict=False)]
    assert growth["noise"] == pytest.approx(statistics.stdev(before_rise)), "the rise stays out of its noise"


def test_growth_needs_a_quiet_period_and_reports_the_slow_slope():
    short = ae.gcc_growth(ae.gcc_daily_series(flat_frames([.30, .31, .32, .33, .34])))
    assert short["noise"] is None and not short["flagged"], "too few quiet differences to judge"
    assert short["rate_per_day"] == pytest.approx(.01)
    flat = ae.gcc_growth(ae.gcc_daily_series(flat_frames([.30] * 12)))
    assert flat["threshold"] == pytest.approx(GccSignalConfig().growth_min_rate), "a noiseless series uses the floor"
    assert not flat["flagged"]
    rising = ae.gcc_growth(ae.gcc_daily_series(flat_frames([.30] * 12 + [.31, .32, .33])))
    assert rising["flagged"] and rising["rate_per_day"] == pytest.approx(.01)
    assert 0 < rising["slow_rate_per_day"] < rising["rate_per_day"], "the 7-day slope still holds flat days"


# --------------------------------------------------------- thresholds

def test_thresholds_are_offsets_above_the_quiet_level():
    days = ae.gcc_daily_series(flat_frames([.30] * 6 + [.40] * 2))
    thresholds = ae.resolve_gcc_thresholds(days, GccSignalConfig(watch_offset=.02, action_offset=.05))
    assert thresholds["baseline"] == pytest.approx(.30)
    assert (thresholds["watch"], thresholds["action"]) == (pytest.approx(.32), pytest.approx(.35))
    assert thresholds["mode"] == "gcc_offset"


def test_a_near_zero_level_does_not_alarm_on_noise():
    """Live 2026-08-27: Amber at 0.0163 against a 3x watch of 0.015. An
    offset above a low level needs a real change, not a multiple."""
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(samples(flat_frames([.005, .0055, .0048, .0052, .0163])))
    assessment = engine.assess()
    assert assessment.signal == "gcc" and assessment.status == "Green"
    assert assessment.watch_threshold == pytest.approx(assessment.gcc_baseline + .03, abs=1e-5)


# ------------------------------------------------------ baseline reset

def test_a_rebaseline_restarts_history_and_the_live_sequence_stays_green():
    engine = AlgaeGrowthEngine()
    before = flat_frames([.0125] * 8)
    after = flat_frames([.25] * 3, start=8)
    engine.ingest_camera_samples(samples(before, green=.0125))
    engine.ingest_camera_samples(samples(after, green=.25, reset_at=0))
    assessment = engine.assess()
    assert assessment.status == "Green", assessment.advisory
    assert len(engine._gcc_frames) == 3 and len(engine._camera_history) == 3, "the old view is forgotten"
    assert assessment.gcc_baseline == pytest.approx(.25)
    assert (assessment.baseline_reset_at, assessment.baseline_reset_reason) == \
        (after[0]["t"], "obstruction_persisted")


def test_without_the_reset_the_same_sequence_would_alarm():
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(samples(flat_frames([.0125] * 8 + [.25] * 3)))
    assert engine.assess().status == "Red"


def test_legacy_green_ratio_path_restarts_on_a_reset_too():
    """The live numbers: about 0.01 before the 2026-10-04 re-baseline and
    0.19 after. Mixed, the 3x/6x thresholds read Red."""
    def rows(values, start, reset=False):
        return [{"created_at": at(start + i, 4).isoformat(), "green_ratio": v, "current_state": ["base", v],
                 "baseline_reset": "obstruction_persisted" if reset and i == 0 else None}
                for i, v in enumerate(values)]
    mixed = AlgaeGrowthEngine()
    mixed.ingest_camera_samples(ae.parse_image_rows(rows([.01] * 8, 0) + rows([.19] * 4, 8)))
    assert mixed.assess().status == "Red"
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(ae.parse_image_rows(rows([.01] * 8, 0) + rows([.19] * 4, 8, reset=True)))
    assessment = engine.assess()
    assert assessment.status == "Green" and assessment.signal == "green_ratio"
    assert assessment.rate_source_detail == "legacy_green_ratio"
    assert len(engine._camera_history) == 4


def test_an_old_snapshot_restarts_from_a_reset_it_already_passed():
    """A v5 twin assimilated the reset frame before it knew about resets:
    the next batch still holds that frame, behind the watermark."""
    frames = flat_frames([.0125] * 8 + [.25] * 3)
    old = AlgaeGrowthEngine()
    old.ingest_camera_samples(samples(frames))
    snap = old.to_snapshot()
    for key in ("gcc_frames", "baseline_reset", "signal_config"):
        snap.pop(key)
    engine = AlgaeGrowthEngine.from_snapshot(json.loads(json.dumps(snap)))
    assert engine._gcc_frames == [] and engine.assess().signal == "green_ratio"
    assert engine.ingest_camera_samples(samples(frames, reset_at=8)) == 3
    assert engine.assess().status == "Green" and len(engine._gcc_frames) == 3
    assert engine.ingest_camera_samples(samples(frames, reset_at=8)) == 0, "the same reset is not applied twice"


# ------------------------------------------------- legacy and snapshots

def test_legacy_rows_without_gcc_still_load_on_the_green_ratio_path():
    rows = [{"created_at": at(i, 4).isoformat(), "green_ratio": .03 * 1.1 ** i,
             "current_state": ["base", .03 * 1.1 ** i]} for i in range(6)]
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(ae.parse_image_rows(rows))
    engine.refit_growth_rate(20000, 29, 10)
    assessment = engine.assess()
    assert not engine.uses_gcc and assessment.signal == "green_ratio" and assessment.gcc is None
    assert assessment.rate_source == "fitted_from_camera"
    assert assessment.rate_source_detail == "legacy_green_ratio"
    projection = engine.project_forward(daily_environment=ENV, horizon_days=3)
    assert projection["projection_method"] == "logistic" and "green_ratio" in projection["trajectory"][0]


def test_old_snapshots_load_and_save_as_the_new_version():
    algae_v5 = {"config": {"carrying_capacity": .75}, "green_ratio": .04, "camera_history": [],
                "thresholds": None, "ratings": []}
    engine = AlgaeGrowthEngine.from_snapshot(algae_v5)
    assert engine.green_ratio == .04 and engine._gcc_frames == [] and engine._baseline_reset is None
    assert engine.signal_config == GccSignalConfig()
    twin = PondTwin.from_snapshot({**PondTwin.create(_twin_config()).to_snapshot(), "version": 5,
                                   "algae": algae_v5})
    assert twin.algae.green_ratio == .04
    assert twin.to_snapshot()["version"] == SNAPSHOT_VERSION == 7


def test_snapshot_round_trip_keeps_gcc_frames_reset_and_config():
    config = GccSignalConfig(watch_offset=.02, action_offset=.04, object_cells=(3,), run_unit="frame")
    engine = AlgaeGrowthEngine(signal_config=config)
    engine.ingest_camera_samples(samples(scene(1, 6), reset_at=0))
    restored = AlgaeGrowthEngine.from_snapshot(json.loads(json.dumps(engine.to_snapshot())))
    assert restored.signal_config == config
    assert restored._gcc_frames == engine._gcc_frames and restored._baseline_reset == engine._baseline_reset
    assert restored.assess().to_dict() == engine.assess().to_dict()


def _twin_config():
    from koi.models.engine import PondConfig
    return PondConfig(volume_litres=2000, estimated_biomass_grams=1500)


# --------------------------------------------------------- zone rule

def test_zone_rule_on_clean_frames_gives_no_flag():
    for seed in range(20):
        zones = ae.gcc_zones(scene(seed, 14))
        assert zones["flagged"] == [] and zones["unreliable"] == [], (seed, zones)
    assert zones["reference_cells"] == [6, 7, 8] and zones["pond_cells"] == [0, 1, 2, 3, 4, 5]
    assert zones["threshold"] == pytest.approx(3 * zones["noise"])


def test_a_one_off_tint_is_a_spike_and_a_sustained_tint_flags():
    spike = ae.gcc_zones(scene(1, 14, tint_cell=4, tint_at=(10,)))
    assert spike["flagged"] == [] and [s["index"] for s in spike["spikes"]] == [4]
    assert spike["spikes"][0]["since"] == "2026-09-11"
    sustained = ae.gcc_zones(scene(1, 14, tint_cell=4, tint_at=(12, 13)))
    assert [(c["index"], c["run_length"]) for c in sustained["flagged"]] == [(4, 2)]
    assert sustained["flagged"][0]["latest_excess"] > sustained["threshold"]
    pending = ae.gcc_zones(scene(1, 14, tint_cell=4, tint_at=(13,)))
    assert pending["flagged"] == [] and [c["index"] for c in pending["pending"]] == [4]
    for tint in (0.16, 0.25):
        assert ae.gcc_zones(scene(2, 14, tint_cell=0, tint_at=(12, 13), tint=tint))["flagged"]


def test_a_tint_with_a_gap_day_is_not_a_run():
    frames = [f for f in scene(1, 14, tint_cell=4, tint_at=(11, 13)) if not f["t"].startswith("2026-09-13")]
    zones = ae.gcc_zones(frames)
    assert zones["flagged"] == [] and [c["index"] for c in zones["pending"]] == [4]


def test_frame_mode_counts_consecutive_frames():
    config = GccSignalConfig(run_unit="frame")
    zones = ae.gcc_zones(scene(1, 3, tint_cell=2, tint_at=(16, 17), by_frame=True), config)
    assert [(c["index"], c["run_length"]) for c in zones["flagged"]] == [(2, 2)]
    one = ae.gcc_zones(scene(1, 3, tint_cell=2, tint_at=(15,), by_frame=True), config)
    assert one["flagged"] == [] and [c["index"] for c in one["spikes"]] == [2]


def test_a_uniform_shift_of_every_cell_is_cancelled_by_the_reference_cells():
    frames = scene(1, 14)
    for f in frames[-12:]:
        f["c"] = [c + .05 for c in f["c"]]
    assert ae.gcc_zones(frames)["flagged"] == []
    without_reference = ae.gcc_zones(frames, GccSignalConfig(reference_cells=()))
    assert without_reference["note"] == "no_reference_cells"


def test_a_uniform_green_rise_still_shows_in_the_pond_wide_series():
    frames = scene(1, 14) + [{**f, "t": (datetime.fromisoformat(f["t"]) + timedelta(days=14)).isoformat()}
                             for f in scene(2, 6, rise=.012)]
    assert ae.gcc_zones(frames)["flagged"] == [], "the floor rises with the water, so no cell stands out"
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(samples(frames))
    assessment = engine.assess()
    assert assessment.growth["flagged"], assessment.growth
    assert assessment.status in ("Amber", "Red") and assessment.gcc > assessment.gcc_baseline + .03


def test_a_noisy_cell_is_unreliable_and_object_cells_are_ignored():
    rng = random.Random(5)
    frames = scene(1, 14)
    for f in frames:
        f["c"][1] += rng.choice((0, .08))
    zones = ae.gcc_zones(frames)
    assert zones["unreliable"] == [1] and all(c["index"] != 1 for c in zones["flagged"] + zones["spikes"])
    ignored = ae.gcc_zones(frames, GccSignalConfig(object_cells=(1, 6)))
    assert ignored["unreliable"] == [] and 1 not in ignored["pond_cells"]
    assert ignored["reference_cells"] == [7, 8] and ignored["object_cells"] == [1, 6]


def test_zone_rule_needs_a_baseline_first():
    zones = ae.gcc_zones(scene(1, 4))
    assert zones["insufficient"] == [0, 1, 2, 3, 4, 5] and zones["noise"] is None


# ------------------------------------------------ engine output

def test_assessment_reports_growth_noise_and_flagged_cells():
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(samples(scene(1, 14, tint_cell=4, tint_at=(12, 13))))
    assessment = engine.assess()
    assert assessment.signal == "gcc" and assessment.gcc_source == "frame"
    assert assessment.rate_source_detail == "gcc_windowed_trend"
    assert assessment.rate_source in ("fitted_from_camera", "measured_declining"), "one of the app's branches"
    assert assessment.growth["noise"] is not None and "cell(s) 4" in assessment.advisory
    assert [c["index"] for c in assessment.zones["flagged"]] == [4]
    json.dumps(assessment.to_dict())


def test_projection_uses_the_windowed_trend_and_says_so():
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(samples(flat_frames([.30] * 8 + [.30 + .005 * i for i in range(1, 5)])))
    projection = engine.project_forward(daily_environment=ENV, horizon_days=10, include_scrub_benefit=True)
    assert projection["projection_method"] == "windowed_gcc_trend"
    assert "logistic growth model does not fit" in projection["projection_note"]
    assert projection["current_gcc"] == pytest.approx(engine.assess().gcc)
    rate = projection["realised_rate_per_day"]
    assert rate == pytest.approx(.005)
    assert projection["trajectory"][0]["gcc"] == pytest.approx(projection["current_gcc"] + rate, abs=1e-5)
    assert projection["first_watch_days_from_now"] is not None
    assert projection["scrub_benefit"]["post_scrub_gcc"] < projection["current_gcc"]
    assert engine.project_scrub_benefit(daily_environment=ENV) == projection["scrub_benefit"]


def test_a_label_calibrated_threshold_takes_over_from_the_offsets():
    engine = AlgaeGrowthEngine()
    engine.ingest_camera_samples(samples(flat_frames([.30] * 6)))
    engine._thresholds = {"watch": .01, "action": .015, "baseline": None, "mode": "label_calibrated"}
    assessment = engine.assess()
    assert assessment.signal == "green_ratio" and assessment.status == "Red" and assessment.calibrated
    assert assessment.gcc is not None, "the GCC readout is still reported"


# ---------------------------------------------------------- settings

def test_settings_build_the_signal_config():
    assert Settings(_env_file=None).algae_signal_config == GccSignalConfig()
    settings = Settings(_env_file=None, KOI_ALGAE_REFERENCE_CELLS="0, 1", KOI_ALGAE_OBJECT_CELLS="",
                        KOI_ALGAE_DAILY_STATISTIC="median", KOI_ALGAE_WATCH_OFFSET="0.02", TEST_MODE="1")
    config = settings.algae_signal_config
    assert (config.reference_cells, config.object_cells) == ((0, 1), ())
    assert (config.daily_statistic, config.watch_offset, config.run_unit) == ("median", .02, "frame")


@pytest.mark.parametrize("env", [
    {"KOI_ALGAE_WATCH_OFFSET": "0.06", "KOI_ALGAE_ACTION_OFFSET": "0.03"},
    {"KOI_ALGAE_DAY_START_HOUR": "19", "KOI_ALGAE_DAY_END_HOUR": "7"},
    {"KOI_ALGAE_DAILY_STATISTIC": "mean"},
    {"KOI_ALGAE_CELL_RUN": "1"},
    {"KOI_ALGAE_REFERENCE_CELLS": "-1"},
])
def test_settings_reject_bad_algae_values(env):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **env)


def test_signal_config_rejects_unknown_modes():
    with pytest.raises(ValueError):
        GccSignalConfig(run_unit="hour")
    assert GccSignalConfig.from_dict({"watch_offset": .02, "later_setting": 1}).watch_offset == .02


# ------------------------------------------------------------ poller

def test_poll_user_hands_the_settings_config_to_the_engine(pond_app):
    storage = pond_app.extensions["koi_storage"]
    registry = pond_app.extensions["koi_registry"]
    storage.add_rows("imageTable", [
        {"user_ID": USER, "id": 9200 + i, "created_at": f["t"], "green_ratio": .02, "current_state": ["base", .02],
         "gcc": f["v"], "colour": {"version": 1, "grid": {"rows": 3, "cols": 3, "gcc": f["c"]}},
         "imageURL": f"https://example/{i}.jpg"}
        for i, f in enumerate(scene(1, 4)[::3])
    ])
    config = GccSignalConfig(watch_offset=.011, action_offset=.022)
    registry.algae_signal_config = config
    poller._poll_user(registry, USER, storage.fetch_active_pond_configs()[0])
    snap = storage.load_engine_snapshot(USER)
    assert snap["algae"]["signal_config"] == config.to_dict()
    row = storage.rows("pond_algae_evaluations")[-1]
    assert row["threshold_mode"] == "gcc_offset"
    assert row["action_threshold"] - row["watch_threshold"] == pytest.approx(.011, abs=1e-4)
