# 0125: Bounded hypoxia feature history in the twin snapshot
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #125 · Date: 2026-10-22

## In short

The twin now stores a bounded series of fresh pH and water-temperature samples and the time the first paired sample was seen. Hypoxia warm-up age comes from that persisted event-time history in both the worker and read-only API responses.

## How it works

1. On each poll, the worker applies the existing per-channel freshness rules to the twin's newest sensor readings. It adds a hypoxia sample only when both pH and water temperature are fresh, using their reading timestamps rather than the poll clock.
2. Samples are grouped into one-hour bins. A later reading in a bin replaces the earlier one, and the history retains no more than 336 samples over 14 days (`Backend/koi/models/hypoxia_state.py::HISTORY_BIN_HOURS`, `HISTORY_SPAN_DAYS`, `HISTORY_SAMPLE_CAP`).
3. The twin stores this feature history in its versioned snapshot. A new or older snapshot without the history starts empty.
4. The worker passes the history age into the overnight advisory. Dashboard and combined-assessment requests read the saved snapshot and use the same age without advancing or saving the model. If the snapshot is unavailable, they use zero hours and the advisory remains unknown.

## Main design choice

History lives inside the existing per-pond snapshot, which keeps it atomic with the twin and avoids a table or schema change. Reconstructing it from raw sensor rows at API request time would require a second history query and could differ from the worker's replayed state. A separate table would support more extensive history queries, but the current warm-up and future baseline need fit the documented 14-day bound.

## Traps: why the obvious fix is wrong

- Using poll time makes replayed data look newly observed; sample time is the sensor reading's own `reading_at`.
- Using the raw latest value bypasses the existing channel freshness gate. Only `fresh_value` results enter history.
- Older snapshots cannot infer when fresh pH and temperature history began; assigning their save time would incorrectly make advisory output known immediately.
- Do not discard `first_seen_at` when old samples leave the bounded feature series; warm-up age describes pond history, not the retained pH baseline window.

## Components

- `Backend/koi/models/hypoxia_state.py::HypoxiaState` - bounded state and warm-up age
- `Backend/koi/models/pond_twin.py::PondTwin` - snapshot v7 ownership
- `Backend/koi/worker/poller.py::_poll_user` - fresh sample capture
- `Backend/koi/api/dashboard.py::build_dashboard` - supplied history age
- `Backend/koi/api/routes.py::_hypoxia_now` - snapshot-backed assessment
- `docs/models.md` - documented retention and bin size

## Breaks or needs follow-up

Existing stored snapshots load with empty history and therefore report unknown until 24 hours accrue (`Backend/koi/models/hypoxia.py::WARMUP_HOURS`). No migration is needed. Tests in `Backend/tests/models/test_pond_twin.py`, `Backend/tests/models/test_event_ledger.py`, and `Backend/tests/api/test_dashboard.py` were updated outside the listed test scope: snapshot-version pins needed adjustment, and API history parity needed endpoint coverage. `/assessment/all` now uses the same API clock helper as the dashboard so read-only assessments agree at the request instant.

## Problem and constraints

The night advisory needs warm-up age, but no persisted first-reading timestamp existed. The feature history must be bounded, deterministic under replay, freshness-gated, and compatible with old JSON snapshots.

## Alternatives considered

- Count from snapshot save time - rejected because downtime and old snapshots would falsely count as fresh measurement history.
- Add a history table - rejected because this state is small and belongs with the twin snapshot; it would require schema and storage changes.

## Trade-offs

The one-hour bins preserve only the latest pair in each hour, so they cannot support finer-grained analysis. The 14-day bound caps memory while leaving enough data for the planned pH baseline.

## Key parameters

- `HISTORY_BIN_HOURS=1`: latest sample per hour; a smaller bin increases stored sample count and detail.
- `HISTORY_SPAN_DAYS=14`: retained span; increasing it retains a longer baseline window and increases state size.
- `HISTORY_SAMPLE_CAP=336`: derived from span divided by bin size.
- `Backend/koi/models/hypoxia.py::WARMUP_HOURS=24`: existing required age; increasing it delays known advisories.

## Assumptions

The channel reading timestamps are timezone-aware and freshness status reflects usable readings. If either is false, the worker may omit or mis-age history.

## Edge cases

- Handled: empty and legacy snapshots, future timestamps yielding nonnegative age, stale channel values excluded by poller gating, latest-in-bin replacement, bounded retention.
- Not handled: pH-relative risk calculation, which remains out of scope.

## Not verified

No live Supabase deployment was used. Docker-backed SQL profile behavior was skipped by the repository runner.

## Changes outside the scope

The snapshot-version assertions in `Backend/tests/models/test_pond_twin.py` and `Backend/tests/models/test_event_ledger.py` were updated. `Backend/tests/api/test_dashboard.py` adds read-only API coverage for matching worker output with stored history and unknown output without a snapshot. `/assessment/all` now uses the established API clock helper; the endpoint test showed its former direct wall-clock read disagreed with the dashboard request time.

## How it was verified

- `python -m pytest -q -k "hypoxia or snapshot"` - 67 passed; verifies focused hypoxia and snapshot selections, not live services.
- `python -m pytest -q tests/tools/test_rebuild.py tests/dev/test_dev_replay.py` - 19 passed; exercises replay and rebuild paths.
- `python -m pytest -q tests/api/test_dashboard.py -k "hypoxia_api_reads_snapshot_history_like_worker or dashboard_read_has_no_side_effects"` - 2 passed; read endpoints agree with the worker result and return unknown without snapshot history.
- `python tools/check.py all` - backend 1,942 passed and 1 skipped; tools 39 passed; lint, type checks, backtest, firmware builds/tests, and Flutter analysis/tests passed. `tests/sql/test_sql_dev_profile.py` skipped because local Supabase/Docker was unavailable.

## To change this

- To retain more baseline data, increase `HISTORY_SPAN_DAYS`; cost: larger snapshots.
- To use finer sample bins, decrease `HISTORY_BIN_HOURS` and recompute `HISTORY_SAMPLE_CAP`; cost: more samples and snapshot bytes.

## Rollback

Remove the `hypoxia_state` snapshot member and v7 parsing only after reverting the worker and API consumers. Old readers already ignore unknown snapshot members; reverting the version constant alone would mislabel v7 snapshots.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-22 13:22 UTC for issue #125 attempt 1; compared HEAD..working tree (base 4668037). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 5 file(s), 8 component(s), +83/-7 lines
- plus 6 test file(s), 1 doc file(s)
- new files: `Backend/koi/models/hypoxia_state.py`

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/hypoxia_state.py`, `Backend/koi/models/pond_twin.py`, `Backend/koi/models/hypoxia.py`, `Backend/koi/worker/poller.py`, `Backend/koi/api/dashboard.py`, `Backend/koi/api/routes.py`, `Backend/tests/models/test_hypoxia.py`, `Backend/tests/models/test_hypoxia_state.py`, `Backend/tests/models/test_algae_gcc.py`, `Backend/tests/models/test_local_days.py`, `Backend/tests/models/test_sensor_inputs.py`, `Backend/tests/test_error_tracking.py`, `docs/models.md`, `docs/decisions/**`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Evidence**

5 checked by the tool, 1 tested in this run, 17 claimed only (of 23 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 5 checked, 0 tested, 1 claimed
- Key parameters: 0 checked, 0 tested, 4 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 1 tested, 3 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/hypoxia_state.py::HypoxiaState` - bounded state and warm-up age (named code exists and changed)
  - [tested] How it was verified: `python tools/check.py all` - backend 1,942 passed and 1 skipped; tools 39 passed; lint... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `Backend/koi/models/pond_twin.py::PondTwin` - snapshot v7 ownership (named code exists and changed)
  - [checked] Components: `Backend/koi/worker/poller.py::_poll_user` - fresh sample capture (named code exists and changed)
  - [checked] Components: `Backend/koi/api/dashboard.py::build_dashboard` - supplied history age (named code exists and changed)
  - [checked] Components: `Backend/koi/api/routes.py::_hypoxia_now` - snapshot-backed assessment (named code exists and changed)
  - [claimed] Components: `docs/models.md` - documented retention and bin size (names no code)
  - [claimed] Key parameters: `HISTORY_BIN_HOURS=1`: latest sample per hour; a smaller bin increases stored sample co... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: empty and legacy snapshots, future timestamps yielding nonnegative age, stale... (names no code)
  - [claimed] How it was verified: `python -m pytest -q -k "hypoxia or snapshot"` - 67 passed; verifies focused hypoxia an... (no test named)
  - full per-claim list (23) in the JSON record

**Consistency**

0 error(s), 2 warning(s) between the rationale above and the change.
- WARN: 'How it works' has 3 code references (path::Name or `identifiers`); it is for human readers - describe the steps in plain language, put names in Components
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0125-hypoxia-feature-history.md` gives the commit.
<!-- verified-facts:end -->
