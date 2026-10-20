# 0109: Unified safe range
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #109 · Date: 2026-10-19

## In short
Stocked species pH and temperature tolerances now produce one shared interval by intersection. The species guide and dashboard use that provider result; disjoint ranges are shown as incompatible and trigger a dashboard warning.

## How it works
1. The app loads the care profiles for the species selected in the pond inventory.
2. It reads each profile's pH and temperature bounds and finds the largest minimum and smallest maximum for each measurement.
3. If a lower bound is greater than its upper bound, the species have no shared safe range. If no bounds could be read, the range is unavailable instead.
4. The dashboard uses valid intervals as the target thresholds in its detailed cards and simple temperature advisory. It warns when either interval is empty. The species guide presents the same intervals, including when only one species is stocked.

## Main design choice
Compute the interval once in the existing mobile data layer from the fetched profile list. The alternative was separate calculations in each screen, which could drift and produce conflicting ranges. A backend endpoint could centralize profile parsing, but it would add API work when the app already has the required profile data. An unavailable interval still uses the existing dashboard defaults so a temporary loading state or missing profile value does not become a false incompatibility warning.

## Traps: why the obvious fix is wrong
- Taking the lowest low and highest high creates a union. A species outside another species' tolerance would be presented as safe for both.
- Missing values are not an empty intersection. Only known bounds participate; no parsed bounds means unavailable.
- An empty interval must not be replaced with the fallback threshold. It represents incompatible stock and is warned about.

## Components
- `MobileUI/mobile_app/lib/data/providers.dart::UnifiedSafeRange` - intersection value
- `MobileUI/mobile_app/lib/data/providers.dart::unifiedSafeRangeProvider` - shared profile result
- `MobileUI/mobile_app/lib/screens/dashboard_view.dart::DashboardViewState.build` - thresholds and conflict warning
- `MobileUI/mobile_app/lib/screens/fish_tips_view.dart::FishTipsViewState.build` - shared interval display
- `MobileUI/mobile_app/test/unified_safe_range_test.dart` - interval states
- `MobileUI/mobile_app/test/dashboard_view_test.dart` - custom simple-view threshold

## Breaks or needs follow-up
None. The thresholds in the graphs are issue #110 scope; this change does not alter graph screens.

## Problem and constraints
The former species display used a widest numerical range, and dashboard targets were generic. The change is limited to the mobile provider and screens named by the issue; no API, storage, or migration change is needed.

## Alternatives considered
- Retain the union and relabel it - still includes unsafe values.
- Calculate independently in each consumer - duplicates logic and risks inconsistent results.
- Add a backend endpoint - unnecessary API change for data already loaded by the mobile app.

## Trade-offs
Profile fields are free-form strings. Bounds not containing two numeric values are omitted for that measurement; where other species have valid values, the computed intersection may therefore overstate compatibility.

## Key parameters
- Shared interval is `[max(minimums), min(maximums)]` (`MobileUI/mobile_app/lib/data/providers.dart::UnifiedSafeRange.fromProfiles`). Raising the computed minimum or lowering the maximum narrows the interval; minimum greater than maximum means incompatible stock.
- Existing fallback pH bounds 6.8–8.2 are `PhOutcomeCard` defaults; temperature bounds 24–28 °C are `MobileUI/mobile_app/lib/screens/dashboard_view.dart::comfortMinTempC` and `comfortMaxTempC`. They apply only when no valid shared interval is available, not when an interval is empty.

## Assumptions
Care profile pH and temperature text presents lower then upper bounds. A different ordering or representation requires changing the parser.

## Edge cases
- Handled: disjoint bounds remain empty; missing/unparseable bounds remain unavailable; one species' parsed bounds form its own interval.
- Not handled: malformed profiles mixed with valid profiles are omitted and could make the result appear more permissive than actual stock permits.

## Not verified
The live Supabase species catalogue and a physical device were not checked. Graph thresholds are not part of this issue and remain unchanged.

## Changes outside the scope
None.

## How it was verified
- `flutter test test/unified_safe_range_test.dart test/dashboard_view_test.dart`: 15 tests passed, covering intersection/empty/unavailable states, existing dashboard rendering, and a simple-view reading evaluated against custom bounds. It does not verify live profile text or the species widget rendering.
- `flutter analyze`: passed with no issues.
- `python tools/check.py all`: backend pytest 1,980 passed and one skipped; tools pytest 39 passed; firmware host tests 296 passed; three ESP32 compiles passed; mobile analysis passed and Flutter tests 270 passed. The one backend skip was `test_dev_stack_local_database_profile_passes_its_check`, skipped because no local Supabase database stack is available. This environment runs mobile tests on Windows, so Linux-only golden tests are not executed.
- Before/after counts available from the prior partial record: backend pytest 1,876 passed and 103 failed before, 1,980 passed and one skipped now; Flutter tests 269 before, 270 now. Tools tests and firmware host tests were 39 and 296 both before and after. The prior backend failure was attributed to stale local database IDs; it was not reproduced in this run.

## To change this
To support a new care-profile format, update bound extraction in `MobileUI/mobile_app/lib/data/providers.dart::UnifiedSafeRange.fromProfiles` and add fixtures for the representation. To change dashboard fallback thresholds, update the cited existing defaults and keep the unavailable versus incompatible distinction.

## Rollback
Remove the derived provider and restore the former species range calculation and fixed dashboard targets. No persisted state depends on the intersection.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-19 03:40 UTC for issue #109 attempt 53; compared HEAD..working tree (base 5633656). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 3 file(s), 0 component(s), +108/-46 lines
- plus 2 test file(s)

**Removed symbols**

None.

**Scope**

No Scope line in the issue.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

5 checked by the tool, 1 tested in this run, 14 claimed only (of 20 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 4 checked, 0 tested, 2 claimed
- Key parameters: 1 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 1 tested, 3 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
- Breaks or needs follow-up: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `MobileUI/mobile_app/lib/data/providers.dart::UnifiedSafeRange` - intersection value (named code exists and changed)
  - [checked] Key parameters: Existing fallback pH bounds 6.8–8.2 are `PhOutcomeCard` defaults; temperature bounds 24... (value matches the code: comfortMinTempC = 24, comfortMaxTempC = 28)
  - [tested] How it was verified: `python tools/check.py all`: backend pytest 1,980 passed and one skipped; tools pytest... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `MobileUI/mobile_app/lib/data/providers.dart::unifiedSafeRangeProvider` - shared profil... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/screens/dashboard_view.dart::DashboardViewState.build` - thres... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/screens/fish_tips_view.dart::FishTipsViewState.build` - shared... (named code exists and changed)
  - [claimed] Components: `MobileUI/mobile_app/test/unified_safe_range_test.dart` - interval states (names no code)
  - [claimed] Key parameters: Shared interval is `[max(minimums), min(maximums)]` (`MobileUI/mobile_app/lib/data/prov... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: disjoint bounds remain empty; missing/unparseable bounds remain unavailable; o... (names no code)
  - [claimed] How it was verified: `flutter test test/unified_safe_range_test.dart test/dashboard_view_test.dart`: 15 test... (no test named)
  - full per-claim list (20) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0109-unified-safe-range.md` gives the commit.
<!-- verified-facts:end -->
