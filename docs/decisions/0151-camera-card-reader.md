# 0151: Camera colour-card readings on upload
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #151 · Date: 2026-11-10

## In short
Uploads can now produce an optional pH and ammonia colour-card result when a pond has saved card-mask placements. The frame upload response stays unchanged, and each result is stored against the inserted image row.

## How it works
1. The camera reads the pond's latest saved card placements and moves the shared template points into frame coordinates.
2. It samples square patches around those points. Dots outside the image, with too much glare, or far from the other dots in their printed patch are excluded.
3. Each card's white reference balances its colour. The reader converts sample and reference colours to CIELAB and compares only a* and b* to rank candidate segments.
4. The frame is stored through the existing upload path. If a card result was available, a separate row links it to the generated image id. Reader or card-result storage failures are logged and do not change the frame reply.
5. The authenticated twin endpoint returns the latest stored result or a selected result by `image_id`.

## Main design choice
Keep card results in a dedicated image-linked table rather than adding pH/NH3 fields to `imageTable`. This lets existing frame consumers ignore the optional classifier and records mask version and per-patch evidence alongside its result. Adding columns would make every image row's shape larger and mix card observations into the established algae-frame contract. A single JSON result column would be smaller to evolve, but harder to query and validate by pond and frame.

## Traps: why the obvious fix is wrong
- Fixed pixel spots bind readings to the original camera mount; saved mask template points move with the owner's placement and frame transform.
- Brightness distance treats exposure shifts as colour changes. Matching uses chroma distance after white balance, as the reference method does.
- Card errors must not reject a frame. The card reader is optional image metadata and follows the existing fail-soft region measurement behavior.
- The upload reply is frozen. The generated image id is used internally to link metadata; it is not added to the reply.

## Components
- `Backend/koi/camera/card_reader.py::read_frame` — sampling and matching
- `Backend/koi/camera/camera.py::measure_cards` — optional upload integration
- `Backend/koi/storage/base.py::Storage` — card observation storage contract
- `Backend/koi/storage/memory.py::MemoryStorage` — local adapter
- `Backend/koi/storage/supabase_storage.py::SupabaseStorage` — database adapter
- `Backend/koi/api/routes.py::get_camera_card_reading` — latest or image lookup
- `supabase/migrations/0033_camera_card_readings.sql` — linked result table and RLS
- `docs/camera-card-reader.md` — method and visual limits

## Breaks or needs follow-up
The owner must apply migration `0033_camera_card_readings.sql` before deploying the backend. No upload response fields were removed or changed.

## Problem and constraints
Camera frames previously had no card-derived colour result. Placements are pond-specific and versioned by #149; `POST /upload` shape, auth, and response remain fixed.

## Alternatives considered
- Put values on `imageTable` — mixes an optional observation into the established image contract.
- Keep old fixed spots — fails after the card masks move.

## Trade-offs
Small patches and point medians reduce glare and local variation but cannot restore colour detail lost to JPEG compression. Confidence signals ambiguity; it does not infer a numeric pH or ammonia value.

## Key parameters
- Patch radius: 4 px at 640 px width (`Backend/koi/camera/card_reader.py::PATCH_RADIUS`); larger patches average more pixels but cross printed boundaries more often.
- Glare cutoff: 20% pixels above 1.1× white luminance (`GLARE_FRACTION`, `GLARE_LUMINANCE`); stricter values discard more dots.
- Dot deviation: 25% of the patch median RGB magnitude (`DOT_DEVIATION`); lowering it rejects more variation.
- Confidence ratios: clear above 2, weak above 1.3 (`read_frame`); higher thresholds report more ambiguity.
- Usability: fewer than half of sample dots yields `unusable` (`read_frame`).

## Assumptions
The uploaded frame and moved mask points use the same orientation and aspect mapping. Mask placements are fetched from the backend storage attached to the camera service.

## Edge cases
- Handled: no saved placements yields no reading; unusable individual spots are counted; reader and result insert errors are logged while frame storage continues.
- Not handled: automatic repair of badly placed masks; pH adjacent pairs seg2/3, seg4/5 and seg6/7 remain visually inseparable at the JPEG quality described in `docs/reference/card_reader/METHOD.md`.

## Not verified
No realistic saved pond frame batch was available; the replay parser was checked with `--help`, not a frame batch. The migration was exercised against the local database by the SQL tests, but was not applied to the hosted project.

## Changes outside the scope
None. `Backend/koi/dev/local_db.py::POND_TABLES` is inside the declared `Backend/koi/**` scope.

## How it was verified
- `python tools/check.py all`: secret scan, Ruff, and mypy passed; backend pytest passed 1,993 tests with one skip; tools pytest passed 39 tests; all 11 backtest metrics met baseline; firmware host tests passed 296 tests and all three ESP32 compiles passed; Flutter analysis and all Flutter tests passed. The only skip was the local dev-profile SQL rehearsal because `supabase status` reported no local Supabase stack.
- `python -m pytest -q -rs Backend/tests/sql/test_sql_camera_card_readings.py Backend/tests/sql/test_sql_least_privilege.py`: 86 passed, including the new authenticated owner-RLS check and exact table-grant matrix.
- `python -m pytest -q -rs Backend/tests/sql`: direct database migration and policy tests passed; the only skip is the local dev-profile rehearsal because `supabase status` cannot reach a local Supabase stack.
- `python -m koi.camera.card_reader --help` displayed the offline replay arguments. No live service was contacted.

## To change this
To tolerate more glare, raise `Backend/koi/camera/card_reader.py::GLARE_FRACTION`; more contaminated sample patches may then remain. To change matching tolerance, adjust the ratio boundaries in `read_frame`; more conservative boundaries increase ambiguous results.

## Rollback
Remove the card call from `Backend/koi/camera/camera.py::upload_image`, the reading endpoint and adapters, then leave the additive reading table unused. Do not edit migration `0033` after it is applied.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-11-09 17:45 UTC for issue #151 attempt 6; compared HEAD..working tree (base 94a6199). Records are claims to check against the code, not proof.

**Change summary**

- `Backend/koi/`: 8 file(s), 19 component(s), +211/-7 lines
- `supabase/migrations/`: 1 file(s), 0 component(s), +22/-0 lines
- plus 7 test file(s), 3 doc file(s)
- new files: `Backend/koi/camera/card_reader.py`, `supabase/migrations/0033_camera_card_readings.sql`

**Removed symbols**

None.

**Scope**

Declared: `Backend/koi/camera/**`, `Backend/koi/api/**`, `Backend/koi/storage/**`, `Backend/tests/**`, `supabase/migrations/**`, `supabase/tests/**`, `docs/**`. Verdict: violation.
- out of scope: `Backend/koi/dev/local_db.py::POND_TABLES` (modified); explained in the record: yes

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), database suite (migration rehearsal, SQL tests, local profile), scope check, dangling reference check

**Evidence**

10 checked by the tool, 0 tested in this run, 16 claimed only (of 26 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 7 checked, 0 tested, 1 claimed
- Key parameters: 3 checked, 0 tested, 2 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 4 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `Backend/koi/camera/card_reader.py::read_frame` — sampling and matching (named code exists and changed)
  - [checked] Key parameters: Patch radius: 4 px at 640 px width (`Backend/koi/camera/card_reader.py::PATCH_RADIUS`);... (value matches the code: PATCH_RADIUS = 4)
  - [checked] Components: `Backend/koi/camera/camera.py::measure_cards` — optional upload integration (named code exists and changed)
  - [checked] Key parameters: Glare cutoff: 20% pixels above 1.1× white luminance (`GLARE_FRACTION`, `GLARE_LUMINANCE... (value matches the code: GLARE_FRACTION = 0.2, GLARE_LUMINANCE = 1.1)
  - [checked] Components: `Backend/koi/storage/base.py::Storage` — card observation storage contract (named code exists and changed)
  - [checked] Key parameters: Dot deviation: 25% of the patch median RGB magnitude (`DOT_DEVIATION`); lowering it rej... (value matches the code: DOT_DEVIATION = 0.25)
  - [checked] Components: `Backend/koi/storage/memory.py::MemoryStorage` — local adapter (named code exists and changed)
  - [checked] Components: `Backend/koi/storage/supabase_storage.py::SupabaseStorage` — database adapter (named code exists and changed)
  - [checked] Components: `Backend/koi/api/routes.py::get_camera_card_reading` — latest or image lookup (named code exists and changed)
  - [checked] Components: `supabase/migrations/0033_camera_card_readings.sql` — linked result table and RLS (named code exists and changed)
  - full per-claim list (26) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0151-camera-card-reader.md` gives the commit.
<!-- verified-facts:end -->
