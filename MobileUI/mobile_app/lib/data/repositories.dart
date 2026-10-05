import 'pond_profile.dart';
import 'dart:typed_data';
import '../utils/digital_twin_api.dart';
import '../utils/pond_camera_storage.dart';

abstract interface class DashboardRepository {
  Future<Map<String, dynamic>?> fetchDashboardPayload(String userId);
}

abstract interface class TelemetryHistoryRepository {
  Future<Map<String, dynamic>?> fetchHistoricalGraphPayload(
    int userId,
    int days,
  );
}

abstract interface class PondProfileRepository {
  Future<PondProfileResponse> fetchPondProfile(int pondId);
  Future<void> deletePond(int pondId);
  Future<List<Map<String, dynamic>>> fetchFishProfiles(
    int pondId,
    List<String> species,
  );
  Future<void> uploadSpeciesPhoto(String path, Uint8List bytes);
  Future<void> deleteSpeciesPhoto(String path);
  Future<List<String>> fetchSpeciesNames();
  Future<Map<String, dynamic>> upsertUserProfile(Map<String, dynamic> row);
}

abstract interface class CameraFramesRepository {
  Future<({PondCameraFrame? frame, String? error})> fetchLatestFrame(
    int userId,
  );
}

abstract interface class AssessmentsRepository {
  Future<WaterChemistryAssessment?> fetchLatestAssessment(int userId);
}

abstract interface class ForecastsRepository {
  Future<({EvaporationForecast? forecast, String? error})>
  fetchEvaporationForecastOrError(int userId);
  Future<({AlgaeForecast? forecast, String? error})> fetchAlgaeForecastOrError(
    int userId,
  );
}

abstract interface class RatingsRepository {
  Future<AlgaeRatingContext?> fetchAlgaeRatingContext(int userId);
  Future<({AlgaeRatingResult? result, String? error})> submitAlgaeRating({
    required int userId,
    required AlgaeSeverity severity,
    int? imageId,
    double? greenRatio,
  });
  Future<bool> undoAlgaeRating({required int userId, int? ratingId});
}

abstract interface class EventsRepository {
  Future<void> insertIntervention(Map<String, dynamic> payload);
  Future<WaterChemistryAssessment?> logSalt({
    required int userId,
    required double saltGrams,
    String? notes,
    DateTime? timestamp,
    required String eventId,
  });
  Future<WaterChemistryAssessment?> logFilterClean({
    required int userId,
    String? notes,
    DateTime? timestamp,
    required String eventId,
  });
  Future<WaterChemistryAssessment?> logFeeding({
    required int userId,
    required double foodGrams,
    required double proteinPercent,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  });
  Future<WaterChemistryAssessment?> logWaterChange({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  });
  Future<WaterChemistryAssessment?> logTopUp({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  });
  Future<WaterChemistryAssessment?> logAlgalScrub({
    required int userId,
    String? scrubType,
    DateTime? timestamp,
    required String eventId,
    String? fishType,
    int? fishCount,
  });
}
