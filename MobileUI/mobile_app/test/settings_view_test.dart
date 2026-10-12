// SettingsView's display mode switch: simple by default, saved to the
// local profile, and the dashboard follows it.
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/local_profile_repository.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/screens/dashboard_view.dart';
import 'package:mobile_app/screens/settings_view.dart';
import 'package:mobile_app/widgets/dashboard/temperature_outcome_card.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/pump_screen.dart';

class _FailingSaveRepository extends FakeLocalProfileRepository {
  _FailingSaveRepository(super.values);
  @override
  Future<void> save(Map<String, Object> values) async =>
      throw StateError('disk full');
}

final _switch = find.byKey(const ValueKey('displayModeSwitch'));

bool _isOn(WidgetTester tester) => tester.widget<SwitchListTile>(_switch).value;

Future<void> _pumpWithRepo(
  WidgetTester tester,
  LocalProfileRepository repo,
  Widget home,
) async {
  tester.view.physicalSize = phoneSize;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      retry: (_, _) => null,
      overrides: [
        pondDataSourceProvider.overrideWithValue(FakePondDataSource()),
        localProfileRepositoryProvider.overrideWithValue(repo),
      ],
      child: MaterialApp(home: home),
    ),
  );
}

void main() {
  testWidgets('a new account starts in the simple view', (tester) async {
    await pumpScreen(tester, const SettingsView());
    await settle(tester);

    expect(_isOn(tester), isFalse);
    expect(
      find.text(
        'The dashboard shows pond status, what to do today and plain '
        'explanations.',
      ),
      findsOneWidget,
    );
  });

  testWidgets('a stored detailed choice is shown on the switch', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      const SettingsView(),
      prefs: {...onboardedPrefs, displayModeKey: 'detailed'},
    );
    await settle(tester);

    expect(_isOn(tester), isTrue);
  });

  testWidgets('turning on Detailed view saves it and the dashboard follows', (
    tester,
  ) async {
    final repo = FakeLocalProfileRepository(onboardedPrefs);
    await _pumpWithRepo(
      tester,
      repo,
      const Column(
        children: [
          Expanded(child: SettingsView()),
          Expanded(child: DashboardView()),
        ],
      ),
    );
    await settle(tester);
    expect(find.byType(SimplePondStatus), findsOneWidget);
    expect(find.byType(TemperatureOutcomeCard), findsNothing);

    await tester.tap(_switch);
    await settle(tester);

    expect(_isOn(tester), isTrue);
    expect(repo.values[displayModeKey], 'detailed');
    expect(find.byType(SimplePondStatus), findsNothing);
    expect(find.byType(TemperatureOutcomeCard), findsOneWidget);

    await tester.tap(_switch);
    await settle(tester);
    expect(_isOn(tester), isFalse);
    expect(repo.values[displayModeKey], 'simple');
    expect(find.byType(SimplePondStatus), findsOneWidget);

    // DashboardView polls on a 30 s Timer; unmount to cancel it.
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('a failed save puts the switch back and says so', (tester) async {
    await _pumpWithRepo(
      tester,
      _FailingSaveRepository(onboardedPrefs),
      const SettingsView(),
    );
    await settle(tester);

    await tester.tap(_switch);
    await settle(tester);

    expect(_isOn(tester), isFalse);
    expect(
      find.text('Could not save the display setting. Try again.'),
      findsOneWidget,
    );
  });
}
