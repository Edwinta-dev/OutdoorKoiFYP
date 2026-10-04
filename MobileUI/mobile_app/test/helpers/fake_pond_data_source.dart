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
    calls.add(FakeCall(name, args));
    if (hanging.contains(name)) return Completer<T>().future;
    if (failing.contains(name)) return Future<T>.error(FakeSourceError(name));
    return Future<T>.value(value);
  }

  @override
  Future<Map<String, dynamic>?> fetchDashboardPayload(String userId) =>
      _answer('fetchDashboardPayload', {'userId': userId}, dashboardPayload);

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
  Future<WaterChemistryAssessment?> logFeeding({
    required int userId,
    required double foodGrams,
    required double proteinPercent,
    DateTime? timestamp,
    String? eventId,
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
    String? eventId,
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
    String? eventId,
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
    String? eventId,
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
}
