import 'package:mobile_app/theme/app_theme.dart';
// Builds any screen or widget with a fake data source and seeded
// SharedPreferences, so widget tests never reach Supabase or the
// DigitalTwin service.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/pond_data_source.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/data/local_profile_repository.dart';

import 'fake_pond_data_source.dart';

/// The SharedPreferences an onboarded device holds.
const Map<String, Object> onboardedPrefs = {
  'isOnboarded': true,
  'userID': '7',
  'tankVolume': '1200',
  'ownedFishSpecies': <String>['Japanese Koi (Kohaku)'],
  'fishCount': '5',
};

/// A portrait phone viewport, tall enough that whole screens lay out
/// without scrolling assertions depending on device size.
const Size phoneSize = Size(412, 1400);

/// Wraps [child] in the data scope and a dark MaterialApp.
Widget wrapWithSource(
  Widget child,
  PondDataSource source, {
  Map<String, Object> prefs = onboardedPrefs,
  Brightness brightness = Brightness.dark,
  double textScale = 1,
}) => ProviderScope(
  retry: (_, _) => null,
  overrides: [
    pondDataSourceProvider.overrideWithValue(source),
    localProfileRepositoryProvider.overrideWithValue(
      FakeLocalProfileRepository(prefs),
    ),
  ],
  child: MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: brightness == Brightness.dark ? AppTheme.dark : AppTheme.light,
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(
        context,
      ).copyWith(textScaler: TextScaler.linear(textScale)),
      child: child!,
    ),
    home: child,
  ),
);

/// Seeds SharedPreferences, sizes the viewport and pumps [screen] with
/// [source]. Returns the source so tests can inspect its calls.
/// Pumps one frame only: the caller decides whether to settle futures
/// (pump) or observe the loading state first.
Future<FakePondDataSource> pumpScreen(
  WidgetTester tester,
  Widget screen, {
  FakePondDataSource? source,
  Map<String, Object> prefs = onboardedPrefs,
  Size size = phoneSize,
  Brightness brightness = Brightness.dark,
  double textScale = 1,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final fake = source ?? FakePondDataSource();
  await tester.pumpWidget(
    wrapWithSource(
      screen,
      fake,
      prefs: prefs,
      brightness: brightness,
      textScale: textScale,
    ),
  );
  return fake;
}

/// Lets pending fake futures and SharedPreferences reads resolve without
/// pumpAndSettle, which would never return while a progress indicator
/// is spinning.
Future<void> settle(WidgetTester tester, {int frames = 5}) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}
