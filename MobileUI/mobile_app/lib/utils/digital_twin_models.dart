// lib/utils/digital_twin_models.dart
//
// The water chemistry and evaporation responses of the DigitalTwin service,
// parsed from its JSON by the client in digital_twin_api.dart (which
// re-exports this file).

/// The message from a DigitalTwin error body. The service sends
/// {"error": {"code", "message", "details"}}; a service not yet
/// redeployed sends `{"error": "<message>"}`. Anything else gives fallback.
String errorMessageFrom(Object? body, String fallback) {
  if (body is! Map) return fallback;
  final error = body['error'];
  if (error is Map && error['message'] is String) {
    return error['message'] as String;
  }
  if (error is String && error.isNotEmpty) return error;
  return fallback;
}

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
  final int riskScore;

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
    required this.riskScore,
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
      riskScore: (json['risk_score'] as num?)?.toInt() ?? 0,
    );
  }
}

/// One simulated day from `GET /forecast/<user_id>` - mirrors an entry in
/// project_forward()'s "trajectory" list in engine.py.
class ForecastDay {
  final int daysFromNow;
  final double tanPpm;
  final double no2Ppm;
  final double no3Ppm;
  final int riskScore;
  final bool nitriteOverride;
  final double? tempCAssumed;
  final double? luxAssumed;

  const ForecastDay({
    required this.daysFromNow,
    required this.tanPpm,
    required this.no2Ppm,
    required this.no3Ppm,
    required this.riskScore,
    required this.nitriteOverride,
    required this.tempCAssumed,
    required this.luxAssumed,
  });

  factory ForecastDay.fromJson(Map<String, dynamic> json) {
    double? asDouble(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));

    return ForecastDay(
      daysFromNow: (json['days_from_now'] as num?)?.toInt() ?? 0,
      tanPpm: asDouble(json['tan_ppm']) ?? 0.0,
      no2Ppm: asDouble(json['no2_ppm']) ?? 0.0,
      no3Ppm: asDouble(json['no3_ppm']) ?? 0.0,
      riskScore: (json['risk_score'] as num?)?.toInt() ?? 0,
      nitriteOverride: json['nitrite_override'] == true,
      tempCAssumed: asDouble(json['temp_c_assumed']),
      luxAssumed: asDouble(json['lux_assumed']),
    );
  }
}

/// Mirrors the dict returned by WaterChemistryEngine.project_forward() /
/// `GET /forecast/<user_id>`. All the "daysFromNow" fields are null if that
/// threshold is never crossed within the requested horizon - treat null
/// as "not projected to be needed within the window you asked for", not
/// as "never needed".
class WaterChemistryForecast {
  final int? predictedActionDaysFromNow;
  final int? firstWatchDaysFromNow;
  final int? firstHighRiskDaysFromNow;
  final int? firstNitriteDaysFromNow;
  final List<ForecastDay> trajectory;
  final String caveat;
  final double avgDailyTanMg;

  const WaterChemistryForecast({
    required this.predictedActionDaysFromNow,
    required this.firstWatchDaysFromNow,
    required this.firstHighRiskDaysFromNow,
    required this.firstNitriteDaysFromNow,
    required this.trajectory,
    required this.caveat,
    required this.avgDailyTanMg,
  });

  factory WaterChemistryForecast.fromJson(Map<String, dynamic> json) {
    int? asInt(dynamic v) => v == null ? null : (v as num).toInt();
    double asDouble(dynamic v) => v == null
        ? 0.0
        : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0.0);

    return WaterChemistryForecast(
      predictedActionDaysFromNow: asInt(json['predicted_action_days_from_now']),
      firstWatchDaysFromNow: asInt(json['first_watch_days_from_now']),
      firstHighRiskDaysFromNow: asInt(json['first_high_risk_days_from_now']),
      firstNitriteDaysFromNow: asInt(json['first_nitrite_days_from_now']),
      trajectory:
          (json['trajectory'] as List?)
              ?.map((e) => ForecastDay.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      caveat: json['caveat']?.toString() ?? '',
      avgDailyTanMg: asDouble(json['avg_daily_tan_mg']),
    );
  }
}

/// ---------------------------------------------------------------------
/// EVAPORATION + FEED LOOKAHEAD  (`GET /forecast/evaporation/<user_id>`)
/// Mirrors EvaporationFeedEngine.project_forward() in
/// Backend/DigitalTwin/evaporation_engine.py
/// ---------------------------------------------------------------------

class EvaporationDay {
  final int daysFromNow;
  final double evaporationMm;
  final double rainOffsetMm;
  final double cumulativeLossLitres;
  final double cumulativeLossPct;
  final double waterTempCAssumed;
  final double feedCapGrams;
  final String feedNote;

  const EvaporationDay({
    required this.daysFromNow,
    required this.evaporationMm,
    required this.rainOffsetMm,
    required this.cumulativeLossLitres,
    required this.cumulativeLossPct,
    required this.waterTempCAssumed,
    required this.feedCapGrams,
    required this.feedNote,
  });

  factory EvaporationDay.fromJson(Map<String, dynamic> j) {
    double d(dynamic v) => v == null
        ? 0.0
        : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0.0);
    return EvaporationDay(
      daysFromNow: (j['days_from_now'] as num?)?.toInt() ?? 0,
      evaporationMm: d(j['evaporation_mm']),
      rainOffsetMm: d(j['rain_offset_mm']),
      cumulativeLossLitres: d(j['cumulative_loss_litres']),
      cumulativeLossPct: d(j['cumulative_loss_pct']),
      waterTempCAssumed: d(j['water_temp_c_assumed']),
      feedCapGrams: d(j['feed_cap_grams']),
      feedNote: j['feed_note']?.toString() ?? '',
    );
  }
}

/// Result of cross-checking the physical evaporation model against the
/// pond's own measured TDS slope. verdict is one of: corroborated,
/// under_predicted, over_predicted, insufficient_data.
class TdsCrossCheck {
  final String verdict;
  final double? predictedSlope;
  final double? observedSlope;
  final String confidence;

  const TdsCrossCheck({
    required this.verdict,
    required this.predictedSlope,
    required this.observedSlope,
    required this.confidence,
  });

  factory TdsCrossCheck.fromJson(Map<String, dynamic> j) {
    double? d(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));
    return TdsCrossCheck(
      verdict: j['verdict']?.toString() ?? 'insufficient_data',
      predictedSlope: d(j['predicted_tds_slope_ppm_per_day']),
      observedSlope: d(j['observed_tds_slope_ppm_per_day']),
      confidence: j['confidence']?.toString() ?? 'low',
    );
  }

  bool get isCorroborated => verdict == 'corroborated';

  String get humanLabel {
    switch (verdict) {
      case 'corroborated':
        return 'Confirmed by TDS trend';
      case 'under_predicted':
        return 'TDS rising faster than evaporation explains';
      case 'over_predicted':
        return 'TDS not rising as expected';
      default:
        return 'Not enough TDS history to confirm';
    }
  }
}

class EvaporationForecast {
  final int? predictedTopupDaysFromNow;
  final int? firstWatchDaysFromNow;
  final int? firstActionDaysFromNow;
  final double avgEvaporationMmPerDay;
  final double avgLossLitresPerDay;
  final double startingLossPct;
  final double surfaceAreaM2;
  final double assumedDepthM;
  final double volumeLitres;
  final int daysUsingRealForecast;
  final String? lastTopupAt;
  final List<EvaporationDay> trajectory;
  final TdsCrossCheck tdsCrossCheck;

  const EvaporationForecast({
    required this.predictedTopupDaysFromNow,
    required this.firstWatchDaysFromNow,
    required this.firstActionDaysFromNow,
    required this.avgEvaporationMmPerDay,
    required this.avgLossLitresPerDay,
    required this.startingLossPct,
    required this.surfaceAreaM2,
    required this.assumedDepthM,
    required this.volumeLitres,
    required this.daysUsingRealForecast,
    required this.lastTopupAt,
    required this.trajectory,
    required this.tdsCrossCheck,
  });

  factory EvaporationForecast.fromJson(Map<String, dynamic> j) {
    double d(dynamic v) => v == null
        ? 0.0
        : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0.0);
    int? i(dynamic v) => v == null ? null : (v as num).toInt();
    return EvaporationForecast(
      predictedTopupDaysFromNow: i(j['predicted_topup_days_from_now']),
      firstWatchDaysFromNow: i(j['first_watch_days_from_now']),
      firstActionDaysFromNow: i(j['first_action_days_from_now']),
      avgEvaporationMmPerDay: d(j['avg_evaporation_mm_per_day']),
      avgLossLitresPerDay: d(j['avg_loss_litres_per_day']),
      startingLossPct: d(j['starting_loss_pct']),
      surfaceAreaM2: d(j['surface_area_m2']),
      assumedDepthM: d(j['assumed_depth_m']),
      volumeLitres: d(j['volume_litres']),
      daysUsingRealForecast:
          (j['days_using_real_forecast'] as num?)?.toInt() ?? 0,
      lastTopupAt: j['last_topup_at']?.toString(),
      trajectory:
          (j['trajectory'] as List?)
              ?.map((e) => EvaporationDay.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      tdsCrossCheck: TdsCrossCheck.fromJson(
        Map<String, dynamic>.from(j['tds_cross_check'] as Map? ?? {}),
      ),
    );
  }

  /// Today's recommended feed ceiling - the first projected day is the
  /// closest thing to "now" the engine produces.
  double? get feedCapTodayGrams =>
      trajectory.isEmpty ? null : trajectory.first.feedCapGrams;

  String? get feedNoteToday =>
      trajectory.isEmpty ? null : trajectory.first.feedNote;
}

/// ---------------------------------------------------------------------
/// ALGAE LOOKAHEAD  (`GET /forecast/algae/<user_id>`)
/// Mirrors AlgaeGrowthEngine.project_forward() in
/// Backend/DigitalTwin/algae_engine.py. Grounded on the ESP32-CAM HSV
/// green-ratio series in imageTable.
/// ---------------------------------------------------------------------
