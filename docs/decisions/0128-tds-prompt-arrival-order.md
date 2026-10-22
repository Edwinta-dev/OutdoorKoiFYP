# 0128: TDS prompts do not depend on arrival order
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #128 · Date: 2026-10-22

## In short

TDS prompt processing now keeps one winner for each reading instant, chosen by the largest TDS row ID. If a later poll supplies a higher-ID row for an instant already seen, prompt state is recalculated from the winning observations. The twin uses the same timestamp-then-row-ID ordering for its newest channel value.

## How it works

1. Each enabled prompt update combines the previously stored TDS observations with the new poll's inputs.
2. For an instant already represented, the observation with the larger TDS row ID wins. Observations are then processed in time order.
3. If the set of winners changed, the rolling baseline, pending steps, and recent readings are rebuilt from those winners. Existing prompts and owner rejections remain available across that rebuild.
4. Prompt observations are stored in the existing twin snapshot. For older snapshots, the persisted baseline, pending, and recent detector windows seed the observation history, so the first update can continue from their accumulated state.
5. The twin's displayed/current channel value advances for a later timestamp, or for the same timestamp when its row ID is higher.

## Main design choice

Retain the prompt's source observations and recompute derived state on a winner change. The main alternative was to change only the duplicate-time guard or current reading; that would leave baseline, step, slope, and prompt results derived from the wrong reading. Keeping sufficient input history allows one consistent recomputation. The cost is snapshot growth proportional to retained TDS observations. A bounded history would reduce storage, but cannot guarantee identical results after an arbitrarily late same-time winner changes an observation before the retained boundary.

## Traps: why the obvious fix is wrong

- Timestamp equality alone does not establish the winner; the ID is the tie-break used for the source rows.
- Rejecting all `time <= last_reading_at` inputs discards a later higher-ID correction.
- Replacing the winner without replaying the rolling detector leaves state derived from the superseded value.
- Old prompt records and rejection answers must survive recalculation; they are owner-facing state, not detector scratch data.

## Components

- `Backend/koi/models/tds_prompts.py::TdsPrompts.observe` - choose and replay observations
- `Backend/koi/models/tds_prompts.py::TdsPrompts.to_dict` - persist observation history
- `Backend/koi/models/pond_twin.py::PondTwin._record_channels` - timestamp and ID tie-break
- `Backend/tests/models/test_tds_prompts.py` - split delivery and late replacement

## Breaks or needs follow-up

None. No migration is required; the added snapshot field is optional.

## Problem and constraints

Issue #128 concerns rows sharing an event instant but delivered across separate polls. The prompt detector previously discarded equality against its most recent timestamp and had no source history from which to correct derived state. Work is limited to prompt state, twin channel selection, focused tests, and this record.

## Alternatives considered

- Skip equal-time inputs: preserves first-arrival behavior and does not resolve the defect.
- Replace only the current equal-time value: leaves rolling state and emitted prompts based on obsolete input.
- Rebuild from external database history: would couple this pure model to a new storage/replay interface and exceed the scoped modules.

## Trade-offs

All TDS observations are retained in prompt snapshot state so late equal-time corrections can be applied consistently. Snapshot size will grow with observation count; if that becomes material, a replayable bounded window plus a defined policy for corrections older than it will be needed.

## Key parameters

- Winner: greatest `row_ids["tds"]` at an equal timestamp (`Backend/koi/models/tds_prompts.py::TdsPrompts.observe`).

## Assumptions

Sensor row IDs are stable and increase with insertion order. If incoming data reuses IDs across different rows, the source cannot unambiguously identify the latest same-time reading.

## Edge cases

- Handled: same-instant rows split across polls, a late higher-ID winner, older snapshots without observation history, equal-time twin channel values.
- Not handled: retroactive corrections that would invalidate prompts already answered by an owner; existing prompt records are retained as user history.

## Not verified

Long-term snapshot growth under production sensor retention was not measured.

## Changes outside the scope

None.

## How it was verified

- `cd Backend && python -m pytest -q -k tds_prompts` - 45 passed, 1910 deselected, 135 assertions; covers prompt behavior including single/split delivery, shuffled chunks, late higher-ID replacement, and legacy snapshots. It does not measure snapshot growth.
- `python tools/check.py all` - Ruff and mypy passed; backend pytest 1954 passed with one skip, tools pytest 39 passed, backtest 11/11 metrics passed, firmware host tests 296 passed, all three firmware compiles passed, and Flutter analyze/tests passed. The SQL least-privilege test skipped because no local database answered; the final runner must repeat local SQL validation.

## To change this

To cap snapshot size, trim `TdsPrompts.observed` only after defining how older corrections are reported; the cost is losing exact recomputation for those instants.

## Rollback

Remove `observed` history and restore the prior timestamp-only prompt gate and channel tie behavior. Snapshots containing the extra key remain loadable because `from_dict` selects known dataclass fields.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-22 15:49 UTC for issue #128 attempt 3; compared HEAD..working tree (base b7edcd7). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 2 file(s), 5 component(s), +52/-5 lines
- plus 1 test file(s)

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/tds_prompts.py`, `Backend/koi/models/pond_twin.py`, `Backend/tests/models/test_tds_prompts.py`, `docs/decisions/**`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Evidence**

3 checked by the tool, 0 tested in this run, 12 claimed only (of 15 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 3 checked, 0 tested, 1 claimed
- Key parameters: 0 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 2 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/tds_prompts.py::TdsPrompts.observe` - choose and replay observations (named code exists and changed)
  - [checked] Components: `Backend/koi/models/tds_prompts.py::TdsPrompts.to_dict` - persist observation history (named code exists and changed)
  - [checked] Components: `Backend/koi/models/pond_twin.py::PondTwin._record_channels` - timestamp and ID tie-break (named code exists and changed)
  - [claimed] Components: `Backend/tests/models/test_tds_prompts.py` - split delivery and late replacement (names no code)
  - [claimed] Key parameters: Winner: greatest `row_ids["tds"]` at an equal timestamp (`Backend/koi/models/tds_prompt... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: same-instant rows split across polls, a late higher-ID winner, older snapshots... (names no code)
  - [claimed] How it was verified: `cd Backend && python -m pytest -q -k tds_prompts` - 45 passed, 1910 deselected, 135 as... (no test named)
  - [claimed] How it was verified: `python tools/check.py all` - Ruff and mypy passed; backend pytest 1954 passed with one... (no test named)
  - full per-claim list (15) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0128-tds-prompt-arrival-order.md` gives the commit.
<!-- verified-facts:end -->
