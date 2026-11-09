# 0149: Shared, versioned colour-card masks
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #149 · Date: 2026-11-09

## In short
The twin now stores each pond's rigid white, ammonia and pH card placements as immutable versions and returns their transformed template patches. The existing camera-mask response uses saved patches when present and retains its configured-file fallback.

## How it works
1. The backend reads the shared patch geometry from its camera template. Each named patch is assigned to the white, ammonia or pH mask by its name.
2. An owner saves all mask placements together. The API checks their shape and numeric values, and checks that a selected reference frame belongs to that pond.
3. Storage appends a new version. The twin returns that latest placement set, the optional clear-water frame id, and every template polygon and sample point moved onto the frame.
4. The existing camera-mask endpoint uses those moved patches for the pond. Before a pond saves placements, it continues to read its configured patch file.

## Main design choice
Store one placement set per version rather than storing expanded patch coordinates. The shared template remains the geometric source, and each immutable version records the owner's compact centre, scale and angle choices. Storing expanded patches would freeze the full output but duplicate geometry in every row. That alternative becomes preferable if template edits must never change the interpretation of old versions; this change does not version the template itself.

## Traps: why the obvious fix is wrong
- Rotating frame fractions directly distorts a mask. `Backend/koi/camera/card_masks.py::CardPlacement.map` first scales x by the frame aspect, rotates, then converts x back, matching the app's pixel-proportional transform.
- A `reference_image_id` must be checked against the path pond. The FK establishes that the image exists; the API's pond-filtered image lookup establishes ownership.
- The template's mask centre comes from all polygons in that mask. Using an individual patch centre would move patches relative to their siblings.
- Old file-based card patches remain a fallback for ponds without saved versions.

## Components
- `Backend/koi/camera/card_template.json` — shared patch geometry
- `Backend/koi/camera/card_masks.py` — validation and transform
- `Backend/koi/api/routes.py`, `schemas.py`, `responses.py` — API and contract
- `Backend/koi/storage/base.py`, `memory.py`, `supabase_storage.py` — storage contract and adapters
- `supabase/migrations/0032_camera_card_masks.sql` — version table, validation, RLS and RPC
- `Backend/tests/camera/test_card_patches.py`, `tests/storage/test_camera_card_masks_storage.py` — transform, API and adapter tests
- `Backend/tests/sql/test_sql_camera_card_masks.py`, `test_sql_least_privilege.py` — local database and grant matrix

## Breaks or needs follow-up
The owner must apply migration `0032_camera_card_masks.sql` before deploying the twin changes. Existing app-local placement preferences are not migrated by this backend-only issue; a future client change must upload them.

## Problem and constraints
The future card reader and clear-water correction need stable shared sample locations and an owner-selected reference frame. `POST /upload` remains unchanged. Pre-existing saved water masks and old image rows are unaffected.

## Alternatives considered
- Keep the template in the mobile app — the server could not transform patches without another copy or client upload.
- Store mutable current placements — no historical version would identify the geometry used by a reader.

## Trade-offs
The backend template is a copied asset; app-side editing still uses its existing asset until a later mobile issue wires it to the API. Template revisions require care because stored placements refer to template geometry by implication.

## Key parameters
- Frame aspect: `4/3` (`Backend/koi/camera/card_masks.py::ASPECT`), matching the app template and transform.
- Allowed scale: `0.2..3.0` (`Backend/koi/camera/card_masks.py::MIN_SCALE`, `MAX_SCALE`), matching `CardPlacement` in the app and the database check.

## Assumptions
The template has patches for all three mask prefixes, as checked in the fixture-derived test.

## Edge cases
- Handled: no saved version returns `{version: null}`; omitted clear-water id is null; unknown or malformed placements return a validation error; a reference image from another pond is rejected.
- Not handled: moving existing phone-only settings into storage; those settings remain local until the mobile client is changed.

## Not verified
Migration application against the hosted project was not performed. This session's focused local SQL tests and full check results are listed under How it was verified. The owner must apply migration `0032_camera_card_masks.sql` before deploying the twin.

## Changes outside the scope
`Backend/pyproject.toml` includes the JSON template as `koi.camera` package data so installed distributions contain the runtime template. `Backend/koi/dev/local_db.py::POND_TABLES` includes the new table in pond cleanup, required to remove seeded pond rows without foreign-key failures. These two edits are outside the issue's declared scope but required for package and local development behavior.

## How it was verified
- Focused run from `Backend/`: `python -m pytest -q tests/camera/test_card_patches.py tests/storage/test_camera_card_masks_storage.py tests/sql/test_sql_camera_card_masks.py tests/sql/test_sql_least_privilege.py` — 106 passed, including the migration constraint, immutability and RLS checks. One existing Pydantic deprecation warning was emitted.
- `python tools/check.py all` — backend Ruff and mypy passed; backend pytest 1,983 passed and 1 skipped; tools 39 passed; backtest 11/11 metrics; firmware host tests 296 passed; all three ESP32 compiles passed; Flutter analyze passed; Flutter tests passed. The single backend skip is the local Supabase dev-profile test, which requires the Supabase CLI stack. No migration rehearsal ran in this command.

## To change this
To change mask sizing limits, update `Backend/koi/camera/card_masks.py::MIN_SCALE` and `MAX_SCALE` and the matching migration check together; the cost is a new migration for an already applied constraint.

## Rollback
The twin card-mask routes and mask-response override depend on the new table. Revert the backend changes and leave the nullable-use feature unused before considering a later cleanup migration; do not edit migration `0032` after it is applied.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-11-09 15:44 UTC for issue #149 attempt 3; compared HEAD..working tree (base 8d8265c). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/`: 1 file(s), 0 component(s), +1/-0 lines
- `Backend/koi/`: 9 file(s), 21 component(s), +214/-2 lines
- `supabase/migrations/`: 1 file(s), 0 component(s), +84/-0 lines
- plus 4 test file(s), 2 doc file(s)
- new files: `Backend/koi/camera/card_masks.py`, `Backend/koi/camera/card_template.json`, `supabase/migrations/0032_camera_card_masks.sql`

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/camera/**`, `Backend/koi/api/**`, `Backend/koi/storage/**`, `Backend/tests/**`, `supabase/migrations/**`, `supabase/tests/**`, `docs/**`. Verdict: violation.
- out of scope: `Backend/koi/dev/local_db.py::POND_TABLES` (modified); explained in the record: yes
- out of scope: `Backend/pyproject.toml` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Evidence**

5 checked by the tool, 0 tested in this run, 15 claimed only (of 20 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 5 checked, 0 tested, 2 claimed
- Key parameters: 0 checked, 0 tested, 2 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 2 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/camera/card_template.json` — shared patch geometry (named code exists and changed)
  - [checked] Components: `Backend/koi/camera/card_masks.py` — validation and transform (named code exists and changed)
  - [checked] Components: `Backend/koi/api/routes.py`, `schemas.py`, `responses.py` — API and contract (named code exists and changed)
  - [checked] Components: `Backend/koi/storage/base.py`, `memory.py`, `supabase_storage.py` — storage contract an... (named code exists and changed)
  - [checked] Components: `supabase/migrations/0032_camera_card_masks.sql` — version table, validation, RLS and RPC (named code exists and changed)
  - [claimed] Components: `Backend/tests/camera/test_card_patches.py`, `tests/storage/test_camera_card_masks_stor... (names no code)
  - [claimed] Key parameters: Frame aspect: `4/3` (`Backend/koi/camera/card_masks.py::ASPECT`), matching the app temp... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: no saved version returns `{version: null}`; omitted clear-water id is null; un... (names no code)
  - [claimed] How it was verified: Focused run from `Backend/`: `python -m pytest -q tests/camera/test_card_patches.py tes... (no test named)
  - [claimed] Components: `Backend/tests/sql/test_sql_camera_card_masks.py`, `test_sql_least_privilege.py` — loca... (names no code)
  - full per-claim list (20) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0149-card-masks.md` gives the commit.
<!-- verified-facts:end -->
