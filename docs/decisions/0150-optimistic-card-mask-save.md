# 0150: Optimistic card-mask save and clear-water reference
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #150 · Date: 2026-11-10

## In short
The mask editor now loads the twin's latest placements before using the phone's saved copy. Saving updates the phone immediately and reports whether the twin accepted the same placements; a failed write can be retried. The editor can attach the frame currently shown as a clear-water reference.

## How it works
1. The editor reads the phone profile and requests the twin's current card-mask record. A valid placement set from the twin takes precedence; if the request fails or the record has no placements, the phone value is used.
2. The frame shown in the editor is retained with its image id and capture date. If the twin names an older reference frame, the app looks up that pond-owned frame to show its date.
3. Saving writes the current placement set to the local profile first, then sends the placement map and selected reference image id to the twin.
4. A successful twin write shows “Saved”. A failed write keeps the local copy and offers Retry, which resends the current placements and reference id.

## Main design choice
Keep the phone profile as an immediate fallback and use the twin response as the authoritative load result when it contains valid placements. This preserves offline editing and makes changes from another device visible on the next load. Treating a failed read as an empty response would hide a good phone copy; treating the phone as authoritative would make twin changes disappear.

## Traps: why the obvious fix is wrong
- Do not replace the local value with an empty twin record: #149 returns `version: null` before the pond has saved placements, so that record means use the fallback.
- A failed twin read must not fail `cardPlacementProvider`; otherwise the available local configuration disappears with a transient network error.
- The selected clear-water frame is sent on the next placement save. Selecting it alone does not persist the reference.
- Frame lookup for the stored reference date can fail independently; that must not prevent editing or loading the latest image.

## Components
- `MobileUI/mobile_app/lib/data/providers.dart::cameraCardMasksProvider` — twin record fetch
- `MobileUI/mobile_app/lib/data/providers.dart::cardPlacementProvider` — twin then phone precedence
- `MobileUI/mobile_app/lib/data/pond_data_source.dart::LivePondDataSource` — twin API calls
- `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart::PondCameraStorage.fetchFrame` — reference date lookup
- `MobileUI/mobile_app/lib/screens/card_placement_screen.dart::_save` — local-first save and retry state
- `MobileUI/mobile_app/test/card_placement_test.dart` — provider and editor coverage

## Breaks or needs follow-up
None. The camera `POST /upload` contract is unchanged.

## Problem and constraints
The existing editor only saved its placements on the phone. Twin placements and a per-pond clear-water frame are provided by #149; this issue connects the editor to those existing endpoints without changing the shared patch provider behavior.

## Alternatives considered
- Make the twin the only copy — loses usable placements during outages.
- Persist the selected frame immediately — creates a separate partial update when the API accepts frame selection alongside placements.

## Trade-offs
The app still needs a phone profile read before the placement provider completes. A slow twin read therefore delays the final placement result, though a failed read falls back to the phone value.

## Key parameters
None.

## Assumptions
The twin endpoint follows #149's `placements` and `reference_image_id` response fields. Frame ids are pond-scoped by the image lookup.

## Edge cases
- Handled: empty twin version, unavailable twin, malformed twin placements, unavailable reference-frame date.
- Not handled: concurrent saves from two devices; the latest accepted twin version wins on a later load.

## Not verified
The live twin and Supabase were not contacted. The mobile implementation is verified against fakes.

## Changes outside the scope
`docs/decisions/0150-optimistic-card-mask-save.md` — required issue decision record; the issue's code scope is limited to `MobileUI/mobile_app/lib/**` and `MobileUI/mobile_app/test/**`.

## How it was verified
- `MobileUI/mobile_app/test/card_placement_test.dart` — 16 tests passed, including twin precedence, failed-read fallback, local retention, retry, reference id transmission and date display.
- `python tools/check.py all` — backend 1,983 passed and one SQL skip; tools 39 passed; backtest 11 metrics passed; firmware host tests 296 passed and all three firmware builds passed; mobile analysis and the full mobile suite passed.
- `Backend/tests/sql` — 422 passed and one skip. `tests/sql/test_sql_dev_profile.py` skipped because `supabase status` could not find a local Supabase stack.
- `flutter analyze` — no issues.

## To change this
- To change precedence, edit `MobileUI/mobile_app/lib/data/providers.dart::cardPlacementProvider`; choosing the phone first would conceal later twin edits.
- To change retry behavior, edit `MobileUI/mobile_app/lib/screens/card_placement_screen.dart::_save`; retries currently save the current editor state.

## Rollback
Remove the twin-first provider and the editor's `saveCameraCardMasks` call, retaining the existing local profile read/write. No schema change is introduced by this issue.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-11-09 16:13 UTC for issue #150 attempt 4; compared HEAD..working tree (base 3947600). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 6 file(s), 0 component(s), +147/-12 lines
- plus 2 test file(s)

**Removed symbols**

None.

**Scope**

Declared: `MobileUI/mobile_app/lib/**`, `MobileUI/mobile_app/test/**`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

5 checked by the tool, 2 tested in this run, 11 claimed only (of 18 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 5 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 2 tested, 2 claimed
- Traps: 0 checked, 0 tested, 4 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `MobileUI/mobile_app/lib/data/providers.dart::cameraCardMasksProvider` — twin record fetch (named code exists and changed)
  - [tested] How it was verified: `MobileUI/mobile_app/test/card_placement_test.dart` — 16 tests passed, including twin p... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `MobileUI/mobile_app/lib/data/providers.dart::cardPlacementProvider` — twin then phone... (named code exists and changed)
  - [tested] How it was verified: `Backend/tests/sql` — 422 passed and one skip. `tests/sql/test_sql_dev_profile.py` skip... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `MobileUI/mobile_app/lib/data/pond_data_source.dart::LivePondDataSource` — twin API calls (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart::PondCameraStorage.fetchFrame`... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/screens/card_placement_screen.dart::_save` — local-first save... (named code exists and changed)
  - [claimed] Components: `MobileUI/mobile_app/test/card_placement_test.dart` — provider and editor coverage (names no code)
  - [claimed] Edge cases: Handled: empty twin version, unavailable twin, malformed twin placements, unavailable r... (names no code)
  - [claimed] How it was verified: `python tools/check.py all` — backend 1,983 passed and one SQL skip; tools 39 passed; b... (no test named)
  - full per-claim list (18) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0150-optimistic-card-mask-save.md` gives the commit.
<!-- verified-facts:end -->
