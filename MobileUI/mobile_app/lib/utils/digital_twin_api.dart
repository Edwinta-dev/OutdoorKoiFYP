// lib/utils/digital_twin_api.dart
//
// Client for the DigitalTwin Flask service (Backend/DigitalTwin), which
// hosts the server-side water chemistry engine (TAN/NO2/NO3 pools +
// buffering trend). GET /assessment/<user_id> returns the most recent
// WaterChemistryAssessment computed by that service's poller or event
// endpoints - see Backend/DigitalTwin/app.py and engine.py.
//
// Same LAN-IP-on-a-dev-machine convention as the ESP32-CAM's flask_server
// constant (Embedded/CameraTest/Camera_Arduino_Sketch/CameraMain.ino) -
// override at build time with --dart-define=DIGITAL_TWIN_BASE_URL=... if
// the Flask host changes.

import 'dart:convert';
import 'package:http/http.dart' as http;

const String _digitalTwinBaseUrl = String.fromEnvironment(
  'DIGITAL_TWIN_BASE_URL',
  defaultValue: 'http://192.168.68.69:8080',
);

/// Mirrors WaterChemistryAssessment.to_dict() in Backend/DigitalTwin/engine.py
class WaterChemistryAssessment {
  final String status; // "Green" | "Amber" | "Red"
  final String category; // e.g. "Stable", "Watch", "High Risk", "Nitrite Risk"
  final double tanPpm;
  final double no2Ppm;
  final double no3Ppm;
  final double? phReactivity;
  final double? reactivityTrend;
  final double? tdsTrend;
  final List<String> sensorWarnings;
  final String advisory;
  final bool addHardenerNow;

  const WaterChemistryAssessment({
    required this.status,
    required this.category,
    required this.tanPpm,
    required this.no2Ppm,
    required this.no3Ppm,
    required this.phReactivity,
    required this.reactivityTrend,
    required this.tdsTrend,
    required this.sensorWarnings,
    required this.advisory,
    required this.addHardenerNow,
  });

  factory WaterChemistryAssessment.fromJson(Map<String, dynamic> json) {
    double? asDouble(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));

    return WaterChemistryAssessment(
      status: json['status']?.toString() ?? 'Green',
      category: json['category']?.toString() ?? 'Stable',
      tanPpm: asDouble(json['tan_ppm']) ?? 0.0,
      no2Ppm: asDouble(json['no2_ppm']) ?? 0.0,
      no3Ppm: asDouble(json['no3_ppm']) ?? 0.0,
      phReactivity: asDouble(json['ph_reactivity']),
      reactivityTrend: asDouble(json['reactivity_trend']),
      tdsTrend: asDouble(json['tds_trend']),
      sensorWarnings:
          (json['sensor_warnings'] as List?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
      advisory: json['advisory']?.toString() ?? '',
      addHardenerNow: json['add_hardener_now'] == true,
    );
  }
}

class DigitalTwinApi {
  /// Returns null if there's no assessment yet for this user, or the
  /// DigitalTwin service is unreachable - callers should treat both cases
  /// as "not available yet" rather than a hard error.
  static Future<WaterChemistryAssessment?> fetchLatestAssessment(
    int userId,
  ) async {
    try {
      final uri = Uri.parse('$_digitalTwinBaseUrl/assessment/$userId');
      final response = await http.get(uri).timeout(const Duration(seconds: 8));
      if (response.statusCode == 200) {
        return WaterChemistryAssessment.fromJson(
          jsonDecode(response.body) as Map<String, dynamic>,
        );
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  /// Pushes a logged pond intervention into the chemistry engine so its
  /// TAN/NO2/NO3 pools reflect it. Best-effort: returns null (rather than
  /// throwing) on any network/server error so a slow or unreachable
  /// DigitalTwin host never blocks the caller's own success path - the
  /// event's historical record (pondInterventions table) is written
  /// separately and is the source of truth for "was this logged".
  static Future<WaterChemistryAssessment?> _postEvent(
    String path,
    Map<String, dynamic> body,
  ) async {
    try {
      final uri = Uri.parse('$_digitalTwinBaseUrl$path');
      final response = await http
          .post(
            uri,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(body),
          )
          .timeout(const Duration(seconds: 8));
      if (response.statusCode == 200) {
        return WaterChemistryAssessment.fromJson(
          jsonDecode(response.body) as Map<String, dynamic>,
        );
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  static Future<WaterChemistryAssessment?> logFeeding({
    required int userId,
    required double foodGrams,
    required double proteinPercent,
    DateTime? timestamp,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/feeding', {
      'user_id': userId,
      'food_grams': foodGrams,
      'protein_percent': proteinPercent,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      if (fishType != null) 'fish_type': fishType,
      if (fishCount != null) 'fish_count': fishCount,
    });
  }

  static Future<WaterChemistryAssessment?> logWaterChange({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/water-change', {
      'user_id': userId,
      if (volumePercent != null) 'volume_percent': volumePercent,
      if (volumeLitres != null) 'volume_litres': volumeLitres,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      if (fishType != null) 'fish_type': fishType,
      if (fishCount != null) 'fish_count': fishCount,
    });
  }

  static Future<WaterChemistryAssessment?> logTopUp({
    required int userId,
    double? volumePercent,
    double? volumeLitres,
    DateTime? timestamp,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/top-up', {
      'user_id': userId,
      if (volumePercent != null) 'volume_percent': volumePercent,
      if (volumeLitres != null) 'volume_litres': volumeLitres,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      if (fishType != null) 'fish_type': fishType,
      if (fishCount != null) 'fish_count': fishCount,
    });
  }

  static Future<WaterChemistryAssessment?> logAlgalScrub({
    required int userId,
    String? scrubType,
    DateTime? timestamp,
    String? fishType,
    int? fishCount,
  }) {
    return _postEvent('/events/algal-scrub', {
      'user_id': userId,
      if (scrubType != null) 'scrub_type': scrubType,
      if (timestamp != null) 'timestamp': timestamp.toUtc().toIso8601String(),
      if (fishType != null) 'fish_type': fishType,
      if (fishCount != null) 'fish_count': fishCount,
    });
  }
}
