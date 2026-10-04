// PondDashboard parses every /v1 dashboard response in
// test/fixtures/v1_dashboard.json. The backend writes that file from its
// synthetic ponds (Backend/tests/api/test_dashboard.py) and checks that the
// local database produces the same responses
// (Backend/tests/sql/test_sql_dashboard.py), so this is the last link of
// SQL rows -> storage -> API model -> this parser.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/pond_dashboard.dart';

void main() {
  final cases = (jsonDecode(
    File('test/fixtures/v1_dashboard.json').readAsStringSync(),
  ) as Map<String, dynamic>)
      .map((name, body) =>
          MapEntry(name, PondDashboard.fromJson(body as Map<String, dynamic>)));

  test('parses every fixture response', () {
    expect(cases.keys, containsAll(<String>[
      'complete',
      'no_tds',
      'stale_channel',
      'missing_station',
      'old_rows',
      'second_pond',
      'daylight_missing_uv',
      'night',
      'complete_with_assessments',
    ]));
  });

  test('complete pond: every channel fresh with its own times', () {
    final d = cases['complete']!;
    expect(d.pondId, 9101);
    expect(d.readings.ph.value, 7.5);
    expect(d.readings.ph.isFresh, isTrue);
    expect(d.readings.ph.ingestedAt, DateTime.utc(2026, 10, 3, 4, 25));
    expect(d.readings.ph.sampleTime, isNull);
    expect(d.readings.ph.sampleTimeBasis, 'unknown');
    expect(d.readings.waterTemp.unit, 'degC');
    expect(d.weather.airTemperature.value, 30.7);
    expect(d.weather.airTemperature.stationId, 'S43');
    expect(d.weather.uvIndex.isMeasured, isTrue);
    expect(d.weather.uvIndex.value, 7);
    expect(d.forecast.twoHour.text, 'Partly Cloudy (Day)');
    expect(d.forecast.twoHour.issuedAt, DateTime.utc(2026, 10, 3, 3, 40));
    expect(d.forecast.twentyFourHourGeneral.temperatureHighC, 35);
    expect(d.forecast.outlook.map((o) => o.date),
        List.generate(4, (i) => DateTime(2026, 10, 4 + i)));
    expect(d.nextActions, isEmpty);
    expect(d.assessments.chemistry, isNull);
  });

  test('no TDS row: value null, status missing', () {
    final tds = cases['no_tds']!.readings.tds;
    expect(tds.value, isNull);
    expect(tds.status, 'missing');
    expect(tds.ingestedAt, isNull);
  });

  test('stale channel keeps its value and its own time; -1 light is invalid', () {
    final r = cases['stale_channel']!.readings;
    expect(r.ph.status, 'stale');
    expect(r.ph.value, 7.75);
    expect(r.ph.ingestedAt, isNot(r.tds.ingestedAt));
    expect(r.lux.status, 'invalid');
    expect(r.lux.value, isNull);
    expect(cases['stale_channel']!.assessments.hypoxia.level, 'unknown');
  });

  test('missing station: no borrowed reading', () {
    final d = cases['missing_station']!;
    expect(d.stations.rainfall, isNull);
    expect(d.weather.rainfall.status, 'missing');
    expect(d.weather.rainfall.value, isNull);
    expect(d.forecast.twoHour.status, 'missing');
  });

  test('old rows: stale readings, unknown issue and observation times', () {
    final d = cases['old_rows']!;
    expect(d.readings.ph.status, 'stale');
    expect(d.weather.airTemperature.observedAt, isNull);
    expect(d.forecast.twoHour.status, 'expired');
    expect(d.forecast.twoHour.issuedAt, isNull);
  });

  test('two ponds keep their own values', () {
    final a = cases['complete']!;
    final b = cases['second_pond']!;
    expect(b.pondId, 9106);
    expect(b.readings.ph.value, 6.5);
    expect(a.readings.ph.value, 7.5);
    expect(b.weather.rainfall.stationId, 'S24');
    expect(b.forecast.twoHour.text, 'Showers');
  });

  test('UV: missing in daylight, derived 0 at night', () {
    final day = cases['daylight_missing_uv']!.weather.uvIndex;
    expect(day.status, 'missing');
    expect(day.value, isNull);
    expect(day.daylight, isTrue);
    final night = cases['night']!.weather.uvIndex;
    expect(night.status, 'night_derived');
    expect(night.value, 0);
    expect(night.isMeasured, isFalse);
  });

  test('assessments parse with the existing chemistry model', () {
    final a = cases['complete_with_assessments']!.assessments;
    expect(a.chemistry!.category, 'Watch');
    expect(a.chemistry!.tanPpm, 0.31);
    expect(a.evaporation!['days_to_topup'], 6);
    expect(a.algae!['green_ratio'], 0.03);
    expect(a.hypoxia.level, 'none');
  });
}
