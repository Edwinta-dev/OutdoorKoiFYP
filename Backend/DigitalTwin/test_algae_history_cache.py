"""Round 3 regression tests: AlgaeGrowthEngine's cached history_samples.

ingest_camera_samples() now caches the GreenSample list it builds
(self._last_history_samples) so refit_growth_rate() can reuse it instead of
re-parsing the whole camera history a second time. This guards against that
cache ever silently drifting from a fresh _history_as_samples() derivation,
and against the optional history_samples parameter breaking a standalone
caller that never populates the cache. Run: python3 test_algae_history_cache.py
"""
from datetime import datetime, timedelta, timezone

from algae_engine import AlgaeGrowthEngine, GreenSample

FAIL = []


def check(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(label)


def make_samples(t0, values):
    return [
        GreenSample(time=t0 + timedelta(hours=4 * i), green_ratio=v, smoothed_green=v, state="base")
        for i, v in enumerate(values)
    ]


t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
values = [0.02, 0.021, 0.023, 0.026, 0.030, 0.035]

# =====================================================================
print("=== 1. Cached history_samples matches a fresh _history_as_samples() derivation ===")
e1 = AlgaeGrowthEngine()
e1.ingest_camera_samples(make_samples(t0, values))
fresh = e1._history_as_samples()
cached = e1._last_history_samples
check("cache is populated after ingest_camera_samples", len(cached) > 0, f"{len(cached)} entries")
check(
    "cached list has the same length as a fresh derivation",
    len(cached) == len(fresh),
    f"cached={len(cached)} fresh={len(fresh)}",
)
same_values = all(
    c.time == f.time and c.green_ratio == f.green_ratio for c, f in zip(cached, fresh)
)
check("cached entries are value-identical to a fresh derivation", same_values)

# =====================================================================
print("\n=== 2. Passing the cache explicitly gives the same fit as the default (None) path ===")
e2a = AlgaeGrowthEngine()
e2a.ingest_camera_samples(make_samples(t0, values))
e2a.refit_growth_rate(recent_lux=15000, recent_temp_c=29.0, recent_no3_ppm=8.0)  # default path

e2b = AlgaeGrowthEngine()
e2b.ingest_camera_samples(make_samples(t0, values))
e2b.refit_growth_rate(
    recent_lux=15000, recent_temp_c=29.0, recent_no3_ppm=8.0,
    history_samples=e2b._last_history_samples,
)

print(f"  default-path intrinsic_rate: {e2a._intrinsic_rate:.6f} ({e2a._rate_source})")
print(f"  explicit-cache intrinsic_rate: {e2b._intrinsic_rate:.6f} ({e2b._rate_source})")
check(
    "explicitly passing the cached list reproduces the default derivation exactly",
    e2a._intrinsic_rate == e2b._intrinsic_rate and e2a._rate_source == e2b._rate_source,
)

# =====================================================================
print("\n=== 3. refit_growth_rate still works with no cache at all (standalone call) ===")
e3 = AlgaeGrowthEngine()
# Populate _camera_history directly, bypassing ingest_camera_samples, so
# _last_history_samples stays at its __init__ default ([]) - simulates any
# future caller that has camera history but never built the cache.
for i, v in enumerate(values):
    e3._camera_history.append({"t": (t0 + timedelta(hours=4 * i)).isoformat(), "v": v})
check("cache is empty before refit_growth_rate is called standalone", e3._last_history_samples == [])
e3.refit_growth_rate(recent_lux=15000, recent_temp_c=29.0, recent_no3_ppm=8.0)
check(
    "standalone call (no cache) still derives a real fit via the None fallback",
    e3._rate_source == "fitted_from_camera",
    e3._rate_source,
)
check(
    "standalone call's result matches the cached-path result (same underlying history)",
    abs(e3._intrinsic_rate - e2a._intrinsic_rate) < 1e-9,
)

# =====================================================================
if FAIL:
    print(f"\n{len(FAIL)} CHECK(S) FAILED: {FAIL}")
    raise SystemExit(1)
print("\nALL ALGAE HISTORY-CACHE CHECKS PASSED")
