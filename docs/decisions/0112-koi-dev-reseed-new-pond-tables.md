# 0112: Keep local reseeds compatible with newer pond tables
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #112 · Date: 2026-10-19

## In short

The local Supabase reseed cleanup now removes rows from `kit_readings`, `pond_calibration`, and `notification_outbox` before deleting their `UserData` pond. A regression test checks that every migration-defined table with a foreign key to `UserData` appears in the cleanup list.

## How it works

1. The local seed prepares its normal demo rows and identifies the pond IDs being replaced.
2. Before inserting anything, cleanup walks pond tables from children toward the pond row and deletes rows belonging to those IDs.
3. The cleanup list now includes the three newer tables so worker-created notifications and other pond data cannot retain a reference to a pond row that is about to be replaced.
4. A test reads the schema migrations, finds tables that declare a direct foreign key to `UserData` inline or with `ALTER TABLE`, and checks that each table is included in cleanup before `UserData`. It also asserts the three reported tables are among those foreign keys.

## Main design choice

Extend the existing explicit `(table, pond column)` cleanup list and guard it with a migration scan. Cascading deletes would alter database behavior and could erase related history on any `UserData` removal. A database-catalog test would only run with the local Supabase stack; scanning migrations lets the coverage check run with ordinary backend tests.

## Traps: why the obvious fix is wrong

- Deleting only the notification row that exposed the crash leaves other newer pond-owned rows able to block replacement.
- Adding a table name without its actual pond column makes cleanup silently ineffective or raises an SQL error. The declarations come from the respective migrations: `kit_readings.pond`, `pond_calibration.pond_id`, and `notification_outbox.pond`.
- A foreign key to `UserData` does not imply the table belongs in the generic in-memory storage adapter. The change is local database cleanup only.

## Components

- `Backend/koi/dev/local_db.py::POND_TABLES` — pond cleanup order
- `Backend/tests/dev/test_dev_stack.py::test_every_userdata_foreign_key_is_deleted_before_userdata` — migration coverage

## Breaks or needs follow-up

None. Existing local rows in the added tables are removed for the seeded pond on reseed, consistent with other pond-owned seed cleanup.

## Problem and constraints

`POND_TABLES` did not include tables added by later migrations. When one had rows for a seeded pond, deleting `UserData` failed due to its foreign key. Scope is local dev seed cleanup and its regression coverage; no schema or service behavior changes.

## Alternatives considered

- Add `ON DELETE CASCADE` constraints — not chosen because it changes deployed data deletion behavior and can discard pond history.
- Keep only the explicit three additions without a coverage check — smaller, but future `UserData` foreign keys could recreate the same failure.

## Trade-offs

The migration scan is a lightweight schema-shape check. It does not prove runtime deletion succeeds against every migration state; the local database profile does that when its stack is available.

## Key parameters

None.

## Assumptions

The migration files represent the local Supabase schema. The test recognizes direct `CREATE TABLE` and `ALTER TABLE` declarations; more complex generated SQL may need additional parsing support.

## Edge cases

- Handled: rows in each listed table for any reseeded pond are deleted before `UserData`.
- Not handled: dynamic SQL that constructs a foreign key declaration is not interpreted by the migration scan.

## Not verified

The reseed against a running local Supabase database was unavailable in this sandbox. The full backend suite had one skip because the local Supabase stack was unavailable.

## Changes outside the scope

None.

## How it was verified

- `python -m pytest -q tests/dev/test_dev_stack.py` from `Backend/` — 19 passed; covers migration FK table coverage and existing dev stack behavior.
- `python -m pytest -q -rs tests/sql/test_sql_dev_profile.py` from `Backend/` — 1 skipped because `supabase status` could not find a running local stack.
- `python tools/check.py all` — all runner checks passed; backend pytest reported 1980 passed and 1 skip (the unavailable local Supabase stack). Ruff, mypy, tools, backtest, firmware host tests and all three firmware compiles passed; Flutter dependency resolution, analysis and tests passed.

## To change this

To cover foreign keys created through dynamic SQL, teach the migration scan to interpret the SQL construction; cost: more parser complexity.

## Rollback

Reverting `POND_TABLES` additions restores the original reseed FK failure whenever these tables contain rows for a seeded pond. Keep the migration coverage test if cleanup is reworked.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-19 00:13 UTC for issue #112 attempt 48; compared HEAD..working tree (base e822414). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 1 file(s), 1 component(s), +3/-0 lines
- plus 1 test file(s)

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/dev/seed.py`, `Backend/koi/dev/local_db.py`, `Backend/koi/dev/stack.py`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), scope check, dangling reference check

**Evidence**

1 checked by the tool, 0 tested in this run, 12 claimed only (of 13 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 1 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 3 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/dev/local_db.py::POND_TABLES` — pond cleanup order (named code exists and changed)
  - [claimed] Components: `Backend/tests/dev/test_dev_stack.py::test_every_userdata_foreign_key_is_deleted_before... (names no code)
  - [claimed] Edge cases: Handled: rows in each listed table for any reseeded pond are deleted before `UserData`. (names no code)
  - [claimed] How it was verified: `python -m pytest -q tests/dev/test_dev_stack.py` from `Backend/` — 19 passed; covers m... (no test named)
  - [claimed] How it was verified: `python -m pytest -q -rs tests/sql/test_sql_dev_profile.py` from `Backend/` — 1 skipped... (no test named)
  - [claimed] How it was verified: `python tools/check.py all` — all runner checks passed; backend pytest reported 1980 pa... (no test named)
  - full per-claim list (13) in the JSON record

**Consistency**

0 error(s), 2 warning(s) between the rationale above and the change.
- WARN: 'How it works' has 3 code references (path::Name or `identifiers`); it is for human readers - describe the steps in plain language, put names in Components
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0112-koi-dev-reseed-new-pond-tables.md` gives the commit.
<!-- verified-facts:end -->
