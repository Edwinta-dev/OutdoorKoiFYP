// lib/data/card_patch.dart
//
// A colour-card patch of the pond camera frame: a named spot of the test
// cards (the white reference area, a segment of the pH or ammonia wheel, or
// a wheel's centre reading pad) with the sample positions the camera
// analysis reads inside it. Served by GET /v1/ponds/{id}/camera/mask as
// card_patches (Backend/koi/camera/card_patches.py). Coordinates are
// fractions of the frame, origin top-left.

class CardPatch {
  final String name;

  /// white_reference, nh3_reference, ph_reference, nh3_reading or ph_reading.
  final String kind;
  final List<List<double>> polygon;
  final List<List<double>> points;

  const CardPatch({
    required this.name,
    required this.kind,
    required this.polygon,
    this.points = const [],
  });

  static List<List<double>> _pairs(Object? raw) => (raw as List? ?? const [])
      .map((p) => (p as List).map((v) => (v as num).toDouble()).toList())
      .toList();

  factory CardPatch.fromJson(Map<String, dynamic> json) => CardPatch(
    name: json['name'] as String,
    kind: json['kind'] as String? ?? '',
    polygon: _pairs(json['polygon']),
    points: _pairs(json['points']),
  );

  /// The patches of a camera mask response; none when it has no card_patches.
  static List<CardPatch> listFromMaskResponse(Map<String, dynamic> data) {
    final block = data['card_patches'];
    if (block is! Map) return const [];
    return (block['patches'] as List? ?? const [])
        .map((p) => CardPatch.fromJson((p as Map).cast<String, dynamic>()))
        .toList();
  }
}
