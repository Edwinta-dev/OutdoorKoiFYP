import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/main.dart';
import 'package:mobile_app/screens/config_error_screen.dart';
import 'package:mobile_app/screens/dashboard_view.dart';
import 'package:mobile_app/screens/detail_graph_screen.dart';
import 'package:mobile_app/screens/fish_tips_view.dart';
import 'package:mobile_app/screens/main_layout.dart';
import 'package:mobile_app/screens/onboarding_screen.dart';
import 'package:mobile_app/screens/settings_view.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:mobile_app/widgets/modals/quick_log_modals.dart';
import 'package:mobile_app/widgets/modals/nea_full_forecast_modal.dart';
import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/widgets/detail_graph/algae_severity_rating_card.dart';
import 'package:mobile_app/utils/pond_camera_storage.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

void main() {
  test('colour literals and inline TextStyles stay in lib/theme', () {
    final violations = <String>[];
    for (final file in Directory(
      'lib',
    ).listSync(recursive: true).whereType<File>()) {
      if (!file.path.endsWith('.dart') ||
          file.path.replaceAll('\\', '/').contains('/theme/')) {
        continue;
      }
      if (RegExp(r'Color\(0x|TextStyle\(').hasMatch(file.readAsStringSync())) {
        violations.add(file.path);
      }
    }
    expect(violations, isEmpty);
  });

  test('light and dark use the same colour roles and type scale', () {
    for (final theme in [AppTheme.light, AppTheme.dark]) {
      final c = AppColors(theme.brightness);
      expect(theme.scaffoldBackgroundColor, c.canvas);
      expect(theme.colorScheme.primary, c.primary);
      expect(theme.colorScheme.surface, c.surface);
      expect(theme.colorScheme.onSurface, c.text);
      expect(theme.textTheme.bodyMedium!.fontSize, AppType.body);
      expect(
        theme.appBarTheme.systemOverlayStyle!.systemNavigationBarColor,
        c.navigation,
      );
      for (final background in [
        c.info,
        c.feeding,
        c.warning,
        c.water,
        c.primary,
      ]) {
        final fg = c.foregroundOn(background).computeLuminance();
        final bg = background.computeLuminance();
        expect(
          fg > bg ? (fg + .05) / (bg + .05) : (bg + .05) / (fg + .05),
          greaterThanOrEqualTo(4.5),
        );
      }
      // Primary text and secondary copy remain legible on each surface.
      for (final text in [c.text, c.textSecondary, c.textMuted]) {
        final a = text.computeLuminance();
        final b = c.surface.computeLuminance();
        expect(
          (a > b ? (a + .05) / (b + .05) : (b + .05) / (a + .05)),
          greaterThanOrEqualTo(4.5),
        );
      }
    }
  });

  testWidgets('app follows platform brightness and preserves 1.6 text scale', (
    tester,
  ) async {
    tester.platformDispatcher.platformBrightnessTestValue = Brightness.light;
    tester.platformDispatcher.textScaleFactorTestValue = 1.6;
    addTearDown(tester.platformDispatcher.clearAllTestValues);
    await tester.pumpWidget(
      const ConfigErrorApp(problems: ['Missing settings']),
    );
    var context = tester.element(find.byType(ConfigErrorScreen));
    expect(Theme.of(context).brightness, Brightness.light);
    expect(MediaQuery.textScalerOf(context).scale(10), 16);
    tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
    await tester.pumpAndSettle();
    context = tester.element(find.byType(ConfigErrorScreen));
    expect(Theme.of(context).brightness, Brightness.dark);
    // Both boot paths have the same system theme policy.
    final app =
        const KoiMonitorApp(isOnboarded: false).build(context) as MaterialApp;
    expect(app.themeMode, ThemeMode.system);
    expect(app.theme!.brightness, Brightness.light);
    expect(app.darkTheme!.brightness, Brightness.dark);
    expect(tester.takeException(), isNull);
  });

  testWidgets('every status has a distinct icon, word and semantic label', (
    tester,
  ) async {
    final semantics = tester.ensureSemantics();

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: Column(
            children: [
              for (final status in PondStatus.values)
                StatusChip(status: status),
            ],
          ),
        ),
      ),
    );
    for (final status in PondStatus.values) {
      expect(find.text(status.word), findsOneWidget);
      expect(find.byIcon(status.icon), findsOneWidget);
      expect(
        find.bySemanticsLabel('${status.word}: ${status.word}'),
        findsOneWidget,
      );
    }
    expect(PondStatus.fromWire('Red'), PondStatus.danger);
    expect(PondStatus.fromWire('Amber'), PondStatus.warning);
    expect(PondStatus.fromWire('Green'), PondStatus.healthy);
    expect(PondStatus.fromWire('missing'), PondStatus.unknown);
    expect(PondStatus.forDays(0), PondStatus.danger);
    expect(PondStatus.forDays(3), PondStatus.warning);
    expect(PondStatus.forDays(null), PondStatus.healthy);
    semantics.dispose();
  });

  testWidgets('shared states wrap copy and keep retry and outcome tap usable', (
    tester,
  ) async {
    var retries = 0;
    var taps = 0;
    await pumpScreen(
      tester,
      Scaffold(
        body: SingleChildScrollView(
          child: Column(
            children: [
              OutcomeCardShell(
                onTap: () => taps++,
                child: const Text('Open pond detail'),
              ),
              const MetricTile(
                label: 'Water temperature',
                value: '27.4 °C',
                status: PondStatus.healthy,
              ),
              const PondEmptyState(
                title: 'No pond readings yet',
                message: 'Readings appear after the sensor reports.',
              ),
              PondErrorState(
                title: 'Pond readings unavailable',
                message: 'Check the connection and try again.',
                onRetry: () => retries++,
              ),
            ],
          ),
        ),
      ),
      size: const Size(320, 1000),
      textScale: 1.6,
    );
    await tester.tap(find.text('Open pond detail'));
    await tester.tap(find.text('Retry'));
    expect(taps, 1);
    expect(retries, 1);
    expect(find.text('27.4 °C'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  for (final brightness in Brightness.values) {
    for (final modal in ['actions', 'weather']) {
      testWidgets('$modal modal at 1.6 text scale in $brightness', (
        tester,
      ) async {
        await pumpScreen(
          tester,
          Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () => modal == 'actions'
                    ? showQuickActionSelector(context)
                    : showNeaFullForecastModal(
                        context,
                        Fixtures.dashboardPayload()['nea_forecasts'],
                      ),
                child: const Text('Open'),
              ),
            ),
          ),
          brightness: brightness,
          textScale: 1.6,
          size: const Size(320, 640),
        );
        await tester.tap(find.text('Open'));
        await tester.pumpAndSettle();
        expect(
          find.text(
            modal == 'actions'
                ? 'Log Pond Intervention'
                : 'Singapore Environmental Outlook',
          ),
          findsOneWidget,
        );
        expect(tester.takeException(), isNull);
      });
    }
    testWidgets('species error offers a working retry in $brightness', (
      tester,
    ) async {
      final source = FakePondDataSource(failing: {'fetchFishProfiles'});
      await pumpScreen(
        tester,
        const Scaffold(body: FishTipsView()),
        source: source,
        brightness: brightness,
        textScale: 1.6,
        size: const Size(320, 640),
      );
      await settle(tester);
      expect(find.byType(PondErrorState), findsOneWidget);
      source.failing.clear();
      await tester.tap(find.text('Retry'));
      await settle(tester);
      expect(find.byType(PondEmptyState), findsOneWidget);
      expect(source.callsTo('fetchFishProfiles'), hasLength(2));
      expect(tester.takeException(), isNull);
    });
  }

  for (final brightness in Brightness.values) {
    for (final kind in [
      'SALT',
      'FILTER_CLEAN',
      'WATER_CHANGE',
      'WATER_TOPUP',
      'ALGAE_SCRUB',
    ]) {
      testWidgets('$kind log sheet in $brightness at 1.6', (tester) async {
        await pumpScreen(
          tester,
          Scaffold(
            body: InterventionLogSheet(
              eventType: kind,
              title: 'Pond maintenance',
              accentColor: AppColors(brightness).info,
              initialTimestamp: Fixtures.now,
            ),
          ),
          brightness: brightness,
          textScale: 1.6,
          size: const Size(320, 640),
        );
        await settle(tester);
        final save = find.text('Save Pond maintenance');
        await tester.ensureVisible(save);
        expect(save.hitTestable(), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }
  }

  for (final brightness in Brightness.values) {
    testWidgets('camera overlay stays legible in $brightness at 1.6', (
      tester,
    ) async {
      // Flutter tests replace HTTP with a 400 response; this reserved URL never
      // contacts a camera or storage service, and exercises the failed-photo UI.
      await pumpScreen(
        tester,
        const Scaffold(
          body: SingleChildScrollView(
            child: AlgaeSeverityRatingCard(userId: 7),
          ),
        ),
        brightness: brightness,
        textScale: 1.6,
        size: const Size(320, 800),
        source: FakePondDataSource(
          frame: PondCameraFrame(
            id: 501,
            imageUrl: 'https://camera.invalid/frame.jpg',
            greenRatio: .12,
            capturedAt: Fixtures.now,
            state: 'obstruction',
          ),
          ratingContext: Fixtures.ratingContext(),
        ),
      );
      await settle(tester);
      final cameraLabel = tester.widget<Text>(find.text('camera reads 12.00%'));
      expect(cameraLabel.style!.color, AppColors(brightness).onImage);
      final blocked = tester.widget<Text>(find.text('view may be blocked'));
      expect(blocked.style!.color, AppColors(brightness).warningOnImage);
      expect(tester.takeException(), isNull);
    });
  }

  final screens = <String, Widget Function()>{
    'dashboard': () => const DashboardView(),
    'main layout': () => const MainLayout(),
    'onboarding': () => const OnboardingScreen(),
    'settings': () => const SettingsView(),
    'species': () => const FishTipsView(),
    'temperature detail': () => const DetailGraphScreen(
      metricType: 'temperature',
      title: 'Detailed Temperature',
    ),
    'pH detail': () => const DetailGraphScreen(
      metricType: 'ph',
      title: 'pH & Buffer Stability',
    ),
    'algae detail': () => const DetailGraphScreen(
      metricType: 'lux',
      title: 'Solar & Algae Risk Analysis',
    ),
    'feeding sheet': () => Scaffold(
      body: InterventionLogSheet(
        eventType: 'FEEDING',
        title: 'Feeding Session',
        accentColor: const AppColors(Brightness.dark).feeding,
        initialTimestamp: Fixtures.now,
      ),
    ),
  };
  for (final brightness in Brightness.values) {
    for (final scale in [1.0, 1.3, 1.6]) {
      for (final entry in screens.entries) {
        testWidgets('${entry.key}: $brightness at $scale on a narrow phone', (
          tester,
        ) async {
          await pumpScreen(
            tester,
            entry.value(),
            brightness: brightness,
            textScale: scale,
            size: const Size(320, 800),
            source: _FullSource(
              historicalPayload: Fixtures.historicalPayload(),
              assessment: Fixtures.assessment(),
              evaporationForecast: Fixtures.evaporationForecast(),
              algaeForecast: Fixtures.algaeForecast(),
              frame: Fixtures.frame(),
              ratingContext: Fixtures.ratingContext(),
            ),
          );
          await settle(tester);
          expect(tester.takeException(), isNull);
        });
      }
    }
  }
}

class _FullSource extends FakePondDataSource {
  _FullSource({
    super.historicalPayload,
    super.assessment,
    super.evaporationForecast,
    super.algaeForecast,
    super.frame,
    super.ratingContext,
  });
  @override
  Future<List<Map<String, dynamic>>> fetchFishProfiles(
    int pondId,
    List<String> species,
  ) async => [
    {
      'Title': 'Japanese Koi (Kohaku)',
      'Common Name': 'Koi carp',
      'Care Level': 'Intermediate',
      'Quantity': 5,
      'Image URL': '',
      'Maximum Size': '90 cm',
      'Lifespan': '25 years',
      'pH': '6.8–8.2',
      'Temperature': '24–28 °C',
      'Behaviour': 'Keep with other koi and give them room to swim.',
      'Tank Region': 'All levels',
      'Gender': 'Not identified',
    },
  ];
}
