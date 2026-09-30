"""Round 3 regression tests: AlgaeGrowthEngine's cached history_samples.

ingest_camera_samples() now caches the GreenSample list it builds
(self._last_history_samples) so refit_growth_rate() can reuse it instead of
re-parsing the whole camera history a second time. This guards against that
cache ever silently drifting from a fresh _history_as_samples() derivation,
and against the optional history_samples parameter breaking a standalone
caller that never populates the cache.
Run from Backend/: python -m pytest DigitalTwin/test_algae_history_cache.py
"""
from datetime import datetime, timedelta, timezone

from algae_engine import AlgaeGrowthEngine, GreenSample

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
VALUES = [0.02, 0.021, 0.023, 0.026, 0.030, 0.035]


def make_samples(t0, values):
    return [
        GreenSample(time=t0 + timedelta(hours=4 * i), green_ratio=v, smoothed_green=v, state="base")
        for i, v in enumerate(values)
    ]


def default_path_engine():
    """Engine refitted through the default (history_samples=None) path."""
    e = AlgaeGrowthEngine()
    e.ingest_camera_samples(make_samples(T0, VALUES))
    e.refit_growth_rate(recent_lux=15000, recent_temp_c=29.0, recent_no3_ppm=8.0)
    return e


def test_cached_history_matches_a_fresh_derivation():
    e1 = AlgaeGrowthEngine()
    e1.ingest_camera_samples(make_samples(T0, VALUES))
    fresh = e1._history_as_samples()
    cached = e1._last_history_samples
    assert len(cached) > 0, \
        f"cache is populated after ingest_camera_samples: {len(cached)} entries"
    assert len(cached) == len(fresh), \
        f"cached list has the same length as a fresh derivation: " \
        f"cached={len(cached)} fresh={len(fresh)}"
    same_values = all(
        c.time == f.time and c.green_ratio == f.green_ratio for c, f in zip(cached, fresh, strict=True)
    )
    assert same_values, "cached entries are value-identical to a fresh derivation"


def test_explicit_cache_gives_the_same_fit_as_the_default_path():
    e2a = default_path_engine()

    e2b = AlgaeGrowthEngine()
    e2b.ingest_camera_samples(make_samples(T0, VALUES))
    e2b.refit_growth_rate(
        recent_lux=15000, recent_temp_c=29.0, recent_no3_ppm=8.0,
        history_samples=e2b._last_history_samples,
    )
    assert e2a._intrinsic_rate == e2b._intrinsic_rate and e2a._rate_source == e2b._rate_source, \
        "explicitly passing the cached list reproduces the default derivation exactly"


def test_refit_works_standalone_with_no_cache():
    e3 = AlgaeGrowthEngine()
    # Populate _camera_history directly, bypassing ingest_camera_samples, so
    # _last_history_samples stays at its __init__ default ([]) - simulates any
    # future caller that has camera history but never built the cache.
    for i, v in enumerate(VALUES):
        e3._camera_history.append({"t": (T0 + timedelta(hours=4 * i)).isoformat(), "v": v})
    assert e3._last_history_samples == [], \
        "cache is empty before refit_growth_rate is called standalone"
    e3.refit_growth_rate(recent_lux=15000, recent_temp_c=29.0, recent_no3_ppm=8.0)
    assert e3._rate_source == "fitted_from_camera", \
        f"standalone call (no cache) still derives a real fit via the None fallback: {e3._rate_source}"
    assert abs(e3._intrinsic_rate - default_path_engine()._intrinsic_rate) < 1e-9, \
        "standalone call's result matches the cached-path result (same underlying history)"
