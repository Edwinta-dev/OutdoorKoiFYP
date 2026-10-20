import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/utils/pond_camera_storage.dart';
import 'package:mobile_app/widgets/detail_graph/algae_status_card.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/pump_screen.dart';

void main() {
  test(
    'GCC forecast selects current_gcc and preserves null trajectory gaps',
    () {
      final forecast = AlgaeForecast.fromJson({
        'current_green_ratio': 0.2,
        'current_gcc': 0.08,
        'projection_method': 'windowed_gcc_trend',
        'rate_source_detail': 'gcc_windowed_trend',
        'thresholds': {'watch': 0.03, 'action': 0.06, 'mode': 'absolute'},
        'trajectory': [
          {'days_from_now': 0, 'green_ratio': null, 'gcc': 0.08},
        ],
      });

      expect(forecast.isGccMode, isTrue);
      expect(forecast.currentSignal, 0.08);
      expect(forecast.signalLabel, 'GCC');
      expect(forecast.thresholdProgress, 1);
      expect(forecast.trajectory.single.greenRatio, isNull);
      expect(forecast.trajectory.single.gcc, 0.08);
    },
  );

  test('legacy forecast keeps green ratio and null GCC optional', () {
    final forecast = AlgaeForecast.fromJson({
      'current_green_ratio': 0.12,
      'thresholds': {'watch': 0.18, 'action': 0.25, 'mode': 'absolute'},
      'trajectory': [
        {'days_from_now': 0, 'green_ratio': 0.12},
        {'days_from_now': 1, 'green_ratio': null},
      ],
    });

    expect(forecast.isGccMode, isFalse);
    expect(forecast.currentSignal, 0.12);
    expect(forecast.signalLabel, 'Green ratio');
    expect(forecast.trajectory.last.greenRatio, isNull);
  });

  test('camera frame parses nullable GCC beside legacy green ratio', () {
    final gccFrame = PondCameraFrame.fromJson({
      'id': 1,
      'green_ratio': 0.2,
      'gcc': 0.34,
      'created_at': '2026-10-19T00:00:00Z',
    });
    final legacyFrame = PondCameraFrame.fromJson({
      'green_ratio': 0.2,
      'gcc': null,
    });

    expect(gccFrame.gcc, 0.34);
    expect(legacyFrame.gcc, isNull);
    expect(legacyFrame.greenRatio, 0.2);
  });

  testWidgets('algae status card labels and displays GCC in GCC mode', (
    tester,
  ) async {
    final forecast = AlgaeForecast.fromJson({
      'current_green_ratio': 0.2,
      'current_gcc': 0.08,
      'rate_source_detail': 'gcc_windowed_trend',
      'thresholds': {'watch': 0.03, 'action': 0.06, 'mode': 'gcc_offset'},
      'trajectory': [],
    });
    await pumpScreen(
      tester,
      const Scaffold(body: AlgaeStatusCard(userId: 7)),
      source: FakePondDataSource(algaeForecast: forecast),
    );
    await settle(tester);

    expect(find.text('GCC vs alert level'), findsOneWidget);
    expect(find.text('8.00% of 6.0%'), findsOneWidget);
    expect(find.text('8.00%'), findsOneWidget);
  });
}
