import 'dart:convert';
import 'dart:ui' show PointerDeviceKind;

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile_app/data/pond_data_source.dart';
import 'package:mobile_app/screens/camera_gallery_screen.dart';
import 'package:mobile_app/screens/detail_graph_screen.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/utils/pond_camera_storage.dart';
import 'package:mobile_app/widgets/detail_graph/algae_severity_rating_card.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

final day = DateTime(2026, 8, 20);
PondCameraFrame frame(
  int id,
  int hour, {
  double? ratio = .12,
  Map<String, dynamic>? quality,
}) => PondCameraFrame(
  id: id,
  imageUrl: null,
  greenRatio: ratio,
  capturedAt: DateTime(2026, 8, 20, hour),
  state: 'base',
  quality: quality,
);
Widget screen() => CameraGalleryScreen(userId: 7, initialDay: day);
FakePondDataSource source() => FakePondDataSource(
  frames: [
    frame(
      480,
      6,
      quality: {
        'version': 1,
        'status': 'fail',
        'reasons': ['too_dark'],
      },
    ),
    frame(501, 18, ratio: .25),
  ],
  frame: Fixtures.frame(),
  ratingContext: Fixtures.ratingContext(),
  ratingResult: Fixtures.ratingResult(),
);

void main() {
  testWidgets(
    'thumbnail uses its URL and falls back to full image then placeholder',
    (tester) async {
      const photo = PondCameraFrame(
        id: 480,
        imageUrl: 'http://image.invalid/full.jpg',
        thumbnailUrl: 'http://image.invalid/thumb.jpg',
        greenRatio: .12,
        capturedAt: null,
        state: null,
      );
      await pumpScreen(
        tester,
        const Scaffold(body: CameraFrameImage(frame: photo, thumbnail: true)),
      );
      final image = tester.widget<Image>(find.byType(Image));
      expect((image.image as NetworkImage).url, photo.thumbnailUrl);
      final context = tester.element(find.byType(Image));
      final fallback =
          image.errorBuilder!(context, Exception('missing thumbnail'), null)
              as Image;
      expect((fallback.image as NetworkImage).url, photo.imageUrl);
      final placeholder =
          fallback.errorBuilder!(context, Exception('missing frame'), null)
              as Center;
      expect((placeholder.child as Text).data, 'Camera image unavailable');
    },
  );

  testWidgets('gallery without a frame id disables rating', (tester) async {
    await pumpScreen(
      tester,
      screen(),
      source: FakePondDataSource(
        frames: const [
          PondCameraFrame(
            id: null,
            imageUrl: null,
            greenRatio: null,
            capturedAt: null,
            state: null,
          ),
        ],
      ),
    );
    await settle(tester);
    final rate = tester.widget<TextButton>(
      find.widgetWithText(TextButton, 'Rate this frame'),
    );
    expect(rate.onPressed, isNull);
    expect(find.textContaining('Time unknown'), findsOneWidget);
    expect(find.textContaining('State: unknown'), findsOneWidget);
  });

  testWidgets(
    'timelapse rates the scrubbed frame and shows offline rating notice',
    (tester) async {
      await pumpScreen(
        tester,
        screen(),
        source: source()..ratingContext = null,
      );
      await settle(tester);
      await tester.tap(find.text('Timelapse'));
      await settle(tester);
      tester.widget<Slider>(find.byType(Slider)).onChanged!(1);
      await settle(tester);
      await tester.ensureVisible(find.text('Rate this frame'));
      await tester.tap(find.text('Rate this frame'));
      await settle(tester);
      expect(find.text('Rate frame · 18:00:00'), findsOneWidget);
      expect(
        find.textContaining('Analysis service unreachable'),
        findsOneWidget,
      );
      expect(find.text('Submit rating'), findsNothing);
    },
  );

  for (final brightness in Brightness.values) {
    testWidgets(
      'gallery and timelapse fit narrow phones with large text in $brightness',
      (tester) async {
        await pumpScreen(
          tester,
          screen(),
          source: source(),
          size: const Size(320, 900),
          textScale: 1.6,
          brightness: brightness,
        );
        await settle(tester);
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('Timelapse'));
        await settle(tester);
        await tester.ensureVisible(find.byType(LineChart));
        await settle(tester);
        expect(tester.takeException(), isNull);
      },
    );
  }

  test('quality preserves legacy and future results as unknown', () {
    for (final quality in [
      null,
      'malformed',
      {},
      {'version': 2, 'status': 'pass'},
      {'version': '1', 'status': 'pass'},
      {'version': 1, 'status': 'other'},
    ]) {
      final parsed = PondCameraFrame.fromJson({'quality': quality});
      expect(parsed.qualityStatus, 'unknown');
      expect(parsed.qualityReasons, isEmpty);
    }
    expect(
      frame(1, 1, quality: {'version': 1, 'status': 'pass'}).qualityStatus,
      'pass',
    );
    final parsed = PondCameraFrame.fromJson({
      'current_state': '["obstruction", 0.2]',
      'quality': {
        'version': 1,
        'status': 'fail',
        'reasons': ['blurred'],
      },
    });
    expect(parsed.isFlaggedObstructed, isTrue);
    expect(parsed.qualityStatus, 'fail');
    expect(parsed.qualityReasons, ['blurred']);
  });

  test(
    'day query scopes pond, uses UTC half-open bounds and reads all pages',
    () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        final offset = int.parse(request.url.queryParameters['offset']!);
        return http.Response(
          jsonEncode([
            for (var i = offset; i < (offset == 0 ? 500 : 501); i++)
              {
                'id': i,
                'created_at': DateTime(
                  2026,
                  8,
                  20,
                  6,
                ).toUtc().toIso8601String(),
                'green_ratio': .12,
                'current_state': ['base', .12],
                'imageURL':
                    'http://old.invalid/storage/v1/object/public/${PondCameraStorage.bucketName}/7/$i.jpg?',
                if (i == 0) 'thumbnail_path': '7/0_thumb.jpg',
                if (i == 0)
                  'quality': {
                    'version': 1,
                    'status': 'fail',
                    'reasons': ['too_dark'],
                  },
              },
          ]),
          200,
          request: request,
        );
      });
      final db = SupabaseClient(
        'http://storage.invalid',
        'offline-key',
        httpClient: client,
      );
      addTearDown(db.dispose);
      addTearDown(client.close);
      final live = LivePondDataSource(client: db);
      final frames = await live.fetchFramesForDay(
        7,
        DateTime(2026, 8, 20, 15, 40),
      );
      expect(frames, hasLength(501));
      expect(requests, hasLength(2));
      expect(
        frames.first.thumbnailUrl,
        contains('storage.invalid/storage/v1/object/public/'),
      );
      expect(frames.first.thumbnailUrl, endsWith('7/0_thumb.jpg'));
      expect(frames.first.imageUrl, endsWith('7/0.jpg'));
      expect(frames.first.qualityStatus, 'fail');
      expect(frames.last.thumbnailUrl, isNull);
      expect(frames.last.qualityStatus, 'unknown');
      for (final request in requests) {
        expect(request.url.path, '/rest/v1/imageTable');
        expect(request.url.queryParameters['user_ID'], 'eq.7');
        expect(request.url.queryParametersAll['created_at'], [
          'gte.${day.toUtc().toIso8601String()}',
          'lt.${DateTime(2026, 8, 21).toUtc().toIso8601String()}',
        ]);
        expect(
          request.url.queryParameters['order'],
          'created_at.asc.nullslast,id.asc.nullslast',
        );
        expect(request.url.queryParameters['limit'], '500');
        expect(
          request.url.queryParameters['select'],
          contains('thumbnail_path'),
        );
      }
      expect(requests.last.url.queryParameters['offset'], '500');
    },
  );

  test(
    'day read rejects missing identity and surfaces storage errors',
    () async {
      final client = MockClient(
        (request) async =>
            http.Response('{"message":"offline"}', 503, request: request),
      );
      final db = SupabaseClient(
        'http://storage.invalid',
        'offline-key',
        httpClient: client,
      );
      addTearDown(db.dispose);
      addTearDown(client.close);
      await expectLater(
        PondCameraStorage.fetchFramesForDay(userId: 0, day: day, client: db),
        throwsArgumentError,
      );
      await expectLater(
        PondCameraStorage.fetchFramesForDay(userId: 7, day: day, client: db),
        throwsA(isA<PostgrestException>()),
      );
    },
  );

  testWidgets('gallery shows time, ratio, state, quality reasons and drift', (
    tester,
  ) async {
    await pumpScreen(tester, screen(), source: source());
    await settle(tester);
    expect(
      find.textContaining('06:00:00 · Green ratio: 12.00%'),
      findsOneWidget,
    );
    expect(
      find.textContaining('State: base · Quality: fail (too dark)'),
      findsOneWidget,
    );
    expect(
      find.textContaining('18:00:00 · Green ratio: 25.00%'),
      findsOneWidget,
    );
    expect(find.textContaining('Quality: unknown'), findsOneWidget);
    expect(find.text('Camera drift: stable. '), findsOneWidget);
    expect(
      tester
          .widgetList<CameraFrameImage>(find.byType(CameraFrameImage))
          .every((image) => image.thumbnail),
      isTrue,
    );
  });

  testWidgets('loading, empty days and previous/next day queries', (
    tester,
  ) async {
    final fake = FakePondDataSource(hanging: {'fetchFramesForDay'});
    await pumpScreen(tester, screen(), source: fake);
    await settle(tester);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    fake.hanging.clear();
    await tester.tap(find.byTooltip('Previous day'));
    await settle(tester);
    expect(find.text('No frames for this day'), findsOneWidget);
    expect(fake.callsTo('fetchFramesForDay').last.args, {
      'userId': 7,
      'day': DateTime(2026, 8, 19),
    });
    await tester.tap(find.byTooltip('Next day'));
    await settle(tester);
    expect(fake.callsTo('fetchFramesForDay').last.args['day'], day);
    await tester.tap(find.text('2026-08-20 · local time'));
    await settle(tester);
    expect(find.byType(DatePickerDialog), findsOneWidget);
    await tester.tap(find.text('21').last);
    await tester.tap(find.text('OK'));
    await settle(tester);
    expect(
      fake.callsTo('fetchFramesForDay').last.args['day'],
      DateTime(2026, 8, 21),
    );
  });

  testWidgets(
    'failed history retries; unavailable drift does not hide frames',
    (tester) async {
      final fake = source()
        ..failing.addAll({'fetchFramesForDay', 'fetchAlgaeRatingContext'});
      await pumpScreen(tester, screen(), source: fake);
      await settle(tester);
      expect(find.text('Camera history unavailable'), findsOneWidget);
      expect(
        find.text('Camera drift: unavailable. Try refreshing.'),
        findsOneWidget,
      );
      fake.failing.remove('fetchFramesForDay');
      await tester.tap(find.text('Retry'));
      await settle(tester);
      expect(find.byType(CameraFrameImage), findsNWidgets(2));
      fake.failing.clear();
      await tester.tap(find.byTooltip('Refresh camera history'));
      await settle(tester);
      expect(find.text('Camera drift: stable. '), findsOneWidget);
      expect(fake.callsTo('fetchFramesForDay'), hasLength(3));
    },
  );

  testWidgets('scrubbing synchronizes full frame, metadata and chart marker', (
    tester,
  ) async {
    await pumpScreen(tester, screen(), source: source());
    await settle(tester);
    await tester.tap(find.text('Timelapse'));
    await settle(tester);
    expect(find.text('Frame 1 of 2'), findsOneWidget);
    var chart = tester.widget<LineChart>(find.byType(LineChart));
    expect(chart.data.lineBarsData.single.spots.map((s) => s.y), [12, 25]);
    final lastX = chart.data.lineBarsData.single.spots.last.x;
    tester.widget<Slider>(find.byType(Slider)).onChanged!(1);
    await settle(tester);
    expect(find.text('Frame 2 of 2'), findsOneWidget);
    expect(
      tester.widget<CameraFrameImage>(find.byType(CameraFrameImage)).frame.id,
      501,
    );
    expect(
      tester.widget<CameraFrameImage>(find.byType(CameraFrameImage)).thumbnail,
      isFalse,
    );
    chart = tester.widget<LineChart>(find.byType(LineChart));
    expect(chart.data.extraLinesData.verticalLines.single.x, lastX);
    // A chart tap selects the same frame as the scrubber.
    chart.data.lineTouchData.touchCallback!(
      FlTapUpEvent(TapUpDetails(kind: PointerDeviceKind.touch)),
      LineTouchResponse(
        touchLocation: Offset.zero,
        touchChartCoordinate: Offset.zero,
        lineBarSpots: [
          TouchLineBarSpot(
            chart.data.lineBarsData.single,
            0,
            chart.data.lineBarsData.single.spots.first,
            0,
          ),
        ],
      ),
    );
    await settle(tester);
    expect(find.text('Frame 1 of 2'), findsOneWidget);
    await tester.tap(find.byTooltip('Previous day'));
    await settle(tester);
    expect(find.text('Frame 1 of 2'), findsOneWidget);
  });

  testWidgets(
    'one frame has disabled scrubber; unknown ratios are chart gaps',
    (tester) async {
      await pumpScreen(
        tester,
        screen(),
        source: FakePondDataSource(frames: [frame(480, 6, ratio: null)]),
      );
      await settle(tester);
      await tester.tap(find.text('Timelapse'));
      await settle(tester);
      expect(tester.widget<Slider>(find.byType(Slider)).onChanged, isNull);
      expect(find.textContaining('Green ratio: unknown'), findsOneWidget);
      expect(
        tester
            .widget<LineChart>(find.byType(LineChart))
            .data
            .lineBarsData
            .single
            .spots,
        [FlSpot.nullSpot],
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'gallery rates historical selection, reuses submit and undo without fetching latest',
    (tester) async {
      final fake = source();
      await pumpScreen(tester, screen(), source: fake);
      await settle(tester);
      await tester.tap(find.text('Rate this frame').first);
      await settle(tester);
      expect(
        find.descendant(
          of: find.byType(AlgaeSeverityRatingCard),
          matching: find.text('Camera image unavailable'),
        ),
        findsOneWidget,
      );
      expect(
        find.text('How does the pond look in this photo?'),
        findsOneWidget,
      );
      expect(
        find.text('This rating stays attached to the selected frame.'),
        findsOneWidget,
      );
      await tester.tap(find.text(AlgaeSeverity.moderate.label));
      await settle(tester);
      await tester.ensureVisible(find.text('Submit rating'));
      await tester.tap(find.text('Submit rating'));
      await settle(tester);
      expect(fake.callsTo('submitAlgaeRating').single.args['imageId'], 480);
      expect(fake.callsTo('submitAlgaeRating').single.args['greenRatio'], .12);
      expect(fake.callsTo('fetchLatestFrame'), isEmpty);
      await tester.ensureVisible(find.text('Undo'));
      await tester.tap(find.text('Undo'));
      await settle(tester);
      expect(fake.callsTo('undoAlgaeRating').single.args['ratingId'], 42);
      await tester.pageBack();
      await settle(tester);
      expect(find.text('Camera drift: stable. '), findsOneWidget);
      expect(fake.callsTo('fetchAlgaeRatingContext'), hasLength(3));
    },
  );

  testWidgets('rating context errors preserve selection and can be refreshed', (
    tester,
  ) async {
    final fake = source()..failing.add('fetchAlgaeRatingContext');
    await pumpScreen(tester, screen(), source: fake);
    await settle(tester);
    await tester.tap(find.text('Rate this frame').first);
    await settle(tester);
    expect(find.text('Rate frame · 06:00:00'), findsOneWidget);
    expect(
      find.descendant(
        of: find.byType(AlgaeSeverityRatingCard),
        matching: find.text('Camera image unavailable'),
      ),
      findsOneWidget,
    );
    expect(find.textContaining('Analysis service unreachable'), findsOneWidget);
    expect(find.text('Submit rating'), findsNothing);
    expect(fake.callsTo('fetchLatestFrame'), isEmpty);
    await tester.pageBack();
    await tester.pumpAndSettle();
    fake.failing.clear();
    await tester.tap(find.byTooltip('Refresh camera history'));
    await settle(tester);
    await tester.tap(find.text('Rate this frame').first);
    await settle(tester);
    expect(find.text('Submit rating'), findsOneWidget);
    expect(find.text('Rate frame · 06:00:00'), findsOneWidget);
  });

  testWidgets('failed rating keeps the historical frame for a retry', (
    tester,
  ) async {
    final fake = source()
      ..ratingResult = null
      ..ratingError = 'Could not save rating.';
    await pumpScreen(tester, screen(), source: fake);
    await settle(tester);
    await tester.tap(find.text('Rate this frame').first);
    await settle(tester);
    await tester.tap(find.text(AlgaeSeverity.moderate.label));
    await settle(tester);
    await tester.ensureVisible(find.text('Submit rating'));
    await tester.tap(find.text('Submit rating'));
    await settle(tester);
    expect(find.text('Could not save rating.'), findsOneWidget);
    fake.ratingResult = Fixtures.ratingResult();
    fake.ratingError = null;
    await tester.tap(find.text('Submit rating'));
    await settle(tester);
    expect(fake.callsTo('submitAlgaeRating'), hasLength(2));
    expect(
      fake
          .callsTo('submitAlgaeRating')
          .every((call) => call.args['imageId'] == 480),
      isTrue,
    );
    expect(fake.callsTo('fetchLatestFrame'), isEmpty);
  });

  testWidgets('chart gaps keep later frame indices aligned with scrubbing', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      screen(),
      source: FakePondDataSource(
        frames: [frame(480, 6), frame(490, 12, ratio: null), frame(501, 18)],
      ),
    );
    await settle(tester);
    await tester.tap(find.text('Timelapse'));
    await settle(tester);
    final chart = tester.widget<LineChart>(find.byType(LineChart));
    expect(chart.data.lineBarsData.single.spots[1], FlSpot.nullSpot);
    chart.data.lineTouchData.touchCallback!(
      FlTapUpEvent(TapUpDetails(kind: PointerDeviceKind.touch)),
      LineTouchResponse(
        touchLocation: Offset.zero,
        touchChartCoordinate: Offset.zero,
        lineBarSpots: [
          TouchLineBarSpot(
            chart.data.lineBarsData.single,
            0,
            chart.data.lineBarsData.single.spots.last,
            0,
          ),
        ],
      ),
    );
    await settle(tester);
    expect(find.text('Frame 3 of 3'), findsOneWidget);
    expect(tester.widget<Slider>(find.byType(Slider)).value, 2);
    expect(
      tester.widget<CameraFrameImage>(find.byType(CameraFrameImage)).frame.id,
      501,
    );
  });

  testWidgets('refresh clamps a selection when frames are removed', (
    tester,
  ) async {
    final fake = source();
    await pumpScreen(tester, screen(), source: fake);
    await settle(tester);
    await tester.tap(find.text('Timelapse'));
    await settle(tester);
    tester.widget<Slider>(find.byType(Slider)).onChanged!(1);
    await settle(tester);
    fake.frames = [fake.frames.first];
    await tester.tap(find.byTooltip('Refresh camera history'));
    await settle(tester);
    expect(find.text('Frame 1 of 1'), findsOneWidget);
    expect(tester.widget<Slider>(find.byType(Slider)).value, 0);
    expect(tester.takeException(), isNull);
  });

  testWidgets('gallery shows the refreshed drift verdict after rating', (
    tester,
  ) async {
    final fake = source();
    await pumpScreen(tester, screen(), source: fake);
    await settle(tester);
    expect(find.text('Camera drift: stable. '), findsOneWidget);
    await tester.tap(find.text('Rate this frame').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text(AlgaeSeverity.moderate.label));
    await settle(tester);
    fake.ratingContext = AlgaeRatingContext.fromJson({
      'calibration': {
        'camera_drift': {
          'verdict': 'drift_suspected',
          'detail': 'Check the camera view against the rated photos.',
        },
      },
    });
    await tester.ensureVisible(find.text('Submit rating'));
    await tester.tap(find.text('Submit rating'));
    await settle(tester);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(
      find.text(
        'Camera drift: suspected drift. Check the camera view against the rated photos.',
      ),
      findsOneWidget,
    );
    expect(fake.callsTo('fetchAlgaeRatingContext'), hasLength(2));
  });

  testWidgets('algae detail opens gallery using the pond identity', (
    tester,
  ) async {
    final fake = await pumpScreen(
      tester,
      const DetailGraphScreen(metricType: 'lux', title: 'Algae'),
    );
    await settle(tester);
    await tester.tap(find.byTooltip('Camera gallery'));
    await settle(tester);
    expect(find.byType(CameraGalleryScreen), findsOneWidget);
    expect(fake.callsTo('fetchFramesForDay').single.args['userId'], 7);
  });

  for (final verdict in [
    'drift_possible',
    'drift_suspected',
    'insufficient_data',
  ]) {
    testWidgets('gallery shows $verdict with server detail', (tester) async {
      await pumpScreen(
        tester,
        screen(),
        source: FakePondDataSource(
          ratingContext: AlgaeRatingContext.fromJson({
            'calibration': {
              'camera_drift': {
                'verdict': verdict,
                'detail': 'Recorded drift detail.',
              },
            },
          }),
        ),
      );
      await settle(tester);
      expect(find.textContaining('Recorded drift detail.'), findsOneWidget);
      final label = switch (verdict) {
        'drift_possible' => 'possible drift',
        'drift_suspected' => 'suspected drift',
        _ => 'not enough rated frames',
      };
      expect(
        find.text('Camera drift: $label. Recorded drift detail.'),
        findsOneWidget,
      );
    });
  }
}
