# 0124: Hypoxia night advisory
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #124 · Date: 2026-10-22

## In short

Hypoxia now means an advisory for the local overnight window. It never raises a flagged alert, and it returns unknown until inputs are fresh and the caller has at least 24 hours of history.

## How it works

1. The caller supplies the assessment time, current pH and water temperature, and the available history duration.
2. The assessment time is converted to the pond clock in Singapore. The advisory window starts at 22:00 inclusive and ends at 06:00 exclusive, crossing midnight (`Backend/koi/models/hypoxia.py::NIGHT_START_HOUR`, `NIGHT_END_HOUR`).
3. Before the required 24 hours of history, or when either required reading is missing or stale, the result says `unknown` and explains the missing condition (`Backend/koi/models/hypoxia.py::WARMUP_HOURS`).
4. With inputs available, the result is `watch` inside the overnight window and `none` outside it. `flagged` remains false, and the overnight explanation explicitly says dissolved oxygen is not measured and advises aeration.
5. Dashboard and assessment endpoints use the same freshness-filtered sensor readings. The poller uses its existing channel freshness results; it passes zero history until the snapshot history work supplies the duration.

## Main design choice

A time-window advisory replaces the prior temperature, darkness, aeration, and algae escalation logic. The owner-approved constants are `NIGHT_START_HOUR=22`, `NIGHT_END_HOUR=6`, `WARMUP_HOURS=24`, `MODEL_ID="hypoxia-night-advisory-v2"`, and `HORIZON="overnight"` (`Backend/koi/models/hypoxia.py`). The supplied external validation handover claims that the old 30 C rule warned on 0% of 374 hypoxia onsets across 17 Indian ponds, while low DO occurred on 76% of nights with median first low near 03:30. These are owner-supplied findings, not reproduced or independently verified here. A standing advisory wins because DO is not measured and a nightly raised alert was explicitly rejected by the owner. A measured DO model would be better if a DO sensor and validated readings became available.

## Traps: why the obvious fix is wrong

- Restoring the 30 C gate is ineffective against the owner-supplied handover findings and is no longer the intended policy.
- Setting `flagged=True` every night would create an alert each night; the owner explicitly chose a non-raised advisory.
- Using UTC boundaries shifts the intended local window; convert through `Asia/Singapore`.
- Reading `raw_sensor` bypasses per-channel staleness and can make `/assessment/all` disagree with the dashboard.
- DO input must not be added: the system does not measure it.
- Keep the retired response fields nullable/deprecated for old clients; callers validating `HypoxiaFlag` still need those keys.

## Components

- `Backend/koi/models/hypoxia.py::assess_hypoxia` - advisory logic
- `Backend/koi/worker/poller.py::_poll_user` - fresh channel values
- `Backend/koi/api/dashboard.py::build_dashboard` - dashboard assessment
- `Backend/koi/api/routes.py::_hypoxia_now` - combined assessment
- `Backend/koi/api/responses.py::HypoxiaFlag` - response contract
- `docs/api/openapi.yaml` - generated contract

## Breaks or needs follow-up

History snapshots are not implemented here, so current poller/API callers supply zero history and therefore report unknown. The next history issue must supply actual `history_hours`. Legacy temperature settings remain accepted but have no effect. No migration or redeploy owner action is required by this change; deployed application code needs the normal release process.

## Problem and constraints

Dissolved oxygen remains unmeasured, and no DO field or parameter may be accepted. Freshness rules are reused without inventing a new sensor age threshold. No Flutter source or database schema changes are in scope.

## Alternatives considered

- Retain the temperature threshold and tune it - the handover says the 30 C gate missed all observed onsets.
- Use raw latest sensor payloads - bypasses existing freshness status.
- Raise an alert every night - rejected by the owner on 2026-10-22.

## Trade-offs

The advisory does not estimate pond-specific oxygen risk and can recommend aeration on nights without low oxygen. Until history is supplied it remains unknown, including kit-only ponds without fresh pH.

## Key parameters

- `NIGHT_START_HOUR=22` and `NIGHT_END_HOUR=6`: owner-approved inclusive/exclusive local window; widening raises advisory coverage.
- `WARMUP_HOURS=24`: owner-approved minimum history; raising it delays known output.
- `MODEL_ID="hypoxia-night-advisory-v2"`; `HORIZON="overnight"`: response identifiers.
- `Backend/koi/models/hypoxia.py::ISSUE_TIME_PRECISION_SECONDS`: issue timestamp precision is one hour so identical assessments in that hour preserve body-based dashboard ETags.

## Assumptions

Sensor timestamps and freshness statuses are already normalized by existing dashboard and poller logic. Asia/Singapore remains the default pond clock. If callers do not supply trustworthy history, `unknown` is the correct output.

## Edge cases

- Handled: midnight crossing, exact start/end, naive timestamp rejection, non-UTC aware instants, stale or absent required channels, and unsupported DO keywords.
- Not handled: pH-relative risk or snapshot-derived history; the issue explicitly defers both.

## Not verified

Owner-supplied validation figures were not reproduced. No measured DO is available. The poller remains unknown until the history issue lands.

## Changes outside the scope

`Backend/tests/worker/test_sensor_ingest.py::test_freshness_comes_from_reading_time_not_poll_count` was updated because it asserted the previous `watch` level for a poll with zero history. The issue explicitly requires `history_hours=0` until snapshot history exists, so the correct expected output is `unknown`; leaving the assertion unchanged made the full backend suite fail.

## How it was verified

See the final issue result block for commands actually run and any sandbox skips.

## To change this

- To extend the night window, change `NIGHT_START_HOUR` or `NIGHT_END_HOUR`; cost: more hours receive an advisory.
- To allow known results sooner, lower `WARMUP_HOURS`; cost: less history supports the output.
- When snapshot history is available, pass its real duration from each caller; do not add a separate freshness rule.

## Rollback

Restore the prior model and callers together only if the previous threshold behavior is desired. Check `HypoxiaFlag` output and dashboard ETags after rollback; old keys and both endpoint integrations are coupled to the model result.



<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-22 12:19 UTC for issue #124; compared HEAD..working tree (base 52d1276). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 6 file(s), 30 component(s), +97/-175 lines
- plus 5 test file(s), 5 doc file(s)

**Removed symbols**

- referenced in docs only: `Backend/koi/models/hypoxia.py::DEFAULT_HIGH_TEMP_C`, `Backend/koi/models/hypoxia.py::DEFAULT_WATCH_TEMP_C`
- STILL REFERENCED in code: `Backend/koi/models/hypoxia.py::HypoxiaFlag.flagged`
- no remaining references: `Backend/koi/models/hypoxia.py::LEVELS`, `Backend/koi/models/hypoxia.py::algae_is_high`, `Backend/koi/settings.py::Settings._hypoxia_levels_ascending`

**Scope**

No Scope line in the issue.

**Supervisor checks passed**

not available

**Evidence**

5 checked by the tool, 0 tested in this run, 17 claimed only (of 22 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 5 checked, 0 tested, 1 claimed
- Key parameters: 0 checked, 0 tested, 4 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 1 claimed
- Traps: 0 checked, 0 tested, 6 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/models/hypoxia.py::assess_hypoxia` - advisory logic (named code exists and changed)
  - [checked] Components: `Backend/koi/worker/poller.py::_poll_user` - fresh channel values (named code exists and changed)
  - [checked] Components: `Backend/koi/api/dashboard.py::build_dashboard` - dashboard assessment (named code exists and changed)
  - [checked] Components: `Backend/koi/api/routes.py::_hypoxia_now` - combined assessment (named code exists and changed)
  - [checked] Components: `Backend/koi/api/responses.py::HypoxiaFlag` - response contract (named code exists and changed)
  - [claimed] Components: `docs/api/openapi.yaml` - generated contract (names no code)
  - [claimed] Key parameters: `NIGHT_START_HOUR=22` and `NIGHT_END_HOUR=6`: owner-approved inclusive/exclusive local... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: midnight crossing, exact start/end, naive timestamp rejection, non-UTC aware i... (names no code)
  - [claimed] How it was verified: See the final issue result block for commands actually run and any sandbox skips. (no test named)
  - [claimed] Key parameters: `WARMUP_HOURS=24`: owner-approved minimum history; raising it delays known output. (no cited constant with a matching value)
  - full per-claim list (22) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: 'How it works' has 7 code references (path::Name or `identifiers`); it is for human readers - describe the steps in plain language, put names in Components

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0124-hypoxia-night-advisory.md` gives the commit.
<!-- verified-facts:end -->
