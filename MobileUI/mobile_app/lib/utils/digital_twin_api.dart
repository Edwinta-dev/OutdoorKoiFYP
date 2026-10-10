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

class AlgaeDay {
  final int daysFromNow;
  final double greenRatio;
  final double growthRatePerDay;
  final double favourability;
  final double luxAssumed;
  final double tempCAssumed;
  final double? no3PpmAssumed;

  const AlgaeDay({
    required this.daysFromNow,
    required this.greenRatio,
    required this.growthRatePerDay,
    required this.favourability,
    required this.luxAssumed,
    required this.tempCAssumed,
    required this.no3PpmAssumed,
  });

  factory AlgaeDay.fromJson(Map<String, dynamic> j) {
    double d(dynamic v) => v == null
        ? 0.0
        : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0.0);
    double? dn(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));
    return AlgaeDay(
      daysFromNow: (j['days_from_now'] as num?)?.toInt() ?? 0,
      greenRatio: d(j['green_ratio']),
      growthRatePerDay: d(j['growth_rate_per_day']),
      favourability: d(j['favourability']),
      luxAssumed: d(j['lux_assumed']),
      tempCAssumed: d(j['temp_c_assumed']),
      no3PpmAssumed: dn(j['no3_ppm_assumed']),
    );
  }
}

class AlgaeThresholds {
  final double watch;
  final double action;
  final double? baseline;
  final String mode; // 'absolute' | 'baseline_relative'

  const AlgaeThresholds({
    required this.watch,
    required this.action,
    required this.baseline,
    required this.mode,
  });

  factory AlgaeThresholds.fromJson(Map<String, dynamic> j) {
    double d(dynamic v) => v == null
        ? 0.0
        : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0.0);
    double? dn(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));
    return AlgaeThresholds(
      watch: d(j['watch']),
      action: d(j['action']),
      baseline: dn(j['baseline']),
      mode: j['mode']?.toString() ?? 'absolute',
    );
  }

  bool get isBaselineRelative => mode == 'baseline_relative';
}

class AlgaeForecast {
  final double currentGreenRatio;
  final int? predictedScrubDaysFromNow;
  final int? firstWatchDaysFromNow;
  final int? firstActionDaysFromNow;
  final AlgaeThresholds thresholds;
  final double intrinsicRatePerDay;
  final double? realisedRatePerDay;
  final String
  rateSource; // fitted_from_camera | measured_declining | literature_fallback
  final String confidence; // high | medium | low
  final int sampleCount;
  final int obstructedSampleCount;
  final int daysUsingRealForecast;
  final int? scrubDaysBought;
  final double? postScrubGreenRatio;
  final String? lastScrubAt;
  final String? latestImageUrl;
  final List<AlgaeDay> trajectory;

  const AlgaeForecast({
    required this.currentGreenRatio,
    required this.predictedScrubDaysFromNow,
    required this.firstWatchDaysFromNow,
    required this.firstActionDaysFromNow,
    required this.thresholds,
    required this.intrinsicRatePerDay,
    required this.realisedRatePerDay,
    required this.rateSource,
    required this.confidence,
    required this.sampleCount,
    required this.obstructedSampleCount,
    required this.daysUsingRealForecast,
    required this.scrubDaysBought,
    required this.postScrubGreenRatio,
    required this.lastScrubAt,
    required this.latestImageUrl,
    required this.trajectory,
  });

  factory AlgaeForecast.fromJson(Map<String, dynamic> j) {
    double d(dynamic v) => v == null
        ? 0.0
        : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0.0);
    double? dn(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));
    int? i(dynamic v) => v == null ? null : (v as num).toInt();

    final scrub = Map<String, dynamic>.from(j['scrub_benefit'] as Map? ?? {});

    return AlgaeForecast(
      currentGreenRatio: d(j['current_green_ratio']),
      predictedScrubDaysFromNow: i(j['predicted_scrub_days_from_now']),
      firstWatchDaysFromNow: i(j['first_watch_days_from_now']),
      firstActionDaysFromNow: i(j['first_action_days_from_now']),
      thresholds: AlgaeThresholds.fromJson(
        Map<String, dynamic>.from(j['thresholds'] as Map? ?? {}),
      ),
      intrinsicRatePerDay: d(j['intrinsic_rate_per_day']),
      realisedRatePerDay: dn(j['realised_rate_per_day']),
      rateSource: j['rate_source']?.toString() ?? 'literature_fallback',
      confidence: j['confidence']?.toString() ?? 'low',
      sampleCount: (j['sample_count'] as num?)?.toInt() ?? 0,
      obstructedSampleCount:
          (j['obstructed_sample_count'] as num?)?.toInt() ?? 0,
      daysUsingRealForecast:
          (j['days_using_real_forecast'] as num?)?.toInt() ?? 0,
      scrubDaysBought: i(scrub['days_bought']),
      postScrubGreenRatio: dn(scrub['post_scrub_green_ratio']),
      lastScrubAt: j['last_scrub_at']?.toString(),
      latestImageUrl: j['latest_image_url']?.toString(),
      trajectory:
          (j['trajectory'] as List?)
              ?.map((e) => AlgaeDay.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
    );
  }

  /// True when the growth rate came from this pond's own camera history
  /// rather than a literature default - drives the "grounded" badge.
  bool get isCameraGrounded => rateSource == 'fitted_from_camera';

  /// green_ratio expressed as a fraction of the action threshold, for a
  /// 0..1 progress meter. Clamped so a pond already past the threshold
  /// pins at full rather than overflowing the bar.
  double get thresholdProgress {
    if (thresholds.action <= 0) return 0.0;
    final p = currentGreenRatio / thresholds.action;
    return p.clamp(0.0, 1.0);
  }
}

/// ---------------------------------------------------------------------
/// ALGAE SEVERITY RATINGS  (POST /v1/ponds/$userId/events/algae-rating)
///
/// A human rating is a first-class observation, not just UI garnish: the
/// engine corrects its modelled level toward the rating at HUMAN_TRUST
/// (0.85, above the camera's 0.70), and the accumulated (label,
/// green_ratio) pairs calibrate the alert thresholds. See
/// Backend/DigitalTwin/algae_engine.py.
/// ---------------------------------------------------------------------

enum AlgaeSeverity { none, minor, moderate, severe, obstruction }

extension AlgaeSeverityX on AlgaeSeverity {
  /// Wire value the Flask endpoint expects.
  String get wire => switch (this) {
    AlgaeSeverity.none => 'none',
    AlgaeSeverity.minor => 'minor',
    AlgaeSeverity.moderate => 'moderate',
    AlgaeSeverity.severe => 'severe',
    AlgaeSeverity.obstruction => 'obstruction',
  };

  String get label => switch (this) {
    AlgaeSeverity.none => 'No algae',
    AlgaeSeverity.minor => 'Minor growth',
    AlgaeSeverity.moderate => 'Moderate growth',
    AlgaeSeverity.severe => 'Severe growth',
    AlgaeSeverity.obstruction => 'Blocked view',
  };

  /// Shown under each option so "minor" and "moderate" mean something
  /// consistent from one week to the next. Anchoring wording is the
  /// cheapest defence against a single rater drifting over time.
  String get hint => switch (this) {
    AlgaeSeverity.none => 'Water clear, liner and rocks clean',
    AlgaeSeverity.minor => 'Faint green tinge or light film starting',
    AlgaeSeverity.moderate => 'Clearly green, surfaces visibly coated',
    AlgaeSeverity.severe => 'Thick growth, water opaque or matted',
    AlgaeSeverity.obstruction => 'Leaf, debris or glare - not a real reading',
  };

  static AlgaeSeverity? fromWire(String? v) {
    switch ((v ?? '').toLowerCase()) {
      case 'none':
        return AlgaeSeverity.none;
      case 'minor':
        return AlgaeSeverity.minor;
      case 'moderate':
        return AlgaeSeverity.moderate;
      case 'severe':
        return AlgaeSeverity.severe;
      case 'obstruction':
        return AlgaeSeverity.obstruction;
      default:
        return null;
    }
  }
}

/// One historical rating row (algae_severity_ratings).
class AlgaeRating {
  final int? id;
  final AlgaeSeverity? severity;
  final bool isObstructed;
  final double? greenRatioAtRating;
  final String? imageUrl;
  final int? imageId;
  final DateTime? ratedAt;

  const AlgaeRating({
    required this.id,
    required this.severity,
    required this.isObstructed,
    required this.greenRatioAtRating,
    required this.imageUrl,
    required this.imageId,
    required this.ratedAt,
  });

  factory AlgaeRating.fromJson(Map<String, dynamic> j) {
    double? d(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));
    final obstructed = j['is_obstructed'] == true;
    return AlgaeRating(
      id: (j['id'] as num?)?.toInt(),
      severity: obstructed
          ? AlgaeSeverity.obstruction
          : AlgaeSeverityX.fromWire(j['severity']?.toString()),
      isObstructed: obstructed,
      greenRatioAtRating: d(j['green_ratio_at_rating']),
      imageUrl: j['image_url']?.toString(),
      imageId: (j['image_id'] as num?)?.toInt(),
      ratedAt: DateTime.tryParse(j['rated_at']?.toString() ?? ''),
    );
  }
}

/// Camera-drift verdict derived from frames the user rated "no algae".
/// If those readings climb over time, the LENS is greening, not the pond -
/// the one failure mode the camera alone can never detect.
class CameraDriftVerdict {
  final String
  verdict; // stable | drift_possible | drift_suspected | insufficient_data
  final int samples;
  final String detail;

  const CameraDriftVerdict({
    required this.verdict,
    required this.samples,
    required this.detail,
  });

  factory CameraDriftVerdict.fromJson(Map<String, dynamic> j) =>
      CameraDriftVerdict(
        verdict: j['verdict']?.toString() ?? 'insufficient_data',
        samples: (j['samples'] as num?)?.toInt() ?? 0,
        detail: j['detail']?.toString() ?? '',
      );

  bool get isConcerning =>
      verdict == 'drift_suspected' || verdict == 'drift_possible';
}

/// Calibration state: how many labels exist, how many are still needed,
/// and whether the thresholds are measured yet or still guessed.
class AlgaeCalibration {
  final Map<String, int> labelCounts;
  final Map<String, int> labelsNeeded;
  final Map<String, double> classTargets;
  final String thresholdMode;
  final double watchThreshold;
  final double actionThreshold;
  final CameraDriftVerdict drift;

  const AlgaeCalibration({
    required this.labelCounts,
    required this.labelsNeeded,
    required this.classTargets,
    required this.thresholdMode,
    required this.watchThreshold,
    required this.actionThreshold,
    required this.drift,
  });

  factory AlgaeCalibration.fromJson(Map<String, dynamic> j) {
    Map<String, int> ints(dynamic m) => (m is Map)
        ? m.map((k, v) => MapEntry('$k', (v as num?)?.toInt() ?? 0))
        : <String, int>{};
    Map<String, double> dbls(dynamic m) => (m is Map)
        ? m.map((k, v) => MapEntry('$k', (v as num?)?.toDouble() ?? 0.0))
        : <String, double>{};

    final th = Map<String, dynamic>.from(j['thresholds'] as Map? ?? {});
    return AlgaeCalibration(
      labelCounts: ints(j['label_counts']),
      labelsNeeded: ints(j['labels_needed']),
      classTargets: dbls(j['class_targets']),
      thresholdMode: th['mode']?.toString() ?? 'absolute',
      watchThreshold: (th['watch'] as num?)?.toDouble() ?? 0.0,
      actionThreshold: (th['action'] as num?)?.toDouble() ?? 0.0,
      drift: CameraDriftVerdict.fromJson(
        Map<String, dynamic>.from(j['camera_drift'] as Map? ?? {}),
      ),
    );
  }

  bool get isCalibrated => thresholdMode == 'label_calibrated';
  bool get isPartiallyCalibrated => thresholdMode == 'partially_calibrated';

  int get totalLabels => labelCounts.entries
      .where((e) => e.key != 'obstruction')
      .fold(0, (a, e) => a + e.value);

  int get remainingForCalibration =>
      labelsNeeded.values.fold(0, (a, v) => a + v);

  /// Which class the model most needs an example of next. Prompting for
  /// the scarcest class is far more label-efficient than prompting at
  /// random, which matters when a healthy pond is "none" 95% of the time.
  String? get mostNeededClass {
    String? worst;
    int worstN = 0;
    labelsNeeded.forEach((k, v) {
      if (v > worstN) {
        worst = k;
        worstN = v;
      }
    });
    return worst;
  }
}

/// Everything the rating card needs in one payload.
class AlgaeRatingContext {
  final List<AlgaeRating> ratings;
  final AlgaeRating? latestRating;
  final AlgaeCalibration? calibration;
  final String? latestImageUrl;
  final int? latestImageId;
  final double? latestGreenRatio;
  final DateTime? latestImageAt;

  const AlgaeRatingContext({
    required this.ratings,
    required this.latestRating,
    required this.calibration,
    required this.latestImageUrl,
    required this.latestImageId,
    required this.latestGreenRatio,
    required this.latestImageAt,
  });

  factory AlgaeRatingContext.fromJson(Map<String, dynamic> j) {
    final img = Map<String, dynamic>.from(j['latest_image'] as Map? ?? {});
    final cal = j['calibration'];
    return AlgaeRatingContext(
      ratings:
          (j['ratings'] as List?)
              ?.map((e) => AlgaeRating.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      latestRating: j['latest_rating'] == null
          ? null
          : AlgaeRating.fromJson(
              Map<String, dynamic>.from(j['latest_rating'] as Map),
            ),
      calibration: cal == null
          ? null
          : AlgaeCalibration.fromJson(Map<String, dynamic>.from(cal as Map)),
      latestImageUrl: img['imageURL']?.toString(),
      latestImageId: (img['id'] as num?)?.toInt(),
      latestGreenRatio: (img['green_ratio'] as num?)?.toDouble(),
      latestImageAt: DateTime.tryParse(img['created_at']?.toString() ?? ''),
    );
  }
}

/// Result of submitting a rating - carries the before/after level so the
/// UI can show what the rating actually did to the model.
class AlgaeRatingResult {
  final int? ratingId;
  final double? greenBefore;
  final double? greenAfter;
  final int removedFrames;
  final Map<String, int> labelCounts;
  final String? warning;
  final bool imageInferred;

  const AlgaeRatingResult({
    required this.ratingId,
    required this.greenBefore,
    required this.greenAfter,
    required this.removedFrames,
    required this.labelCounts,
    required this.warning,
    required this.imageInferred,
  });

  factory AlgaeRatingResult.fromJson(Map<String, dynamic> j) {
    double? d(dynamic v) =>
        v == null ? null : (v is num ? v.toDouble() : double.tryParse('$v'));
    return AlgaeRatingResult(
      ratingId: (j['rating_id'] as num?)?.toInt(),
      greenBefore: d(j['green_ratio_before']),
      greenAfter: d(j['green_ratio_after']),
      removedFrames: (j['removed_frames'] as num?)?.toInt() ?? 0,
      labelCounts: (j['label_counts'] is Map)
          ? (j['label_counts'] as Map).map(
              (k, v) => MapEntry('$k', (v as num?)?.toInt() ?? 0),
            )
          : <String, int>{},
      warning: j['warning']?.toString(),
      imageInferred: j['image_inferred'] == true,
    );
  }

  /// Signed change the rating made to the modelled level, for the
  /// "moved the estimate from X to Y" confirmation line.
  double? get delta => (greenBefore == null || greenAfter == null)
      ? null
      : greenAfter! - greenBefore!;
}

/// One fired weather rule from the dashboard's `next_actions`, as built by
/// Backend/koi/models/ladder.py::_action: {rule, lead_time, action,
/// evidence}. The service sends no urgency, so [urgencyRank] orders the
/// known rules by how soon the owner has to act.
class PondAction {
  final String rule;
  final String leadTime;
  final String action;
  final String evidence;

  const PondAction({
    required this.rule,
    required this.leadTime,
    required this.action,
    required this.evidence,
  });

  factory PondAction.fromJson(Map<String, dynamic> json) => PondAction(
    rule: '${json['rule'] ?? ''}'.toUpperCase(),
    leadTime: '${json['lead_time'] ?? ''}',
    action: '${json['action'] ?? ''}',
    evidence: '${json['evidence'] ?? ''}',
  );

  /// Most urgent first: rain within 2 hours, then yesterday's heavy rain,
  /// then the hot stretch, then the dry window. Unknown rules come last.
  static const List<String> urgencyOrder = [
    'NOWCAST',
    'REACT',
    'PREEMPT',
    'WINDOW',
  ];

  int get urgencyRank {
    final i = urgencyOrder.indexOf(rule);
    return i < 0 ? urgencyOrder.length : i;
  }

  /// [actions] most urgent first; equal ranks keep the service's order.
  static List<PondAction> byUrgency(Iterable<PondAction> actions) {
    final indexed = actions.toList().asMap().entries.toList()
      ..sort((a, b) {
        final byRank = a.value.urgencyRank.compareTo(b.value.urgencyRank);
        return byRank != 0 ? byRank : a.key.compareTo(b.key);
      });
    return [for (final e in indexed) e.value];
  }

  /// The rule in pond-keeping words.
  String get ruleLabel => switch (rule) {
    'NOWCAST' => 'Rain in the 2-hour forecast',
    'REACT' => 'Heavy rain yesterday',
    'PREEMPT' => 'Hot stretch ahead',
    'WINDOW' => 'Dry weather window',
    _ => rule.isEmpty ? 'Weather rule' : rule,
  };

  /// When the action applies, in plain words, as seen at [now].
  String whenText(DateTime now) {
    switch (leadTime) {
      case 'within 2 hours':
        final until = now.add(const Duration(hours: 2));
        final minute = until.minute - until.minute % 15;
        return 'Now, until about ${_hhmm(until.hour, minute)}';
      case 'next morning':
        return now.hour < 12
            ? "This morning, after yesterday's rain"
            : "Today, after yesterday's rain";
      case 'today':
        return 'Today, while it stays dry';
      case 'ahead of the hot stretch':
        return 'Before the hot days ahead';
    }
    if (leadTime.isEmpty) return 'Today';
    return leadTime[0].toUpperCase() + leadTime.substring(1);
  }

  /// The intervention event that carries out this action, or null when
  /// the action has nothing to log (a check, or pausing work).
  String? get logEventType => switch (rule) {
    'WINDOW' => 'WATER_CHANGE',
    'PREEMPT' => 'FEEDING',
    _ => null,
  };

  /// Identifies this action within one day, for dismissing it.
  String get dismissKey => '$rule|$action';

  static String _hhmm(int h, int m) =>
      '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}';
}

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
    } catch (_) {
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
    } catch (_) {
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
    } catch (_) {
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
    } catch (_) {
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
    } catch (_) {
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
    } catch (_) {
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
