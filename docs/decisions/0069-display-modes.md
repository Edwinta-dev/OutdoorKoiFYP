# 0069: Simple and detailed display modes
Status: implemented · Issue: #69 · Date: 2026-10-12

## In short
Settings has a "Detailed view" switch. Off (the default when nothing is stored) the dashboard shows the Today action feed and a short "Pond status" list of plain sentences; on, it shows the dashboard as it was before (weather ribbon, the three outcome cards and their links to the charts). The choice is saved in the local profile and the dashboard switches as soon as the setting changes.

## Traps: why the obvious fix is wrong
- "Default simple for new accounts" cannot be told apart from "existing install that never chose", because onboarding (`lib/screens/onboarding_screen.dart`) is outside the scope and so does not write the key. Any profile without `displayMode` gets simple, so owners who used the app before this change also land in the simple view once. Writing `detailed` for already-onboarded profiles on first launch would need a migration flag in the local profile; it was not done.
- `DisplayModeNotifier::build` watches `localProfileProvider`, so `SettingsView::deletePondData` invalidating that provider re-reads the stored mode instead of dropping it. Reading the mode with `ref.read` would leave a stale value after a profile reload.
- `DisplayModeNotifier::set` changes `state` before the save and restores it on failure. Without the restore, a failed save would leave the switch showing a choice that is gone after restart.
- The existing `test/dashboard_view_test.dart` tests now pass `detailedPrefs`: without it they render the simple view and the card assertions fail. The assertions themselves are unchanged.

## Components
- `MobileUI/mobile_app/lib/data/providers.dart::DisplayMode` - the two modes.
- `MobileUI/mobile_app/lib/data/providers.dart::displayModeKey` - local profile key (`displayMode`), stored as the enum name.
- `MobileUI/mobile_app/lib/data/providers.dart::DisplayModeNotifier` / `displayModeProvider` - loads the mode (simple when absent or unknown) and saves changes.
- `MobileUI/mobile_app/lib/screens/settings_view.dart::_SettingsViewState._setDetailed` - the switch handler; shows a SnackBar when saving fails.
- `MobileUI/mobile_app/lib/screens/dashboard_view.dart::DashboardViewState.build` - picks the simple or detailed body.
- `MobileUI/mobile_app/lib/screens/dashboard_view.dart::SimplePondStatus` - the simple view's status lines (`temperatureLine`, `phLine`, `oxygenLine`).
- `MobileUI/mobile_app/lib/screens/dashboard_view.dart::comfortMinTempC` / `comfortMaxTempC` - the 24-28 °C range, now shared by the temperature card call and the simple view.

## Problem and constraints
The dashboard showed every model value to every user. The issue asks for a simple view (status, actions, plain explanations) and keeps the full view as an option. Scope is limited to the settings screen, `providers.dart` and the dashboard screen, so the outcome cards in `lib/widgets/dashboard/` were not split into simple and detailed parts; the simple view is built in `dashboard_view.dart` from `PondDashboard` (readings, `assessments.chemistry`, `assessments.hypoxia`), which `pondDashboardProvider` already fetches. No new request is made.

## Alternatives considered
- Pass a `simple` flag into each outcome card so it hides its model numbers - the cards are outside the scope, and they render goldens, which cannot be re-recorded here.
- Store the mode on the server profile - needs a backend change and a migration; the dismissals from #55 already use the local profile, so this follows that.

## Trade-offs
- The simple view leaves out the weather ribbon, evaporation and algae, because there is no plain-language field for them in the dashboard response. An owner in simple mode sees no algae or top-up status unless an action fires in the Today feed.
- The setting is per device. A second phone for the same pond starts in simple mode.

## Key parameters
- Default mode: `DisplayMode.simple` (`providers.dart::DisplayModeNotifier::build`), set by the issue.
- Comfort range 24-28 °C (`dashboard_view.dart::comfortMinTempC`, `comfortMaxTempC`): the values the dashboard already passed to `TemperatureOutcomeCard` as `targetMinTemp`/`targetMaxTemp`; not changed. Changing them moves both the card's and the simple view's range.
- Temperature and pH are shown to 1 decimal place (`toStringAsFixed(1)` in `SimplePondStatus`).

## Assumptions
- `SensorReading.status` values are `ok | stale | missing | invalid` (comment in `lib/utils/pond_dashboard.dart::SensorReading`); any other value is worded as "no reading has arrived".
- `HypoxiaFlag.level` is `unknown | none | watch | high`; `watch` is worded "raised". A new level would show as "unknown".
- `WaterChemistryAssessment.advisory` is plain owner-facing text, as the pH card already shows it verbatim.

## Edge cases
- Handled: no stored mode, or an unrecognised value (`expert`) - simple view.
- Handled: dashboard fetch failed - simple view says "Pond status could not be loaded. Pull down to try again."
- Handled: stale, missing or invalid sensor readings - a sentence telling the owner to check the sensor or probe instead of a number.
- Handled: save fails - switch flips back and a SnackBar says the setting was not saved.
- Not handled: while the profile is still loading the dashboard renders the simple body; an owner who chose detailed could see it for a frame on start-up. In practice the dashboard spinner covers that time.
- Not handled: hypoxia `advice` is not shown in the simple view; the owner sees only the explanation.

## Not verified
- How the simple view looks on a device; it is covered by widget tests and by the existing layout sweep in `test/design_system_test.dart` (dashboard and settings at text scale 1.0-1.6 on a 320 px phone, both themes), not by a golden.
- The hypoxia line with real service output: `FakePondDataSource.fetchDashboardPayload` replaces `assessments`, so the widget tests always see an unknown hypoxia flag; `oxygenLine` is tested directly.
- Behaviour with the real `PreferencesProfileRepository` (tests use `FakeLocalProfileRepository`).

## Changes outside the scope
Tests only (allowed): new `MobileUI/mobile_app/test/settings_view_test.dart`; edits to `test/dashboard_view_test.dart`, `test/today_action_feed_test.dart` and `test/flows/onboarding_to_feed_flow.dart`, the last two because they assumed the detailed dashboard was the default. No other files.

## How it was verified
- `flutter test` in `MobileUI/mobile_app` (whole suite).
- `test/dashboard_view_test.dart`: existing five tests now run in detailed mode; added "simple view" group: default when the profile has no mode, unknown mode falls back to simple, failed fetch message, and unit tests of `temperatureLine`, `phLine`, `oxygenLine`.
- `test/settings_view_test.dart`: new account starts in simple; stored detailed shows the switch on; toggling saves `detailed`/`simple` and a dashboard in the same scope switches; failed save reverts the switch and shows the SnackBar.
- `test/today_action_feed_test.dart`: "shows the Today feed above the weather and monitors" now sets detailed; added "simple view: the Today feed sits above the pond status".
- `test/flows/onboarding_to_feed_flow.dart` (run by `test/onboarding_flow_test.dart`): after onboarding a new account sees `POND STATUS` and no `TEMPERATURE & FEED MONITOR`, and the chemistry advisory appears inside the pH line.
- `python tools/check.py all`.

## To change this
- To default existing installs to detailed, have `DisplayModeNotifier::build` return `detailed` when the key is absent and `isOnboarded` is true, and make onboarding write `simple`; cost: touches `onboarding_screen.dart`.
- To add a status line (for example algae), add a static `...Line` method to `SimplePondStatus` and an entry in its `lines` list, once the dashboard response carries a plain-language field for it.
- To show the weather in simple mode, move `NeaWeatherRibbon` above the `if (!detailed)` branch in `DashboardViewState.build`.
- Likely bug sources: new `status`/`level` spellings from the service, and tests that pump `DashboardView` expecting the cards without setting `displayModeKey`.

## Rollback
Nothing else reads `displayModeProvider`. Reverting restores the single detailed dashboard; a stored `displayMode` key is then ignored. Restore the `targetMinTemp: 24` / `targetMaxTemp: 28` literals if the constants are removed.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-12 08:02 UTC for issue #69 attempt 5; compared HEAD..working tree (base 27d83f7). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 3 file(s), 0 component(s), +268/-67 lines
- plus 4 test file(s)

**Removed symbols**

None.

**Scope**

Declared: `MobileUI/mobile_app/lib/screens/settings_view.dart`, `MobileUI/mobile_app/lib/data/providers.dart`, `MobileUI/mobile_app/lib/screens/dashboard_view.dart`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0069-display-modes.md` gives the commit.
<!-- verified-facts:end -->
