import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/camera_card_reading.dart';
import 'package:mobile_app/widgets/detail_graph/camera_card_reading_card.dart';
import 'helpers/fake_pond_data_source.dart';
import 'helpers/pump_screen.dart';
import 'helpers/fixtures.dart';

void main() {
  FakePondDataSource source({
    Map<String, dynamic> masks = const {'version': 1, 'reference_image_id': 12},
    CameraCardReading? reading,
  }) => FakePondDataSource(frame: Fixtures.frame())
    ..cameraCardMasks = masks
    ..cameraCardReading = reading;

  testWidgets('no masks directs keeper to Camera gallery', (tester) async {
    await pumpScreen(
      tester,
      const CameraCardReadingCard(userId: 7),
      source: source(masks: const {'version': null}),
    );
    await settle(tester);
    expect(find.text('Place the card masks in Camera gallery'), findsOneWidget);
  });

  testWidgets('missing clear-water reference has its own state', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      const CameraCardReadingCard(userId: 7),
      source: source(masks: const {'version': 1, 'reference_image_id': null}),
    );
    await settle(tester);
    expect(
      find.text('Choose a clear-water reference frame in Camera gallery'),
      findsOneWidget,
    );
  });

  testWidgets('unusable frame is explicit', (tester) async {
    await pumpScreen(
      tester,
      const CameraCardReadingCard(userId: 7),
      source: source(
        reading: const CameraCardReading(
          quality: 'unusable',
          ph: null,
          nh3: null,
          usableDots: 2,
          totalDots: 72,
          turbidityIndex: null,
          agreement: null,
          frameAgreement: null,
          patches: {},
        ),
      ),
    );
    await settle(tester);
    expect(
      find.text('This camera frame is unusable for a card reading'),
      findsOneWidget,
    );
  });

  testWidgets('shows confidence, agreement, dot count and turbidity label', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      const CameraCardReadingCard(userId: 7),
      source: source(
        reading: const CameraCardReading(
          quality: 'usable',
          ph: {'segment': 'ph_ref_7', 'confidence': 'clear'},
          nh3: {'segment': 'nh3_ref_2', 'confidence': 'weak'},
          usableDots: 61,
          totalDots: 72,
          turbidityIndex: .2,
          agreement: {'ph': true, 'nh3': false},
          frameAgreement: 'disagree',
          patches: {},
        ),
      ),
    );
    await settle(tester);
    expect(find.text('pH: segment 7'), findsOneWidget);
    expect(find.text('Ammonia: segment 2'), findsOneWidget);
    expect(find.text('clear'), findsOneWidget);
    expect(find.text('weak'), findsOneWidget);
    expect(find.text('Methods disagree'), findsOneWidget);
    expect(find.text('Usable dots: 61 of 72'), findsOneWidget);
    expect(
      find.text('Turbidity Index: 0.20 · slightly cloudy'),
      findsOneWidget,
    );
  });

  testWidgets('caveats are behind the existing info popup', (tester) async {
    await pumpScreen(
      tester,
      const CameraCardReadingCard(userId: 7),
      source: source(),
    );
    await settle(tester);
    await tester.tap(find.byKey(const ValueKey('card-reading-info')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Adjacent pH segments'), findsOneWidget);
    expect(find.textContaining('clear-water reference frame'), findsOneWidget);
  });
}
