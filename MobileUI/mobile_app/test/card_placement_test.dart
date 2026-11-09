// The colour card as three rigid masks (white reference, ammonia wheel, pH
// wheel): placing a mask moves only its own patches and dots, the saved
// placements override the served patches, and the screen adjusts the picked
// mask, saves, resets and removes them.
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/card_patch.dart';
import 'package:mobile_app/data/card_placement.dart';
import 'package:mobile_app/data/local_profile_repository.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/screens/card_placement_screen.dart';
import 'package:mobile_app/widgets/detail_graph/sample_overlay.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/pump_screen.dart';

final template = CardTemplate.parse(
  File('lib/assets/card_template.json').readAsStringSync(),
);

double _dist(Offset a, Offset b, double aspect) =>
    math.sqrt(math.pow((a.dx - b.dx) * aspect, 2) + math.pow(a.dy - b.dy, 2));

void main() {
  group('template', () {
    test('splits into the three masks', () {
      expect(template.masks['white']!.patches.map((p) => p.name), [
        'white_ref',
      ]);
      expect(template.masks['nh3']!.patches.length, 6); // 5 segments + pad
      expect(template.masks['ph']!.patches.length, 9); // 8 segments + pad
      final ph = template.masks['ph']!.patches.where(
        (p) => p.name.startsWith('ph_ref_'),
      );
      expect(ph.fold<int>(0, (n, p) => n + p.points.length), 72);
    });

    test('home placements leave every point where it was', () {
      final moved = template.home.patches(template);
      final original = template.patches;
      for (var i = 0; i < moved.length; i++) {
        for (var j = 0; j < moved[i].points.length; j++) {
          expect(
            moved[i].points[j][0],
            closeTo(original[i].points[j][0], 1e-9),
          );
          expect(
            moved[i].points[j][1],
            closeTo(original[i].points[j][1], 1e-9),
          );
        }
      }
    });
  });

  group('placement', () {
    final ph = template.masks['ph']!;
    final p = ph.home.copyWith(cx: 0.4, cy: 0.55, scale: 0.6, angle: 0.7);

    test('keeps a mask rigid: distances scale, nothing shears', () {
      final a = ph.patches.first.points.first;
      final b = ph.patches.last.points.last;
      final before = _dist(
        Offset(a[0], a[1]),
        Offset(b[0], b[1]),
        template.aspect,
      );
      final after = _dist(
        p.map(ph, template.aspect, a[0], a[1]),
        p.map(ph, template.aspect, b[0], b[1]),
        template.aspect,
      );
      expect(after, closeTo(before * 0.6, 1e-9));
    });

    test('the mask centre goes to the placement centre', () {
      final c = p.map(ph, template.aspect, ph.center.dx, ph.center.dy);
      expect(c.dx, closeTo(0.4, 1e-12));
      expect(c.dy, closeTo(0.55, 1e-12));
    });

    test('every sample stays inside its own moved patch', () {
      for (final patch in p.patches(ph, template.aspect)) {
        for (final q in patch.points) {
          expect(
            polygonContains(patch.polygon, q[0], q[1]),
            isTrue,
            reason: patch.name,
          );
        }
      }
    });

    test('moving one mask leaves the other two untouched', () {
      final moved = template.home.withMask('ph', p).patches(template);
      final original = template.patches;
      for (var i = 0; i < moved.length; i++) {
        final same = cardMaskOf(moved[i].name) != 'ph';
        final first = moved[i].polygon.first;
        expect(
          (first[0] - original[i].polygon.first[0]).abs() < 1e-12,
          same,
          reason: moved[i].name,
        );
      }
    });

    test('scale is clamped and the saved text round-trips', () {
      expect(p.copyWith(scale: 99).scale, CardPlacement.maxScale);
      final all = template.home.withMask('nh3', p);
      final back = CardPlacements.tryParse(all.encode())!;
      for (final id in cardMaskIds) {
        expect(back[id].toJson(), all[id].toJson());
      }
      expect(CardPlacements.tryParse(''), isNull);
      expect(CardPlacements.tryParse(null), isNull);
      expect(CardPlacements.tryParse('{"ph": {}}'), isNull);
    });
  });

  group('providers', () {
    const served = CardPatch(
      name: 'white_ref',
      kind: 'white_reference',
      polygon: [
        [0.1, 0.1],
        [0.2, 0.1],
        [0.2, 0.2],
      ],
    );

    ProviderContainer container(Map<String, Object> prefs) {
      final c = ProviderContainer(
        overrides: [
          pondDataSourceProvider.overrideWithValue(
            FakePondDataSource()..cameraCardPatches = [served],
          ),
          localProfileRepositoryProvider.overrideWithValue(
            FakeLocalProfileRepository(prefs),
          ),
          cardTemplateProvider.overrideWith((_) async => template),
        ],
      );
      addTearDown(c.dispose);
      return c;
    }

    test('no placement: the served patches', () async {
      final c = container(onboardedPrefs);
      expect(await c.read(cameraCardPatchesProvider(7).future), [served]);
    });

    test('saved placements replace them with the moved masks', () async {
      final all = template.home.withMask(
        'white',
        template.masks['white']!.home.copyWith(cx: 0.3),
      );
      final c = container({
        ...onboardedPrefs,
        cardPlacementKey(7): all.encode(),
      });
      final got = await c.read(cameraCardPatchesProvider(7).future);
      expect(got.length, 16);
      expect(
        got.first.points.first[0],
        closeTo(all.patches(template).first.points.first[0], 1e-12),
      );
    });

    test('removed placements (empty text) fall back to served', () async {
      final c = container({...onboardedPrefs, cardPlacementKey(7): ''});
      expect(await c.read(cameraCardPatchesProvider(7).future), [served]);
    });
  });

  group('screen', () {
    Future<FakeLocalProfileRepository> pump(WidgetTester tester) async {
      final repo = FakeLocalProfileRepository(onboardedPrefs);
      tester.view.physicalSize = phoneSize;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          retry: (_, _) => null,
          overrides: [
            pondDataSourceProvider.overrideWithValue(FakePondDataSource()),
            localProfileRepositoryProvider.overrideWithValue(repo),
            cardTemplateProvider.overrideWith((_) async => template),
          ],
          child: MaterialApp(
            home: CardPlacementScreen(
              userId: 7,
              imageProvider: MemoryImage(_png),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      return repo;
    }

    CardOutlinePainter outline(WidgetTester tester, String id) =>
        tester
                .widget<CustomPaint>(find.byKey(ValueKey('card-outline-$id')))
                .painter!
            as CardOutlinePainter;

    testWidgets('three outlines; the buttons adjust only the picked mask', (
      tester,
    ) async {
      final repo = await pump(tester);
      for (final id in cardMaskIds) {
        expect(find.byKey(ValueKey('card-outline-$id')), findsOneWidget);
      }
      expect(outline(tester, 'white').active, isTrue);

      await tester.tap(find.byKey(const ValueKey('card-mask-ph')));
      await tester.pump();
      expect(outline(tester, 'ph').active, isTrue);
      expect(outline(tester, 'white').active, isFalse);

      await tester.tap(find.byKey(const ValueKey('card-rotate-cw')));
      await tester.tap(find.byKey(const ValueKey('card-grow')));
      await tester.pump();
      expect(find.text('pH wheel: size 105%  ·  turned 5°'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('card-save')));
      await tester.pumpAndSettle();
      final saved = CardPlacements.tryParse(repo.values[cardPlacementKey(7)])!;
      expect(saved['ph'].angle, closeTo(5 * math.pi / 180, 1e-9));
      expect(saved['ph'].scale, closeTo(1.05, 1e-9));
      expect(saved['white'].toJson(), template.masks['white']!.home.toJson());
      expect(saved['nh3'].toJson(), template.masks['nh3']!.home.toJson());
    });

    testWidgets('dragging moves only the picked mask', (tester) async {
      await pump(tester);
      await tester.tap(find.byKey(const ValueKey('card-mask-nh3')));
      await tester.pump();
      final white = outline(tester, 'white').outline.first;
      final before = outline(tester, 'nh3').outline.first;
      await tester.drag(
        find.byKey(const ValueKey('card-canvas')),
        const Offset(40, 0),
      );
      await tester.pump();
      expect(outline(tester, 'nh3').outline.first.dx, greaterThan(before.dx));
      expect(outline(tester, 'white').outline.first, white);
    });

    testWidgets('remove clears the saved placements', (tester) async {
      final repo = await pump(tester);
      await tester.tap(find.byKey(const ValueKey('card-save')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const ValueKey('card-remove')));
      await tester.tap(find.byKey(const ValueKey('card-remove')));
      await tester.pumpAndSettle();
      expect(CardPlacements.tryParse(repo.values[cardPlacementKey(7)]), isNull);
    });
  });
}

// A 1x1 transparent PNG.
final _png = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
);
