# 0126: Pond-relative pH indicator in shadow mode
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #126 · Date: 2026-10-22

## In short

The worker now computes a robust pH deviation against each pond's own recent pH history. The result is logged and the snapshot retains only the latest value; the user-facing hypoxia advisory and API remain unchanged.

## How it works

1. Each poll uses the existing hypoxia history, which contains only fresh paired pH and water-temperature readings recorded with their sensor event times.
2. At the poll's assessment time, samples later than that time or older than the trailing 14-day window (`Backend/koi/models/hypoxia.py::SHADOW_WINDOW_DAYS`) are excluded.
3. With at least 48 eligible values (`Backend/koi/models/hypoxia.py::SHADOW_MIN_SAMPLES`), the model finds their median and median absolute deviation. It calculates robust z as `0.6744897501960817 * (current - median) / MAD` (`Backend/koi/models/hypoxia.py::MAD_TO_STD`) and marks a low outlier below median minus 3 MADs (`Backend/koi/models/hypoxia.py::SHADOW_RANGE_MADS`).
4. The poller saves the three shadow fields in the feature-history snapshot and emits one structured log event. No assessment or response is given these fields.

## Main design choice

The calculation is an isolated observer on the existing snapshot history. This meets the owner's snapshot-and-log decision without a database migration, and makes it straightforward to verify that it cannot alter advisory output. Rebuilding from raw database rows would add a query and make replay behavior less aligned with the persisted feature state; that becomes useful only if this signal needs independent historical analysis.

## Traps: why the obvious fix is wrong

- Do not attach the signal to `HypoxiaFlag`: that type is serialized into API responses and the signal is explicitly shadow-only.
- Do not use future event-time samples during replay; the calculation is cut off at its supplied `now`.
- A zero MAD cannot produce an ordinary finite z score for a different current value. The implementation returns `None` in that case rather than infinity.
- Historical snapshots without `last_shadow` remain valid and load with no latest shadow result.

## Components

- `Backend/koi/models/hypoxia.py::shadow_ph_relative` - robust signal
- `Backend/koi/models/hypoxia_state.py::HypoxiaState` - snapshot value
- `Backend/koi/worker/poller.py::_poll_user` - once-per-poll log
- `Backend/tests/models/test_hypoxia.py` - maths and isolation
- `Backend/tests/models/test_hypoxia_state.py` - serialization
- `Backend/tests/api/test_dashboard.py` - non-exposure
- `docs/models.md` - parameters and shadow-only policy

## Breaks or needs follow-up

None. No migration or redeployment owner action is required beyond deploying the worker code.

## Problem and constraints

The pH baseline must describe the pond itself and remain observational. It must not alter `level`, `flagged`, `explanation`, or `advice`, nor enter API, OpenAPI, fixture, or app output.

## Alternatives considered

- Mean and standard deviation: more sensitive to extreme readings that are precisely what this indicator is meant to detect; median and MAD resist them.
- A separate persisted series: unnecessary for one current diagnostic and conflicts with the approved snapshot-only storage choice.

## Trade-offs

The one-hour-binned bounded history limits the available sample count; sparse ponds can remain without a score. The signal is not calibrated against live pond outcomes.

## Key parameters

- 14 days: `Backend/koi/models/hypoxia.py::SHADOW_WINDOW_DAYS`, owner-approved trailing window; a longer window adapts more slowly.
- 48 samples: `Backend/koi/models/hypoxia.py::SHADOW_MIN_SAMPLES`, owner-approved minimum; raising it delays availability.
- 3 MADs: `Backend/koi/models/hypoxia.py::SHADOW_RANGE_MADS`, robust low-range boundary; raising it reduces low-outlier detections.
- 0.6744897501960817: `Backend/koi/models/hypoxia.py::MAD_TO_STD`, normal-consistent robust z scaling.

## Assumptions

The paired pH and temperature samples retained by `HypoxiaState` represent fresh values under the poller's existing freshness gate. If pH must be considered independently when temperature is stale, the history shape needs a separate issue.

## Edge cases

- Handled: insufficient history yields null values; future samples are excluded; zero MAD with no deviation returns zero.
- Not handled: zero MAD with a nonzero deviation has no finite z value, so `ph_z` is null.

## Not verified

No calibration against live pond data was performed.

## Changes outside the scope

None.

## How it was verified

See the final result block for commands actually run and their outcomes.

## To change this

- To require more baseline evidence, raise `SHADOW_MIN_SAMPLES`; cost: later or absent scores.
- To change the low-outlier boundary, adjust `SHADOW_RANGE_MADS`; cost: different sensitivity.

## Rollback

Remove the poller computation/log and helper; the optional `last_shadow` snapshot member can be ignored by old readers and removed after no new writer uses it.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-22 13:54 UTC for issue #126 attempt 2; compared HEAD..working tree (base c75a46d). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 3 file(s), 11 component(s), +37/-2 lines
- plus 3 test file(s), 1 doc file(s)

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/hypoxia.py`, `Backend/koi/models/hypoxia_state.py`, `Backend/koi/worker/poller.py`, `Backend/tests/models/test_hypoxia.py`, `Backend/tests/models/test_hypoxia_state.py`, `Backend/tests/api/test_dashboard.py`, `docs/models.md`, `docs/decisions/**`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Evidence**

7 checked by the tool, 0 tested in this run, 14 claimed only (of 21 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 3 checked, 0 tested, 4 claimed
- Key parameters: 4 checked, 0 tested, 0 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 1 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/hypoxia.py::shadow_ph_relative` - robust signal (named code exists and changed)
  - [checked] Key parameters: 14 days: `Backend/koi/models/hypoxia.py::SHADOW_WINDOW_DAYS`, owner-approved trailing w... (value matches the code: SHADOW_WINDOW_DAYS = 14)
  - [checked] Components: `Backend/koi/models/hypoxia_state.py::HypoxiaState` - snapshot value (named code exists and changed)
  - [checked] Key parameters: 48 samples: `Backend/koi/models/hypoxia.py::SHADOW_MIN_SAMPLES`, owner-approved minimum... (value matches the code: SHADOW_MIN_SAMPLES = 48)
  - [checked] Components: `Backend/koi/worker/poller.py::_poll_user` - once-per-poll log (named code exists and changed)
  - [checked] Key parameters: 3 MADs: `Backend/koi/models/hypoxia.py::SHADOW_RANGE_MADS`, robust low-range boundary;... (value matches the code: SHADOW_RANGE_MADS = 3)
  - [checked] Key parameters: 0.6744897501960817: `Backend/koi/models/hypoxia.py::MAD_TO_STD`, normal-consistent robu... (value matches the code: MAD_TO_STD = 0.67449)
  - [claimed] Components: `Backend/tests/models/test_hypoxia.py` - maths and isolation (names no code)
  - [claimed] Edge cases: Handled: insufficient history yields null values; future samples are excluded; zero MAD... (names no code)
  - [claimed] How it was verified: See the final result block for commands actually run and their outcomes. (no test named)
  - full per-claim list (21) in the JSON record

**Consistency**

0 error(s), 2 warning(s) between the rationale above and the change.
- WARN: 'How it works' has 5 code references (path::Name or `identifiers`); it is for human readers - describe the steps in plain language, put names in Components
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0126-pond-relative-ph-shadow.md` gives the commit.
<!-- verified-facts:end -->
