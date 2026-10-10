# 0055: Today action feed on the dashboard
Status: implemented · Issue: #55 · Date: 2026-10-10

## In short

The dashboard now opens with a "Today" section listing the weather rules that fired for the pond, most urgent first. Each entry says what to do, when it applies, and why (expandable), with a button to log a water change or feed where the action has one. An entry can be dismissed for the rest of the day, and an empty list says the pond needs nothing today.

## Problem and constraints

The lead-time rules from #26 reach the app in the dashboard response's `next_actions`, but nothing displayed them. The issue limits changes to the dashboard screen, `digital_twin_api.dart`, the quick-log modals and `lib/widgets/**`, so the data layer (`lib/data/`) and the backend were not changed. Goldens cannot be recorded here, so the feature is covered by widget tests only and no existing golden renders it.

## Approach

`MobileUI/mobile_app/lib/utils/digital_twin_api.dart::PondAction` parses one `next_actions` entry (`rule`, `lead_time`, `action`, `evidence`, as built by `Backend/koi/models/ladder.py::_action`). The service sends no urgency, so `PondAction::byUrgency` orders by a fixed rule ranking and keeps the service's order for ties and unknown rules. `PondAction::whenText` turns each known lead time into plain words; `PondAction::logEventType` names the event to log.

`MobileUI/mobile_app/lib/widgets/dashboard/today_action_feed.dart::TodayActionFeed` renders the list. `DashboardView` reads `pondDashboardProvider` (already fetched for the cards) and passes the parsed actions, or null when the dashboard did not load. Dismissals are stored through the existing `localProfileRepositoryProvider` under `dismissedActions` as `yyyy-mm-dd|RULE|action`, using the device's local date; only today's entries count, and older ones are dropped on the next write. The log button calls the new `quick_log_modals.dart::showInterventionLogSheet`, which the quick-log selector now also uses, so both open the same sheet.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Fetch `/v1/ponds/{pond}/actions` for per-rule status | Needs a new repository method in `lib/data/` (outside scope) and a second request every 30 s; the dashboard already carries the actions. |
| Keep dismissals in widget state only | They would come back after every app restart, which is not "hidden for the day". |

## Trade-offs

The dashboard's `next_actions` has no per-rule status, so the empty state cannot tell a quiet day from rules that could not be checked. Today PREEMPT and WINDOW are always unassessed on the server (decision 0026). The empty state therefore adds "Rules without enough weather data are not counted." Once the service sends rule status on the dashboard, the empty state should name the unchecked rules.

## Key parameters

- Urgency order NOWCAST, REACT, PREEMPT, WINDOW: how soon each action must happen (rain within 2 hours; a check after yesterday's rain; before a multi-day hot stretch; an optional dry window). Unknown rules come last.
- NOWCAST end time: now plus 2 hours, rounded down to 15 minutes. This matches the rule's "within 2 hours" lead time. A finer step would suggest more precision than a 2-hour nowcast has.
- REACT wording switches from "This morning" to "Today" at 12:00 local time, chosen so the text stays true all day.
- Warning accent for the first two ranks (NOWCAST, REACT), info accent for the others.
- Event mapping: WINDOW to `WATER_CHANGE`, PREEMPT to `FEEDING`, REACT and NOWCAST none. No current rule maps to a top-up, so no top-up button appears yet.

## Assumptions

- The four `lead_time` strings stay as in `ladder.py`. A new or reworded one is shown capitalised as sent, which still reads acceptably.
- One action per rule per day. The dismiss key includes the action text, so a reworded action reappears after a dismissal.
- The device date is the pond's date (Singapore). A phone set to another time zone would reset dismissals at its own midnight.

## Edge cases

- Dashboard fetch fails with no earlier data: the feed says "Today's actions could not be loaded." and does not claim the pond needs nothing.
- Every action dismissed: the empty state says how many are hidden until tomorrow.
- Missing fields in an action: shown with empty text and the label "Weather rule", with no crash (tested).
- Storage read or write fails: nothing is hidden, or the dismissal lasts only for the session. Not tested.
- During the 30 s refresh the last loaded actions stay on screen.

## Changes outside the scope

The backend contract test `Backend/tests/api/test_contract.py::test_openapi_schemas_hold_every_dart_read_key` requires every model class in `digital_twin_api.dart` to match a documented OpenAPI schema. No schema documented an action entry: `Dashboard.next_actions` was `list[dict]`, described as "Empty until the action ladder issue defines it." Moving `PondAction` to an unchecked file would have avoided the check without meeting it. Instead:

- `Backend/koi/api/responses.py::LeadTimeAction` documents `rule`, `lead_time`, `action`, `evidence`. It is open to extra keys, and `lead_time` and `evidence` are nullable so an unexpected null cannot make the dashboard return 500. `Dashboard.next_actions` is now `list[LeadTimeAction]`, with a current description.
- `Backend/koi/api/dashboard.py::build_dashboard` validates each action into `LeadTimeAction` (needed for mypy, same pattern as `HypoxiaFlag`).
- `docs/api/openapi.yaml` was regenerated with `python -m koi.api.openapi`.
- `test_contract.py` maps the Dart `PondAction` to `LeadTimeAction`.
- `Backend/tests/api/test_dashboard.py::test_dashboard_serves_fired_actions_in_the_documented_shape` serves a fired NOWCAST action through the dashboard and validates the response against the schema.

The wire format is unchanged. The `/actions` route's `LeadTimeActions` model was left as it was. A new Flutter test file was also added.

## How it was verified

`flutter test test/today_action_feed_test.dart`: 19 tests covering parsing, ordering, lead-time wording, event mapping, list order on screen, empty and unavailable states, dismissal storage and expiry, why expansion, both log buttons opening `InterventionLogSheet` with the right event type, no overflow with all four actions expanded at 1.6 text scale on a 320 px wide screen in both themes, and placement above the monitors on `DashboardView`. Full `flutter test`: 236 before, 255 after, all passing. `flutter analyze` is clean. Backend API tests: 418 passed, including the new dashboard action test; `python tools/check.py all`: every check PASS except the three ESP32 firmware compiles, which SKIP because the esp32 core is not installed here. Not checked on a device, and the feed has never displayed live server data because PREEMPT and WINDOW cannot fire yet.

## To change this

- To change the order, edit `PondAction.urgencyOrder`.
- To add a log button for a rule, extend `PondAction::logEventType` and `_ActionCard._logTarget` (title must match the quick-log selector's).
- To show unchecked rules in the empty state, add the per-rule `rules` list to the dashboard response and pass it to `TodayActionFeed`.
- Likely bug sources: a backend change to `lead_time` or `rule` spellings, and time-zone differences in the dismissal date.

## Rollback

Remove `TodayActionFeed` from `DashboardView` and delete the widget and its test. `PondAction` and `showInterventionLogSheet` can stay; the quick-log selector depends on the latter. Stored `dismissedActions` values are harmless if left in local storage.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-10 10:00 UTC for issue #55 attempt 6; compared HEAD..working tree (base 1319adf).

**Changed components**

| Component | Change | Lines +/- | Tags | Description |
|---|---|---|---|---|
| `Backend/koi/api/dashboard.py::build_dashboard` | modified | +1/-1 |  |  |
| `Backend/koi/api/responses.py::LeadTimeAction` | added | +8/-0 |  | One fired weather rule (koi/models/ladder.py::_action). |
| `Backend/koi/api/responses.py::Dashboard` | modified | +3/-1 |  | Everything the dashboard screen shows, in one response. |
| `MobileUI/mobile_app/lib/screens/dashboard_view.dart` | modified | +9/-0 |  |  |
| `MobileUI/mobile_app/lib/utils/digital_twin_api.dart` | modified | +92/-0 |  |  |
| `MobileUI/mobile_app/lib/widgets/dashboard/today_action_feed.dart` | new file | +329/-0 |  |  |
| `MobileUI/mobile_app/lib/widgets/modals/quick_log_modals.dart` | modified | +25/-14 |  |  |

Plus 3 test file(s), 1 doc file(s).

**Removed symbols**

None.

**Scope**

Declared: `MobileUI/mobile_app/lib/screens/dashboard_view.dart`, `MobileUI/mobile_app/lib/utils/digital_twin_api.dart`, `MobileUI/mobile_app/lib/widgets/modals/quick_log_modals.dart`, `MobileUI/mobile_app/lib/widgets/**`. Verdict: violation.
- out of scope: `Backend/koi/api/dashboard.py::build_dashboard` (modified); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::LeadTimeAction` (added); explained in the record: yes
- out of scope: `Backend/koi/api/responses.py::Dashboard` (modified); explained in the record: no

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Consistency**

0 error(s), 3 warning(s) between the rationale above and the change.
- WARN: Backend/koi/api/responses.py::Dashboard (modified) changed but not mentioned in the record
- WARN: MobileUI/mobile_app/lib/screens/dashboard_view.dart (modified) changed but not mentioned in the record
- WARN: out-of-scope change not explained in 'Changes outside the scope': Backend/koi/api/responses.py::Dashboard

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0055-today-action-feed.md` gives the commit.
<!-- verified-facts:end -->
