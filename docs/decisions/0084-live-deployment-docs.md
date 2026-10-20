# 0084: Bring deployment docs in line with what is live
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #84 · Date: 2026-10-19

## In short

The integration guide now describes the live arrangement: PythonAnywhere hosts the camera, while the API and worker run on the owner's laptop. It inventories the environment names in `Backend/.env.example` and records the supplied owner evidence for migrations 0006-0011.

## How it works

1. The Supabase guide marks migrations 0006-0011 as applied on 2026-10-03 and records the backup, rehearsal, history check and post-apply checks reported by the owner.
2. The integration guide lists the settings used by the backend, including camera state and obstruction handling, colour quality, algae analysis, NEA, weather history, local development, TDS prompts and Sentry.
3. The deployment section describes PythonAnywhere's camera-only free tier, sparse clone, virtual environment, WSGI entry point, setup, update, rollback and renewal.
4. The API and worker instructions describe laptop startup and note that polling pauses when that machine is off. The root README links to this deployment section.

## Main design choice

Keep deployment details in `Backend/README_INTEGRATION.md`, the existing operational reference, and have the root README point there. Duplicating deployment commands at the root would make two sources drift; a separate deployment document would split closely related integration information. A dedicated deployment page would make sense if deployments become independently maintained or substantially more complex.

## Traps: why the obvious fix is wrong

- The issue calls out 14 omitted settings, while acceptance requires every `.env.example` name. The table was checked against all 67 assignment names; the local comparison found no missing names or duplicate table rows.
- PythonAnywhere currently hosts only the camera. The free tier has no always-on task for the API or worker, so describing them as hosted there would misstate the deployment.
- The reported migration evidence is owner-provided. The repository was unlinked after application; this task did not contact the live project.

## Components

- `supabase/README.md` - migration status and evidence
- `Backend/README_INTEGRATION.md` - settings and deployment instructions
- `README.md` - deployment link

## Breaks or needs follow-up

None.

## Problem and constraints

The deployment wording was stale after the reported 2026-10-03 live migration and camera deployment. Scope was the three requested documentation files and this decision record; no live service was contacted.

## Alternatives considered

- Add only the 14 named settings - incomplete because acceptance checks every environment name.
- Move deployment instructions to a new page - unnecessary for this update and adds another document to maintain.

## Trade-offs

The laptop deployment has no polling while the laptop is off. This accurately describes the current arrangement but remains operationally dependent on that machine.

## Key parameters

None; values describe existing configuration and the deployment facts supplied in the issue.

## Assumptions

The supplied issue context accurately describes current hosting and migration evidence. No live service was independently checked.

## Edge cases

- Handled: optional environment values and defaults are present in the settings inventory.
- Not handled: future hosting changes require updating the operational documentation.

## Not verified

Live PythonAnywhere settings and Supabase migration history were not independently accessed. The local Supabase stack was unavailable, so SQL dev-profile behavior was not exercised.

## Changes outside the scope

None.

## How it was verified

- `python tools/check.py all`: exited 0. Backend pytest: 1,970 passed, 1 skipped, 529,405 assertions; tools pytest: 39 passed; firmware host tests: 296 passed; all three firmware compiles passed; mobile Flutter tests: 266 passed and analysis passed. Backend Ruff, mypy, secret scan (678 files), and backtest (11/11 metrics) passed.
- The sole backend skip was `Backend/tests/sql/test_sql_dev_profile.py`; `supabase status` failed because the local Supabase stack was unavailable.
- `python -m pytest -q -rs Backend/tests/sql/test_sql_dev_profile.py`: 1 skipped for the unavailable local stack.
- Settings inventory comparison: all 67 assignment names in `Backend/.env.example` appear once in the table.
- `git diff --check`: passed.

## To change this

To change the deployment topology, update `Backend/README_INTEGRATION.md` and the root README link. To change the settings inventory, compare it again against `Backend/.env.example`.

## Rollback

Revert this decision record and the three documentation edits together; no runtime behavior depends on them.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-18 17:47 UTC for issue #84 attempt 22; compared HEAD..working tree (base b26c64a). Records are claims to check against the code, not proof.

**Change summary**

No code components changed.
- plus 3 doc file(s)

**Removed symbols**

None.

**Scope**

No Scope line in the issue.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Evidence**

0 checked by the tool, 1 tested in this run, 15 claimed only (of 16 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 0 checked, 0 tested, 3 claimed
- Key parameters: 0 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 1 tested, 4 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [tested] How it was verified: The sole backend skip was `Backend/tests/sql/test_sql_dev_profile.py`; `supabase status... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [claimed] Components: `supabase/README.md` - migration status and evidence (names no code)
  - [claimed] Key parameters: None; values describe existing configuration and the deployment facts supplied in the i... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: optional environment values and defaults are present in the settings inventory. (names no code)
  - [claimed] How it was verified: `python tools/check.py all`: exited 0. Backend pytest: 1,970 passed, 1 skipped, 529,405... (no test named)
  - [claimed] Components: `Backend/README_INTEGRATION.md` - settings and deployment instructions (names no code)
  - [claimed] How it was verified: `python -m pytest -q -rs Backend/tests/sql/test_sql_dev_profile.py`: 1 skipped for the... (no test named)
  - [claimed] Components: `README.md` - deployment link (names no code)
  - [claimed] How it was verified: Settings inventory comparison: all 67 assignment names in `Backend/.env.example` appear... (no test named)
  - [claimed] How it was verified: `git diff --check`: passed. (no test named)
  - full per-claim list (16) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0084-live-deployment-docs.md` gives the commit.
<!-- verified-facts:end -->
