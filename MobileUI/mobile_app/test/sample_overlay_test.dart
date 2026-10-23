// The sampled-areas overlay: dots only inside the colour-card patches, mapped
// onto the contain rectangle of the 4:3 frame; the rating card's switch (a row
// below the photo), empty state and (i) caveat.
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/card_patch.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/utils/pond_camera_storage.dart';
import 'package:mobile_app/widgets/detail_graph/algae_severity_rating_card.dart';
import 'package:mobile_app/widgets/detail_graph/sample_overlay.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

const _square = [
  [0.1, 0.1],
  [0.3, 0.1],
  [0.3, 0.3],
  [0.1, 0.3],
];

const _white = CardPatch(
  name: 'white_ref',
  kind: 'white_reference',
  polygon: _square,
  points: [
    [0.15, 0.15],
    [0.25, 0.15],
    [0.2, 0.25],
  ],
);

// No explicit points: the grid fallback clips to the polygon.
const _bare = CardPatch(
  name: 'ph_ref_1',
  kind: 'ph_reference',
  polygon: [
    [0.6, 0.6],
    [0.9, 0.6],
    [0.9, 0.9],
    [0.6, 0.9],
  ],
);

void main() {
  group('containRect', () {
    test('a 4:3 box is filled exactly', () {
      expect(
        containRect(const Size(400, 300)),
        const Rect.fromLTWH(0, 0, 400, 300),
      );
    });

    test('a taller box letterboxes top and bottom', () {
      final r = containRect(const Size(400, 400));
      expect(r.width, 400);
      expect(r.height, closeTo(300, 1e-9));
      expect(r.top, closeTo(50, 1e-9));
    });

    test('a wider box pillarboxes left and right', () {
      final r = containRect(const Size(800, 300));
      expect(r.height, 300);
      expect(r.width, closeTo(400, 1e-9));
      expect(r.left, closeTo(200, 1e-9));
    });
  });

  group('patchDots', () {
    test('map normalised points onto the contain rect (letterbox aware)', () {
      final rect = containRect(const Size(400, 400)); // (0, 50, 400, 300)
      final dots = patchDots([_white], rect);
      expect(dots.length, 3);
      expect(dots.first.at.dx, closeTo(0.15 * 400, 1e-9));
      expect(dots.first.at.dy, closeTo(50 + 0.15 * 300, 1e-9));
      for (final d in dots) {
        expect(rect.contains(d.at), isTrue);
      }
    });

    test('dots are only inside their patches', () {
      final rect = containRect(const Size(640, 480));
      final dots = patchDots([_white, _bare], rect);
      expect(dots.where((d) => d.patch == 'ph_ref_1'), isNotEmpty);
      final byName = {_white.name: _white, _bare.name: _bare};
      for (final d in dots) {
        expect(
          polygonContains(byName[d.patch]!.polygon, d.frame.dx, d.frame.dy),
          isTrue,
          reason: '${d.patch} ${d.frame}',
        );
      }
    });

    test('no patches, or an empty rect, give no dots', () {
      expect(patchDots(const [], const Rect.fromLTWH(0, 0, 100, 75)), isEmpty);
      expect(patchDots([_white], Rect.zero), isEmpty);
    });
  });

  group('painter', () {
    test('dots lie inside the contain rect for any box', () {
      const painter = SampleOverlayPainter(
        patches: [_white, _bare],
        color: Colors.green,
        rejectedColor: Colors.pink,
      );
      const size = Size(300, 500);
      final rect = containRect(size);
      for (final d in painter.dots(size)) {
        expect(rect.contains(d.at), isTrue);
      }
    });

    test('PixelProbe rejects clipped pixels like quality.py', () {
      // 2x1 image: a black pixel (V=0) and a mid grey pixel.
      final probe = PixelProbe(
        Uint8List.fromList([0, 0, 0, 255, 120, 120, 120, 255]),
        2,
        1,
      );
      expect(probe.rejected(0.1, 0.5), isTrue);
      expect(probe.rejected(0.9, 0.5), isFalse);
      final white = PixelProbe(Uint8List.fromList([10, 251, 10, 255]), 1, 1);
      expect(white.rejected(0.5, 0.5), isTrue);
    });
  });

  group('CardPatch', () {
    test('parses the mask response, and an absent block gives none', () {
      final patches = CardPatch.listFromMaskResponse({
        'card_patches': {
          'version': 1,
          'patches': [
            {
              'name': 'ph_reading',
              'kind': 'ph_reading',
              'polygon': _square,
              'points': [
                [0.2, 0.2],
              ],
            },
          ],
        },
      });
      expect(patches.single.name, 'ph_reading');
      expect(patches.single.points, [
        [0.2, 0.2],
      ]);
      expect(CardPatch.listFromMaskResponse({'mask': null}), isEmpty);
    });
  });

  group('rating card overlay', () {
    final frame = PondCameraFrame(
      id: 501,
      imageUrl: 'http://127.0.0.1:1/frame.jpg',
      greenRatio: 0.12,
      gcc: null,
      capturedAt: Fixtures.now.subtract(const Duration(hours: 2)),
      state: 'base',
    );

    Widget card() => const Scaffold(
      body: SingleChildScrollView(
        padding: EdgeInsets.all(16),
        child: AlgaeSeverityRatingCard(userId: 7),
      ),
    );

    FakePondDataSource source(List<CardPatch> patches) => FakePondDataSource(
      frame: frame,
      ratingContext: Fixtures.ratingContext(),
      ratingResult: Fixtures.ratingResult(),
    )..cameraCardPatches = patches;

    int painters(WidgetTester tester) =>
        find.byKey(const ValueKey('sample-overlay-painter')).evaluate().length;

    testWidgets('shows dots by default; the switch hides them', (tester) async {
      await pumpScreen(tester, card(), source: source([_white]));
      await settle(tester);

      expect(painters(tester), 1);
      final painter =
          tester
                  .widget<CustomPaint>(
                    find.byKey(const ValueKey('sample-overlay-painter')),
                  )
                  .painter!
              as SampleOverlayPainter;
      expect(painter.patches, [_white]);
      expect(find.text('Show sampled areas'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('sample-overlay-toggle')));
      await tester.pump();
      expect(painters(tester), 0);
      expect(find.text('Show sampled areas'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('sample-overlay-toggle')));
      await tester.pump();
      expect(painters(tester), 1);
    });

    testWidgets('the switch is below the photo, not inside the image stack', (
      tester,
    ) async {
      await pumpScreen(tester, card(), source: source([_white]));
      await settle(tester);

      final stack = find.byKey(const ValueKey('frame-image-stack'));
      final toggle = find.byKey(const ValueKey('sample-overlay-toggle'));
      expect(stack, findsOneWidget);
      expect(find.descendant(of: stack, matching: toggle), findsNothing);
      expect(
        find.descendant(of: stack, matching: find.text('Show sampled areas')),
        findsNothing,
      );
      expect(
        tester.getTopLeft(toggle).dy,
        greaterThanOrEqualTo(tester.getBottomLeft(stack).dy - 1),
      );
    });

    testWidgets('the photo is shown whole (contain) in a 4:3 box', (
      tester,
    ) async {
      await pumpScreen(tester, card(), source: source([_white]));
      await settle(tester);

      final box = tester.getSize(
        find.byKey(const ValueKey('frame-image-stack')),
      );
      expect(box.width / box.height, closeTo(4 / 3, 0.01));
      final image = tester.widget<Image>(find.byType(Image).first);
      expect(image.fit, BoxFit.contain);
    });

    testWidgets('no patches: no overlay and no switch', (tester) async {
      await pumpScreen(tester, card(), source: source(const []));
      await settle(tester);

      expect(painters(tester), 0);
      expect(find.byKey(const ValueKey('sample-overlay-toggle')), findsNothing);
      expect(find.text('Show sampled areas'), findsNothing);
    });

    testWidgets('a failed patches read still shows the photo card', (
      tester,
    ) async {
      final failing = source([_white]);
      failing.failing.add('fetchCameraCardPatches');
      await pumpScreen(tester, card(), source: failing);
      await settle(tester);

      expect(find.text('Rate What You See'), findsOneWidget);
      expect(painters(tester), 0);
    });

    testWidgets(
      'a failed patches read recovers once the provider is refreshed',
      (tester) async {
        final flaky = source([_white]);
        flaky.failing.add('fetchCameraCardPatches');
        await pumpScreen(tester, card(), source: flaky);
        await settle(tester);
        expect(painters(tester), 0);

        // The service is back; a refresh (screen reload or the card's own
        // reload) invalidates the patches provider and the dots appear.
        flaky.failing.remove('fetchCameraCardPatches');
        ProviderScope.containerOf(
          tester.element(find.byType(AlgaeSeverityRatingCard)),
        ).invalidate(cameraCardPatchesProvider(7));
        await settle(tester);
        expect(painters(tester), 1);
      },
    );

    testWidgets('the calibration caveat is behind the (i) button', (
      tester,
    ) async {
      await pumpScreen(tester, card(), source: source(const []));
      await settle(tester);

      expect(find.textContaining('Still needed to calibrate'), findsNothing);
      expect(find.textContaining('Watch at'), findsNothing);
      expect(find.textContaining('Enough ratings collected'), findsNothing);
      final info = find.byKey(const ValueKey('calibration-info'));
      await tester.ensureVisible(info);
      await tester.tap(info);
      await tester.pumpAndSettle();
      expect(find.text('Tap anywhere to close'), findsOneWidget);
    });
  });
}
