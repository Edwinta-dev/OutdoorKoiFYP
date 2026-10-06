import 'dart:typed_data';
import 'package:mobile_app/data/pond_profile.dart';
// A PondDataSource for tests. Each call answers from the fields below,
// so a test sets up exactly the state it wants:
//   - leave a field at its default for the "data" state,
//   - set it to null / empty for the "empty" state,
//   - name the call in [hanging] for the "loading" state (the future
//     never completes),
//   - name the call in [failing] for the "error" state (the future
//     throws [FakeSourceError]).
// Every call is appended to [calls] so tests can assert what was sent.

import 'dart:async';

import 'package:mobile_app/data/pond_data_source.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/utils/pond_camera_storage.dart';

import 'fixtures.dart';

class FakeSourceError implements Exception {
  const FakeSourceError(this.call);
  final String call;
  @override
  String toString() => 'FakeSourceError($call)';
}

class FakeCall {
  const FakeCall(this.name, this.args);
  final String name;
  final Map<String, Object?> args;
  @override
  String toString() => '$name($args)';
}

class FakePondDataSource extends PondDataSource {
  FakePondDataSource({
    Map<String, dynamic>? dashboardPayload,
    this.historicalPayload,
    List<String>? speciesNames,
    this.userProfileResponse = const {'ClosestStations': null},
    this.frame,
    this.frames = const [],
    this.frameError,
    this.assessment,
    this.evaporationForecast,
    this.evaporationError,
    this.algaeForecast,
    this.algaeError,
    this.ratingContext,
    this.ratingResult,
    this.ratingError,
    this.undoSucceeds = true,
    this.eventAssessment,
    Set<String>? hanging,
    Set<String>? failing,
  }) : dashboardPayload = dashboardPayload ?? Fixtures.dashboardPayload(),
       speciesNames = speciesNames ?? const ['Japanese Koi (Kohaku)'],
       hanging = hanging ?? <String>{},
       failing = failing ?? <String>{};

  Map<String, dynamic>? dashboardPayload;
  Map<String, dynamic>? historicalPayload;
  List<String> speciesNames;
  Map<String, dynamic> userProfileResponse;
  PondCameraFrame? frame;
  List<PondCameraFrame> frames;
  String? frameError;
  WaterChemistryAssessment? assessment;
  EvaporationForecast? evaporationForecast;
  String? evaporationError;
  AlgaeForecast? algaeForecast;
  String? algaeError;
  AlgaeRatingContext? ratingContext;
  AlgaeRatingResult? ratingResult;
  String? ratingError;
  bool undoSucceeds;
  WaterChemistryAssessment? eventAssessment;

  /// Call names whose futures never complete.
  final Set<String> hanging;

  /// Call names whose futures throw.
  final Set<String> failing;

  final List<FakeCall> calls = [];

  Iterable<FakeCall> callsTo(String name) => calls.where((c) => c.name == name);

  Future<T> _answer<T>(String name, Map<String, Object?> args, T value) {
    if (name.startsWith('log')) {
      final kind = {
        'logFeeding': 'FEEDING',
        'logSalt': 'SALT',
        'logFilterClean': 'FILTER_CLEAN',
        'logWaterChange': 'WATER_CHANGE',
        'logTopUp': 'WATER_TOPUP',
        'logAlgalScrub': 'ALGAE_SCRUB',
      }[name];
      calls.add(
        FakeCall('insertIntervention', {
          'userID': args['userId'],
          'event_type': kind,
          'event_id': args['eventId'],
          'event_timestamp': (args['timestamp'] as DateTime?)
              ?.toIso8601String(),
          if (args.containsKey('saltGrams')) 'salt_grams': args['saltGrams'],
          if (args.containsKey('notes')) 'notes': args['notes'],
          if (args.containsKey('foodGrams')) 'food_grams': args['foodGrams'],
          if (args.containsKey('proteinPercent'))
            'protein_percentage': args['proteinPercent'],
          if (args.containsKey('volumePercent'))
            'volume_percentage': args['volumePercent'],
          if (args.containsKey('volumeLitres'))
            'volume_litres': args['volumeLitres'],
          if (args.containsKey('scrubType')) 'algae_method': args['scrubType'],
        }),
      );
      if (hanging.contains('insertIntervention')) return Completer<T>().future;
      if (failing.contains('insertIntervention')) {
        return Future<T>.error(const FakeSourceError('insertIntervention'));
      }
    }
    calls.add(FakeCall(name, args));
    if (hanging.contains(name)) return Completer<T>().future;
    if (failing.contains(name)) return Future<T>.error(FakeSourceError(name));
    return Future<T>.value(value);
  }

  @override
  Future<PondProfileResponse> fetchPondProfile(int pondId) => _answer(
    'fetchPondProfile',
    {'pondId': pondId},
    PondProfileResponse.fromJson({
      'pond_id': pondId,
      'source': 'userdata',
      'current': {'volume_l': 1200, 'biomass_g': 5000},
      'history': [],
    }),
  );

  @override
  Future<Map<String, dynamic>?> fetchDashboardPayload(String userId) async {
    final payload = await _answer('fetchDashboardPayload', {
      'userId': userId,
    }, dashboardPayload);
    if (payload == null) return null;
    return {
      ...payload,
      'assessments': {
        'chemistry': assessment == null
            ? null
            : {
                'status': assessment!.status,
                'category': assessment!.category,
                'advisory': assessment!.advisory,
              },
      },
    };
  }

  @override
  Future<List<Map<String, dynamic>>> fetchFishProfiles(
    int pondId,
    List<String> species,
  ) => _answer('fetchFishProfiles', {'pondId': pondId, 'species': species}, []);
  @override
  Future<void> deletePond(int pondId) =>
      _answer<void>('deletePond', {'pondId': pondId}, null);

  @override
  Future<Map<String, dynamic>?> fetchHistoricalGraphPayload(
    int userId,
    int days,
  ) => _answer('fetchHistoricalGraphPayload', {
    'userId': userId,
    'days': days,
  }, historicalPayload);

  @override
  Future<void> insertIntervention(Map<String, dynamic> payload) =>
      _answer<void>('insertIntervention', Map.of(payload), null);

  @override
  Future<List<String>> fetchSpeciesNames() =>
      _answer('fetchSpeciesNames', const {}, speciesNames);

  @override
  Future<Map<String, dynamic>> upsertUserProfile(Map<String, dynamic> row) =>
      _answer('upsertUserProfile', Map.of(row), userProfileResponse);

  @override
  Future<({PondCameraFrame? frame, String? error})> fetchLatestFrame(
    int userId,
  ) => _answer(
    'fetchLatestFrame',
    {'userId': userId},
    (frame: frame, error: frameError),
  );

  @override
  Future<List<PondCameraFrame>> fetchFramesForDay(int userId, DateTime day) =>
      _answer('fetchFramesForDay', {'userId': userId, 'day': day}, frames);

  @override
  Future<WaterChemistryAssessment?> fetchLatestAssessment(int userId) =>
      _answer('fetchLatestAssessment', {'userId': userId}, assessment);

  @override
  Future<({EvaporationForecast? forecast, String? error})>
  fetchEvaporationForecastOrError(int userId) => _answer(
    'fetchEvaporationForecastOrError',
    {'userId': userId},
    (forecast: evaporationForecast, error: evaporationError),
  );

  @override
  Future<({AlgaeForecast? forecast, String? error})> fetchAlgaeForecastOrError(
    int userId,
  ) => _answer(
    'fetchAlgaeForecastOrError',
    {'userId': userId},
    (forecast: algaeForecast, error: algaeError),
  );

  @override
  Future<AlgaeRatingContext?> fetchAlgaeRatingContext(int userId) =>
      _answer('fetchAlgaeRatingContext', {'userId': userId}, ratingContext);

  @override
  Future<({AlgaeRatingResult? result, String? error})> submitAlgaeRating({
    required int userId,
    required AlgaeSeverity severity,
    int? imageId,
    double? greenRatio,
  }) => _answer(
    'submitAlgaeRating',
    {
      'userId': userId,
      'severity': severity,
      'imageId': imageId,
      'greenRatio': greenRatio,
    },
    (result: ratingResult, error: ratingError),
  );

  @override
  Future<bool> undoAlgaeRating({required int userId, int? ratingId}) => _answer(
    'undoAlgaeRating',
    {'userId': userId, 'ratingId': ratingId},
    undoSucceeds,
  );

  @override
  Future<WaterChemistryAssessment?> logSalt({
    required int userId,
    required double saltGrams,
    String? notes,
    DateTime? timestamp,
    required String eventId,
  }) => _answer('logSalt', {
    'userId': userId,
    'saltGrams': saltGrams,
    'notes': notes,
    'timestamp': timestamp,
    'eventId': eventId,
  }, eventAssessment);

  @override
  Future<WaterChemistryAssessment?> logFilterClean({
    required int userId,
    String? notes,
    DateTime? timestamp,
    required String eventId,
  }) => _answer('logFilterClean', {
    'userId': userId,
    'notes': notes,
    'timestamp': timestamp,
    'eventId': eventId,
  }, eventAssessment);

  @override
  Future<WaterChemistryAssessment?> logFeeding({
    required int userId,
    required double foodGrams,
    required double proteinPercent,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _answer('logFeeding', {
    'userId': userId,
    'foodGrams': foodGrams,
    'proteinPercent': proteinPercent,
    'timestamp': timestamp,
    'eventId': eventId,
    'fishType': fishType,
    'fishCount': fishCount,
  }, eventAssessment);

  @override
  Future<WaterChemistryAssessment?> logWaterChange({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _answer('logWaterChange', {
    'userId': userId,
    'volumePercent': volumePercent,
    'volumeLitres': volumeLitres,
    'timestamp': timestamp,
    'eventId': eventId,
    'fishType': fishType,
    'fishCount': fishCount,
  }, eventAssessment);

  @override
  Future<WaterChemistryAssessment?> logTopUp({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _answer('logTopUp', {
    'userId': userId,
    'volumePercent': volumePercent,
    'volumeLitres': volumeLitres,
    'timestamp': timestamp,
    'eventId': eventId,
    'fishType': fishType,
    'fishCount': fishCount,
  }, eventAssessment);

  @override
  Future<WaterChemistryAssessment?> logAlgalScrub({
    required int userId,
    String? scrubType,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _answer('logAlgalScrub', {
    'userId': userId,
    'scrubType': scrubType,
    'timestamp': timestamp,
    'eventId': eventId,
    'fishType': fishType,
    'fishCount': fishCount,
  }, eventAssessment);
  @override
  Future<void> uploadSpeciesPhoto(String path, Uint8List bytes) =>
      _answer<void>('uploadSpeciesPhoto', {'path': path, 'bytes': bytes}, null);
  @override
  Future<void> deleteSpeciesPhoto(String path) =>
      _answer<void>('deleteSpeciesPhoto', {'path': path}, null);
}
