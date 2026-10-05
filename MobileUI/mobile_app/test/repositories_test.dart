import 'dart:convert';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile_app/data/local_profile_repository.dart';
import 'package:mobile_app/data/pond_data_source.dart';
import 'package:mobile_app/data/pond_profile.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/data/telemetry_history.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/utils/pond_camera_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';

void main() {
  test(
    'onboarding keeps location in storage and writes profile chemistry through /v1',
    () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        return http.Response(
          jsonEncode(
            request.url.host == 'storage.invalid'
                ? {
                    'ClosestStations': {'rainfall': 'offline-station'},
                  }
                : <String, dynamic>{},
          ),
          request.url.host == 'storage.invalid' ? 200 : 201,
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
      final source = LivePondDataSource(
        client: db,
        api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
      );
      final result = await source.upsertUserProfile({
        'userID': '7',
        'volume': '1200',
        'biomass': '5',
        'latitude': '1.3',
        'longitude': '103.8',
        'manualpostallocation': 18956,
      });
      expect(requests.map((r) => r.url.path), [
        '/rest/v1/UserData',
        '/v1/ponds/7/profile',
      ]);
      expect(jsonDecode(requests.last.body), {
        'volume_l': 1200.0,
        'biomass_g': 5000.0,
      });
      expect(requests.last.method, 'PUT');
      expect(result['ClosestStations'], {'rainfall': 'offline-station'});
    },
  );

  test('historical telemetry uses the uncovered storage RPC', () async {
    final client = MockClient((request) async {
      expect(request.url.path, '/rest/v1/rpc/get_historical_graph_payload');
      expect(jsonDecode(request.body), {'p_userid': 7, 'p_days': 30});
      return http.Response(
        jsonEncode(Fixtures.historicalPayload()),
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
    final source = LivePondDataSource(
      client: db,
      api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
    );
    final result = await source.fetchHistoricalGraphPayload(7, 30);
    expect(TelemetryHistory.fromJson(result!).days, hasLength(28));
  });

  for (final stored in <Object>['7', 7]) {
    test(
      'local storage loads pond identity stored as ${stored.runtimeType}',
      () async {
        SharedPreferences.setMockInitialValues({
          'userID': stored,
          'fishCount': 5,
        });
        final repository = PreferencesProfileRepository();
        expect((await repository.load()).pondId, 7);
        expect((await repository.load()).fishCount, 5);
        await repository.save({'userID': 8, 'isOnboarded': true});
        expect((await repository.load()).pondId, 8);
        expect((await repository.load()).isOnboarded, isTrue);
      },
    );
  }

  test('missing or invalid stored identity is unknown', () async {
    for (final values in <Map<String, Object>>[
      {},
      {'userID': 'invalid'},
    ]) {
      expect((await FakeLocalProfileRepository(values).load()).pondId, isNull);
    }
  });

  test('dashboard shares one request and refresh invalidates it', () async {
    final fake = FakePondDataSource(assessment: Fixtures.assessment());
    final container = ProviderContainer(
      retry: (_, _) => null,
      overrides: [
        pondDataSourceProvider.overrideWithValue(fake),
        localProfileRepositoryProvider.overrideWithValue(
          FakeLocalProfileRepository({'userID': 7}),
        ),
      ],
    );
    addTearDown(container.dispose);
    final first = await container.read(pondDashboardProvider.future);
    final presentation = await container.read(dashboardProvider.future);
    expect(first!.readings.waterTemp.value, 27.4);
    expect(
      presentation!['chemistry_assessment'],
      isA<WaterChemistryAssessment>(),
    );
    expect(fake.callsTo('fetchDashboardPayload'), hasLength(1));
    expect(fake.callsTo('fetchLatestAssessment'), isEmpty);
    container.invalidate(pondDashboardProvider);
    await container.read(pondDashboardProvider.future);
    expect(fake.callsTo('fetchDashboardPayload'), hasLength(2));
  });

  test('changing stored pond identity replaces the dashboard cache', () async {
    final local = FakeLocalProfileRepository({'userID': 7});
    final fake = FakePondDataSource();
    final container = ProviderContainer(
      retry: (_, _) => null,
      overrides: [
        pondDataSourceProvider.overrideWithValue(fake),
        localProfileRepositoryProvider.overrideWithValue(local),
      ],
    );
    addTearDown(container.dispose);
    await container.read(pondDashboardProvider.future);
    await local.save({'userID': 8});
    container.invalidate(localProfileProvider);
    await container.read(pondDashboardProvider.future);
    expect(fake.callsTo('fetchDashboardPayload').map((c) => c.args['userId']), [
      '7',
      '8',
    ]);
  });

  test('all read providers can use offline repository fakes', () async {
    final fake = FakePondDataSource(
      historicalPayload: Fixtures.historicalPayload(),
    );
    final container = ProviderContainer(
      retry: (_, _) => null,
      overrides: [
        pondDataSourceProvider.overrideWithValue(fake),
        localProfileRepositoryProvider.overrideWithValue(
          FakeLocalProfileRepository({'userID': '7'}),
        ),
      ],
    );
    addTearDown(container.dispose);
    await container.read(speciesProvider.future);
    await container.read(historyProvider((pond: 7, days: 30)).future);
    await container.read(assessmentProvider(7).future);
    await container.read(evaporationForecastProvider(7).future);
    await container.read(algaeForecastProvider(7).future);
    await container.read(cameraFrameProvider(7).future);
    await container.read(ratingContextProvider(7).future);
    expect(fake.calls.map((c) => c.name), [
      'fetchSpeciesNames',
      'fetchHistoricalGraphPayload',
      'fetchLatestAssessment',
      'fetchEvaporationForecastOrError',
      'fetchAlgaeForecastOrError',
      'fetchLatestFrame',
      'fetchAlgaeRatingContext',
    ]);
  });

  test(
    'dashboard uses only the versioned endpoint and current session',
    () async {
      final requests = <http.Request>[];
      var token = 'offline-session-one';
      final client = MockClient((request) async {
        requests.add(request);
        return http.Response(jsonEncode(Fixtures.dashboardPayload()), 200);
      });
      addTearDown(client.close);
      final source = LivePondDataSource(
        api: DigitalTwinApi(
          client: client,
          baseUrl: 'http://pond.invalid/',
          accessToken: () => token,
        ),
      );
      await source.fetchDashboardPayload('7');
      token = 'offline-session-two';
      await source.fetchDashboardPayload('7');
      expect(requests.map((r) => r.url.path), [
        '/v1/ponds/7/dashboard',
        '/v1/ponds/7/dashboard',
      ]);
      expect(requests.map((r) => r.headers['Authorization']), [
        'Bearer offline-session-one',
        'Bearer offline-session-two',
      ]);
    },
  );

  test('assessment, forecasts and ratings use /v1', () async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final api = DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid');
    await api.fetchLatestAssessment(7);
    await api.fetchForecast(7);
    await api.fetchEvaporationForecastOrError(7);
    await api.fetchAlgaeForecastOrError(7);
    await api.fetchAlgaeRatingContext(7);
    await api.submitAlgaeRating(
      userId: 7,
      severity: AlgaeSeverity.minor,
      imageId: 10,
      greenRatio: 0.1,
    );
    await api.undoAlgaeRating(userId: 7, ratingId: 3);
    expect(paths, [
      '/v1/ponds/7/assessments/chemistry',
      '/v1/ponds/7/forecasts/chemistry',
      '/v1/ponds/7/forecasts/evaporation',
      '/v1/ponds/7/forecasts/algae',
      '/v1/ponds/7/ratings/algae',
      '/v1/ponds/7/events/algae-rating',
      '/v1/ponds/7/events/algae-rating/undo',
    ]);
  });

  test('forecast errors preserve the server reason', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({
          'error': {'message': 'Not enough camera history.'},
        }),
        422,
      ),
    );
    addTearDown(client.close);
    final api = DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid');
    final result = await api.fetchAlgaeForecastOrError(7);
    expect(result.forecast, isNull);
    expect(result.error, 'Not enough camera history.');
  });

  test(
    'rating submission pins the visible frame and undo pins the rating',
    () async {
      final bodies = <Map<String, dynamic>>[];
      final client = MockClient((request) async {
        bodies.add(jsonDecode(request.body) as Map<String, dynamic>);
        return http.Response('{}', 200);
      });
      addTearDown(client.close);
      final api = DigitalTwinApi(
        client: client,
        baseUrl: 'http://pond.invalid',
      );
      await api.submitAlgaeRating(
        userId: 7,
        severity: AlgaeSeverity.minor,
        imageId: 10,
        greenRatio: 0.1,
      );
      await api.undoAlgaeRating(userId: 7, ratingId: 3);
      expect(bodies.first, {
        'user_id': 7,
        'severity': 'minor',
        'image_id': 10,
        'green_ratio': 0.1,
      });
      expect(bodies.last, {'user_id': 7, 'rating_id': 3});
    },
  );

  test(
    'event repository records history before /v1 and reads chemistry from the response',
    () async {
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        return request.url.host == 'storage.invalid'
            ? http.Response('', 201, request: request)
            : http.Response(
                jsonEncode({
                  'chemistry': {'tan_ppm': 0.42, 'category': 'Watch'},
                }),
                200,
              );
      });
      final db = SupabaseClient(
        'http://storage.invalid',
        'offline-key',
        httpClient: client,
      );
      addTearDown(db.dispose);
      addTearDown(client.close);
      final source = LivePondDataSource(
        client: db,
        api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
      );
      final result = await source.logSalt(
        userId: 7,
        saltGrams: 15,
        eventId: 'offline-event',
        timestamp: DateTime.utc(2026, 8, 12, 9, 30),
      );
      expect(requests.map((r) => r.url.path), [
        '/rest/v1/pondInterventions',
        '/v1/ponds/7/events/salt',
      ]);
      final row = jsonDecode(requests.first.body) as Map<String, dynamic>;
      final event = jsonDecode(requests.last.body) as Map<String, dynamic>;
      expect(row['event_id'], event['event_id']);
      expect(row['salt_grams'], event['salt_grams']);
      expect(row['event_timestamp'], event['timestamp']);
      expect(result!.tanPpm, 0.42);
    },
  );

  test('failed history persistence prevents an API write', () async {
    final requests = <http.Request>[];
    final client = MockClient((request) async {
      requests.add(request);
      return http.Response(
        '{"message":"Storage unavailable","code":"offline"}',
        503,
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
    final source = LivePondDataSource(
      client: db,
      api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
    );
    await expectLater(
      source.logFilterClean(userId: 7, eventId: 'offline-event'),
      throwsA(isA<PostgrestException>()),
    );
    expect(requests, hasLength(1));
  });

  test(
    'saved history survives an unreachable API for worker reconciliation',
    () async {
      final client = MockClient(
        (request) async => request.url.host == 'storage.invalid'
            ? http.Response('', 201, request: request)
            : throw const SocketException('offline'),
      );
      final db = SupabaseClient(
        'http://storage.invalid',
        'offline-key',
        httpClient: client,
      );
      addTearDown(db.dispose);
      addTearDown(client.close);
      final source = LivePondDataSource(
        client: db,
        api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
      );
      expect(
        await source.logFilterClean(userId: 7, eventId: 'offline-event'),
        isNull,
      );
    },
  );

  test('profile read uses /v1 and loads older optional fields', () async {
    final client = MockClient((request) async {
      expect(request.url.path, '/v1/ponds/7/profile');
      return http.Response(
        jsonEncode({
          'pond_id': 7,
          'source': 'userdata',
          'current': {'volume_l': 1200, 'biomass_g': 5000},
          'history': [],
        }),
        200,
      );
    });
    addTearDown(client.close);
    final source = LivePondDataSource(
      api: DigitalTwinApi(client: client, baseUrl: 'http://pond.invalid'),
    );
    final profile = await source.fetchPondProfile(7);
    expect(profile.current.volumeL, 1200);
    expect(profile.current.depthM, isNull);
    expect(profile.history, isEmpty);
  });

  test('profile rows retain every contract field', () {
    final response = PondProfileResponse.fromJson({
      'pond_id': 7,
      'source': 'pond_profile',
      'current': {
        'volume_l': 1200,
        'biomass_g': 5000,
        'depth_m': 0.8,
        'aeration': true,
      },
      'history': [
        {
          'id': 1,
          'pond_id': 7,
          'source': 'owner',
          'created_at': '2026-08-12T00:00:00Z',
          'effective_from': '2026-08-12T00:00:00Z',
          'volume_l': 1200,
          'biomass_g': 5000,
          'fish_type': 'Koi',
          'fish_count': 5,
          'tap_tds_ppm': 120,
          'tap_nitrate_ppm': 2,
        },
      ],
    });
    expect(response.current.depthM, 0.8);
    expect(response.current.aeration, isTrue);
    final row = response.history.single;
    expect(row.id, 1);
    expect(row.pondId, 7);
    expect(row.source, 'owner');
    expect(row.createdAt, row.effectiveFrom);
    expect(row.fishType, 'Koi');
    expect(row.fishCount, 5);
    expect(row.tapTdsPpm, 120);
    expect(row.tapNitratePpm, 2);
  });

  test('typed history retains maintenance notes and accepts older rows', () {
    final history = TelemetryHistory.fromJson({
      'daily_trends': [
        {
          'sensor_type': 'TDS',
          'date': '2026-08-12',
          'avg_value': 200,
          'after_maintenance': true,
        },
      ],
      'interventions': [
        {
          'event_type': 'SALT',
          'timestamp': '2026-08-12T09:30:00Z',
          'salt_grams': 15,
          'notes': 'Added salt',
        },
      ],
    });
    expect(history.days.single.average, 200);
    expect(history.days.single.toJson()['after_maintenance'], isTrue);
    expect(history.interventions.single.toJson()['salt_grams'], 15);
    expect(history.interventions.single.toJson()['notes'], 'Added salt');
    expect(history.interventions.single.isMajorReset, isFalse);
    expect(TelemetryHistory.fromJson({}).days, isEmpty);
  });

  test('camera fromJson loads older rows with optional metadata', () {
    final frame = PondCameraFrame.fromJson({
      'imageURL': 'http://images.invalid/pond.jpg',
      'current_state': '["base", 0.1]',
    });
    expect(frame.id, isNull);
    expect(frame.greenRatio, isNull);
    expect(frame.capturedAt, isNull);
    expect(frame.state, 'base');
    expect(frame.imageUrl, 'http://images.invalid/pond.jpg');
  });

  test('screens and widgets have no storage imports or singleton reads', () {
    for (final folder in ['lib/screens', 'lib/widgets']) {
      for (final file
          in Directory(folder)
              .listSync(recursive: true)
              .whereType<File>()
              .where((f) => f.path.endsWith('.dart'))) {
        expect(
          file.readAsStringSync(),
          isNot(contains('Supabase.instance')),
          reason: file.path,
        );
        expect(
          file.readAsStringSync(),
          isNot(contains('SharedPreferences')),
          reason: file.path,
        );
        expect(
          file.readAsStringSync(),
          isNot(contains('supabase_flutter')),
          reason: file.path,
        );
        expect(
          file.readAsStringSync(),
          isNot(contains('shared_preferences')),
          reason: file.path,
        );
      }
    }
  });
}
