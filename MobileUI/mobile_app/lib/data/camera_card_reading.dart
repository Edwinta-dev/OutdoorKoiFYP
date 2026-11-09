/// A stored camera colour-card reading for the latest frame.
class CameraCardReading {
  const CameraCardReading({
    required this.quality,
    required this.ph,
    required this.nh3,
    required this.usableDots,
    required this.totalDots,
    required this.turbidityIndex,
    required this.agreement,
    required this.frameAgreement,
    required this.patches,
  });

  final String quality;
  final Map<String, dynamic>? ph;
  final Map<String, dynamic>? nh3;
  final int usableDots;
  final int totalDots;
  final double? turbidityIndex;
  final Map<String, dynamic>? agreement;
  final String? frameAgreement;
  final Map<String, Map<String, dynamic>> patches;

  factory CameraCardReading.fromJson(Map<String, dynamic> json) {
    Map<String, dynamic>? obj(Object? value) =>
        value is Map ? Map<String, dynamic>.from(value) : null;
    final rawPatches = obj(json['patches']) ?? const <String, dynamic>{};
    return CameraCardReading(
      quality: json['quality']?.toString() ?? 'unusable',
      ph: obj(json['ph']),
      nh3: obj(json['nh3']),
      usableDots: (json['usable_dots'] as num?)?.toInt() ?? 0,
      totalDots: (json['total_dots'] as num?)?.toInt() ?? 0,
      turbidityIndex: (json['turbidity_index'] as num?)?.toDouble(),
      agreement: obj(json['agreement']),
      frameAgreement: json['frame_agreement']?.toString(),
      patches: {
        for (final entry in rawPatches.entries)
          if (entry.value is Map)
            entry.key.toString(): Map<String, dynamic>.from(entry.value as Map),
      },
    );
  }

  static const double slightlyCloudyThreshold = 0.10;
  static const double cloudyThreshold = 0.30;

  String turbidityLabel(double value) => value < slightlyCloudyThreshold
      ? 'clear'
      : value < cloudyThreshold
      ? 'slightly cloudy'
      : 'cloudy';
}
