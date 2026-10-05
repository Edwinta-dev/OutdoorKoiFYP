// Golden images of the three dashboard outcome cards and the log sheet.
// Font and comparator setup: test/flutter_test_config.dart.
//
// The goldens are recorded on Linux, as CI runs them: text anti-aliasing
// on Windows differs by 3-6 % of pixels, so this file is skipped there.
// Regenerate after an intended visual change with tools/update_goldens.py
// (Docker, the Flutter version CI pins; see docs/dev.md) and review the
// PNG diff before committing.
@TestOn('linux')
library;

import 'package:flutter/material.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/pond_heuristics.dart';
import 'package:mobile_app/widgets/dashboard/ph_outcome_card.dart';
import 'package:mobile_app/widgets/dashboard/solar_outcome_card.dart';
import 'package:mobile_app/widgets/dashboard/temperature_outcome_card.dart';
import 'package:mobile_app/widgets/modals/quick_log_modals.dart';

import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

const _cardWidth = 372.0; // 412 dp phone minus the dashboard's 20 dp padding

/// Pumps [child] at a fixed width with the dashboard background, decodes
/// every Image.asset in it (so the golden never catches a half-loaded
/// image) and returns a finder for the boundary to capture.
Future<Finder> _pumpGolden(
  WidgetTester tester,
  Widget child, {
  Color? background,
  Brightness brightness = Brightness.dark,
  double textScale = 1,
  double height = 400,
}) async {
  background ??= AppColors(brightness).canvas;
  await pumpScreen(
    tester,
    Scaffold(
      backgroundColor: background,
      body: Align(
        alignment: Alignment.topCenter,
        child: RepaintBoundary(
          key: const ValueKey('golden'),
          child: Container(
            color: background,
            width: _cardWidth,
            padding: const EdgeInsets.all(8),
            // Unbounded height, so the capture is as tall as the content.
            child: Column(mainAxisSize: MainAxisSize.min, children: [child]),
          ),
        ),
      ),
    ),
    size: Size(_cardWidth + 40, height),
    brightness: brightness,
    textScale: textScale,
  );
  await tester.runAsync(() async {
    for (final element in find.byType(Image).evaluate()) {
      final image = element.widget as Image;
      await precacheImage(image.image, element);
    }
  });
  await tester.pumpAndSettle();
  return find.byKey(const ValueKey('golden'));
}

String _goldenPath(String name) => 'goldens/$name';

void main() {
  final payload = Fixtures.dashboardPayload();
  final sensor = payload['raw_sensor'] as Map<String, dynamic>;
  final forecast = payload['nea_forecasts'] as Map<String, dynamic>;
  final telemetry = payload['nea_telemetry'] as Map<String, dynamic>;

  testWidgets('temperature outcome card', (tester) async {
    final target = await _pumpGolden(
      tester,
      TemperatureOutcomeCard(
        sensorData: sensor,
        telemetryData: telemetry,
        forecastData: forecast,
        targetMinTemp: 24,
        targetMaxTemp: 28,
        onTap: () {},
      ),
    );
    await expectLater(
      target,
      matchesGoldenFile(_goldenPath('temperature_outcome_card.png')),
    );
  });

  testWidgets('pH outcome card with a backend assessment', (tester) async {
    final history = [
      for (final s in payload['telemetry_history'] as List)
        PondSample(
          DateTime.parse(s['time'] as String),
          (s['ph'] as num).toDouble(),
          (s['tds'] as num).toDouble(),
          (s['tempC'] as num).toDouble(),
          (s['lux'] as num).toDouble(),
        ),
    ];
    final target = await _pumpGolden(
      tester,
      PhOutcomeCard(
        sensorData: sensor,
        forecastData: forecast,
        telemetryHistory: history,
        backendAssessment: Fixtures.assessment(),
        onTap: () {},
      ),
    );
    await expectLater(
      target,
      matchesGoldenFile(_goldenPath('ph_outcome_card.png')),
    );
  });

  testWidgets('solar outcome card', (tester) async {
    final target = await _pumpGolden(
      tester,
      SolarOutcomeCard(
        sensorData: sensor,
        forecastData: forecast,
        onTap: () {},
      ),
    );
    await expectLater(
      target,
      matchesGoldenFile(_goldenPath('solar_outcome_card.png')),
    );
  });

  testWidgets('feeding log sheet', (tester) async {
    final target = await _pumpGolden(
      tester,
      InterventionLogSheet(
        eventType: 'FEEDING',
        title: 'Feeding Session',
        accentColor: const AppColors(Brightness.dark).feeding,
        initialTimestamp: Fixtures.now,
      ),
      background: const AppColors(Brightness.dark).surface,
      height: 520,
    );
    await expectLater(
      target,
      matchesGoldenFile(_goldenPath('feeding_log_sheet.png')),
    );
  });
  for (final brightness in Brightness.values) {
    for (final scale in [1.0, 1.6]) {
      testWidgets('shared components $brightness at $scale', (tester) async {
        final target = await _pumpGolden(
          tester,
          Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionHeader(
                title: 'Pond readings',
                icon: Icons.water_drop_outlined,
              ),
              const MetricTile(label: 'Water temperature', value: '27.4 °C'),
              const SizedBox(height: AppSpace.sm),
              const MetricTile(
                label: 'Estimated water loss',
                value: '8.4 L',
                estimate: true,
              ),
              const SizedBox(height: AppSpace.sm),
              Wrap(
                spacing: AppSpace.sm,
                runSpacing: AppSpace.sm,
                children: [
                  for (final status in PondStatus.values)
                    StatusChip(status: status),
                ],
              ),
              const SizedBox(height: AppSpace.sm),
              const OutcomeCardShell(
                child: PondEmptyState(
                  title: 'No readings yet',
                  message: 'Readings appear after the sensor reports.',
                ),
              ),
              const SizedBox(height: AppSpace.sm),
              OutcomeCardShell(
                child: PondErrorState(
                  title: 'Pond readings unavailable',
                  message: 'Check the connection and try again.',
                  onRetry: () {},
                ),
              ),
            ],
          ),
          brightness: brightness,
          textScale: scale,
          height: 1400,
        );
        await expectLater(
          target,
          matchesGoldenFile(
            _goldenPath(
              'shared_${brightness.name}_${scale.toStringAsFixed(1)}.png',
            ),
          ),
        );
      });
    }
  }
}
