// Round 1 characterization tests for TempHelpers.evaluateTemperatureAdvisory.
// The real project had zero working tests (test/widget_test.dart didn't even
// compile - KoiMonitorApp's constructor drifted from it). These pin down
// current behavior before any refactor touches this file.
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/helpers/temp_helpers.dart';
import 'package:mobile_app/utils/pond_heuristics.dart';

void main() {
  const minT = 24.0, maxT = 28.0;

  test('water temp above target max -> red, not forecast-driven', () {
    final r = TempHelpers.evaluateTemperatureAdvisory(
      waterTemp: 29.0,
      airTemp: 30.0,
      windSpeed: 2.0,
      targetMinTemp: minT,
      targetMaxTemp: maxT,
    )!;
    expect(r.severity, AdvisorySeverity.red);
    expect(r.isForecastDriven, false);
    expect(r.message, contains('aeration'));
  });

  test('water temp below target min -> red', () {
    final r = TempHelpers.evaluateTemperatureAdvisory(
      waterTemp: 23.0,
      airTemp: 27.0,
      windSpeed: 0.0,
      targetMinTemp: minT,
      targetMaxTemp: maxT,
    )!;
    expect(r.severity, AdvisorySeverity.red);
  });

  test('water temp within range but >70% of half-range from midpoint -> amber', () {
    // midpoint=26, halfRange=2, threshold=1.4 -> 27.5 has deviation 1.5
    final r = TempHelpers.evaluateTemperatureAdvisory(
      waterTemp: 27.5,
      airTemp: 29.0,
      windSpeed: 1.0,
      targetMinTemp: minT,
      targetMaxTemp: maxT,
    )!;
    expect(r.severity, AdvisorySeverity.amber);
    expect(r.isForecastDriven, false);
  });

  test('comfortable water temp + hot forecast (>=31.5C air) -> amber, forecast-driven', () {
    final r = TempHelpers.evaluateTemperatureAdvisory(
      waterTemp: 26.0, // exactly midpoint, deviation 0
      airTemp: 32.0,
      windSpeed: 5.0,
      targetMinTemp: minT,
      targetMaxTemp: maxT,
    )!;
    expect(r.severity, AdvisorySeverity.amber);
    expect(r.isForecastDriven, true);
    expect(r.message, contains('32.0'));
  });

  test('comfortable water temp + mild forecast -> null (safe)', () {
    final r = TempHelpers.evaluateTemperatureAdvisory(
      waterTemp: 26.0,
      airTemp: 29.0,
      windSpeed: 3.0,
      targetMinTemp: minT,
      targetMaxTemp: maxT,
    );
    expect(r, isNull);
  });

  test(
    'windSpeed is accepted but does not affect the outcome (unused parameter, '
    'documented here so a future removal is a deliberate choice, not a surprise)',
    () {
      final calm = TempHelpers.evaluateTemperatureAdvisory(
        waterTemp: 26.0, airTemp: 29.0, windSpeed: 0.0,
        targetMinTemp: minT, targetMaxTemp: maxT,
      );
      final gusty = TempHelpers.evaluateTemperatureAdvisory(
        waterTemp: 26.0, airTemp: 29.0, windSpeed: 999.0,
        targetMinTemp: minT, targetMaxTemp: maxT,
      );
      expect(calm, isNull);
      expect(gusty, isNull);
    },
  );

  test('non-numeric inputs fall back to defaults (26.0 water / 30.0 air) rather than crashing', () {
    final r = TempHelpers.evaluateTemperatureAdvisory(
      waterTemp: 'not-a-number',
      airTemp: 'also-not-a-number',
      windSpeed: 1.0,
      targetMinTemp: minT,
      targetMaxTemp: maxT,
    );
    // defaults: water=26 (midpoint, deviation 0), air=30 (< 31.5) -> safe
    expect(r, isNull);
  });
}
