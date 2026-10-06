// The first-run flow: onboarding -> dashboard -> log a feed, run against
// a FakePondDataSource. Shared by test/onboarding_flow_test.dart (runs in
// `flutter test`, so CI covers it) and integration_test/app_flow_test.dart
// (runs the same steps on a device).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/data/local_profile_repository.dart';
import 'package:mobile_app/main.dart';
import 'package:mobile_app/screens/dashboard_view.dart';
import 'package:mobile_app/screens/onboarding_screen.dart';

import '../helpers/fake_pond_data_source.dart';
import '../helpers/fixtures.dart';

/// Pumps a few frames. pumpAndSettle cannot be used once the main layout
/// is up: screens behind the IndexedStack keep progress indicators
/// spinning.
Future<void> _frames(WidgetTester tester, [int n = 10]) async {
  for (var i = 0; i < n; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> _tapVisible(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pump();
  await tester.tap(finder);
}

Future<void> runOnboardingToFeedFlow(WidgetTester tester) async {
  final local = FakeLocalProfileRepository();
  final fake = FakePondDataSource(
    assessment: Fixtures.assessment(),
    eventAssessment: Fixtures.assessment(),
  );

  await tester.pumpWidget(
    ProviderScope(
      retry: (_, _) => null,
      overrides: [
        pondDataSourceProvider.overrideWithValue(fake),
        localProfileRepositoryProvider.overrideWithValue(local),
      ],
      child: const KoiMonitorApp(isOnboarded: false),
    ),
  );
  await tester.pumpAndSettle();

  // --- Onboarding ---
  expect(find.byType(OnboardingScreen), findsOneWidget);
  expect(fake.callsTo('fetchSpeciesNames'), hasLength(1));

  await tester.enterText(
    find.widgetWithText(TextField, 'Tank Volume (Liters)'),
    '1200',
  );
  // Manual location, so the flow never asks the device for GPS.
  await _tapVisible(tester, find.byType(SwitchListTile));
  await tester.pumpAndSettle();
  await tester.enterText(
    find.widgetWithText(TextField, 'Manual Location Input (Postal Code)'),
    '018956',
  );
  await tester.enterText(
    find.widgetWithText(TextField, 'Find user ID on the hardware for syncing'),
    '7',
  );
  await _tapVisible(tester, find.text('Save & Go to Dashboard'));
  await _frames(tester);

  final profile = fake.callsTo('upsertUserProfile').single.args;
  expect(profile['userID'], '7');
  expect(profile['volume'], '1200');
  expect(profile['manualpostallocation'], 18956);
  expect((await local.load()).isOnboarded, isTrue);
  expect((await local.load()).pondId, 7);

  // --- Dashboard ---
  expect(find.byType(OnboardingScreen), findsNothing);
  expect(find.byType(DashboardView), findsOneWidget);
  expect(find.text('Pond Dashboard Centre'), findsOneWidget);
  expect(find.text('TEMPERATURE & FEED MONITOR'), findsOneWidget);
  expect(fake.callsTo('fetchDashboardPayload').last.args['userId'], '7');
  expect(find.text(Fixtures.assessment().advisory), findsOneWidget);

  // --- Log a feed ---
  await tester.tap(find.text('Log'));
  await _frames(tester, 5);
  await tester.tap(find.text('Feeding Session'));
  await _frames(tester, 5);
  expect(find.text('Log Feeding Session'), findsOneWidget);

  await tester.enterText(
    find.widgetWithText(TextFormField, 'Food per session (g)'),
    '20',
  );
  await _tapVisible(tester, find.text('Save Feeding Session'));
  await _frames(tester);

  final insert = fake.callsTo('insertIntervention').single.args;
  expect(insert['userID'], 7);
  expect(insert['event_type'], 'FEEDING');
  expect(insert['food_grams'], 20.0);
  final push = fake.callsTo('logFeeding').single.args;
  expect(push['userId'], 7);
  expect(push['foodGrams'], 20.0);
  expect(find.text('Log Feeding Session'), findsNothing);
  expect(
    find.text('Feeding Session logged • water buffer status: Watch'),
    findsOneWidget,
  );

  // Unmount so the dashboard's poll timer is cancelled.
  await tester.pumpWidget(const SizedBox.shrink());
}
