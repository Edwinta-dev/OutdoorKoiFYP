// lib/utils/algae_models.dart
//
// The algae forecast, rating and calibration responses of the DigitalTwin
// service, parsed from its JSON by the client in digital_twin_api.dart
// (which re-exports this file).

class AlgaeDay {
  final int daysFromNow;
  final double? greenRatio;
  final double? gcc;
  final double growthRatePerDay;
  final double favourability;
  final double luxAssumed;
  final double tempCAssumed;
  final double? no3PpmAssumed;

  const AlgaeDay({
    required this.daysFromNow,
    required this.greenRatio,
    required this.gcc,
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
      greenRatio: dn(j['green_ratio']),
      gcc: dn(j['gcc']),
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
  final double? currentGreenRatio;
  final double? currentGcc;
  final String? rateSourceDetail;
  final String? projectionMethod;
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
    required this.currentGcc,
    required this.rateSourceDetail,
    required this.projectionMethod,
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
      currentGreenRatio: dn(j['current_green_ratio']),
      currentGcc: dn(j['current_gcc']),
      rateSourceDetail: j['rate_source_detail']?.toString(),
      projectionMethod: j['projection_method']?.toString(),
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

  bool get isGccMode =>
      rateSourceDetail == 'gcc_windowed_trend' ||
      projectionMethod == 'windowed_gcc_trend';

  double? get currentSignal => isGccMode ? currentGcc : currentGreenRatio;

  String get signalLabel => isGccMode ? 'GCC' : 'Green ratio';

  /// green_ratio expressed as a fraction of the action threshold, for a
  /// 0..1 progress meter. Clamped so a pond already past the threshold
  /// pins at full rather than overflowing the bar.
  double get thresholdProgress {
    if (thresholds.action <= 0 || currentSignal == null) return 0.0;
    final p = currentSignal! / thresholds.action;
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
