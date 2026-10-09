import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/providers.dart';
import '../theme/app_theme.dart';

const _rectangle = [
  Offset(.1, .1),
  Offset(.9, .1),
  Offset(.9, .9),
  Offset(.1, .9),
];

/// Matches the camera's OpenCV HSV bounds: H 18..42, S/V 40..255.
bool isCameraGreen(int r, int g, int b) {
  final hsv = HSVColor.fromColor(Color.fromARGB(255, r, g, b));
  final hue = (hsv.hue / 2).round();
  return hue >= 18 &&
      hue <= 42 &&
      (hsv.saturation * 255).round() >= 40 &&
      hsv.value * 255 >= 40;
}

Path greenPixelPath(Uint8List rgba, int width, int height) {
  final path = Path();
  for (var y = 0; y < height; y++) {
    for (var x = 0; x < width; x++) {
      final i = (y * width + x) * 4;
      if (isCameraGreen(rgba[i], rgba[i + 1], rgba[i + 2])) {
        path.addRect(Rect.fromLTWH(x.toDouble(), y.toDouble(), 1, 1));
      }
    }
  }
  return path;
}

class WaterMaskEditorScreen extends ConsumerStatefulWidget {
  const WaterMaskEditorScreen({
    super.key,
    required this.userId,
    this.imageProvider,
    this.initialImage,
  });
  final int userId;
  final ImageProvider? imageProvider;
  final ui.Image? initialImage;

  @override
  ConsumerState<WaterMaskEditorScreen> createState() =>
      _WaterMaskEditorScreenState();
}

class _WaterMaskEditorScreenState extends ConsumerState<WaterMaskEditorScreen> {
  List<Offset> _points = List.of(_rectangle);
  ui.Image? _image;
  Path _green = Path();
  bool _loading = true;
  bool _saving = false;
  String? _error;
  String? _notice;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    ui.Image? image;
    try {
      final repository = ref.read(cameraFramesRepositoryProvider);
      final frame = await repository.fetchLatestFrame(widget.userId);
      if (frame.frame?.imageUrl == null &&
          widget.imageProvider == null &&
          widget.initialImage == null) {
        throw StateError('No latest camera image');
      }
      final mask = await repository.fetchCameraMask(widget.userId);
      if (!mounted) return;
      if (widget.initialImage != null) {
        image = widget.initialImage!.clone();
      } else {
        final stream =
            (widget.imageProvider ?? NetworkImage(frame.frame!.imageUrl!))
                .resolve(createLocalImageConfiguration(context));
        final result = Completer<ui.Image>();
        final listener = ImageStreamListener(
          (info, _) => result.complete(info.image.clone()),
          onError: (Object error, StackTrace? stack) =>
              result.completeError(error),
        );
        stream.addListener(listener);
        try {
          image = await result.future.timeout(const Duration(seconds: 10));
        } finally {
          stream.removeListener(listener);
        }
      }
      final bytes = await image.toByteData(format: ui.ImageByteFormat.rawRgba);
      final green = greenPixelPath(
        bytes!.buffer.asUint8List(),
        image.width,
        image.height,
      );
      if (!mounted) {
        image.dispose();
        return;
      }
      setState(() {
        _image?.dispose();
        _image = image;
        _green = green;
        _points = mask == null
            ? List.of(_rectangle)
            : mask.map((p) => Offset(p[0], p[1])).toList();
        _loading = false;
      });
    } catch (_) {
      image?.dispose();
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Could not load the latest frame and water mask. Try again.';
        });
      }
    }
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
      _notice = null;
    });
    try {
      await ref
          .read(cameraFramesRepositoryProvider)
          .saveCameraMask(
            widget.userId,
            _points.map((p) => [p.dx, p.dy]).toList(),
          );
      if (!mounted) return;
      ref.invalidate(cameraFrameProvider(widget.userId));
      ref.invalidate(ratingContextProvider(widget.userId));
      ref.invalidate(algaeForecastProvider(widget.userId));
      setState(
        () => _notice =
            'Water mask saved. The next camera frame resets the baseline.',
      );
    } catch (_) {
      if (mounted) {
        setState(
          () => _error =
              'Water mask was not saved. Check the connection and try again.',
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  void dispose() {
    _image?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Water mask')),
    body: SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            'Drag the corners over the water. Tap the image to add a point on the nearest edge.',
          ),
          const Text(
            'Highlighted pixels inside the polygon are the camera’s green-pixel preview.',
          ),
          const Text(
            'Saving resets the camera baseline on the next frame. Keep plants and pond edges outside the mask.',
          ),
          const SizedBox(height: 16),
          if (_loading)
            const Center(child: CircularProgressIndicator())
          else if (_image == null)
            TextButton(onPressed: _load, child: const Text('Retry loading'))
          else
            WaterMaskCanvas(
              image: _image!,
              green: _green,
              points: _points,
              onChanged: _saving
                  ? null
                  : (points) => setState(() {
                      _points = points;
                      _notice = null;
                    }),
            ),
          if (_image != null) ...[
            Text('${_points.length} of 64 points'),
            TextButton(
              onPressed: _saving
                  ? null
                  : () => setState(() {
                      _points = List.of(_rectangle);
                      _notice = null;
                    }),
              child: const Text('Start from rectangle'),
            ),
            if (!validMaskArea(_points))
              const Text('Select at least 1% of the frame before saving.'),
            FilledButton(
              onPressed: _saving || !validMaskArea(_points) ? null : _save,
              child: Text(_saving ? 'Saving…' : 'Save water mask'),
            ),
          ],
          if (_error != null) Text(_error!),
          if (_notice != null) Text(_notice!),
        ],
      ),
    ),
  );
}

bool validMaskArea(List<Offset> points) {
  var twice = 0.0;
  for (var i = 0; i < points.length; i++) {
    final a = points[i], b = points[(i + 1) % points.length];
    twice += a.dx * b.dy - b.dx * a.dy;
  }
  return points.length >= 3 && points.length <= 64 && twice.abs() / 2 >= .01;
}

class WaterMaskCanvas extends StatefulWidget {
  const WaterMaskCanvas({
    super.key,
    required this.image,
    required this.green,
    required this.points,
    required this.onChanged,
  });
  final ui.Image image;
  final Path green;
  final List<Offset> points;
  final ValueChanged<List<Offset>>? onChanged;
  @override
  State<WaterMaskCanvas> createState() => _WaterMaskCanvasState();
}

class _WaterMaskCanvasState extends State<WaterMaskCanvas> {
  int? _drag;
  @override
  Widget build(BuildContext context) => AspectRatio(
    aspectRatio: widget.image.width / widget.image.height,
    child: LayoutBuilder(
      builder: (context, constraints) {
        final size = constraints.biggest;
        Offset pixel(Offset p) => Offset(p.dx * size.width, p.dy * size.height);
        Offset normalized(Offset p) => Offset(
          (p.dx / size.width).clamp(0, 1),
          (p.dy / size.height).clamp(0, 1),
        );
        return GestureDetector(
          key: const ValueKey('water-mask-canvas'),
          behavior: HitTestBehavior.opaque,
          onPanDown: widget.onChanged == null
              ? null
              : (event) {
                  _drag = null;
                  var distance = 56.0;
                  for (var i = 0; i < widget.points.length; i++) {
                    final d = (pixel(widget.points[i]) - event.localPosition)
                        .distance;
                    if (d < distance) {
                      distance = d;
                      _drag = i;
                    }
                  }
                },
          onPanUpdate: widget.onChanged == null
              ? null
              : (event) {
                  if (_drag == null) return;
                  final points = List.of(widget.points);
                  points[_drag!] = normalized(event.localPosition);
                  widget.onChanged!(points);
                },
          onPanEnd: (_) => _drag = null,
          onTapUp: widget.onChanged == null
              ? null
              : (event) {
                  if (widget.points.length >= 64) return;
                  var nearest = 0, distance = double.infinity;
                  for (var i = 0; i < widget.points.length; i++) {
                    final a = pixel(widget.points[i]);
                    final ab =
                        pixel(widget.points[(i + 1) % widget.points.length]) -
                        a;
                    final ap = event.localPosition - a;
                    final t = ab.distanceSquared == 0
                        ? 0.0
                        : ((ap.dx * ab.dx + ap.dy * ab.dy) / ab.distanceSquared)
                              .clamp(0.0, 1.0);
                    final d = (ap - ab * t).distance;
                    if (d < distance) {
                      nearest = i;
                      distance = d;
                    }
                  }
                  widget.onChanged!(
                    List.of(widget.points)
                      ..insert(nearest + 1, normalized(event.localPosition)),
                  );
                },
          child: CustomPaint(
            painter: WaterMaskPainter(
              widget.image,
              widget.green,
              widget.points,
            ),
          ),
        );
      },
    ),
  );
}

class WaterMaskPainter extends CustomPainter {
  WaterMaskPainter(this.image, this.green, this.points);
  final ui.Image image;
  final Path green;
  final List<Offset> points;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawImageRect(
      image,
      Rect.fromLTWH(0, 0, image.width.toDouble(), image.height.toDouble()),
      Offset.zero & size,
      Paint(),
    );
    final vertices = points
        .map((p) => Offset(p.dx * size.width, p.dy * size.height))
        .toList();
    final polygon = Path()..addPolygon(vertices, true);
    canvas.save();
    canvas.clipPath(polygon);
    canvas.scale(size.width / image.width, size.height / image.height);
    canvas.drawPath(green, Paint()..color = AppColors.waterMaskPreview);
    canvas.restore();
    canvas.drawPath(
      polygon,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
    for (final point in vertices) {
      canvas.drawCircle(
        point,
        math.min(10, size.width / 30),
        Paint()..color = Colors.white,
      );
      canvas.drawCircle(point, 4, Paint()..color = Colors.black);
    }
  }

  @override
  bool shouldRepaint(WaterMaskPainter oldDelegate) => true;
}
