// lib/data/pond_data_source.dart
//
// Every remote read and write the dashboard, detail screens, log sheet,
// rating card and onboarding make, behind one interface.
//
// The app uses LivePondDataSource, which forwards to the Supabase client
// and DigitalTwinApi exactly as those screens did before. Tests wrap a
// screen in a PondDataScope holding a fake (test/helpers/), so a screen
// can be built in each state - loading, empty, data, error - without a
// network or an initialised Supabase client.
//
// The two writes whose payload is built in a screen (the intervention
// insert and the onboarding profile upsert) are implemented next to that
// payload, in quick_log_modals.dart and onboarding_screen.dart: the schema
// check in Backend/tests/storage/test_schema.py matches a write's map
// keys to table columns within one file.

import 'package:flutter/widgets.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../screens/onboarding_screen.dart' show upsertUserProfileRow;
import '../utils/digital_twin_api.dart';
import '../utils/pond_camera_storage.dart';
import '../widgets/modals/quick_log_modals.dart' show insertPondIntervention;

abstract class PondDataSource {
  const PondDataSource();

  /// Supabase RPC get_bundled_dashboard_payload.
  Future<Map<String, dynamic>?> fetchDashboardPayload(String userId);

  /// Supabase RPC get_historical_graph_payload.
  Future<Map<String, dynamic>?> fetchHistoricalGraphPayload(
    int userId,
    int days,
  );

  /// Row insert into pondInterventions.
  Future<void> insertIntervention(Map<String, dynamic> payload);

  /// Species names from Fish_Database, for onboarding.
  Future<List<String>> fetchSpeciesNames();

  /// UserData upsert at onboarding; returns the written row.
  Future<Map<String, dynamic>> upsertUserProfile(Map<String, dynamic> row);

  Future<({PondCameraFrame? frame, String? error})> fetchLatestFrame(
    int userId,
  );

  Future<WaterChemistryAssessment?> fetchLatestAssessment(int userId);

  Future<({EvaporationForecast? forecast, String? error})>
  fetchEvaporationForecastOrError(int userId);

  Future<({AlgaeForecast? forecast, String? error})> fetchAlgaeForecastOrError(
    int userId,
  );

  Future<AlgaeRatingContext?> fetchAlgaeRatingContext(int userId);

  Future<({AlgaeRatingResult? result, String? error})> submitAlgaeRating({
    required int userId,
    required AlgaeSeverity severity,
    int? imageId,
    double? greenRatio,
  });

  Future<bool> undoAlgaeRating({required int userId, int? ratingId});

  Future<WaterChemistryAssessment?> logSalt({
    required int userId,
    required double saltGrams,
    String? notes,
    DateTime? timestamp,
    String? eventId,
  });

  Future<WaterChemistryAssessment?> logFilterClean({
    required int userId,
    String? notes,
    DateTime? timestamp,
    String? eventId,
  });

  Future<WaterChemistryAssessment?> logFeeding({
    required int userId,
    required double foodGrams,
    required double proteinPercent,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  });

  Future<WaterChemistryAssessment?> logWaterChange({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  });

  Future<WaterChemistryAssessment?> logTopUp({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  });

  Future<WaterChemistryAssessment?> logAlgalScrub({
    required int userId,
    String? scrubType,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  });
}

/// The production data source: Supabase plus the DigitalTwin service.
class LivePondDataSource extends PondDataSource {
  const LivePondDataSource();

  SupabaseClient get _client => Supabase.instance.client;

  @override
  Future<Map<String, dynamic>?> fetchDashboardPayload(String userId) async {
    final response = await _client.rpc(
      'get_bundled_dashboard_payload',
      params: {'p_user_id': userId},
    );
    return response == null ? null : Map<String, dynamic>.from(response as Map);
  }

  @override
  Future<Map<String, dynamic>?> fetchHistoricalGraphPayload(
    int userId,
    int days,
  ) async {
    final response = await _client.rpc(
      'get_historical_graph_payload',
      params: {'p_userid': userId, 'p_days': days},
    );
    return response == null ? null : Map<String, dynamic>.from(response as Map);
  }

  @override
  Future<void> insertIntervention(Map<String, dynamic> payload) =>
      insertPondIntervention(payload);

  @override
  Future<List<String>> fetchSpeciesNames() async {
    final List<dynamic> response = await _client
        .from('Fish_Database')
        .select('Title');
    return response.map((row) => row['Title'].toString()).toList();
  }

  @override
  Future<Map<String, dynamic>> upsertUserProfile(Map<String, dynamic> row) =>
      upsertUserProfileRow(row);

  @override
  Future<({PondCameraFrame? frame, String? error})> fetchLatestFrame(
    int userId,
  ) => PondCameraStorage.fetchLatestFrame(userId: userId);

  @override
  Future<WaterChemistryAssessment?> fetchLatestAssessment(int userId) =>
      DigitalTwinApi.fetchLatestAssessment(userId);

  @override
  Future<({EvaporationForecast? forecast, String? error})>
  fetchEvaporationForecastOrError(int userId) =>
      DigitalTwinApi.fetchEvaporationForecastOrError(userId);

  @override
  Future<({AlgaeForecast? forecast, String? error})> fetchAlgaeForecastOrError(
    int userId,
  ) => DigitalTwinApi.fetchAlgaeForecastOrError(userId);

  @override
  Future<AlgaeRatingContext?> fetchAlgaeRatingContext(int userId) =>
      DigitalTwinApi.fetchAlgaeRatingContext(userId);

  @override
  Future<({AlgaeRatingResult? result, String? error})> submitAlgaeRating({
    required int userId,
    required AlgaeSeverity severity,
    int? imageId,
    double? greenRatio,
  }) => DigitalTwinApi.submitAlgaeRating(
    userId: userId,
    severity: severity,
    imageId: imageId,
    greenRatio: greenRatio,
  );

  @override
  Future<bool> undoAlgaeRating({required int userId, int? ratingId}) =>
      DigitalTwinApi.undoAlgaeRating(userId: userId, ratingId: ratingId);

  @override
  Future<WaterChemistryAssessment?> logSalt({
    required int userId,
    required double saltGrams,
    String? notes,
    DateTime? timestamp,
    String? eventId,
  }) => DigitalTwinApi.logSalt(
    userId: userId,
    saltGrams: saltGrams,
    notes: notes,
    timestamp: timestamp,
    eventId: eventId,
  );

  @override
  Future<WaterChemistryAssessment?> logFilterClean({
    required int userId,
    String? notes,
    DateTime? timestamp,
    String? eventId,
  }) => DigitalTwinApi.logFilterClean(
    userId: userId,
    notes: notes,
    timestamp: timestamp,
    eventId: eventId,
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
  }) => DigitalTwinApi.logFeeding(
    userId: userId,
    foodGrams: foodGrams,
    proteinPercent: proteinPercent,
    timestamp: timestamp,
    eventId: eventId,
    fishType: fishType,
    fishCount: fishCount,
  );

  @override
  Future<WaterChemistryAssessment?> logWaterChange({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  }) => DigitalTwinApi.logWaterChange(
    userId: userId,
    volumePercent: volumePercent,
    volumeLitres: volumeLitres,
    timestamp: timestamp,
    eventId: eventId,
    fishType: fishType,
    fishCount: fishCount,
  );

  @override
  Future<WaterChemistryAssessment?> logTopUp({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  }) => DigitalTwinApi.logTopUp(
    userId: userId,
    volumePercent: volumePercent,
    volumeLitres: volumeLitres,
    timestamp: timestamp,
    eventId: eventId,
    fishType: fishType,
    fishCount: fishCount,
  );

  @override
  Future<WaterChemistryAssessment?> logAlgalScrub({
    required int userId,
    String? scrubType,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  }) => DigitalTwinApi.logAlgalScrub(
    userId: userId,
    scrubType: scrubType,
    timestamp: timestamp,
    eventId: eventId,
    fishType: fishType,
    fishCount: fishCount,
  );
}

/// Supplies a PondDataSource to the widgets below it. Without one in the
/// tree, [PondDataScope.of] returns the live source, so the app itself
/// does not need to install a scope.
class PondDataScope extends InheritedWidget {
  const PondDataScope({super.key, required this.source, required super.child});

  final PondDataSource source;

  static const PondDataSource _live = LivePondDataSource();

  /// Safe to call from initState: it does not register a dependency.
  static PondDataSource of(BuildContext context) =>
      context.getInheritedWidgetOfExactType<PondDataScope>()?.source ?? _live;

  @override
  bool updateShouldNotify(PondDataScope oldWidget) =>
      source != oldWidget.source;
}
