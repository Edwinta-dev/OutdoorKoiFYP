// lib/widgets/detail_graph/sample_overlay.dart
//
// The "sampled areas" overlay of the Rate What You See card: small dots on
// the spots of the colour test cards the camera analysis reads - the white
// reference area, each segment of the pH and ammonia wheels, and each
// wheel's centre reading pad. Nothing else is dotted.
//
// The patches come from the pond's camera mask response (card_patches,
// Backend/koi/camera/card_patches.py): normalised [x, y] fractions of the
// 4:3 camera frame, origin top-left. The overlay maps them onto the area the
// image really occupies (BoxFit.contain, so letterboxing is honoured), and
// colours a dot magenta when its pixel is one the analysis rejects as
// clipped (V <= 5 or V >= 250, koi/camera/quality.py).

import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../../data/card_patch.dart';
import '../../theme/app_theme.dart';

/// The camera frame's aspect ratio (640 x 480).
const double frameAspect = 4 / 3;

/// Dot radius in logical pixels.
const double sampleDotRadius = 1.6;

/// Pixel value limits of koi/camera/quality.py (V = max of R, G, B).
const int clipLowV = 5;
const int clipHighV = 250;

/// The rectangle an image of [aspect] (width / height) occupies inside [box]
/// under BoxFit.contain: as large as fits, centred.
Rect containRect(Size box, {double aspect = frameAspect}) {
  if (box.isEmpty) return Rect.zero;
  var w = box.width, h = box.width / aspect;
  if (h > box.height) {
    h = box.height;
    w = h * aspect;
  }
  return Rect.fromLTWH((box.width - w) / 2, (box.height - h) / 2, w, h);
}

/// True when the normalised point ([x], [y]) lies inside [polygon]
/// (even-odd rule).
bool polygonContains(List<List<double>> polygon, double x, double y) {
  var inside = false;
  for (var i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    final xi = polygon[i][0], yi = polygon[i][1];
    final xj = polygon[j][0], yj = polygon[j][1];
    if ((yi > y) != (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/// One dot: [at] in pixels of the overlay box, and its normalised position
/// in the frame.
class SampleDot {
  final Offset at;
  final Offset frame;
  final String patch;
  const SampleDot(this.at, this.frame, this.patch);
}

/// The dots of [patches] over the image rectangle [rect]: each patch's own
/// sample points, or, for a patch without any, a grid clipped to its
/// polygon. Every dot lies inside its patch.
List<SampleDot> patchDots(List<CardPatch> patches, Rect rect) {
  if (rect.isEmpty) return const [];
  final dots = <SampleDot>[];
  for (final p in patches) {
    var points = p.points;
    if (points.isEmpty && p.polygon.length >= 3) {
      final step = rect.width / 70;
      final grid = <List<double>>[];
      for (var y = step / 2; y < rect.height; y += step) {
        for (var x = step / 2; x < rect.width; x += step) {
          if (polygonContains(p.polygon, x / rect.width, y / rect.height)) {
            grid.add([x / rect.width, y / rect.height]);
          }
        }
      }
      points = grid;
    }
    for (final q in points) {
      dots.add(
        SampleDot(
          Offset(rect.left + q[0] * rect.width, rect.top + q[1] * rect.height),
          Offset(q[0], q[1]),
          p.name,
        ),
      );
    }
  }
  return dots;
}

/// Decoded pixels of the frame, to tell which sample dots fall on pixels the
/// analysis rejects.
class PixelProbe {
  final Uint8List rgba;
  final int width;
  final int height;
  const PixelProbe(this.rgba, this.width, this.height);

  /// True when the pixel at normalised ([x], [y]) is clipped: V = max(R,G,B)
  /// at or below [clipLowV] or at or above [clipHighV].
  bool rejected(double x, double y) {
    final px = (x * width).floor().clamp(0, width - 1);
    final py = (y * height).floor().clamp(0, height - 1);
    final i = (py * width + px) * 4;
    final v = [rgba[i], rgba[i + 1], rgba[i + 2]].reduce((a, b) => a > b ? a : b);
    return v <= clipLowV || v >= clipHighV;
  }
}

class SampleOverlayPainter extends CustomPainter {
  final List<CardPatch> patches;
  final Color color;
  final Color rejectedColor;
  final PixelProbe? probe;
  final double aspect;
  final double radius;

  const SampleOverlayPainter({
    required this.patches,
    required this.color,
    required this.rejectedColor,
    this.probe,
    this.aspect = frameAspect,
    this.radius = sampleDotRadius,
  });

  /// Dots of this painter's patches in a box of [size].
  List<SampleDot> dots(Size size) =>
      patchDots(patches, containRect(size, aspect: aspect));

  @override
  void paint(Canvas canvas, Size size) {
    final all = dots(size);
    if (all.isEmpty) return;
    final ok = <Offset>[], bad = <Offset>[];
    for (final d in all) {
      (probe?.rejected(d.frame.dx, d.frame.dy) == true ? bad : ok).add(d.at);
    }
    void draw(List<Offset> points, Color c) {
      if (points.isEmpty) return;
      canvas.drawPoints(
        ui.PointMode.points,
        points,
        Paint()
          ..color = c
          ..strokeWidth = radius * 2
          ..strokeCap = StrokeCap.round,
      );
    }

    draw(ok, color);
    draw(bad, rejectedColor);
  }

  @override
  bool shouldRepaint(SampleOverlayPainter old) =>
      old.patches != patches ||
      old.color != color ||
      old.rejectedColor != rejectedColor ||
      old.probe != probe ||
      old.aspect != aspect ||
      old.radius != radius;
}

/// The overlay as a widget, filling its parent (the image box). Draws nothing
/// for no patches. With [image] it reads the frame's pixels to colour
/// rejected dots; [probe] overrides that (tests).
class SampleOverlay extends StatefulWidget {
  final List<CardPatch> patches;
  final ImageProvider? image;
  final PixelProbe? probe;
  const SampleOverlay({
    super.key,
    required this.patches,
    this.image,
    this.probe,
  });

  @override
  State<SampleOverlay> createState() => _SampleOverlayState();
}

class _SampleOverlayState extends State<SampleOverlay> {
  PixelProbe? _probe;
  ImageStream? _stream;
  ImageStreamListener? _listener;

  @override
  void initState() {
    super.initState();
    _watch();
  }

  @override
  void didUpdateWidget(SampleOverlay old) {
    super.didUpdateWidget(old);
    if (old.image != widget.image) {
      _unwatch();
      _probe = null;
      _watch();
    }
  }

  void _watch() {
    final image = widget.image;
    if (image == null || widget.probe != null) return;
    final stream = image.resolve(ImageConfiguration.empty);
    final listener = ImageStreamListener((info, _) async {
      try {
        final data = await info.image.toByteData(
          format: ui.ImageByteFormat.rawRgba,
        );
        if (data == null || !mounted) return;
        final probe = PixelProbe(
          data.buffer.asUint8List(),
          info.image.width,
          info.image.height,
        );
        setState(() => _probe = probe);
      } catch (_) {
        // No pixels, no magenta: every dot stays the normal colour.
      }
    }, onError: (_, _) {});
    _stream = stream;
    _listener = listener;
    stream.addListener(listener);
  }

  void _unwatch() {
    if (_stream != null && _listener != null) {
      _stream!.removeListener(_listener!);
    }
    _stream = null;
    _listener = null;
  }

  @override
  void dispose() {
    _unwatch();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = AppColors.of(context);
    return IgnorePointer(
      child: CustomPaint(
        key: const ValueKey('sample-overlay-painter'),
        size: Size.infinite,
        painter: SampleOverlayPainter(
          patches: widget.patches,
          color: colors.samplePoint,
          rejectedColor: colors.sampleRejected,
          probe: widget.probe ?? _probe,
        ),
      ),
    );
  }
}
