// lib/data/card_placement.dart
//
// The colour cards as three rigid masks: the white reference, the ammonia
// (NH3) wheel and the pH wheel. Within each mask the patches and sample
// points never move relative to each other (lib/assets/card_template.json,
// taken from the demo frames' card_patches), so placing a mask on a pond's
// frame needs only four numbers: where its centre is, how big it is, and how
// it is turned. Each mask is placed on its own.
//
// Coordinates are fractions of the 4:3 frame, origin top-left. Rotation and
// scale are done in pixel-proportional space (x times the aspect) so a mask
// keeps its shape when turned.

import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui' show Offset;

import 'card_patch.dart';

/// The three masks, in the order the editor offers them.
const cardMaskIds = ['white', 'nh3', 'ph'];

const cardMaskLabels = {
  'white': 'White reference',
  'nh3': 'Ammonia wheel',
  'ph': 'pH wheel',
};

/// Which mask a patch belongs to, from its name.
String cardMaskOf(String patchName) => patchName.startsWith('white')
    ? 'white'
    : patchName.startsWith('nh3')
    ? 'nh3'
    : 'ph';

/// One mask: its patches in template coordinates and the rectangle around them.
class CardMask {
  final String id;
  final List<CardPatch> patches;
  final double left, top, right, bottom;

  const CardMask._(
    this.id,
    this.patches,
    this.left,
    this.top,
    this.right,
    this.bottom,
  );

  factory CardMask._of(String id, List<CardPatch> patches, double margin) {
    var l = double.infinity, t = double.infinity;
    var r = -double.infinity, b = -double.infinity;
    for (final p in patches) {
      for (final q in p.polygon) {
        l = math.min(l, q[0]);
        r = math.max(r, q[0]);
        t = math.min(t, q[1]);
        b = math.max(b, q[1]);
      }
    }
    return CardMask._(
      id,
      patches,
      l - margin,
      t - margin,
      r + margin,
      b + margin,
    );
  }

  Offset get center => Offset((left + right) / 2, (top + bottom) / 2);

  /// Where the mask sits in the template's own frame.
  CardPlacement get home =>
      CardPlacement(cx: center.dx, cy: center.dy, scale: 1, angle: 0);
}

/// The three masks of the card template.
class CardTemplate {
  final Map<String, CardMask> masks;
  final double aspect;
  const CardTemplate._(this.masks, this.aspect);

  /// [margin] pads each mask's outline beyond its outermost patch.
  factory CardTemplate.fromJson(
    Map<String, dynamic> json, {
    double margin = 0.015,
  }) {
    final patches = (json['patches'] as List)
        .map((p) => CardPatch.fromJson((p as Map).cast<String, dynamic>()))
        .toList();
    final aspect = (json['frame_aspect'] as num?)?.toDouble() ?? 4 / 3;
    return CardTemplate._({
      for (final id in cardMaskIds)
        id: CardMask._of(
          id,
          patches.where((p) => cardMaskOf(p.name) == id).toList(),
          margin,
        ),
    }, aspect);
  }

  factory CardTemplate.parse(String source) =>
      CardTemplate.fromJson(jsonDecode(source) as Map<String, dynamic>);

  List<CardPatch> get patches => [
    for (final id in cardMaskIds) ...masks[id]!.patches,
  ];

  /// Every mask where it sits in the template's own frame.
  CardPlacements get home =>
      CardPlacements({for (final id in cardMaskIds) id: masks[id]!.home});
}

/// Where one mask sits on a frame: centre (frame fractions), size relative
/// to the template, and clockwise rotation in radians.
class CardPlacement {
  final double cx, cy, scale, angle;
  const CardPlacement({
    required this.cx,
    required this.cy,
    required this.scale,
    required this.angle,
  });

  static const minScale = 0.2, maxScale = 3.0;

  CardPlacement copyWith({
    double? cx,
    double? cy,
    double? scale,
    double? angle,
  }) => CardPlacement(
    cx: cx ?? this.cx,
    cy: cy ?? this.cy,
    scale: (scale ?? this.scale).clamp(minScale, maxScale).toDouble(),
    angle: angle ?? this.angle,
  );

  Map<String, double> toJson() => {
    'cx': cx,
    'cy': cy,
    'scale': scale,
    'angle': angle,
  };

  static CardPlacement fromJson(Map<String, dynamic> m) {
    double v(String k) => (m[k] as num).toDouble();
    return CardPlacement(
      cx: v('cx'),
      cy: v('cy'),
      scale: v('scale'),
      angle: v('angle'),
    );
  }

  /// Maps a template point of [mask] onto the frame.
  Offset map(CardMask mask, double aspect, double x, double y) {
    final dx = (x - mask.center.dx) * aspect * scale;
    final dy = (y - mask.center.dy) * scale;
    final c = math.cos(angle), s = math.sin(angle);
    return Offset(cx + (dx * c - dy * s) / aspect, cy + (dx * s + dy * c));
  }

  /// The mask's four outline corners on the frame, clockwise from top-left.
  List<Offset> outline(CardMask m, double aspect) => [
    map(m, aspect, m.left, m.top),
    map(m, aspect, m.right, m.top),
    map(m, aspect, m.right, m.bottom),
    map(m, aspect, m.left, m.bottom),
  ];

  /// The mask's patches, moved onto the frame.
  List<CardPatch> patches(CardMask m, double aspect) => [
    for (final p in m.patches)
      CardPatch(
        name: p.name,
        kind: p.kind,
        polygon: [for (final q in p.polygon) _pair(map(m, aspect, q[0], q[1]))],
        points: [for (final q in p.points) _pair(map(m, aspect, q[0], q[1]))],
      ),
  ];

  static List<double> _pair(Offset o) => [o.dx, o.dy];
}

/// The three masks' placements.
class CardPlacements {
  final Map<String, CardPlacement> byMask;
  const CardPlacements(this.byMask);

  CardPlacement operator [](String id) => byMask[id]!;

  CardPlacements withMask(String id, CardPlacement p) =>
      CardPlacements({...byMask, id: p});

  String encode() =>
      jsonEncode({for (final e in byMask.entries) e.key: e.value.toJson()});

  /// Null for anything but a saved set of all three masks.
  static CardPlacements? tryParse(Object? raw) {
    try {
      final m = raw is String
          ? jsonDecode(raw) as Map<String, dynamic>
          : (raw as Map).cast<String, dynamic>();
      return CardPlacements({
        for (final id in cardMaskIds)
          id: CardPlacement.fromJson((m[id] as Map).cast<String, dynamic>()),
      });
    } catch (_) {
      return null;
    }
  }

  /// Every patch of the template, each moved by its own mask's placement.
  List<CardPatch> patches(CardTemplate t) => [
    for (final id in cardMaskIds) ...this[id].patches(t.masks[id]!, t.aspect),
  ];
}

/// The SharedPreferences key of a pond's card placements.
String cardPlacementKey(int pond) => 'cardPlacements.$pond';
