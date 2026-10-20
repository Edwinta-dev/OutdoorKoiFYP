# 0097: Distinguish history load failures from scarce data
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #97 · Date: 2026-10-19

## In short

The graph screen now presents a retryable load failure when history fetching fails. A successful response with no readings for the selected metric still presents the existing scarce-data message.

## How it works

1. The screen requests history for the selected pond and time range.
2. While the request is pending, the screen shows its loading indicator.
3. After a successful response, the screen filters readings for the selected metric. If none remain, it explains that daily averages are still being established; otherwise it draws the chart.
4. If the request fails, the graph area shows a connection message and Retry. Retry invalidates the history and assessment data so Riverpod requests them again.
5. The other metric cards remain visible while the graph reports its own load failure.

## Main design choice

Use Riverpod's existing error state at the screen boundary and give the existing placeholder an explicit error mode. A result type would expand the data contract for a distinction already represented by the provider. Turning an exception into an empty result would continue to label failures as scarce data. The database aggregation defect belongs to #17 and is not changed here.

## Traps: why the obvious fix is wrong

- Empty filtered points can mean a successful sparse response; only the provider error state should select the error presentation.
- `PondDataSource::fetchHistoricalGraphPayload` lets RPC exceptions propagate. Catching them there would prevent the screen from distinguishing failure from an empty response.
- The error state is for the history graph; replacing the whole page would hide independent metric cards.

## Components

- `MobileUI/mobile_app/lib/screens/detail_graph_screen.dart::DetailGraphScreen` — select error, empty, or chart state
- `MobileUI/mobile_app/lib/widgets/detail_graph/scarce_data_placeholder.dart::ScarceDataPlaceholder` — empty and retryable error presentation
- `MobileUI/mobile_app/lib/data/telemetry_history.dart::TelemetryHistory` — unchanged payload model
- `MobileUI/mobile_app/test/detail_graph_screen_test.dart` — state and retry coverage

## Breaks or needs follow-up

None. #17 owns the database-side correction.

## Problem and constraints

A failed history RPC previously left the filtered graph points empty, rendering the same message as a successful empty response. The mobile change preserves the history payload and lets the screen show the correct recovery action.

## Alternatives considered

- Wrap fetches in a success/error type — expands a contract when Riverpod already retains exceptions.
- Show a generic error over the whole screen — hides useful cards whose data loaded independently.

## Trade-offs

The message gives a retry path but does not expose the backend cause, such as the known database defect.

## Key parameters

None.

## Assumptions

The history provider reports repository/RPC failures as errors. Successful empty payloads remain successful data.

## Edge cases

- Handled: failed fetch with retry; successful empty history; successful history with points.
- Not handled: retry cannot resolve a persistent server-side failure; the user will see the error again.

## Not verified

The local Supabase RPC and its pond-less row behavior were not exercised. The database fix remains assigned to #17. The repository-wide pytest skipped the local Supabase dev-profile check because no local database was available.

## Changes outside the scope

The widget test and this required decision record accompany the scoped mobile changes.

## How it was verified

- `flutter test test/detail_graph_screen_test.dart` — 17 tests passed; covers loading, populated, empty, and failed history for each of the three graph domains, including retry.
- `flutter analyze` — passed with no issues.
- `python tools/check.py all` — passed secret scan, Ruff, mypy, backend pytest (1,980 passed, one local Supabase dev-profile test skipped; 508,456 assertions), tools pytest (39 passed), backtest (11 of 11 metrics), firmware host tests (296 passed), all three ESP32 compiles, Flutter analyze, and Flutter tests (267 passed).
- The one pytest skip was the local Supabase dev-profile check because Docker/Supabase is unavailable in this sandbox; the issue instructions assign that database gate to the host.

## To change this

- To show server-specific recovery guidance, expose a safe error category from the history repository and render it in the error mode; cost: a broader error contract and UI work.

## Rollback

Revert the screen branching and placeholder error mode together to restore the prior behavior. The history payload contract was not changed.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-19 02:43 UTC for issue #97 attempt 51; compared HEAD..working tree (base 9f3c736). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 2 file(s), 0 component(s), +25/-8 lines
- plus 1 test file(s)

**Removed symbols**

None.

**Scope**

Declared: `MobileUI/mobile_app/lib/screens/detail_graph_screen.dart`, `MobileUI/mobile_app/lib/widgets/detail_graph/scarce_data_placeholder.dart`, `MobileUI/mobile_app/lib/data/telemetry_history.dart`. Verdict: pass.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

2 checked by the tool, 0 tested in this run, 13 claimed only (of 15 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 2 checked, 0 tested, 2 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 4 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `MobileUI/mobile_app/lib/screens/detail_graph_screen.dart::DetailGraphScreen` — select... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/widgets/detail_graph/scarce_data_placeholder.dart::ScarceDataP... (named code exists and changed)
  - [claimed] Components: `MobileUI/mobile_app/lib/data/telemetry_history.dart::TelemetryHistory` — unchanged pay... (`MobileUI/mobile_app/lib/data/telemetry_history.dart::TelemetryHistory` exists but did not change)
  - [claimed] Edge cases: Handled: failed fetch with retry; successful empty history; successful history with poi... (names no code)
  - [claimed] How it was verified: `flutter test test/detail_graph_screen_test.dart` — 17 tests passed; covers loading, po... (no test named)
  - [claimed] Components: `MobileUI/mobile_app/test/detail_graph_screen_test.dart` — state and retry coverage (names no code)
  - [claimed] How it was verified: `flutter analyze` — passed with no issues. (no test named)
  - [claimed] How it was verified: `python tools/check.py all` — passed secret scan, Ruff, mypy, backend pytest (1,980 pas... (no test named)
  - [claimed] How it was verified: The one pytest skip was the local Supabase dev-profile check because Docker/Supabase is... (no test named)
  - full per-claim list (15) in the JSON record

**Consistency**

0 error(s), 1 warning(s) between the rationale above and the change.
- WARN: 'Main design choice' names no alternative; say which 1-2 real options were possible and why this one won

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0097-history-load-errors.md` gives the commit.
<!-- verified-facts:end -->
