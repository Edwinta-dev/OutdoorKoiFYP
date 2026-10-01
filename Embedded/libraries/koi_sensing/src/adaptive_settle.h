#pragma once
// NEW in Round 2 (not a straight extraction). FullSketch.ino waits a fixed
// PH_SETTLE_MS = 5000 before every pH read - a worst-case guess. Meanwhile
// Embedded/bench_tests/ph_hw828_ads1115/ph_hw828_ads1115.ino's readSettled() already proves out a
// better pattern for the exact same kind of probe: take a few readings and
// stop as soon as they stop moving. This generalizes that pattern so
// FullSketch's pH (and TDS) reads can adopt it: typically-shorter settle
// time -> less powered-on time per wake cycle -> better battery life,
// without weakening the worst-case guarantee (see settleTrackerTimedOut).

struct SettleTracker {
  float lo = 1e30f, hi = -1e30f;
  float last = 0.0f;
  int   count = 0;
};

inline void settleTrackerReset(SettleTracker &t) { t = SettleTracker(); }

inline void settleTrackerAdd(SettleTracker &t, float value) {
  if (value < t.lo) t.lo = value;
  if (value > t.hi) t.hi = value;
  t.last = value;
  t.count++;
}

inline float settleTrackerSpread(const SettleTracker &t) {
  return t.count > 0 ? (t.hi - t.lo) : 0.0f;
}

// Settled once at least minSamples readings all fall within toleranceMv of each other.
inline bool settleTrackerIsSettled(const SettleTracker &t, float toleranceMv, int minSamples) {
  return t.count >= minSamples && settleTrackerSpread(t) <= toleranceMv;
}

// Worst-case fallback: if it never settles, give up at maxMs and use the last
// reading anyway (matches FullSketch's current fixed-wait behavior as the floor).
inline bool settleTrackerTimedOut(unsigned long elapsedMs, unsigned long maxMs) {
  return elapsedMs >= maxMs;
}
