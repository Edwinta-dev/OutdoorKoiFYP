// lib/screens/card_placement_screen.dart
//
// Place the three colour-card masks (white reference, ammonia wheel, pH
// wheel) on the pond's latest camera frame. Each is a rigid outline carrying
// its fixed patches and sample points (lib/data/card_placement.dart). Pick a
// mask, then drag to move, pinch to scale and twist to rotate, or use the
// buttons. Saved on this phone per pond; the sampled-areas overlay uses it in
// place of the patches the digital twin serves.

import 'dart:math' as math;
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/card_placement.dart';
import '../data/providers.dart';
import '../theme/app_theme.dart';
import '../utils/pond_camera_storage.dart';
import '../widgets/detail_graph/sample_overlay.dart';

class CardPlacementScreen extends ConsumerStatefulWidget {
  const CardPlacementScreen({
    super.key,
    required this.userId,
    this.imageProvider,
  });
  final int userId;

  /// Overrides the latest frame's image (tests).
  final ImageProvider? imageProvider;

  @override
  ConsumerState<CardPlacementScreen> createState() =>
      _CardPlacementScreenState();
}

class _CardPlacementScreenState extends ConsumerState<CardPlacementScreen> {
  CardPlacements? _placements;
  String _active = cardMaskIds.first;
  ImageProvider? _image;
  bool _loading = true;
  String? _error;
  String? _notice;
  PondCameraFrame? _shownFrame;
  PondCameraFrame? _referenceFrame;
  int? _referenceImageId;
  bool _saving = false;

  // The active mask's placement when a gesture began.
  CardPlacement? _start;

  CardPlacement get _current => _placements![_active];

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
    try {
      final template = await ref.read(cardTemplateProvider.future);
      final saved = await ref.read(cardPlacementProvider(widget.userId).future);
      final masks = await ref.read(
        cameraCardMasksProvider(widget.userId).future,
      );
      final referenceId = (masks['reference_image_id'] as num?)?.toInt();
      final frame = await ref
          .read(cameraFramesRepositoryProvider)
          .fetchLatestFrame(widget.userId);
      ImageProvider? image = widget.imageProvider;
      final shownFrame = frame.frame;
      if (image == null) {
        final url = shownFrame?.imageUrl;
        if (url == null) throw StateError('No latest camera image');
        image = NetworkImage(url);
      }
      PondCameraFrame? reference;
      if (referenceId != null) {
        if (referenceId == shownFrame?.id) {
          reference = shownFrame;
        } else {
          try {
            reference = await ref
                .read(cameraFramesRepositoryProvider)
                .fetchCameraFrame(widget.userId, referenceId);
          } catch (_) {
            // Keep editing available if the reference frame's date is unavailable.
          }
        }
      }
      if (!mounted) return;
      setState(() {
        _placements = saved ?? template.home;
        _image = image;
        _shownFrame = shownFrame;
        _referenceImageId = referenceId;
        _referenceFrame = reference;
        _loading = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Could not load the latest camera frame. Try again.';
        });
      }
    }
  }

  void _set(CardPlacement p) => setState(() {
    _placements = _placements!.withMask(_active, p);
    _notice = null;
  });

  void _rotate(double degrees) =>
      _set(_current.copyWith(angle: _current.angle + degrees * math.pi / 180));

  void _zoom(double factor) =>
      _set(_current.copyWith(scale: _current.scale * factor));

  Future<void> _save() async {
    if (_saving) return;
    setState(() {
      _saving = true;
      _notice = null;
    });
    final placements = _placements!;
    await ref.read(localProfileRepositoryProvider).save({
      cardPlacementKey(widget.userId): placements.encode(),
    });
    ref.invalidate(localProfileProvider);
    try {
      await ref
          .read(cameraFramesRepositoryProvider)
          .saveCameraCardMasks(
            widget.userId,
            jsonDecode(placements.encode()) as Map<String, dynamic>,
            _referenceImageId,
          );
      if (mounted) {
        setState(() {
          _saving = false;
          _notice = 'Saved';
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _saving = false;
          _notice = 'Not saved to the digital twin yet';
        });
      }
    }
    ref.invalidate(cameraCardPatchesProvider(widget.userId));
  }

  Future<void> _remove() async {
    // An empty string parses as no placement: the served patches return.
    await ref.read(localProfileRepositoryProvider).save({
      cardPlacementKey(widget.userId): '',
    });
    ref.invalidate(localProfileProvider);
    ref.invalidate(cameraCardPatchesProvider(widget.userId));
    final template = await ref.read(cardTemplateProvider.future);
    if (mounted) {
      setState(() {
        _placements = template.home;
        _notice = 'Saved mask positions removed.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final template = ref.watch(cardTemplateProvider).asData?.value;
    return Scaffold(
      appBar: AppBar(title: const Text('Colour card masks')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpace.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Pick a mask, then move its outline over that part of the card: '
              'drag to move, pinch to resize, twist with two fingers to rotate. '
              'Its dots follow it.',
            ),
            const SizedBox(height: AppSpace.md),
            SegmentedButton<String>(
              key: const ValueKey('card-mask-picker'),
              showSelectedIcon: false,
              segments: [
                for (final id in cardMaskIds)
                  ButtonSegment(
                    value: id,
                    label: Text(
                      cardMaskLabels[id]!,
                      key: ValueKey('card-mask-$id'),
                    ),
                  ),
              ],
              selected: {_active},
              onSelectionChanged: (s) => setState(() => _active = s.first),
            ),
            const SizedBox(height: AppSpace.md),
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else if (_image == null || template == null)
              TextButton(onPressed: _load, child: const Text('Retry loading'))
            else
              _canvas(template),
            if (_placements != null && !_loading) ...[
              const SizedBox(height: AppSpace.md),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  IconButton(
                    key: const ValueKey('card-rotate-ccw'),
                    tooltip: 'Rotate anticlockwise 5°',
                    icon: const Icon(Icons.rotate_left),
                    onPressed: () => _rotate(-5),
                  ),
                  IconButton(
                    key: const ValueKey('card-rotate-cw'),
                    tooltip: 'Rotate clockwise 5°',
                    icon: const Icon(Icons.rotate_right),
                    onPressed: () => _rotate(5),
                  ),
                  IconButton(
                    key: const ValueKey('card-rotate-90'),
                    tooltip: 'Rotate 90°',
                    icon: const Icon(Icons.screen_rotation_alt),
                    onPressed: () => _rotate(90),
                  ),
                  IconButton(
                    key: const ValueKey('card-shrink'),
                    tooltip: 'Smaller',
                    icon: const Icon(Icons.zoom_out),
                    onPressed: () => _zoom(1 / 1.05),
                  ),
                  IconButton(
                    key: const ValueKey('card-grow'),
                    tooltip: 'Larger',
                    icon: const Icon(Icons.zoom_in),
                    onPressed: () => _zoom(1.05),
                  ),
                ],
              ),
              Text(
                '${cardMaskLabels[_active]}: '
                'size ${(_current.scale * 100).round()}%  ·  '
                'turned ${(_current.angle * 180 / math.pi).round() % 360}°',
                key: const ValueKey('card-readout'),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: AppSpace.md),
              FilledButton(
                key: const ValueKey('card-save'),
                onPressed: _saving ? null : _save,
                child: const Text('Save mask positions'),
              ),
              TextButton(
                key: const ValueKey('card-clear-water-reference'),
                onPressed: _shownFrame?.id == null
                    ? null
                    : () => setState(() {
                        _referenceImageId = _shownFrame!.id;
                        _referenceFrame = _shownFrame;
                        _notice = null;
                      }),
                child: const Text('Use this frame as clear-water reference'),
              ),
              if (_referenceImageId != null)
                Text(
                  'Clear-water reference: ${_referenceFrame?.capturedAt?.toLocal().toString().split(' ').first ?? 'frame $_referenceImageId'}',
                  key: const ValueKey('card-reference-date'),
                ),
              if (_notice == 'Not saved to the digital twin yet')
                TextButton(
                  key: const ValueKey('card-save-retry'),
                  onPressed: _saving ? null : _save,
                  child: const Text('Retry'),
                ),
              TextButton(
                key: const ValueKey('card-reset'),
                onPressed: template == null
                    ? null
                    : () => _set(template.masks[_active]!.home),
                child: Text('Reset ${cardMaskLabels[_active]!.toLowerCase()}'),
              ),
              TextButton(
                key: const ValueKey('card-remove'),
                onPressed: _remove,
                child: const Text('Remove saved positions'),
              ),
            ],
            if (_error != null) Text(_error!),
            if (_notice != null) Text(_notice!),
          ],
        ),
      ),
    );
  }

  Widget _canvas(CardTemplate template) {
    final colors = AppColors.of(context);
    return AspectRatio(
      aspectRatio: frameAspect,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final size = constraints.biggest;
          return GestureDetector(
            key: const ValueKey('card-canvas'),
            behavior: HitTestBehavior.opaque,
            onScaleStart: (_) => _start = _current,
            onScaleUpdate: (d) {
              final s = _start;
              if (s == null) return;
              final p = _current;
              _set(
                p.copyWith(
                  cx: (p.cx + d.focalPointDelta.dx / size.width).clamp(
                    0.0,
                    1.0,
                  ),
                  cy: (p.cy + d.focalPointDelta.dy / size.height).clamp(
                    0.0,
                    1.0,
                  ),
                  scale: s.scale * d.scale,
                  angle: s.angle + d.rotation,
                ),
              );
            },
            onScaleEnd: (_) => _start = null,
            child: Stack(
              fit: StackFit.expand,
              children: [
                Image(image: _image!, fit: BoxFit.fill),
                for (final id in cardMaskIds)
                  CustomPaint(
                    key: ValueKey('card-outline-$id'),
                    painter: CardOutlinePainter(
                      outline: _placements![id].outline(
                        template.masks[id]!,
                        template.aspect,
                      ),
                      color: id == _active
                          ? colors.accent
                          : colors.accent.withValues(alpha: 0.35),
                      active: id == _active,
                    ),
                  ),
                SampleOverlay(patches: _placements!.patches(template)),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// A mask's outline, in frame fractions, over a frame-sized box.
class CardOutlinePainter extends CustomPainter {
  const CardOutlinePainter({
    required this.outline,
    required this.color,
    this.active = true,
  });
  final List<Offset> outline;
  final Color color;
  final bool active;

  @override
  void paint(Canvas canvas, Size size) {
    final pts = [
      for (final o in outline) Offset(o.dx * size.width, o.dy * size.height),
    ];
    canvas.drawPath(
      Path()..addPolygon(pts, true),
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = active ? 2 : 1.5,
    );
    // Marks the mask's top edge so its orientation reads at a glance.
    canvas.drawLine(
      pts[0],
      pts[1],
      Paint()
        ..color = color
        ..strokeWidth = active ? 5 : 3,
    );
  }

  @override
  bool shouldRepaint(CardOutlinePainter old) =>
      old.outline != outline || old.color != color || old.active != active;
}
