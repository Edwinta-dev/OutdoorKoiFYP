// Round 1 characterization tests for AlgalHelpers.evaluateSolarAdvisory.
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/helpers/algal_helpers.dart';

void main() {
  test('high lux alone triggers the warning', () {
    final r = AlgalHelpers.evaluateSolarAdvisory(
      lux: 20000, uvIndex: 2, isFairForecast: false,
    );
    expect(r, isNotNull);
    expect(r, contains('algae bloom'));
  });

  test('high UV index alone triggers the warning', () {
    final r = AlgalHelpers.evaluateSolarAdvisory(
      lux: 5000, uvIndex: 8, isFairForecast: false,
    );
    expect(r, isNotNull);
  });

  test('fair forecast alone triggers the warning', () {
    final r = AlgalHelpers.evaluateSolarAdvisory(
      lux: 5000, uvIndex: 2, isFairForecast: true,
    );
    expect(r, isNotNull);
  });

  test('low lux, low UV, non-fair forecast -> no warning', () {
    final r = AlgalHelpers.evaluateSolarAdvisory(
      lux: 5000, uvIndex: 3, isFairForecast: false,
    );
    expect(r, isNull);
  });

  test('non-numeric lux/uvIndex fall back to defaults (500 / 0) rather than crashing', () {
    final r = AlgalHelpers.evaluateSolarAdvisory(
      lux: 'nope', uvIndex: 'also-nope', isFairForecast: false,
    );
    expect(r, isNull); // 500 lux, 0 UV, not fair -> safe
  });
}
