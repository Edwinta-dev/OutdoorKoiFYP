import 'pond_profile.dart';
import 'dart:typed_data';
import 'repositories.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../utils/digital_twin_api.dart';
import '../utils/pond_camera_storage.dart';

abstract class PondDataSource
    implements
        DashboardRepository,
        TelemetryHistoryRepository,
        PondProfileRepository,
        CameraFramesRepository,
        AssessmentsRepository,
        ForecastsRepository,
        RatingsRepository,
        EventsRepository {
  const PondDataSource();
}

/// The production data source: Supabase plus the DigitalTwin service.
class LivePondDataSource extends PondDataSource {
  final DigitalTwinApi api;
  final SupabaseClient? client;
  LivePondDataSource({DigitalTwinApi? api, this.client})
    : api =
          api ??
          DigitalTwinApi(
            accessToken: () =>
                (client ?? Supabase.instance.client).auth.currentSession?.accessToken,
          );

  @override
  Future<PondProfileResponse> fetchPondProfile(int pondId) async =>
      PondProfileResponse.fromJson(
        await api.request('GET', '/v1/ponds/$pondId/profile'),
      );

  SupabaseClient get _client => client ?? Supabase.instance.client;

  @override
  Future<Map<String, dynamic>?> fetchDashboardPayload(String userId) async {
    return api.request('GET', '/v1/ponds/$userId/dashboard');
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
      _client.from('pondInterventions').insert(payload);

  @override
  Future<List<String>> fetchSpeciesNames() async {
    final List<dynamic> response = await _client
        .from('Fish_Database')
        .select('Title');
    return response.map((row) => row['Title'].toString()).toList();
  }

  @override
  Future<Map<String, dynamic>> upsertUserProfile(
    Map<String, dynamic> row,
  ) async {
    // Location and account linkage have no /v1 write endpoint.
    final written = await _client
        .from('UserData')
        .upsert(row)
        .select()
        .single();
    await api.request('PUT', "/v1/ponds/${row['userID']}/profile", {
      'volume_l': double.parse('${row['volume']}'),
      'biomass_g': double.parse('${row['biomass']}') * 1000,
    });
    return written;
  }

  @override
  Future<({PondCameraFrame? frame, String? error})> fetchLatestFrame(
    int userId,
  ) => PondCameraStorage.fetchLatestFrame(userId: userId, client: _client);

  @override
  Future<WaterChemistryAssessment?> fetchLatestAssessment(int userId) =>
      api.fetchLatestAssessment(userId);

  @override
  Future<({EvaporationForecast? forecast, String? error})>
  fetchEvaporationForecastOrError(int userId) =>
      api.fetchEvaporationForecastOrError(userId);

  @override
  Future<({AlgaeForecast? forecast, String? error})> fetchAlgaeForecastOrError(
    int userId,
  ) => api.fetchAlgaeForecastOrError(userId);

  @override
  Future<AlgaeRatingContext?> fetchAlgaeRatingContext(int userId) =>
      api.fetchAlgaeRatingContext(userId);

  @override
  Future<({AlgaeRatingResult? result, String? error})> submitAlgaeRating({
    required int userId,
    required AlgaeSeverity severity,
    int? imageId,
    double? greenRatio,
  }) => api.submitAlgaeRating(
    userId: userId,
    severity: severity,
    imageId: imageId,
    greenRatio: greenRatio,
  );

  @override
  Future<bool> undoAlgaeRating({required int userId, int? ratingId}) =>
      api.undoAlgaeRating(userId: userId, ratingId: ratingId);

  @override
  Future<WaterChemistryAssessment?> logSalt({
    required int userId,
    required double saltGrams,
    String? notes,
    DateTime? timestamp,
    required String eventId,
  }) => _recordEvent(
    userId,
    'SALT',
    timestamp,
    eventId,
    {'salt_grams': saltGrams, 'notes': notes},
    () => api.logSalt(
      userId: userId,
      saltGrams: saltGrams,
      notes: notes,
      timestamp: timestamp,
      eventId: eventId,
    ),
  );

  @override
  Future<WaterChemistryAssessment?> logFilterClean({
    required int userId,
    String? notes,
    DateTime? timestamp,
    required String eventId,
  }) => _recordEvent(
    userId,
    'FILTER_CLEAN',
    timestamp,
    eventId,
    {'notes': notes},
    () => api.logFilterClean(
      userId: userId,
      notes: notes,
      timestamp: timestamp,
      eventId: eventId,
    ),
  );

  @override
  Future<WaterChemistryAssessment?> logFeeding({
    required int userId,
    required double foodGrams,
    required double proteinPercent,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _recordEvent(
    userId,
    'FEEDING',
    timestamp,
    eventId,
    {'food_grams': foodGrams, 'protein_percentage': proteinPercent},
    () => api.logFeeding(
      userId: userId,
      foodGrams: foodGrams,
      proteinPercent: proteinPercent,
      timestamp: timestamp,
      eventId: eventId,
      fishType: fishType,
      fishCount: fishCount,
    ),
  );

  @override
  Future<WaterChemistryAssessment?> logWaterChange({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _recordEvent(
    userId,
    'WATER_CHANGE',
    timestamp,
    eventId,
    {'volume_percentage': volumePercent, 'volume_litres': volumeLitres},
    () => api.logWaterChange(
      userId: userId,
      volumePercent: volumePercent,
      volumeLitres: volumeLitres,
      timestamp: timestamp,
      eventId: eventId,
      fishType: fishType,
      fishCount: fishCount,
    ),
  );

  @override
  Future<WaterChemistryAssessment?> logTopUp({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _recordEvent(
    userId,
    'WATER_TOPUP',
    timestamp,
    eventId,
    {'volume_percentage': volumePercent, 'volume_litres': volumeLitres},
    () => api.logTopUp(
      userId: userId,
      volumePercent: volumePercent,
      volumeLitres: volumeLitres,
      timestamp: timestamp,
      eventId: eventId,
      fishType: fishType,
      fishCount: fishCount,
    ),
  );

  @override
  Future<WaterChemistryAssessment?> logAlgalScrub({
    required int userId,
    String? scrubType,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  }) => _recordEvent(
    userId,
    'ALGAE_SCRUB',
    timestamp,
    eventId,
    {'algae_method': scrubType},
    () => api.logAlgalScrub(
      userId: userId,
      scrubType: scrubType,
      timestamp: timestamp,
      eventId: eventId,
      fishType: fishType,
      fishCount: fishCount,
    ),
  );

  Future<WaterChemistryAssessment?> _recordEvent(
    int pond,
    String kind,
    DateTime? timestamp,
    String eventId,
    Map<String, dynamic> fields,
    Future<WaterChemistryAssessment?> Function() push,
  ) async {
    final payload = {
      'userID': pond,
      'event_type': kind,
      'event_id': eventId,
      'event_timestamp': (timestamp ?? DateTime.now())
          .toUtc()
          .toIso8601String(),
      ...fields,
    };
    await insertIntervention(payload);
    // The worker can apply the saved event if the immediate API call fails.
    try {
      return await push();
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> deletePond(int pondId) async {
    // The API has no account/profile deletion endpoint.
    await _client.from('UserData').delete().eq('userID', pondId);
  }

  @override
  Future<List<Map<String, dynamic>>> fetchFishProfiles(
    int pondId,
    List<String> species,
  ) async {
    if (species.isEmpty) return [];
    final response = await _client
        .from('Fish_Database')
        .select()
        .inFilter('Title', species);
    final profiles = List<Map<String, dynamic>>.from(response);
    final bucket = _client.storage.from('pond-images');
    List<FileObject> files = [];
    try {
      files = await bucket.list(path: '$pondId');
    } catch (_) {
      /* Legacy gallery may be empty. */
    }
    for (final profile in profiles) {
      final title = '${profile['Title']}'.replaceAll(' ', '_');
      final pattern = RegExp('^${RegExp.escape(title)}_[0-9]+\\.jpg\$');
      final urls = files
          .where((f) => pattern.hasMatch(f.name))
          .map((f) => bucket.getPublicUrl('$pondId/${f.name}'))
          .toList();
      if (urls.isEmpty && profile['Image URL'] != null) {
        urls.add('${profile['Image URL']}');
      }
      profile['ImageUrls'] = urls;
    }
    return profiles;
  }

  @override
  Future<void> uploadSpeciesPhoto(String path, Uint8List bytes) async {
    await _client.storage
        .from('pond-images')
        .uploadBinary(
          path,
          bytes,
          fileOptions: const FileOptions(
            contentType: 'image/jpeg',
            upsert: true,
          ),
        );
  }

  @override
  Future<void> deleteSpeciesPhoto(String path) async {
    await _client.storage.from('pond-images').remove([path]);
  }
}
