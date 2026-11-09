# 0153: App shows the camera card reading and Turbidity Index
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #153 · Date: 2026-11-10

## In short
The Algal & Solar detail screen now displays the latest camera card reading below its photo card. It shows pH and ammonia matches with confidence, pipeline agreement, usable-dot count, and a labelled Turbidity Index, with separate guidance when masks, a reference, or a usable frame are missing.

## How it works
1. The detail screen asks the DigitalTwin service for the pond's latest stored camera card reading and card-mask settings.
2. When no masks have been saved, the section points the keeper to Camera gallery. When masks exist but no clear-water reference is selected, it asks the keeper to choose one. An unusable latest result gets its own message.
3. A usable result shows each card's closest segment and confidence badge, whether the two pipelines agree, how many sample dots were usable, and the turbidity index with a plain-language label.
4. The information button keeps the limits out of the main reading: similar adjacent pH segments may be hard to distinguish, and comparing readings requires a clear-water reference.
5. The sampled-area overlay continues to assess visible clipped pixels. When a stored patch result says zero dots were usable, it colours that patch's displayed dots with the existing rejection colour.

## Main design choice
Fetch one typed reading model through the existing camera repository and Riverpod provider. This follows the same repository pattern as camera masks and makes refresh invalidation explicit. Passing an untyped map directly from the API into the widget would be shorter, but would spread response-key parsing through presentation code. The endpoint already returns all fields needed, so no backend or database change is needed.

## Traps: why the obvious fix is wrong
- The stored `patches` result contains counts per patch, not which individual dots were rejected. Only a patch with `dots_used == 0` can be wholly marked rejected from stored data; assigning partial rejections to arbitrary dots would show false locations.
- `frame_agreement` is the comparison result between ordinary and reference-corrected readings. The separate `agreement` object contains per-disc booleans; it is not the overall label.
- The Turbidity Index is a camera-derived optical estimate, not NTU. The card shows the API's index and uses product display thresholds rather than presenting a laboratory unit.

## Components
- `MobileUI/mobile_app/lib/data/camera_card_reading.dart::CameraCardReading` — typed API mapping and display thresholds
- `MobileUI/mobile_app/lib/data/pond_data_source.dart::fetchCameraCardReading` — endpoint read
- `MobileUI/mobile_app/lib/data/providers.dart::cameraCardReadingProvider` — Riverpod cache
- `MobileUI/mobile_app/lib/widgets/detail_graph/camera_card_reading_card.dart::CameraCardReadingCard` — reading and empty states
- `MobileUI/mobile_app/lib/widgets/detail_graph/sample_overlay.dart::SampleOverlayPainter` — rejected patch display
- `MobileUI/mobile_app/test/camera_card_reading_card_test.dart` — reading and empty-state widgets
- `MobileUI/mobile_app/test/sample_overlay_test.dart` — stored rejected-patch behavior
- `MobileUI/mobile_app/test/detail_graph_screen_test.dart` — refresh invalidation

## Breaks or needs follow-up
None.

## Problem and constraints
The backend already persists the reading and serves it at `/v1/ponds/{id}/camera/card-reading`. Keep the camera upload request and reply unchanged.

## Alternatives considered
- Put API map parsing in the widget — duplicates protocol details in presentation code.
- Infer individual rejected-dot locations from stored counts — impossible because the endpoint stores aggregate counts only.

## Trade-offs
Only wholly rejected patches are identified from saved results. Partial patch rejection detail is not available; clipped pixels still use the existing image probe.

## Key parameters
- Clear below `0.10` and slightly cloudy below `0.30` (`MobileUI/mobile_app/lib/data/camera_card_reading.dart::slightlyCloudyThreshold`, `::cloudyThreshold`); raising either threshold makes more index values use the lighter label, while lowering it makes labels cloudier sooner. These are display bands for the unitless API index.

## Assumptions
The latest card reading corresponds closely enough to the latest frame displayed in the existing photo card. If frame uploads race between the two reads, the section can briefly show a different frame's card result.

## Edge cases
- Handled: absent masks, absent clear-water reference, unusable reading, unavailable reading, null turbidity index, missing disc match, and older payloads without optional fields.
- Not handled: mapping a partial stored rejection count to particular sample dots; the server does not return their positions.

## Not verified
No live pond frame or hosted service was available to compare the reading section against real captures.

## Changes outside the scope
`docs/decisions/0153-app-card-reading.md` is outside the declared mobile scope because the project requires an issue decision record alongside implementation.

## How it was verified
- `flutter test test/camera_card_reading_card_test.dart test/sample_overlay_test.dart test/detail_graph_screen_test.dart`: 49 tests passed; covers widget states, overlay behavior, and refresh invalidation.
- `flutter analyze`: passed with no issues.
- `python tools/check.py all`: passed secret scan, Ruff, mypy, backend pytest (1,997 passed, one local-stack skip), tools pytest (39 passed), backtest (11 metrics), firmware host tests (296 passed), all three ESP32 compiles, Flutter analysis, and Flutter tests (337 passed).
- `python -m pytest -q -rs Backend/tests/sql/test_sql_dev_profile.py`: skipped because `supabase status` found no local Supabase stack; no migration SQL was run.

## To change this
To label more readings clear, raise `CameraCardReading::slightlyCloudyThreshold`; cost: higher index readings retain the clear label. To expose partial per-dot rejection, extend the backend response with individual sample outcomes before changing the overlay mapping.

## Rollback
Remove the detail-screen section and reading provider/model, and restore `SampleOverlay` to use only its pixel probe. No migration or service deployment depends on this client addition.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-11-09 19:08 UTC for issue #153 attempt 8; compared HEAD..working tree (base 20abd59). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 8 file(s), 0 component(s), +293/-3 lines
- plus 4 test file(s)
- new files: `MobileUI/mobile_app/lib/data/camera_card_reading.dart`, `MobileUI/mobile_app/lib/widgets/detail_graph/camera_card_reading_card.dart`

**Removed symbols**

None.

**Scope**

Declared: `MobileUI/mobile_app/lib/**`, `MobileUI/mobile_app/test/**`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

5 checked by the tool, 0 tested in this run, 15 claimed only (of 20 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 5 checked, 0 tested, 3 claimed
- Key parameters: 0 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 4 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `MobileUI/mobile_app/lib/data/camera_card_reading.dart::CameraCardReading` — typed API... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/data/pond_data_source.dart::fetchCameraCardReading` — endpoint... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/data/providers.dart::cameraCardReadingProvider` — Riverpod cache (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/widgets/detail_graph/camera_card_reading_card.dart::CameraCard... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/widgets/detail_graph/sample_overlay.dart::SampleOverlayPainter... (named code exists and changed)
  - [claimed] Components: `MobileUI/mobile_app/test/camera_card_reading_card_test.dart` — reading and empty-state... (names no code)
  - [claimed] Key parameters: Clear below `0.10` and slightly cloudy below `0.30` (`MobileUI/mobile_app/lib/data/came... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: absent masks, absent clear-water reference, unusable reading, unavailable read... (names no code)
  - [claimed] How it was verified: `flutter test test/camera_card_reading_card_test.dart test/sample_overlay_test.dart tes... (no test named)
  - [claimed] Components: `MobileUI/mobile_app/test/sample_overlay_test.dart` — stored rejected-patch behavior (names no code)
  - full per-claim list (20) in the JSON record

**Consistency**

0 error(s), 4 warning(s) between the rationale above and the change.
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won
- WARN: MobileUI/mobile_app/lib/data/repositories.dart (modified) changed but not mentioned in the record
- WARN: MobileUI/mobile_app/lib/screens/detail_graph_screen.dart (modified) changed but not mentioned in the record
- WARN: MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart (modified) changed but not mentioned in the record

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0153-app-card-reading.md` gives the commit.
<!-- verified-facts:end -->
