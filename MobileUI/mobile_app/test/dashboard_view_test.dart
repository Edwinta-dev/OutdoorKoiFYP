// DashboardView in each state: loading, empty, data, error, in the
// detailed view; and the simple view that new accounts get.
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/screens/dashboard_view.dart';
import 'package:mobile_app/utils/pond_dashboard.dart';
import 'package:mobile_app/widgets/dashboard/nea_weather_ribbon.dart';
import 'package:mobile_app/widgets/dashboard/today_action_feed.dart';
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

/// An onboarded device that chose the detailed view.
const detailedPrefs = <String, Object>{
  ...onboardedPrefs,
  displayModeKey: 'detailed',
};

void main() {
  testWidgets('NEA sheet renders typed forecast from the /v1 fixture', (
    tester,
  ) async {
    final fixture =
        jsonDecode(File('test/fixtures/v1_dashboard.json').readAsStringSync())
            as Map<String, dynamic>;
    final source = FakePondDataSource(
      dashboardPayload: fixture['complete'] as Map<String, dynamic>,
    );
    await pumpScreen(
      tester,
      const DashboardView(),
      prefs: detailedPrefs,
      source: source,
    );
    await settle(tester);

    await tester.tap(find.byType(NeaWeatherRibbon));
    await tester.pumpAndSettle();

    expect(find.text('Singapore Environmental Outlook'), findsOneWidget);
    expect(find.text('Thundery Showers'), findsWidgets);
    expect(find.text('2/10 22:00 – 3/10 22:00'), findsOneWidget);
    expect(find.text('SUNDAY'), findsOneWidget);
    expect(find.text('Humidity: 60–95%'), findsOneWidget);
    expect(find.text('Wind: 5–15 km/h'), findsOneWidget);
    expect(find.text('No 4-day outlook available.'), findsNothing);
    expect(tester.takeException(), isNull);
    await unmount(tester);
  });

  testWidgets('loading: spinner and no cards while the payload is pending', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      const DashboardView(),
      prefs: detailedPrefs,
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
        prefs: detailedPrefs,
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
      expect(fake.callsTo('fetchLatestAssessment'), isEmpty);
      await unmount(tester);
    },
  );

  testWidgets('empty: a pond with no readings still shows the monitors', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      const DashboardView(),
      prefs: detailedPrefs,
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
      prefs: detailedPrefs,
      source: FakePondDataSource(failing: {'fetchDashboardPayload'}),
    );
    await settle(tester);

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byType(PhOutcomeCard), findsOneWidget);
    expect(find.text('Insufficient History'), findsOneWidget);
    await unmount(tester);
  });

  testWidgets('refreshData fetches again', (tester) async {
    final fake = await pumpScreen(
      tester,
      const DashboardView(),
      prefs: detailedPrefs,
    );
    await settle(tester);

    await tester
        .state<DashboardViewState>(find.byType(DashboardView))
        .refreshData();
    await settle(tester);

    expect(fake.callsTo('fetchDashboardPayload'), hasLength(2));
    await unmount(tester);
  });

  group('simple view', () {
    testWidgets('is the default when the profile has no display mode', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DashboardView(),
        source: FakePondDataSource(assessment: Fixtures.assessment()),
      );
      await settle(tester);

      expect(find.byType(TodayActionFeed), findsOneWidget);
      expect(find.byType(SimplePondStatus), findsOneWidget);
      expect(find.text('POND STATUS'), findsOneWidget);
      // No model cards and no weather ribbon.
      expect(find.byType(TemperatureOutcomeCard), findsNothing);
      expect(find.byType(PhOutcomeCard), findsNothing);
      expect(find.byType(SolarOutcomeCard), findsNothing);
      expect(find.byType(NeaWeatherRibbon), findsNothing);
      // Fixture readings: 27.4 degC water, pH 7.3, chemistry "Watch".
      expect(
        find.text(
          'Water is 27.4 °C (measured), inside the 24-28 °C koi comfort '
          'range.',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'pH is 7.3 (measured). Water chemistry: Watch (model estimate). '
          '${Fixtures.assessment().advisory}',
        ),
        findsOneWidget,
      );
      expect(
        find.text('Low-oxygen risk could not be estimated yet.'),
        findsOneWidget,
      );
      await unmount(tester);
    });

    testWidgets('an unknown stored mode falls back to simple', (tester) async {
      await pumpScreen(
        tester,
        const DashboardView(),
        prefs: {...onboardedPrefs, displayModeKey: 'expert'},
      );
      await settle(tester);

      expect(find.byType(SimplePondStatus), findsOneWidget);
      expect(find.byType(TemperatureOutcomeCard), findsNothing);
      await unmount(tester);
    });

    testWidgets('a failed fetch says the status could not be loaded', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DashboardView(),
        source: FakePondDataSource(failing: {'fetchDashboardPayload'}),
      );
      await settle(tester);

      expect(
        find.text('Pond status could not be loaded. Pull down to try again.'),
        findsOneWidget,
      );
      expect(find.byType(PhOutcomeCard), findsNothing);
      await unmount(tester);
    });

    SensorReading reading(String status, double? value) =>
        SensorReading.fromJson({
          'channel': 'water_temp',
          'status': status,
          'value': value,
        });

    test('temperature line compares a measurement with the comfort range', () {
      expect(
        SimplePondStatus.temperatureLine(reading('ok', 22.04)),
        'Water is 22.0 °C (measured), below the 24-28 °C koi comfort range. '
        'Koi eat less in cool water.',
      );
      expect(
        SimplePondStatus.temperatureLine(reading('ok', 30.0)),
        'Water is 30.0 °C (measured), above the 24-28 °C koi comfort range. '
        'Warm water holds less oxygen.',
      );
      expect(
        SimplePondStatus.temperatureLine(reading('ok', 28.0)),
        contains('inside the 24-28 °C'),
      );
      expect(
        SimplePondStatus.temperatureLine(reading('stale', 27.0)),
        'The last water temperature reading is old; check the sensor is '
        'online.',
      );
      expect(
        SimplePondStatus.temperatureLine(reading('missing', null)),
        'No water temperature reading has arrived from the pond sensor.',
      );
    });

    test('pH line without a chemistry estimate says so', () {
      expect(
        SimplePondStatus.phLine(reading('invalid', 15.0), null),
        'The pH sensor sent a value outside its range; check the probe. '
        'No water chemistry estimate yet.',
      );
    });

    test('oxygen line labels the hypoxia flag as a model estimate', () {
      expect(
        SimplePondStatus.oxygenLine(
          HypoxiaFlag.fromJson({
            'level': 'watch',
            'flagged': true,
            'explanation': 'Warm, still night ahead.',
          }),
        ),
        'Low-oxygen risk is raised (model estimate). Warm, still night ahead.',
      );
      expect(
        SimplePondStatus.oxygenLine(HypoxiaFlag.fromJson({'level': 'none'})),
        'Low-oxygen risk is low (model estimate).',
      );
    });
  });
}
