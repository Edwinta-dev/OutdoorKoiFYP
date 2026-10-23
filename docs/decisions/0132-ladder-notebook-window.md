# 0132: Ladder thresholds and outlook input match the notebook window
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #132 · Date: 2026-10-23

## In short

PREEMPT's new-regime temperature cut now matches the notebook's climate window through 2026-08-20. Its outlook input represents the calibrated mean of lead 1-4 daily midpoints, and the ladder reads its stored dry-rank and outlook defaults.

## How it works

1. The regeneration command reads the committed daily climate rows and limits new-regime rows to an inclusive end date. The default end date is the end of the notebook window; `--end-date` can select another endpoint explicitly.
2. The existing z-matched 70th-percentile formula is applied to the older regime and the selected new-regime window. The resulting cuts are rounded for the JSON parameter file.
3. PREEMPT compares observed daily maximum temperature with the regime cut, then compares the caller's outlook mean with the calibrated outlook cutoff. The outlook mean represents the midpoint of each forecast day, averaged over leads 1-4.
4. WINDOW uses the stored pooled dry-rank cutoff when the caller omits one. PREEMPT similarly uses the stored outlook cutoff; an explicit `None` still means the corresponding input is unavailable.

## Main design choice

The calibration window is explicit and defaults to the notebook's endpoint, rather than silently including later rows. A different endpoint remains useful for deliberate regeneration, so the script accepts an override. This is better aligned with the evidence that generated the threshold; a rolling cutoff would be better only if production calibration were intended to track data beyond the notebook window.

The new keyword is `outlook_mean_c`; `outlook_high_c` remains a deprecated keyword alias to ease caller migration. Supplying both raises `ValueError`, avoiding an implicit precedence rule that could conceal mismatched forecast measures. Removing the alias would be simpler but would break existing keyword callers immediately.

## Traps: why the obvious fix is wrong

- A daily high is not the statistic calibrated at 29.125 °C. Treating it as the outlook input makes the forecast gate nearly always true.
- Omitting a dry cutoff or outlook cutoff now means use the calibrated JSON value. Explicit `None` continues to mean the signal is unavailable.
- The end date applies to new-regime climate rows; the other calibrated label data retain their established training cutoff.

## Components

- `Backend/koi/models/ladder.py::preempt` — calibrated mean input and alias
- `Backend/koi/models/ladder.py::window` — dry-rank default
- `Backend/koi/models/ladder.py::actions` — defaults and forwarded outlook measure
- `Backend/tools/regenerate_ladder_params.py::main` — bounded regeneration window
- `Backend/koi/models/ladder_params.json` — regenerated threshold
- `Backend/tests/models/test_ladder.py` — regression coverage
- `Backend/koi/api/routes.py::_lead_time_actions` — renamed unavailable input

## Breaks or needs follow-up

None. Existing keyword callers can use the deprecated alias during migration.

## Problem and constraints

The checked-in cut had been computed using later rows than the notebook. The forecast cutoff was calibrated on mean daily midpoints, while the production keyword suggested a daily high. The pooled dry cutoff was stored but was not used as the function default.

## Alternatives considered

- Remove the old keyword immediately — simpler interface, but breaks existing callers.
- Regenerate using all available climate rows — retains the observed drift from the analysis window.

## Trade-offs

The default regeneration endpoint is fixed to the analysis window until intentionally overridden. New rows do not automatically alter the production cut.

## Key parameters

- New-regime window end: 2026-08-20 (`Backend/tools/regenerate_ladder_params.py::DEFAULT_WINDOW_END`), chosen from the notebook endpoint; a later date changes the new-regime distribution.
- New-regime cut: 31.80 °C (`Backend/koi/models/ladder_params.json::temperature_threshold_c.new`), the z-matched formula's 31.795 result rounded to two decimals.
- Outlook cut: 29.125 °C (`Backend/koi/models/ladder_params.json::outlook_hot_threshold_c`), used by default when omitted.
- Dry rank cut: 0.1 (`Backend/koi/models/ladder_params.json::pooled_dry_rank_max`), used by WINDOW and actions when omitted.

## Assumptions

The notebook endpoint and supplied formula output correspond to the committed climate CSV. The outlook caller will provide the mean of lead 1-4 daily midpoints.

## Edge cases

- Handled: both old and new outlook keywords together raise a clear error; missing observed or outlook values remain unassessed.
- Not handled: no runtime source currently produces the notebook-matched pooled outlook mean; PREEMPT remains unassessed until one is available.

## Not verified

No production backtest was rerun; its owner-supplied research branch was unavailable in this repository.

## Changes outside the scope

`Backend/tools/regenerate_ladder_params.py` is outside the literal `Backend/koi/tools/**` scope, but is the regeneration script explicitly required by the issue.

## How it was verified

See the issue result block for the focused ladder suite and repository check results.

## To change this

To follow a newer calibration window, pass `--end-date YYYY-MM-DD` to `Backend/tools/regenerate_ladder_params.py` and review the regenerated threshold against its validation data.

## Rollback

Restore the former JSON cut and revert the ladder keyword/default changes together. Callers using the new `outlook_mean_c` keyword would then need to return to the old interface.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-23 02:15 UTC for issue #132 attempt 1; compared HEAD..working tree (base 1c82e8c). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 3 file(s), 4 component(s), +26/-14 lines
- `Backend/tools/`: 1 file(s), 3 component(s), +10/-4 lines
- plus 1 test file(s), 1 doc file(s)

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/models/ladder.py`, `Backend/koi/models/ladder_params.json`, `Backend/koi/tools/**`, `Backend/tests/models/**`, `Backend/koi/api/routes.py`, `Backend/koi/notifications.py`, `docs/models.md`, `docs/decisions/**`. Verdict: violation.
- out of scope: `Backend/tools/regenerate_ladder_params.py::main` (modified); explained in the record: yes
- out of scope: `Backend/tools/regenerate_ladder_params.py::DEFAULT_WINDOW_END` (added); explained in the record: yes
- out of scope: `Backend/tools/regenerate_ladder_params.py::<module>` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Evidence**

6 checked by the tool, 0 tested in this run, 13 claimed only (of 19 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 6 checked, 0 tested, 1 claimed
- Key parameters: 0 checked, 0 tested, 4 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 1 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/ladder.py::preempt` — calibrated mean input and alias (named code exists and changed)
  - [checked] Components: `Backend/koi/models/ladder.py::window` — dry-rank default (named code exists and changed)
  - [checked] Components: `Backend/koi/models/ladder.py::actions` — defaults and forwarded outlook measure (named code exists and changed)
  - [checked] Components: `Backend/tools/regenerate_ladder_params.py::main` — bounded regeneration window (named code exists and changed)
  - [checked] Components: `Backend/koi/models/ladder_params.json` — regenerated threshold (named code exists and changed)
  - [checked] Components: `Backend/koi/api/routes.py::_lead_time_actions` — renamed unavailable input (named code exists and changed)
  - [claimed] Components: `Backend/tests/models/test_ladder.py` — regression coverage (names no code)
  - [claimed] Key parameters: New-regime window end: 2026-08-20 (`Backend/tools/regenerate_ladder_params.py::DEFAULT_... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: both old and new outlook keywords together raise a clear error; missing observ... (names no code)
  - [claimed] How it was verified: See the issue result block for the focused ladder suite and repository check results. (no test named)
  - full per-claim list (19) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0132-ladder-notebook-window.md` gives the commit.
<!-- verified-facts:end -->
