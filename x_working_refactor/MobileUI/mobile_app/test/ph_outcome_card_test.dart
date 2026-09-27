// Round 3 regression tests: PhOutcomeCard now prefers the backend's already-
// computed WaterChemistryAssessment over recomputing an equivalent risk
// model client-side via PondHeuristics, falling back to the old heuristic
// only when the backend assessment isn't available.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/utils/pond_heuristics.dart';
import 'package:mobile_app/widgets/dashboard/ph_outcome_card.dart';

const _stableHistory = <PondSample>[];

void main() {
  group('resolvePhCardData', () {
    test('backend assessment present -> used verbatim, marked fromBackend', () {
      const assessment = WaterChemistryAssessment(
        status: 'Amber',
        category: 'Watch',
        tanPpm: 0.6,
        no2Ppm: 0.1,
        no3Ppm: 45.0,
        phReactivity: 0.02,
        reactivityTrend: 0.001,
        tdsTrend: 2.0,
        sensorWarnings: [],
        advisory: 'Some drift in waste load or buffering trend.',
        addHardenerNow: false,
        riskScore: 3,
      );

      final data = resolvePhCardData(
        backendAssessment: assessment,
        currentPh: 7.4,
        currentTds: 180,
        forecast2hr: 'Fair',
        rainfallMm: 0.0,
        telemetryHistory: _stableHistory,
      );

      expect(data.fromBackend, isTrue);
      expect(data.status, 'Amber');
      expect(data.category, 'Watch');
      expect(data.riskScore, 3);
      expect(data.phReactivity, 0.02);
      expect(data.addHardenerNow, false);
      expect(data.message, assessment.advisory);
      expect(data.severity, AdvisorySeverity.amber);
    });

    test('backend Red status maps to AdvisorySeverity.red', () {
      const assessment = WaterChemistryAssessment(
        status: 'Red', category: 'Nitrite Risk', tanPpm: 0, no2Ppm: 0.6,
        no3Ppm: 0, phReactivity: null, reactivityTrend: null, tdsTrend: null,
        sensorWarnings: [], advisory: 'x', addHardenerNow: false, riskScore: 0,
      );
      final data = resolvePhCardData(
        backendAssessment: assessment, currentPh: 7.4, currentTds: 180,
        forecast2hr: 'Fair', rainfallMm: 0.0, telemetryHistory: _stableHistory,
      );
      expect(data.severity, AdvisorySeverity.red);
    });

    test('null phReactivity from the backend (no trend data yet) becomes 0.0, not a crash', () {
      const assessment = WaterChemistryAssessment(
        status: 'Green', category: 'Stable', tanPpm: 0, no2Ppm: 0, no3Ppm: 0,
        phReactivity: null, reactivityTrend: null, tdsTrend: null,
        sensorWarnings: [], advisory: 'stable', addHardenerNow: false, riskScore: 0,
      );
      final data = resolvePhCardData(
        backendAssessment: assessment, currentPh: 7.4, currentTds: 180,
        forecast2hr: 'Fair', rainfallMm: 0.0, telemetryHistory: _stableHistory,
      );
      expect(data.phReactivity, 0.0);
    });

    test('backend assessment absent -> falls back to the unchanged client heuristic', () {
      final withBackend = resolvePhCardData(
        backendAssessment: null,
        currentPh: 7.4, currentTds: 180, forecast2hr: 'Fair', rainfallMm: 0.0,
        telemetryHistory: _stableHistory,
      );
      final direct = PondHeuristics.getPhAdvisory(
        ph: 7.4, tds: 180, forecast2hr: 'Fair', rainfallMm: 0.0,
        history: _stableHistory,
      );

      expect(withBackend.fromBackend, isFalse);
      expect(withBackend.status, direct.bufferAssessment.status);
      expect(withBackend.message, direct.message);
      expect(withBackend.severity, direct.severity);
    });
  });

  group('PhOutcomeCard widget', () {
    testWidgets('renders the backend category/message when a backend assessment is supplied', (tester) async {
      const assessment = WaterChemistryAssessment(
        status: 'Red', category: 'High Risk', tanPpm: 1.2, no2Ppm: 0.1,
        no3Ppm: 90, phReactivity: 0.03, reactivityTrend: 0.002, tdsTrend: 1.0,
        sensorWarnings: [], advisory: 'Consider a partial water change.',
        addHardenerNow: true, riskScore: 7,
      );

      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: PhOutcomeCard(
            sensorData: const {'pH': '7.1', 'TDS': '200'},
            forecastData: const {},
            telemetryHistory: _stableHistory,
            backendAssessment: assessment,
            onTap: () {},
          ),
        ),
      ));

      expect(find.text('High Risk'), findsOneWidget);
      expect(find.text('Consider a partial water change.'), findsOneWidget);
      expect(find.text('7'), findsOneWidget); // risk score
      expect(find.text('BUFFER NOW'), findsOneWidget); // addHardenerNow badge
    });

    testWidgets('falls back to the client heuristic category when no backend assessment is supplied', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: PhOutcomeCard(
            sensorData: const {'pH': '7.4', 'TDS': '180'},
            forecastData: const {},
            telemetryHistory: _stableHistory,
            onTap: () {},
          ),
        ),
      ));

      // Empty history -> PondHeuristics' "Insufficient History" fallback category.
      expect(find.text('Insufficient History'), findsOneWidget);
    });
  });
}
