// errorMessageFrom reads the DigitalTwin error envelope
// ({"error": {"code", "message", "details"}}) and the older string form.
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';

void main() {
  test('reads the message from the error envelope', () {
    final body = {
      'error': {
        'code': 'not_enough_history',
        'message': 'Not enough recent feeding history.',
        'details': <String, dynamic>{},
      },
    };
    expect(errorMessageFrom(body, 'Unavailable'),
        'Not enough recent feeding history.');
  });

  test('still reads a plain string error', () {
    expect(errorMessageFrom({'error': 'no assessment yet'}, 'Unavailable'),
        'no assessment yet');
  });

  test('falls back when there is no usable message', () {
    expect(errorMessageFrom({'error': {'code': 'x'}}, 'Unavailable'),
        'Unavailable');
    expect(errorMessageFrom({'status': 'ok'}, 'Unavailable'), 'Unavailable');
    expect(errorMessageFrom([1, 2], 'Unavailable'), 'Unavailable');
    expect(errorMessageFrom(null, 'Unavailable'), 'Unavailable');
  });
}
