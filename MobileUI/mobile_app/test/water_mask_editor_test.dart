import 'dart:convert';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile_app/data/pond_data_source.dart';
import 'package:mobile_app/screens/camera_gallery_screen.dart';
import 'package:mobile_app/screens/water_mask_editor_screen.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

Future<ui.Image> testImage() async {
  final recorder = ui.PictureRecorder();
  Canvas(recorder).drawRect(
    const Rect.fromLTWH(0, 0, 40, 20),
    Paint()..color = const Color(0xFF808000),
  );
  final picture = recorder.endRecording();
  final image = await picture.toImage(40, 20);
  picture.dispose();
  return image;
}

Future<void> editor(
  WidgetTester tester,
  FakePondDataSource source, {
  double textScale = 1,
}) async {
  source.frame ??= Fixtures.frame();
  late ui.Image image;
  await tester.runAsync(() async {
    image = await testImage();
  });
  await pumpScreen(
    tester,
    WaterMaskEditorScreen(userId: 7, initialImage: image),
    source: source,
    size: const Size(320, 900),
    textScale: textScale,
  );
  await tester.runAsync(() async {
    await Future<void>.delayed(const Duration(milliseconds: 100));
  });
  await settle(tester);
  addTearDown(image.dispose);
}

WaterMaskCanvas canvas(WidgetTester tester) =>
    tester.widget(find.byType(WaterMaskCanvas));

void main() {
  test(
    'mask adapter uses authenticated GET and PUT with the polygon contract',
    () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        return http.Response(
          jsonEncode({
            'mask': [
              [0, 0],
              [1, 0],
              [1, 1],
              [0, 1],
            ],
          }),
          200,
        );
      });
      addTearDown(client.close);
      final source = LivePondDataSource(
        api: DigitalTwinApi(
          client: client,
          baseUrl: 'http://pond.invalid',
          accessToken: () => 'offline-token',
        ),
      );
      final polygon = (await source.fetchCameraMask(7))!;
      await source.saveCameraMask(7, polygon);
      expect(requests.map((r) => r.method), ['GET', 'PUT']);
      for (final request in requests) {
        expect(request.url.path, '/v1/ponds/7/camera/mask');
        expect(request.headers['Authorization'], 'Bearer offline-token');
      }
      expect(jsonDecode(requests.last.body), {'polygon': polygon});
    },
  );

  test('mask adapter accepts no mask and propagates save errors', () async {
    final client = MockClient(
      (r) async => r.method == 'GET'
          ? http.Response('{"mask":null}', 200)
          : http.Response('{"error":{"message":"Unavailable"}}', 503),
    );
    addTearDown(client.close);
    final source = LivePondDataSource(
      api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
    );
    expect(await source.fetchCameraMask(7), isNull);
    await expectLater(source.saveCameraMask(7, []), throwsStateError);
  });

  test('preview pins camera HSV bounds and excludes non-green pixels', () {
    expect(isCameraGreen(128, 128, 0), isTrue); // OpenCV H=30
    expect(isCameraGreen(255, 0, 0), isFalse);
    expect(isCameraGreen(0, 255, 0), isFalse); // H=60, outside camera bounds
    expect(isCameraGreen(30, 30, 0), isFalse); // too dark
    expect(isCameraGreen(128, 128, 120), isFalse); // too little saturation
    final path = greenPixelPath(
      Uint8List.fromList([128, 128, 0, 255, 0, 0, 255, 255]),
      2,
      1,
    );
    expect(path.contains(const Offset(.5, .5)), isTrue);
    expect(path.contains(const Offset(1.5, .5)), isFalse);
  });

  test('mask area matches endpoint minimum and point limit', () {
    expect(
      validMaskArea([
        Offset.zero,
        const Offset(.1, 0),
        const Offset(.1, .1),
        const Offset(0, .1),
      ]),
      isTrue,
    );
    expect(
      validMaskArea([
        Offset.zero,
        const Offset(.05, 0),
        const Offset(.05, .05),
      ]),
      isFalse,
    );
    expect(validMaskArea(List.filled(65, Offset.zero)), isFalse);
  });

  testWidgets(
    'starts with rectangle, edits points, inserts on nearest edge and saves',
    (tester) async {
      final source = FakePondDataSource();
      await editor(tester, source);
      expect(canvas(tester).points.length, 4);
      expect(
        find.textContaining('Saving resets the camera baseline'),
        findsOneWidget,
      );
      final target = find.byKey(const ValueKey('water-mask-canvas'));
      final rect = tester.getRect(target);
      expect(rect.width / rect.height, 2); // actual image, no letterboxing
      canvas(tester).onChanged!([
        const Offset(.2, .1),
        ...canvas(tester).points.skip(1),
      ]);
      await settle(tester);
      expect(canvas(tester).points.first.dx, .2);
      await tester.tapAt(
        rect.topLeft + Offset(rect.width * .5, rect.height * .1),
      );
      await settle(tester);
      expect(canvas(tester).points.length, 5);
      expect(canvas(tester).points[1].dx, closeTo(.5, .001));
      final expected = canvas(tester).points.map((p) => [p.dx, p.dy]).toList();
      await tester.tap(find.text('Save water mask'));
      await settle(tester);
      expect(source.callsTo('saveCameraMask').single.args, {
        'userId': 7,
        'polygon': expected,
      });
      expect(find.textContaining('Water mask saved.'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'loads saved mask, accepts boundary points and resets to rectangle',
    (tester) async {
      final source = FakePondDataSource()
        ..cameraMask = [
          [.2, .2],
          [.8, .2],
          [.8, .8],
          [.2, .8],
        ];
      await editor(tester, source, textScale: 1.6);
      expect(canvas(tester).points.first, const Offset(.2, .2));
      canvas(tester).onChanged!([
        const Offset(0, .2),
        ...canvas(tester).points.skip(1),
      ]);
      await settle(tester);
      expect(canvas(tester).points.first.dx, 0);
      await tester.ensureVisible(find.text('Start from rectangle'));
      await tester.tap(find.text('Start from rectangle'));
      await settle(tester);
      expect(canvas(tester).points.first, const Offset(.1, .1));
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('failed save preserves edits for retry', (tester) async {
    final source = FakePondDataSource(failing: {'saveCameraMask'});
    await editor(tester, source);
    final edited = [
      const Offset(.2, .2),
      const Offset(.8, .2),
      const Offset(.8, .8),
      const Offset(.2, .8),
    ];
    canvas(tester).onChanged!(edited);
    await settle(tester);
    await tester.tap(find.text('Save water mask'));
    await settle(tester);
    expect(find.textContaining('Water mask was not saved.'), findsOneWidget);
    expect(canvas(tester).points, edited);
    source.failing.clear();
    await tester.tap(find.text('Save water mask'));
    await settle(tester);
    expect(source.callsTo('saveCameraMask').length, 2);
    expect(find.textContaining('Water mask saved.'), findsOneWidget);
  });

  testWidgets('pending save disables edits and duplicate submission', (
    tester,
  ) async {
    await editor(tester, FakePondDataSource(hanging: {'saveCameraMask'}));
    await tester.tap(find.text('Save water mask'));
    await settle(tester);
    expect(canvas(tester).onChanged, isNull);
    expect(
      tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
      isNull,
    );
  });

  testWidgets('too small mask disables saving and 64 points stops insertion', (
    tester,
  ) async {
    await editor(tester, FakePondDataSource());
    canvas(tester).onChanged!([
      Offset.zero,
      const Offset(.01, 0),
      const Offset(.01, .01),
    ]);
    await settle(tester);
    expect(
      tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
      isNull,
    );
    canvas(tester).onChanged!(List.generate(64, (i) => Offset(i / 64, .5)));
    await settle(tester);
    await tester.tap(find.byKey(const ValueKey('water-mask-canvas')));
    await settle(tester);
    expect(canvas(tester).points.length, 64);
  });

  for (final failure in ['fetchLatestFrame', 'fetchCameraMask']) {
    testWidgets('$failure failure allows retry without saving', (tester) async {
      final source = FakePondDataSource(failing: {failure});
      await editor(tester, source);
      expect(find.text('Save water mask'), findsNothing);
      expect(find.textContaining('Could not load'), findsOneWidget);
      source.failing.clear();
      await tester.tap(find.text('Retry loading'));
      await tester.runAsync(
        () async => Future<void>.delayed(const Duration(milliseconds: 100)),
      );
      await settle(tester);
      expect(find.byType(WaterMaskCanvas), findsOneWidget);
    });
  }

  testWidgets(
    'gallery opens editor for latest frame regardless of selected day',
    (tester) async {
      final source = FakePondDataSource(frame: Fixtures.frame());
      await pumpScreen(
        tester,
        CameraGalleryScreen(userId: 7, initialDay: DateTime(2020)),
        source: source,
      );
      await settle(tester);
      await tester.tap(find.byTooltip('Edit water mask'));
      await settle(tester);
      expect(find.byType(WaterMaskEditorScreen), findsOneWidget);
      expect(source.callsTo('fetchLatestFrame').single.args, {'userId': 7});
    },
  );

  testWidgets('missing latest image shows retry and no save', (tester) async {
    await pumpScreen(tester, const WaterMaskEditorScreen(userId: 7));
    await settle(tester);
    expect(find.text('Retry loading'), findsOneWidget);
    expect(find.text('Save water mask'), findsNothing);
  });

  testWidgets('preview highlights only pixels inside polygon', (tester) async {
    late ui.Image image;
    await tester.runAsync(() async {
      image = await testImage();
    });
    addTearDown(image.dispose);
    await pumpScreen(
      tester,
      RepaintBoundary(
        key: const ValueKey('preview'),
        child: SizedBox(
          width: 200,
          height: 100,
          child: CustomPaint(
            painter: WaterMaskPainter(
              image,
              Path()..addRect(const Rect.fromLTWH(0, 0, 40, 20)),
              [
                const Offset(.5, 0),
                const Offset(1, 0),
                const Offset(1, 1),
                const Offset(.5, 1),
              ],
            ),
          ),
        ),
      ),
    );
    await settle(tester);
    final boundary = tester.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('preview')),
    );
    await tester.runAsync(() async {
      final rendered = await boundary.toImage();
      final bytes = (await rendered.toByteData())!.buffer.asUint8List();
      final outside =
          (rendered.height ~/ 2 * rendered.width + rendered.width ~/ 4) * 4;
      final inside =
          (rendered.height ~/ 2 * rendered.width + rendered.width * 3 ~/ 4) * 4;
      expect(bytes[outside + 1], 128);
      expect(bytes[inside + 1], greaterThan(128));
      rendered.dispose();
    });
  });
}
