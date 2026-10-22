# 0127: SensorGate rate recovery after a real step
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #127 · Date: 2026-10-22

## In short

The per-channel rate allowance now grows with elapsed time since that channel's last trusted reading. A sustained level shift can therefore become trusted after a bounded period, while an isolated jump remains suspect. Snapshots persist the trusted timestamps and continue loading snapshots written before this field existed.

## How it works

1. Each incoming sample still updates the gate's overall last-sample time, which keeps the existing sample cadence available to old saved states.
2. When a channel has a trusted timestamp, its change allowance grows using elapsed time from that timestamp. A suspect value does not update the reference timestamp.
3. Repeated readings at the new level eventually fit within the accumulated allowance and become trusted. That accepted reading becomes the new reference point.
4. When the gate is saved, it stores one trusted timestamp per channel. When an older save is loaded without those timestamps, its first rate comparison uses the former sample-to-sample interval behavior; the next trusted value establishes the timestamp.

## Main design choice

The rate clock belongs to each channel's trusted reference, not to the gate's newest sample. A single shared timestamp would be wrong when one channel is missing or suspect while another channel remains trusted. Keeping only the newest raw timestamp would recreate the latch because rejected observations would keep resetting the allowance.

Keeping rejected values as a moving baseline could recover even faster, but it would allow a one-sample spike to influence later verdicts. The trusted baseline avoids that; a longer bounded recovery is appropriate because no event explains the shift.

## Traps: why the obvious fix is wrong

- Do not advance trusted timestamps for `suspect_rate`, range-rejected, or stale readings. Doing so would let an untrusted input move the comparison reference.
- Do not replace the legacy fallback with a guessed timestamp. Old snapshots contain no timestamp provenance, so their first comparison intentionally preserves the old rule.
- Do not change pH `max_rate`: the issue concerns the elapsed-time basis, and the existing rate is 1.0 pH/hour (`Backend/koi/models/engine.py::_BOUNDS`).

## Components

- `Backend/koi/models/engine.py::SensorGate` - per-channel rate reference and snapshot.
- `Backend/tests/models/test_new_engines.py` - sustained step, spike, exemption, and snapshot regressions.
- `Backend/tests/models/test_property_engines.py::test_property_snapshot_round_trip_is_exact` - engine snapshot round trip.
- `docs/models.md` - operational rule and unchanged rate.

## Breaks or needs follow-up

None.

## Problem and constraints

At a 15-minute cadence, pH 7.02 to 7.60 exceeds the existing allowance of 0.25 per sample. Since the old clock advanced on every sample while the comparison value advanced only for trusted readings, the same sustained 0.58 step could remain suspect indefinitely. Preserve the existing channel ranges, max-rate constants, logged-volume-event exemption, and loading of older snapshots.

## Alternatives considered

- Compare against the last raw value - rejected observations could become the baseline and hide a real isolated spike.
- Increase the pH rate - changes model policy for every pH sample, including channels and cadences outside this reproducer.
- Store one global trusted timestamp - channels can be missing or suspect independently, so the clock must be per channel.

## Trade-offs

A real unexplained step is delayed until accumulated allowance covers it. At faster cadences, the time bound is still derived from the same rate and last trusted time; cadence-aware dense-data policy remains out of scope.

## Key parameters

- pH maximum rate: 1.0 pH/hour (`Backend/koi/models/engine.py::_BOUNDS`), unchanged. Raising it shortens recovery but admits faster unexplained shifts; lowering it lengthens recovery.
- Reproducer cadence: 15 minutes and step: 0.58 pH (issue #127 evidence). With the unchanged rate, this requires 34.8 minutes of allowance from the last trusted timestamp; a 15-minute sample grid accepts within 45 minutes if the shift begins just after a trusted sample.

## Assumptions

- Channel sample times are ordered, as in the existing input path. Out-of-order time behavior was not changed.
- No logged volume event means the unexplained step uses the rate check. Nearby logged events keep their existing exemption.

## Edge cases

- Handled: individual channel trusted times survive a current snapshot round trip; missing legacy fields load as empty state.
- Handled: one abrupt pH spike remains suspect; a return to the trusted value remains accepted.
- Not handled: cadence-aware rate limits for dense one-minute streams; the issue explicitly leaves this out of scope.

## Not verified

The owner-supplied external pond replay could not be rerun by the runner. Exact replay recovery durations for Oman, IDN-T, and FWI ponds remain unmeasured here.

## Changes outside the scope

None.

## How it was verified

- `Backend/tests/models/test_new_engines.py -k sensor_gate` - three tests passed: sustained-step recovery, an isolated spike, event exemption, and current/legacy snapshot loading.
- `cd Backend && python -m pytest -q -k "gate or engine"` - 45 passed; this includes the new gate regressions and existing engine-selected tests. It does not rerun the owner-supplied pond replay.
- `python tools/check.py all` - the complete runner finished successfully: backend 1,950 passed and one skipped; tools 39 passed; firmware host 296 passed and all three ESP32 builds passed; Flutter analysis and tests passed. The backend skip is the Docker-dependent local SQL dev-profile check, unavailable in this sandbox.

## To change this

To admit faster pH changes, raise the pH rate in `Backend/koi/models/engine.py::_BOUNDS`; this also makes isolated spikes easier to trust. To change recovery without changing the policy rate, alter the time basis in `Backend/koi/models/engine.py::SensorGate`; update the sustained-step and single-spike regressions together.

## Rollback

Remove the per-channel trusted timestamp state and restore `allowed_delta = max_rate * hours` in `Backend/koi/models/engine.py::SensorGate`. Before rollback, confirm snapshots written with `last_trusted_time` remain harmless to the old loader (it ignores the extra field).

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-22 14:53 UTC for issue #127 attempt 1; compared HEAD..working tree (base 4a66ffe). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 1 file(s), 4 component(s), +12/-1 lines
- plus 1 test file(s), 1 doc file(s)

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/engine.py`, `Backend/tests/models/test_new_engines.py`, `Backend/tests/models/test_property_engines.py`, `docs/models.md`, `docs/decisions/**`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Evidence**

1 checked by the tool, 0 tested in this run, 17 claimed only (of 18 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 1 checked, 0 tested, 3 claimed
- Key parameters: 0 checked, 0 tested, 2 claimed
- Edge cases: 0 checked, 0 tested, 2 claimed
- How it was verified: 0 checked, 0 tested, 3 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 2 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/engine.py::SensorGate` - per-channel rate reference and snapshot. (named code exists and changed)
  - [claimed] Components: `Backend/tests/models/test_new_engines.py` - sustained step, spike, exemption, and snap... (names no code)
  - [claimed] Key parameters: pH maximum rate: 1.0 pH/hour (`Backend/koi/models/engine.py::_BOUNDS`), unchanged. Rais... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: individual channel trusted times survive a current snapshot round trip; missin... (names no code)
  - [claimed] How it was verified: `Backend/tests/models/test_new_engines.py -k sensor_gate` - three tests passed: sustain... (no test named)
  - [claimed] Components: `Backend/tests/models/test_property_engines.py::test_property_snapshot_round_trip_is_ex... (names no code)
  - [claimed] Key parameters: Reproducer cadence: 15 minutes and step: 0.58 pH (issue #127 evidence). With the unchan... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: one abrupt pH spike remains suspect; a return to the trusted value remains acc... (names no code)
  - [claimed] How it was verified: `cd Backend && python -m pytest -q -k "gate or engine"` - 45 passed; this includes the... (no test named)
  - [claimed] Components: `docs/models.md` - operational rule and unchanged rate. (names no code)
  - full per-claim list (18) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0127-sensor-gate-rate-recovery.md` gives the commit.
<!-- verified-facts:end -->
