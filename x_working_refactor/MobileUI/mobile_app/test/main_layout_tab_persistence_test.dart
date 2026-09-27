// Round 2 regression test: switching tabs must NOT dispose/recreate
// DashboardView's State (and therefore must not cancel its poll Timer or
// discard its cached _dashboardData/_telemetryHistory). Before the
// IndexedStack fix, `body: _screens[_currentIndex]` did exactly that on
// every tab switch - this test fails against that old code (the captured
// State identity changes) and passes against the fix.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/screens/dashboard_view.dart';
import 'package:mobile_app/screens/main_layout.dart';

void main() {
  testWidgets(
    'DashboardViewState survives switching away to another tab and back',
    (tester) async {
      await tester.pumpWidget(const MaterialApp(home: MainLayout()));
      await tester.pump(); // let the initial (network-failing, caught) fetch settle

      final dashboardStateBefore = tester.state<DashboardViewState>(
        find.byType(DashboardView, skipOffstage: false),
      );

      // Switch to Fish Tips (index 1). Its label Text is only rendered
      // while selected, so tap the always-visible inactive icon instead.
      await tester.tap(find.byIcon(Icons.phishing_outlined));
      await tester.pump();

      // DashboardView must still be in the tree (IndexedStack keeps every
      // child mounted, just hidden via Visibility - hence skipOffstage:
      // false, since the default finder behavior skips offstage widgets
      // entirely). If this regresses to swapping bodies instead of hiding
      // them, this widget genuinely disappears from the tree instead.
      expect(
        find.byType(DashboardView, skipOffstage: false),
        findsOneWidget,
        reason: 'IndexedStack should keep DashboardView mounted even while '
            'a different tab is showing',
      );

      // Switch back to Dashboard (index 0).
      await tester.tap(find.byIcon(Icons.water_drop_outlined));
      await tester.pump();

      final dashboardStateAfter = tester.state<DashboardViewState>(
        find.byType(DashboardView, skipOffstage: false),
      );

      expect(
        identical(dashboardStateBefore, dashboardStateAfter),
        isTrue,
        reason:
            'Switching tabs and back recreated DashboardViewState - its poll '
            'Timer was cancelled and its cached data was thrown away, which '
            'is exactly the bug this fix addresses.',
      );

      // Unmount cleanly so DashboardView's Timer.periodic is cancelled via
      // dispose() before the test ends (a still-pending Timer fails the test).
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
