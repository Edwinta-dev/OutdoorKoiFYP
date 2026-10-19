# 0119: App reads GCC algae values
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #119 · Date: 2026-10-19

## In short
The mobile algae forecast now selects GCC values when the backend identifies a GCC projection, while forecasts marked as legacy continue to use green ratio. Camera frames carry their nullable GCC value into the gallery and severity card, and missing algae trajectory values stay null instead of becoming zero.

## How it works
1. The forecast parser reads both current measurement fields, the optional GCC on each trajectory day, and the backend's projection and rate detail markers.
2. It treats `gcc_windowed_trend` or `windowed_gcc_trend` as GCC mode. In that mode the card compares current GCC with the response's thresholds and labels the number GCC. Otherwise it reads the legacy green ratio and gives it a green-ratio label.
3. Camera history reads `gcc` with `green_ratio` from each row. The gallery chart and rating card prefer GCC where present, and display green ratio for older frames without GCC.
4. A missing trajectory signal remains null in the parsed model. The camera chart gives missing samples a chart gap.

## Main design choice
Mode follows explicit backend provenance (`rate_source_detail` and `projection_method`) instead of inferring it from thresholds. Threshold numeric ranges can overlap across modes, and `rate_source` is intentionally pinned to three existing values by the backend contract. The marker approach would be less useful only if a backend response omitted both provenance fields while still returning GCC; it would then follow legacy behavior until the API supplies a mode marker.

## Traps: why the obvious fix is wrong
- Do not turn null `green_ratio` into zero. On GCC projections it is intentionally absent; zero would show a false plunge and misrepresent the missing series.
- Do not decide the mode from `rate_source`. Those values describe the growth-rate source and remain the existing three contract values.
- Do not apply a green-ratio fallback when a forecast explicitly says GCC mode. If current GCC is missing, the number is shown as unknown rather than silently displaying zero or green ratio.

## Components
- `MobileUI/mobile_app/lib/utils/digital_twin_api.dart::AlgaeDay`, `AlgaeForecast` - nullable fields and mode selection
- `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart::PondCameraFrame` - stored frame GCC
- `MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart::AlgaeStatusCard` - current value and unit label
- `MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart::AlgaeSeverityRatingCard` - frame metric label
- `MobileUI/mobile_app/lib/screens/camera_gallery_screen.dart::CameraGreenRatioChart` - GCC preference and null gap
- `MobileUI/mobile_app/test/algae_gcc_mode_test.dart` - parser cases

## Breaks or needs follow-up
None. Camera upload request and response contracts were not changed.

## Problem and constraints
The backend moved algae thresholds to GCC while older app code compared green ratio and converted absent trajectory measurements to zero. The camera frames have nullable GCC data since migration `0021_camera_colour`; pre-migration rows may not have GCC.

## Alternatives considered
- Infer mode from `thresholds.mode` - not selected because the backend marker describes threshold baseline semantics, not the signal family.
- Keep the gallery on green ratio - that would leave its chart and per-frame display inconsistent with the GCC forecast.

## Trade-offs
The status meter is empty if GCC mode is marked but `current_gcc` is absent; the numeric display says unknown. Existing green-ratio values remain parsed for compatibility.

## Key parameters
None. The backend continues to own threshold values.

## Assumptions
`rate_source_detail` and `projection_method` identify GCC forecasts. If both are absent, the app preserves legacy behavior.

## Edge cases
- Handled: null or absent GCC on old camera rows; null trajectory green ratio; legacy forecasts without GCC fields.
- Handled: inconsistent API payloads that declare GCC mode but omit current GCC; the value is shown as unknown and the normalized meter is empty.

## Not verified
No live Supabase query was made. The database column is present in repository migration `0021_camera_colour`; this change does not validate a deployed project's migration state.

## Changes outside the scope
`MobileUI/mobile_app/test/helpers/fixtures.dart`, `MobileUI/mobile_app/test/design_system_test.dart`, and `MobileUI/mobile_app/test/algae_gcc_mode_test.dart` provide regression coverage. `Backend/tests/api/test_contract.py` now treats `gcc` and `current_gcc` as optional because legacy responses omit those nullable keys; requiring them broke the existing backend-to-Dart key check.

## How it was verified
- `flutter test test/algae_gcc_mode_test.dart test/algae_severity_rating_card_test.dart test/camera_gallery_test.dart`: GCC and legacy parsing, nullable trajectory data, severity card behavior, and gallery behavior.
- `python tools/check.py all`: backend suite (1,981 passed, one skipped), tools suite (39 passed), firmware host tests (296 passed), all three firmware compiles, Flutter analysis and full Flutter test suite.
- `flutter test test/algae_gcc_mode_test.dart`: rerun after adding the GCC status-card widget assertion (4 tests passed).

## To change this
To prefer another signal marker, update `MobileUI/mobile_app/lib/utils/digital_twin_api.dart::AlgaeForecast.isGccMode`; the cost is coupling app mode detection to a different backend contract field.

## Rollback
Revert the mobile parsing and display changes together. Check the algae outlook and camera gallery against both GCC and legacy payloads after reverting.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-19 08:27 UTC for issue #119 attempt 60; compared HEAD..working tree (base 0abee8a). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 5 file(s), 0 component(s), +48/-19 lines
- plus 4 test file(s)

**Removed symbols**

None.

**Scope**

Declared: `MobileUI/mobile_app/lib/utils/digital_twin_api.dart`, `MobileUI/mobile_app/lib/data/pond_data_source.dart`, `MobileUI/mobile_app/lib/data/repositories.dart`, `MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart`, `MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart`, `MobileUI/mobile_app/lib/screens/camera_gallery_screen.dart`, `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

5 checked by the tool, 0 tested in this run, 12 claimed only (of 17 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 5 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 2 claimed
- How it was verified: 0 checked, 0 tested, 3 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `MobileUI/mobile_app/lib/utils/digital_twin_api.dart::AlgaeDay`, `AlgaeForecast` - null... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/utils/pond_camera_storage.dart::PondCameraFrame` - stored fram... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/widgets/detail_graph/algae_status_card.dart::AlgaeStatusCard`... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/widgets/detail_graph/algae_severity_rating_card.dart::AlgaeSev... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/screens/camera_gallery_screen.dart::CameraGreenRatioChart` - G... (named code exists and changed)
  - [claimed] Components: `MobileUI/mobile_app/test/algae_gcc_mode_test.dart` - parser cases (names no code)
  - [claimed] Edge cases: Handled: null or absent GCC on old camera rows; null trajectory green ratio; legacy for... (names no code)
  - [claimed] How it was verified: `flutter test test/algae_gcc_mode_test.dart test/algae_severity_rating_card_test.dart t... (no test named)
  - [claimed] Edge cases: Handled: inconsistent API payloads that declare GCC mode but omit current GCC; the valu... (names no code)
  - [claimed] How it was verified: `python tools/check.py all`: backend suite (1,981 passed, one skipped), tools suite (39... (no test named)
  - full per-claim list (17) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: 'How it works' has 4 code references (path::Name or `identifiers`); it is for human readers - describe the steps in plain language, put names in Components

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0119-app-gcc-algae-display.md` gives the commit.
<!-- verified-facts:end -->
