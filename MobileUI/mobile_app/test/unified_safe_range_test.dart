import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/providers.dart';

void main() {
  test('computes the shared interval across all stocked species', () {
    final range = UnifiedSafeRange.fromProfiles([
      {'pH': '6.0-7.0', 'Temperature': '20-28 °C'},
      {'pH': '6.5-8.0', 'Temperature': '24-30 °C'},
    ]);

    expect(range.ph!.minimum, 6.5);
    expect(range.ph!.maximum, 7.0);
    expect(range.temperatureC!.minimum, 24);
    expect(range.temperatureC!.maximum, 28);
  });

  test('represents incompatible tolerances as an empty intersection', () {
    final range = UnifiedSafeRange.fromProfiles([
      {'pH': '6.0-7.0'},
      {'pH': '8.1-8.4'},
    ]);
    expect(range.ph!.isEmpty, isTrue);
  });

  test('missing profile bounds remain unavailable', () {
    final range = UnifiedSafeRange.fromProfiles([
      {'pH': 'unknown'},
    ]);
    expect(range.ph, isNull);
  });
}
