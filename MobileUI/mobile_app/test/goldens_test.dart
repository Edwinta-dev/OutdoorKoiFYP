// Golden images of the three dashboard outcome cards and the log sheet.
// Font and comparator setup: test/flutter_test_config.dart.
//
// The goldens are recorded on Linux, as CI runs them: text anti-aliasing
// on Windows differs by 3-6 % of pixels, so this file is skipped there.
// Regenerate after an intended visual change, from MobileUI/mobile_app:
//   docker run --rm -v "$PWD:/app" -w /app ghcr.io/cirruslabs/flutter:<CI version> \
//     flutter test --update-goldens test/goldens_test.dart
// and review the PNG diff before committing.
@TestOn('linux')
library;

import 'package:flutter/material.dart';
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
  Color background = const Color(0xFF070B12),
  double height = 400,
}) async {
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
      matchesGoldenFile('goldens/temperature_outcome_card.png'),
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
    await expectLater(target, matchesGoldenFile('goldens/ph_outcome_card.png'));
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
      matchesGoldenFile('goldens/solar_outcome_card.png'),
    );
  });

  testWidgets('feeding log sheet', (tester) async {
    final target = await _pumpGolden(
      tester,
      InterventionLogSheet(
        eventType: 'FEEDING',
        title: 'Feeding Session',
        accentColor: Colors.orangeAccent,
        initialTimestamp: Fixtures.now,
      ),
      background: const Color(0xFF131B2A),
      height: 520,
    );
    await expectLater(
      target,
      matchesGoldenFile('goldens/feeding_log_sheet.png'),
    );
  });
}
