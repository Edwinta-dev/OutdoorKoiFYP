// The "Last hour" card of raw readings on DetailGraphScreen: each state
// (loading, data, empty, failed), the 30 s auto-refresh and the screen's
// refresh button.
import 'dart:convert';

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile_app/data/pond_data_source.dart';
import 'package:mobile_app/data/recent_readings.dart';
import 'package:mobile_app/screens/detail_graph_screen.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/widgets/detail_graph/last_hour_card.dart';
import 'package:mobile_app/widgets/detail_graph/timeline_header_selector.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

const _recent = 'fetchRecentSensorReadings';
final _latestAt = DateTime.utc(2026, 11, 10, 13, 5, 30);

Widget _card({String sensor = 'pH'}) =>
    Scaffold(body: LastHourCard(userId: 7, sensorType: sensor));

Finder _inCard(Finder matching) =>
    find.descendant(of: find.byType(LastHourCard), matching: matching);

void main() {
  test('RecentReadings parses the route response', () {
    final parsed = RecentReadings.fromJson(Fixtures.recentReadings());
    expect((parsed.sensor, parsed.minutes), ('pH', 60));
    expect(parsed.readings.map((r) => r.value), [7.10, 7.18, 7.31, 7.42]);
    expect(parsed.latest!.at, _latestAt);
    expect((parsed.minimum, parsed.maximum), (7.10, 7.42));
    expect(
      RecentReadings.fromJson({
        'sensor': 'pH',
        'minutes': 60,
        'readings': [],
      }).latest,
      isNull,
    );
  });

  test('the live source asks the twin for the sensor and window', () async {
    final requests = <http.Request>[];
    final client = MockClient((request) async {
      requests.add(request);
      return http.Response(jsonEncode(Fixtures.recentReadings()), 200);
    });
    addTearDown(client.close);
    final source = LivePondDataSource(
      api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
    );
    final json = await source.fetchRecentSensorReadings(7, 'pH', 60);
    expect(requests.single.method, 'GET');
    expect(requests.single.url.path, '/v1/ponds/7/sensors/recent');
    expect(requests.single.url.queryParameters, {
      'sensor': 'pH',
      'minutes': '60',
    });
    expect(RecentReadings.fromJson(json).readings, hasLength(4));
  });

  testWidgets('data: trace, latest value with its time, min and max', (
    tester,
  ) async {
    final fake = await pumpScreen(tester, _card());
    await settle(tester);

    expect(find.text('Last hour'), findsOneWidget);
    expect(
      find.text('7.42 · ${LastHourCard.formatTime(_latestAt)}'),
      findsOneWidget,
    );
    expect(find.text('Min 7.10'), findsOneWidget);
    expect(find.text('Max 7.42'), findsOneWidget);
    expect(_inCard(find.byType(LineChart)), findsOneWidget);
    expect(fake.callsTo(_recent).single.args, {
      'userId': 7,
      'sensor': 'pH',
      'minutes': 60,
    });
  });

  testWidgets('data: a single reading still draws', (tester) async {
    await pumpScreen(
      tester,
      _card(sensor: 'temp'),
      source: FakePondDataSource(
        recentReadings: Fixtures.recentReadings(values: [28.44]),
      ),
    );
    await settle(tester);
    expect(
      find.text('28.4 °C · ${LastHourCard.formatTime(_latestAt)}'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('loading: progress while the first fetch is pending', (
    tester,
  ) async {
    await pumpScreen(
      tester,
      _card(),
      source: FakePondDataSource(hanging: {_recent}),
    );
    await settle(tester);
    expect(find.text('Loading the last hour of readings'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(find.byType(LineChart), findsNothing);
  });

  testWidgets('empty: says to check the sensor', (tester) async {
    await pumpScreen(
      tester,
      _card(),
      source: FakePondDataSource(
        recentReadings: Fixtures.recentReadings(values: []),
      ),
    );
    await settle(tester);
    expect(
      find.text('No readings in the last hour. Check the sensor is online.'),
      findsOneWidget,
    );
    expect(find.byType(LineChart), findsNothing);
    expect(find.byKey(const ValueKey('last-hour-latest')), findsNothing);
  });

  testWidgets('failed: error with a retry that loads the readings', (
    tester,
  ) async {
    final fake = await pumpScreen(
      tester,
      _card(),
      source: FakePondDataSource(failing: {_recent}),
    );
    await settle(tester);
    expect(find.text('Last hour could not be loaded'), findsOneWidget);
    expect(find.byType(LineChart), findsNothing);

    fake.failing.clear();
    await tester.tap(find.text('Retry'));
    await settle(tester);
    expect(fake.callsTo(_recent), hasLength(2));
    expect(find.text('Last hour could not be loaded'), findsNothing);
    expect(find.byType(LineChart), findsOneWidget);
  });

  testWidgets('refreshes every 30 s and shows the new latest reading', (
    tester,
  ) async {
    final fake = await pumpScreen(tester, _card());
    await settle(tester);
    expect(fake.callsTo(_recent), hasLength(1));

    final next = _latestAt.add(const Duration(seconds: 30));
    fake.recentReadings = Fixtures.recentReadings(
      values: [7.18, 7.31, 7.42, 6.95],
      latest: next,
    );
    await tester.pump(const Duration(seconds: 30));
    await settle(tester);
    expect(fake.callsTo(_recent), hasLength(2));
    expect(
      find.text('6.95 · ${LastHourCard.formatTime(next)}'),
      findsOneWidget,
    );
    expect(find.text('Min 6.95'), findsOneWidget);

    await tester.pump(const Duration(seconds: 30));
    await settle(tester);
    expect(fake.callsTo(_recent), hasLength(3));
  });

  testWidgets('a failed refresh keeps the readings and says so', (
    tester,
  ) async {
    final fake = await pumpScreen(tester, _card());
    await settle(tester);

    fake.failing.add(_recent);
    await tester.pump(const Duration(seconds: 30));
    await settle(tester);
    expect(fake.callsTo(_recent), hasLength(2));
    expect(find.byType(LineChart), findsOneWidget);
    expect(find.byKey(const ValueKey('last-hour-latest')), findsOneWidget);
    expect(
      find.text('Update failed. Showing the last readings received.'),
      findsOneWidget,
    );
  });

  for (final (metric, sensor) in [
    ('ph', 'pH'),
    ('temperature', 'temp'),
    ('lux', 'LUX'),
  ]) {
    testWidgets('$metric screen: the card is first and reads $sensor; '
        'the refresh button re-fetches it', (tester) async {
      final fake = await pumpScreen(
        tester,
        DetailGraphScreen(metricType: metric, title: 'Detail'),
        source: FakePondDataSource(
          historicalPayload: Fixtures.historicalPayload(),
        ),
      );
      await settle(tester);

      expect(find.byType(LastHourCard), findsOneWidget);
      expect(
        tester.getTopLeft(find.byType(LastHourCard)).dy,
        lessThan(tester.getTopLeft(find.byType(TimelineHeaderSelector)).dy),
      );
      expect(fake.callsTo(_recent).single.args['sensor'], sensor);

      await tester.tap(
        find.descendant(
          of: find.byType(AppBar),
          matching: find.byIcon(Icons.refresh),
        ),
      );
      await settle(tester);
      expect(fake.callsTo(_recent), hasLength(2));
    });
  }
}
