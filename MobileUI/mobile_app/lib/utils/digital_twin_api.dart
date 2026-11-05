// lib/utils/digital_twin_api.dart
//
// Client for the DigitalTwin Flask service (Backend/DigitalTwin), which
// hosts the server-side water chemistry engine (TAN/NO2/NO3 pools +
// buffering trend). GET /assessment/<user_id> returns the most recent
// WaterChemistryAssessment computed by that service's poller or event
// endpoints - see Backend/DigitalTwin/app.py and engine.py.
// GET /forecast/<user_id> projects that same engine forward assuming
// feeding continues at the pond's recent average rate and no further
// interventions occur, to estimate when a water change would become
// necessary - see engine.py's project_forward().
//
// The service address is DIGITAL_TWIN_BASE_URL in env/*.json - see
// lib/config/app_config.dart.

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../config/app_config.dart';
import 'algae_models.dart';
import 'app_log.dart';
import 'digital_twin_models.dart';

export 'algae_models.dart';
export 'digital_twin_models.dart';
export 'pond_action.dart';

class DigitalTwinApi {
  final String _digitalTwinBaseUrl;
  final http.Client client;
  final String? Function() accessToken;
  DigitalTwinApi({
    http.Client? client,
    String? baseUrl,
    String? Function()? accessToken,
  }) : client = client ?? http.Client(),
       _digitalTwinBaseUrl =
           (baseUrl ?? AppConfig.environment.digitalTwinBaseUrl).replaceFirst(
             RegExp(r'/+$'),
             '',
           ),
       accessToken = accessToken ?? (() => null);

  Map<String, String> get headers => {
    'Content-Type': 'application/json',
    if (accessToken() case final String token) 'Authorization': 'Bearer $token',
  };

  Future<Map<String, dynamic>> request(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final uri = Uri.parse('$_digitalTwinBaseUrl$path');
    final response = await (switch (method) {
      'GET' => client.get(uri, headers: headers),
      'PUT' => client.put(uri, headers: headers, body: jsonEncode(body)),
      'DELETE' => client.delete(uri, headers: headers),
      _ => client.post(uri, headers: headers, body: jsonEncode(body)),
    }).timeout(const Duration(seconds: 10));
    final data = jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw StateError(errorMessageFrom(data, 'Pond service unavailable'));
    }
    return data;
  }

  /// Returns null if there's no assessment yet for this user, or the
  /// DigitalTwin service is unreachable - callers should treat both cases
  /// as "not available yet" rather than a hard error.
  Future<WaterChemistryAssessment?> fetchLatestAssessment(int userId) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/assessments/chemistry',
      );
      final response = await client
          .get(uri, headers: headers)
          .timeout(const Duration(seconds: 8));
      if (response.statusCode == 200) {
        return WaterChemistryAssessment.fromJson(
          jsonDecode(response.body) as Map<String, dynamic>,
        );
      }
      return null;
    } catch (error) {
      log('fetchLatestAssessment failed: $error');
      return null;
    }
  }

  /// Projects the pond's chemistry forward assuming feeding continues at
  /// its recent average rate and no further interventions occur, to
  /// estimate when a water change would become necessary. Returns null
  /// if the service is unreachable OR if it doesn't yet have enough
  /// feeding/sensor history to project (HTTP 422) - callers can't tell
  /// those apart from the return value alone; use fetchForecastOrError
  /// below if you need to show the user *why* it's unavailable.
  Future<WaterChemistryForecast?> fetchForecast(
    int userId, {
    int horizonDays = 21,
  }) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/forecasts/chemistry?horizon_days=$horizonDays',
      );
      final response = await client
          .get(uri, headers: headers)
          .timeout(const Duration(seconds: 8));
      if (response.statusCode == 200) {
        return WaterChemistryForecast.fromJson(
          jsonDecode(response.body) as Map<String, dynamic>,
        );
      }
      return null;
    } catch (error) {
      log('fetchForecast failed: $error');
      return null;
    }
  }

  /// Same as fetchForecast, but surfaces the server's "not enough history
  /// yet" message (HTTP 422) instead of collapsing it to null, for UIs
  /// that want to show that reason rather than a generic "unavailable".
  Future<({WaterChemistryForecast? forecast, String? error})>
  fetchForecastOrError(int userId, {int horizonDays = 21}) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/forecasts/chemistry?horizon_days=$horizonDays',
      );
      final response = await client
          .get(uri, headers: headers)
          .timeout(const Duration(seconds: 8));
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      if (response.statusCode == 200) {
        return (forecast: WaterChemistryForecast.fromJson(body), error: null);
      }
      return (forecast: null, error: errorMessageFrom(body, 'Unavailable'));
    } catch (error) {
      log('fetchForecastOrError failed: $error');
      return (forecast: null, error: 'DigitalTwin service unreachable');
    }
  }

  /// Evaporation + feed lookahead for the Temperature & Feed detail
  /// screen. Returns null if unreachable OR if the pond has no config
  /// yet (HTTP 422) - use fetchEvaporationForecastOrError to tell those
  /// apart.
  Future<EvaporationForecast?> fetchEvaporationForecast(
    int userId, {
    int horizonDays = 14,
    double? depthM,
  }) async {
    final r = await fetchEvaporationForecastOrError(
      userId,
      horizonDays: horizonDays,
      depthM: depthM,
    );
    return r.forecast;
  }

  /// Same, but surfaces the server's "not enough history" reason instead
  /// of collapsing it to null.
  Future<({EvaporationForecast? forecast, String? error})>
  fetchEvaporationForecastOrError(
    int userId, {
    int horizonDays = 14,
    double? depthM,
  }) async {
    try {
      final params = <String, String>{
        'horizon_days': '$horizonDays',
        if (depthM != null) 'depth_m': '$depthM',
      };
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/forecasts/evaporation',
      ).replace(queryParameters: params);

      final response = await client
          .get(uri, headers: headers)
          .timeout(const Duration(seconds: 10));
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      if (response.statusCode == 200) {
        return (forecast: EvaporationForecast.fromJson(body), error: null);
      }
      return (
        forecast: null,
        error: errorMessageFrom(body, 'Forecast unavailable'),
      );
    } catch (error) {
      log('fetchEvaporationForecastOrError failed: $error');
      return (forecast: null, error: 'DigitalTwin service unreachable');
    }
  }

  /// Algae lookahead for the Algal & Solar detail screen, grounded on the
  /// ESP32-CAM HSV history. Returns null if unreachable or if the camera
  /// has never reported (HTTP 422).
  Future<AlgaeForecast?> fetchAlgaeForecast(
    int userId, {
    int horizonDays = 21,
  }) async {
    final r = await fetchAlgaeForecastOrError(userId, horizonDays: horizonDays);
    return r.forecast;
  }

  Future<({AlgaeForecast? forecast, String? error})> fetchAlgaeForecastOrError(
    int userId, {
    int horizonDays = 21,
  }) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/forecasts/algae?horizon_days=$horizonDays',
      );
      final response = await client
          .get(uri, headers: headers)
          .timeout(const Duration(seconds: 10));
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      if (response.statusCode == 200) {
        return (forecast: AlgaeForecast.fromJson(body), error: null);
      }
      return (
        forecast: null,
        error: errorMessageFrom(body, 'Forecast unavailable'),
      );
    } catch (error) {
      log('fetchAlgaeForecastOrError failed: $error');
      return (forecast: null, error: 'DigitalTwin service unreachable');
    }
  }

  /// Fetches the latest camera frame plus rating history and calibration
  /// state - everything AlgaeSeverityRatingCard needs in one round-trip.
  Future<AlgaeRatingContext?> fetchAlgaeRatingContext(int userId) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/ratings/algae',
      );
      final response = await client
          .get(uri, headers: headers)
          .timeout(const Duration(seconds: 10));
      if (response.statusCode == 200) {
        return AlgaeRatingContext.fromJson(
          jsonDecode(response.body) as Map<String, dynamic>,
        );
      }
      return null;
    } catch (error) {
      log('fetchAlgaeRatingContext failed: $error');
      return null;
    }
  }

  /// Submits a human severity rating. Pass the [imageId] of the frame
  /// being rated - do NOT let the server infer it. If the user rates at
  /// 9pm and the newest frame is from 6pm, an inferred pairing silently
  /// corrupts the calibration set with a mismatched (label, measurement)
  /// pair.
  Future<({AlgaeRatingResult? result, String? error})> submitAlgaeRating({
    required int userId,
    required AlgaeSeverity severity,
    int? imageId,
    double? greenRatio,
    String? notes,
  }) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/events/algae-rating',
      );
      final response = await client
          .post(
            uri,
            headers: headers,
            body: jsonEncode({
              'user_id': userId,
              'severity': severity.wire,
              'image_id': ?imageId,
              'green_ratio': ?greenRatio,
              if (notes != null && notes.isNotEmpty) 'notes': notes,
            }),
          )
          .timeout(const Duration(seconds: 10));

      final body = jsonDecode(response.body) as Map<String, dynamic>;
      if (response.statusCode == 200) {
        return (result: AlgaeRatingResult.fromJson(body), error: null);
      }
      return (
        result: null,
        error: errorMessageFrom(body, 'Could not save rating'),
      );
    } catch (_) {
      return (result: null, error: 'DigitalTwin service unreachable');
    }
  }

  /// Reverses the most recent rating, restoring the engine's level,
  /// thresholds and growth fit exactly. Only the most recent rating is
  /// reversible - that is the mis-tap case this exists for.
  Future<bool> undoAlgaeRating({required int userId, int? ratingId}) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/$userId/events/algae-rating/undo',
      );
      final response = await client
          .post(
            uri,
            headers: headers,
            body: jsonEncode({'user_id': userId, 'rating_id': ?ratingId}),
          )
          .timeout(const Duration(seconds: 10));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// Applies the recorded event through /v1; the repository handles history persistence.
  Future<WaterChemistryAssessment?> _postEvent(
    String path,
    Map<String, dynamic> body,
  ) async {
    try {
      final uri = Uri.parse(
        '$_digitalTwinBaseUrl/v1/ponds/${body['user_id']}$path',
      );
      final response = await client
          .post(uri, headers: headers, body: jsonEncode(body))
          .timeout(const Duration(seconds: 8));
      if (response.statusCode == 200) {
        return WaterChemistryAssessment.fromJson(
          (jsonDecode(response.body) as Map<String, dynamic>)['chemistry']
              as Map<String, dynamic>,
        );
      }
      throw StateError(
        errorMessageFrom(jsonDecode(response.body), 'Could not save event'),
      );
    } catch (_) {
      rethrow;
    }
  }

  Future<WaterChemistryAssessment?> logSalt({
    required int userId,
    required double saltGrams,
    String? notes,
    DateTime? timestamp,
    String? eventId,
  }) => _postEvent('/events/salt', {
    'user_id': userId,
    'salt_grams': saltGrams,
    'notes': ?notes,
    if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
    'event_id': ?eventId,
  });

  Future<WaterChemistryAssessment?> logFilterClean({
    required int userId,
    String? notes,
    DateTime? timestamp,
    String? eventId,
  }) => _postEvent('/events/filter-clean', {
    'user_id': userId,
    'notes': ?notes,
    if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
    'event_id': ?eventId,
  });

  Future<WaterChemistryAssessment?> logFeeding({
    required int userId,
    required double foodGrams,
    required double proteinPercent,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/feeding', {
      'user_id': userId,
      'food_grams': foodGrams,
      'protein_percent': proteinPercent,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      'event_id': ?eventId,
      'fish_type': ?fishType,
      'fish_count': ?fishCount,
    });
  }

  Future<WaterChemistryAssessment?> logWaterChange({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/water-change', {
      'user_id': userId,
      'volume_percent': ?volumePercent,
      'volume_litres': ?volumeLitres,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      'event_id': ?eventId,
      'fish_type': ?fishType,
      'fish_count': ?fishCount,
    });
  }

  Future<WaterChemistryAssessment?> logTopUp({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/top-up', {
      'user_id': userId,
      'volume_percent': ?volumePercent,
      'volume_litres': ?volumeLitres,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      'event_id': ?eventId,
      'fish_type': ?fishType,
      'fish_count': ?fishCount,
    });
  }

  Future<WaterChemistryAssessment?> logAlgalScrub({
    required int userId,
    String? scrubType,
    DateTime? timestamp,
    String? eventId,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/algal-scrub', {
      'user_id': userId,
      'scrub_type': ?scrubType,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      'event_id': ?eventId,
      'fish_type': ?fishType,
      'fish_count': ?fishCount,
    });
  }
}
