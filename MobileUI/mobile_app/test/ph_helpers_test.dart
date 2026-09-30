// Round 1 characterization tests for PhHelpers - the client-side pH/buffer
// risk model that duplicates (with different math/units) WaterChemistryEngine
// in Backend/DigitalTwin/engine.py. See the Round 3 notes for why that
// duplication matters; these tests just pin down PhHelpers' CURRENT behavior
// first, since nothing in the real project tested it at all.
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/helpers/ph_helpers.dart';
import 'package:mobile_app/utils/pond_heuristics.dart';

/// [days] days of 12 samples each, daytime-only (lux triangle peaking at
/// noon so every day clears the maxLux>=500 "day counts" gate), with pH
/// oscillating between basePh and basePh + swings[dayIndex] within each day.
List<PondSample> _buildHistory({
  required List<double> swings,
  double basePh = 7.4,
  double tds = 180.0,
}) {
  final t0 = DateTime(2026, 1, 1, 0);
  final samples = <PondSample>[];
  for (int d = 0; d < swings.length; d++) {
    for (int h = 0; h < 12; h++) {
      final time = t0.add(Duration(days: d, hours: h));
      final triangle = (h <= 6) ? h / 6.0 : (12 - h) / 6.0; // 0..1..0
      final lux = 2000.0 + triangle * 10000.0;
      final ph = basePh + triangle * swings[d];
      samples.add(PondSample(time, ph, tds, 28.0, lux));
    }
  }
  return samples;
}

void main() {
  group('calculatePhRateOfChange', () {
    test('fewer than 2 samples -> 0.0', () {
      expect(PhHelpers.calculatePhRateOfChange([]), 0.0);
      expect(
        PhHelpers.calculatePhRateOfChange([
          {'timestamp': '2026-01-01T00:00:00Z', 'value': 7.4},
        ]),
        0.0,
      );
    });

    test('sorts out-of-order input and computes pH/hour over the full span', () {
      final rate = PhHelpers.calculatePhRateOfChange([
        {'timestamp': '2026-01-01T02:00:00Z', 'value': 7.0}, // out of order
        {'timestamp': '2026-01-01T00:00:00Z', 'value': 7.4},
      ]);
      // (7.0 - 7.4) / 2h = -0.2 /hr, using earliest as "first" regardless of input order
      expect(rate, closeTo(-0.2, 1e-9));
    });
  });

  group('evaluateRainVulnerabilityRefined - insufficient history', () {
    test('< 24 samples -> Unknown / Insufficient History, uses latest sample as fallback', () {
      final history = _buildHistory(swings: [0.1]); // 12 samples only
      final result = PhHelpers.evaluateRainVulnerabilityRefined(
        history: history, rainIncoming: false,
      );
      expect(result.status, 'Unknown');
      expect(result.category, 'Insufficient History');
      expect(result.nightLowPh, history.last.ph);
      expect(result.estimatedDGH, history.last.tds / 20.0);
      expect(result.riskScore, 0);
    });

    test('empty history -> Unknown with the hardcoded 7.4/180.0 defaults', () {
      final result = PhHelpers.evaluateRainVulnerabilityRefined(
        history: const [], rainIncoming: false,
      );
      expect(result.status, 'Unknown');
      expect(result.nightLowPh, 7.4);
      expect(result.estimatedDGH, 180.0 / 20.0);
    });

    test('>=24 samples but fewer than 3 days have >=12 samples -> "need 3 valid days"', () {
      // 4 days x 6 samples/day = 24 samples total (clears the first gate),
      // but no single day reaches the 12-sample minimum to be "valid".
      final t0 = DateTime(2026, 1, 1);
      final history = [
        for (int d = 0; d < 4; d++)
          for (int h = 0; h < 6; h++)
            PondSample(t0.add(Duration(days: d, hours: h)), 7.4, 180.0, 28.0, 5000.0),
      ];
      expect(history.length, 24);
      final result = PhHelpers.evaluateRainVulnerabilityRefined(
        history: history, rainIncoming: false,
      );
      expect(result.status, 'Unknown');
      expect(result.advisory, contains('3 valid days'));
    });
  });

  group('evaluateRainVulnerabilityRefined - scored scenarios', () {
    test('flat, tiny daily pH swing and no rain -> Green / Stable, risk 0', () {
      final history = _buildHistory(swings: [0.05, 0.05, 0.05, 0.05]);
      final result = PhHelpers.evaluateRainVulnerabilityRefined(
        history: history, rainIncoming: false,
      );
      expect(result.status, 'Green');
      expect(result.category, 'Stable Buffer');
      expect(result.riskScore, 0);
      expect(result.addHardenerNow, false);
    });

    test(
      'sharply rising daily pH swing + heavy rain incoming -> Red / High Rain-Crash Risk',
      () {
        final history = _buildHistory(swings: [0.1, 1.0, 2.5, 4.0]);
        final result = PhHelpers.evaluateRainVulnerabilityRefined(
          history: history, rainIncoming: true, rainIntensity: 'heavy',
        );
        expect(result.status, 'Red');
        expect(result.category, 'High Rain-Crash Risk');
        expect(result.addHardenerNow, true);
        expect(result.riskScore, greaterThanOrEqualTo(6));
      },
    );

    test('larger daily swing produces higher-or-equal reactivity than a smaller one '
        '(monotonicity, not a magic number)', () {
      // 4 raw days needed: _groupByDay's 6h shift splits each raw day across
      // two shifted-day buckets (6 samples each), so only the 3 "seams"
      // between 4 raw days actually reach the 12-samples-per-day minimum.
      final calm = PhHelpers.evaluateRainVulnerabilityRefined(
        history: _buildHistory(swings: [0.05, 0.05, 0.05, 0.05]), rainIncoming: false,
      );
      final volatile = PhHelpers.evaluateRainVulnerabilityRefined(
        history: _buildHistory(swings: [1.0, 1.0, 1.0, 1.0]), rainIncoming: false,
      );
      expect(volatile.phReactivity, greaterThan(calm.phReactivity));
      expect(volatile.riskScore, greaterThanOrEqualTo(calm.riskScore));
    });
  });

  group('evaluatePhAdvisory', () {
    final stableHistory = _buildHistory(swings: [0.05, 0.05, 0.05, 0.05]);

    test('ground-truth pH outside target bounds -> red, regardless of buffer state', () {
      final r = PhHelpers.evaluatePhAdvisory(
        ph: 9.0, tds: 180, rainIncoming: false,
        maxTargetpH: 8.5, minTargetpH: 6.8,
        history: stableHistory,
      );
      expect(r.severity, AdvisorySeverity.red);
      expect(r.message, contains('9.00'));
    });

    test('rapid pH drop (dpH/dt < -0.15) -> amber even with a stable buffer', () {
      final r = PhHelpers.evaluatePhAdvisory(
        ph: 7.4, tds: 180, rainIncoming: false,
        maxTargetpH: 8.5, minTargetpH: 6.8,
        history: stableHistory,
        phTelemetry: [
          {'timestamp': '2026-01-05T00:00:00Z', 'value': 7.6},
          {'timestamp': '2026-01-05T01:00:00Z', 'value': 7.2}, // -0.4/hr
        ],
      );
      expect(r.severity, AdvisorySeverity.amber);
      expect(r.message, contains('Rapid pH drop'));
    });

    test('rain incoming + low pH/TDS -> amber acid-crash warning', () {
      final r = PhHelpers.evaluatePhAdvisory(
        ph: 7.0, tds: 100, rainIncoming: true,
        maxTargetpH: 8.5, minTargetpH: 6.8,
        history: stableHistory,
      );
      expect(r.severity, AdvisorySeverity.amber);
      expect(r.message, contains('acid crash'));
    });

    test('everything nominal -> severity none, stable message', () {
      final r = PhHelpers.evaluatePhAdvisory(
        ph: 7.4, tds: 180, rainIncoming: false,
        maxTargetpH: 8.5, minTargetpH: 6.8,
        history: stableHistory,
      );
      expect(r.severity, AdvisorySeverity.none);
      expect(r.bufferAssessment.status, 'Green');
    });
  });
}
