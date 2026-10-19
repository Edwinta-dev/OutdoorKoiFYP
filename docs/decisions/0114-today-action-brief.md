# 0114: Today action cards are too wordy for a quick brief
<!-- verify-banner:begin -->
> ⚠ Agent-written record: its claims are hypotheses to verify against the code, not ground truth. Evidence levels in Verified facts: [checked] confirmed by the tool · [tested] cited test exists and passed in this run · [claimed] not verified.
<!-- verify-banner:end -->
Status: implemented · Issue: #114 · Date: 2026-10-19

## In short

Collapsed Today cards now show a rule icon, a short rule-specific headline, and compact timing and evidence chips. Expanding “Why” reveals the original server action, complete rule evidence and the weather-data disclaimer.

## How it works

1. The dashboard continues to parse the same `next_actions` fields; each parsed action exposes a local headline and icon based on its rule.
2. The card shows that headline beside the icon and presents its timing and evidence as compact chips. The evidence chip is constrained and ellipsized on narrow layouts.
3. “Why” expands the card to show the original action sentence, full evidence with the rule label, and the existing disclaimer about the source and limits of weather rules.
4. Dismiss and log controls continue to use the existing action identity and event mapping.

## Main design choice

Rule-specific headlines are derived in the app from the existing rule identifier rather than adding a headline to the API. This keeps the dashboard contract unchanged and makes wording immediately reusable by other clients only if they independently implement the same mapping. A server-provided headline would centralize copy and help notifications reuse it, but would require backend and contract changes beyond this mobile presentation issue.

## Traps: why the obvious fix is wrong

- Simply truncating the existing action sentence hides useful instructions with no way to recover them; the complete sentence remains available on expansion.
- Timing and evidence remain visible as chips because they are the quick brief's supporting facts; only the longer instruction and rationale move behind “Why”.
- Dismissal keys still contain the original action string, preserving existing saved dismissals.

## Components

- `MobileUI/mobile_app/lib/utils/digital_twin_api.dart::PondAction` - derived headlines and icons.
- `MobileUI/mobile_app/lib/widgets/dashboard/today_action_feed.dart::_ActionCard` - compact and expanded presentations.
- `MobileUI/mobile_app/test/today_action_feed_test.dart` - presentation, data and expansion assertions.

## Breaks or needs follow-up

None.

## Problem and constraints

The long action sentence, timing line, “Why” evidence, and disclaimer made cards too dense for a quick glance. The dashboard response shape, backend, and notification outbox were left unchanged.

## Alternatives considered

- Put full content behind expansion and show no evidence chips - rejected because the requested brief should retain actionable timing and evidence.
- Add headline fields to API actions - rejected for this mobile-only display change; reconsider when notifications need shared copy.

## Trade-offs

Headline wording is maintained in the Flutter client. If backend rules change, update the local mapping and its tests. Evidence chips can truncate long evidence, while expansion preserves the complete text.

## Key parameters

- Evidence chip maximum width is 210 logical pixels (`today_action_feed.dart::_evidenceChip`); wider values make chips more prominent and narrower values increase truncation.

## Assumptions

The four rule identifiers remain NOWCAST, REACT, PREEMPT and WINDOW. Unknown rules fall back to the server action sentence and a generic rule icon.

## Edge cases

- Handled: missing evidence omits its chip; unknown rules use the existing generic label and fallback headline.
- Not handled: headline copy is not localized.

## Not verified

Physical-device appearance was not checked.

## Changes outside the scope

The issue text did not include an explicit Scope line. Changes are limited to the mobile action model and card widget, its focused test, and this required decision record.

## How it was verified

- `flutter test test/today_action_feed_test.dart` - 20 tests passed for action parsing, ordering, compact card content, expansion, dismissal/logging, large text and dashboard placement.
- `flutter analyze` - no issues found after formatting.
- `python tools/check.py all` - backend 1,981 passed and the local Supabase dev-profile integration test skipped because no local stack was running; tools 39 passed; firmware host tests 296 passed and all three ESP32 builds passed; Flutter analyze passed and 270 Flutter tests passed.

## To change this

- To change concise copy, edit `PondAction::headline` and its expectations in `today_action_feed_test.dart`.
- To favour more evidence visibility over compactness, raise the 210 logical pixel chip constraint; the trade-off is larger cards.

## Rollback

Restore the prior action sentence and “Why” rendering in `_ActionCard`; the dashboard API and dismissal storage need no rollback.

<!-- verified-facts:begin (generated by integrity record; do not edit) -->
## Verified facts

Generated 2026-10-19 07:14 UTC for issue #114 attempt 58; compared HEAD..working tree (base b12a5b9). Records are claims to check against the code, not proof.

**Change summary**

- `MobileUI/mobile_app/`: 2 file(s), 0 component(s), +91/-19 lines
- plus 1 test file(s)

**Removed symbols**

None.

**Scope**

No Scope line in the issue.

**Supervisor checks passed**

whitespace/conflict-marker check, apply pending migrations to the local Supabase stack, OutdoorKoi host test suites (layout-aware), mobile goldens on Linux (tools/update_goldens.py --check), scope check, dangling reference check

**Evidence**

2 checked by the tool, 0 tested in this run, 12 claimed only (of 14 claims). [checked] = the named code exists and changed, or the cited value matches; [tested] = the cited test exists and the test gate passed in this run; [claimed] = not verified.
- Components: 2 checked, 0 tested, 1 claimed
- Key parameters: 0 checked, 0 tested, 1 claimed
- Edge cases: 0 checked, 0 tested, 1 claimed
- How it was verified: 0 checked, 0 tested, 3 claimed
- Traps: 0 checked, 0 tested, 3 claimed
- Assumptions: 0 checked, 0 tested, 1 claimed
- Trade-offs: 0 checked, 0 tested, 1 claimed
- Not verified: 0 checked, 0 tested, 1 claimed
  - [checked] Components: `MobileUI/mobile_app/lib/utils/digital_twin_api.dart::PondAction` - derived headlines a... (named code exists and changed)
  - [checked] Components: `MobileUI/mobile_app/lib/widgets/dashboard/today_action_feed.dart::_ActionCard` - compa... (named code exists and changed)
  - [claimed] Components: `MobileUI/mobile_app/test/today_action_feed_test.dart` - presentation, data and expansi... (names no code)
  - [claimed] Key parameters: Evidence chip maximum width is 210 logical pixels (`today_action_feed.dart::_evidenceCh... (no cited constant with a matching value)
  - [claimed] Edge cases: Handled: missing evidence omits its chip; unknown rules use the existing generic label... (names no code)
  - [claimed] How it was verified: `flutter test test/today_action_feed_test.dart` - 20 tests passed for action parsing, o... (no test named)
  - [claimed] How it was verified: `flutter analyze` - no issues found after formatting. (no test named)
  - [claimed] How it was verified: `python tools/check.py all` - backend 1,981 passed and the local Supabase dev-profile i... (no test named)
  - full per-claim list (14) in the JSON record

**Consistency**

0 error(s), 0 warning(s) between the rationale above and the change.

**Commit**

This record is committed with the change it describes; `git log -1 -- docs/decisions/0114-today-action-brief.md` gives the commit.
<!-- verified-facts:end -->
