# 0096: NEA full forecast sheet
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #96 · Date: 2026-10-19

## In short

The full forecast sheet now receives the typed forecast parsed from the `/v1` dashboard. It opens from the weather ribbon, displays the forecast fields available in that model, and renders the model's four day outlook.

## How it works

1. The dashboard screen loads the `/v1` dashboard and its parsed pond dashboard.
2. When the weather ribbon is tapped, the screen passes the parsed forecast to the sheet.
3. The sheet displays the regional summary, general temperature, humidity, wind speed and validity period from that forecast.
4. It lists each available outlook day with its summary and weather ranges, or shows the empty state when no days are present.

## Main design choice

The sheet accepts `DashboardForecast` (`MobileUI/mobile_app/lib/widgets/modals/nea_full_forecast_modal.dart::showNeaFullForecastModal`) because this screen already parses the `/v1` response into that type. Reconstructing a raw-looking map from the adapter would keep two forecast representations and preserve the shape mismatch. Parsing raw NEA payloads would be preferable only if the sheet were called directly with raw NEA responses. The current typed model has no wind direction field, so the sheet displays wind speed without direction.

## Traps: why the obvious fix is wrong

- Changing only the 24-hour summary access still leaves the four day outlook empty: the typed model stores days as `forecast.outlook`.
- The API model includes humidity, wind speed and validity timestamps. Keeping the old hard-coded values would hide those mapped fields.
- The dashboard's card adapter is still a map with a different shape; it must not be passed to this sheet.

## Components

- `MobileUI/mobile_app/lib/utils/pond_dashboard.dart::DashboardForecast` - parsed forecast.
- `MobileUI/mobile_app/lib/screens/dashboard_view.dart::DashboardViewState` - supplies forecast on tap.
- `MobileUI/mobile_app/lib/widgets/modals/nea_full_forecast_modal.dart::_NeaForecastSheetContent` - renders forecast.
- `MobileUI/mobile_app/test/dashboard_view_test.dart` - `/v1` fixture regression.
- `MobileUI/mobile_app/test/design_system_test.dart` - typed modal input at large text scale.

## Breaks or needs follow-up

None.

## Problem and constraints

The sheet previously read raw NEA fields from an untyped map even though the dashboard caller supplied a `/v1` adapter map. The issue is fixed within the mobile screen, sheet and their tests; no backend, API or stored data changes are needed.

## Alternatives considered

- Change the card adapter to imitate raw NEA JSON - rejected because it duplicates the typed forecast and leaves untyped coupling.
- Add a separate raw NEA parser - unnecessary for the current caller, which already has a parsed dashboard forecast.

## Trade-offs

Wind direction is not shown because `DashboardForecast` has no direction property. This would matter if compass direction is a requirement; it needs a model/API field populated upstream.

## Key parameters

None.

## Assumptions

The dashboard's parsed `PondDashboard.forecast` is the intended source for this sheet. If a future caller supplies raw NEA data, it must convert that data to the typed forecast first.

## Edge cases

- Handled: missing forecast values retain display fallbacks; missing period timestamps show “Next 24 Hours”; an empty outlook shows its empty state.
- Not handled: wind direction is unavailable in the current typed forecast.

## Not verified

Physical-device behavior was not exercised. The fixture regression verifies the app's `/v1` parsing and widget path, not an external live service. `Backend/tests/sql/test_sql_dev_profile.py` skipped because the sandbox has no local Supabase stack.

## Changes outside the scope

None.

## How it was verified

- `flutter test test/dashboard_view_test.dart test/design_system_test.dart` - all 89 tests passed; includes opening the sheet from `test/fixtures/v1_dashboard.json` and checking summary, valid period, humidity, wind speed and outlook. Does not verify physical hardware.
- `python tools/check.py all` - backend: Ruff and mypy passed; pytest 1,980 passed and 1 skipped (the local Supabase dev-profile test); backtest 11/11 metrics passed; tools 39 passed; firmware host tests 296 passed and all three firmware compiles passed; Flutter analyze passed and 267 mobile tests passed. This does not verify physical hardware. The SQL test skip is expected without Docker/Supabase in the sandbox.

## To change this

To display compass direction, add a direction property to the typed forecast and populate it at the `/v1` boundary; the current model does not carry one.

## Rollback

Restore the map parameter and caller together. That reintroduces the raw NEA versus `/v1` shape mismatch.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-19 02:13 UTC for issue #96 attempt 50; compared HEAD..working tree (base ec17473). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 2 file(s), 0 component(s), +45/-60 lines
- plus 2 test file(s)

**Removed symbols**

None.

**Scope**

No Scope line in the issue.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

2 checked by the tool, 1 tested in this run, 11 claimed only (of 14 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 2 checked, 0 tested, 3 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 1 tested, 1 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `MobileUI/mobile_app/lib/screens/dashboard_view.dart::DashboardViewState` - supplies fo... (named code exists and changed)
  - [tested] How it was verified: `flutter test test/dashboard_view_test.dart test/design_system_test.dart` - all 89 test... (test gate passed: OutdoorKoi host test suites (layout-aware))
  - [checked] Components: `MobileUI/mobile_app/lib/widgets/modals/nea_full_forecast_modal.dart::_NeaForecastSheet... (named code exists and changed)
  - [claimed] Components: `MobileUI/mobile_app/lib/utils/pond_dashboard.dart::DashboardForecast` - parsed forecast. (`MobileUI/mobile_app/lib/utils/pond_dashboard.dart::DashboardForecast` exists but did not change)
  - [claimed] Edge cases: Handled: missing forecast values retain display fallbacks; missing period timestamps sh... (names no code)
  - [claimed] How it was verified: `python tools/check.py all` - backend: Ruff and mypy passed; pytest 1,980 passed and 1... (no test named)
  - [claimed] Components: `MobileUI/mobile_app/test/dashboard_view_test.dart` - `/v1` fixture regression. (names no code)
  - [claimed] Components: `MobileUI/mobile_app/test/design_system_test.dart` - typed modal input at large text sc... (names no code)
  - full per-claim list (14) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0096-nea-forecast-sheet.md` gives the commit.
<!-- verified-facts:end -->
