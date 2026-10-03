// DashboardView in each state: loading, empty, data, error.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/screens/dashboard_view.dart';
import 'package:mobile_app/widgets/dashboard/ph_outcome_card.dart';
import 'package:mobile_app/widgets/dashboard/solar_outcome_card.dart';
import 'package:mobile_app/widgets/dashboard/temperature_outcome_card.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

/// DashboardView polls on a 30 s Timer; unmounting cancels it so the test
/// does not end with a pending timer.
Future<void> unmount(WidgetTester tester) =>
    tester.pumpWidget(const SizedBox.shrink());

void main() {
  testWidgets('loading: spinner and no cards while the payload is pending', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      const DashboardView(),
      source: FakePondDataSource(hanging: {'fetchDashboardPayload'}),
    );
    await settle(tester);

    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.byType(TemperatureOutcomeCard), findsNothing);
    expect(find.byType(PhOutcomeCard), findsNothing);
    await unmount(tester);
  });

  testWidgets(
    'data: three monitors render the payload and backend assessment',
    (tester) async {
      final fake = await pumpScreen(
        tester,
        const DashboardView(),
        source: FakePondDataSource(assessment: Fixtures.assessment()),
      );
      await settle(tester);

      expect(find.byType(CircularProgressIndicator), findsNothing);
      expect(find.text('TEMPERATURE & FEED MONITOR'), findsOneWidget);
      expect(find.text('PH & BUFFER HEALTH'), findsOneWidget);
      expect(find.text('ALGAL & SOLAR MONITOR'), findsOneWidget);
      expect(find.byType(TemperatureOutcomeCard), findsOneWidget);
      expect(find.byType(SolarOutcomeCard), findsOneWidget);
      // The pH card shows the server's category and advisory verbatim.
      expect(find.text('Watch'), findsOneWidget);
      expect(find.text(Fixtures.assessment().advisory), findsOneWidget);

      // Both sources are asked for the stored user id.
      expect(fake.callsTo('fetchDashboardPayload').single.args['userId'], '7');
      expect(fake.callsTo('fetchLatestAssessment').single.args['userId'], 7);
      await unmount(tester);
    },
  );

  testWidgets('empty: a pond with no readings still shows the monitors', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      const DashboardView(),
      source: FakePondDataSource(
        dashboardPayload: Fixtures.emptyDashboardPayload(),
      ),
    );
    await settle(tester);

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byType(TemperatureOutcomeCard), findsOneWidget);
    expect(find.byType(PhOutcomeCard), findsOneWidget);
    expect(find.byType(SolarOutcomeCard), findsOneWidget);
    // No history and no backend assessment: the client heuristic's
    // fallback category.
    expect(find.text('Insufficient History'), findsOneWidget);
    await unmount(tester);
  });

  testWidgets('error: a failed fetch clears the spinner and shows the monitors '
      'with fallback values', (tester) async {
    // Pins current behaviour: there is no error banner yet, so a failed
    // fetch looks the same as an empty pond.
    await pumpScreen(
      tester,
      const DashboardView(),
      source: FakePondDataSource(failing: {'fetchDashboardPayload'}),
    );
    await settle(tester);

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byType(PhOutcomeCard), findsOneWidget);
    expect(find.text('Insufficient History'), findsOneWidget);
    await unmount(tester);
  });

  testWidgets('refreshData fetches again', (tester) async {
    final fake = await pumpScreen(tester, const DashboardView());
    await settle(tester);

    await tester
        .state<DashboardViewState>(find.byType(DashboardView))
        .refreshData();
    await settle(tester);

    expect(fake.callsTo('fetchDashboardPayload'), hasLength(2));
    await unmount(tester);
  });
}
