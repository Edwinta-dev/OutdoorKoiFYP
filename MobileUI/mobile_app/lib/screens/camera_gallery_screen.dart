import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/providers.dart';
import '../theme/app_theme.dart';
import '../utils/pond_camera_storage.dart';
import '../widgets/detail_graph/algae_severity_rating_card.dart';
import '../widgets/shared/pond_widgets.dart';
import 'water_mask_editor_screen.dart';

String _time(DateTime? time) {
  if (time == null) return 'Time unknown';
  final local = time.toLocal();
  return '${local.hour.toString().padLeft(2, '0')}:'
      '${local.minute.toString().padLeft(2, '0')}:'
      '${local.second.toString().padLeft(2, '0')}';
}

String _date(DateTime day) =>
    '${day.year}-'
    '${day.month.toString().padLeft(2, '0')}-'
    '${day.day.toString().padLeft(2, '0')}';

class CameraGalleryScreen extends ConsumerStatefulWidget {
  const CameraGalleryScreen({super.key, required this.userId, this.initialDay});
  final int userId;
  final DateTime? initialDay;

  @override
  ConsumerState<CameraGalleryScreen> createState() =>
      _CameraGalleryScreenState();
}

class _CameraGalleryScreenState extends ConsumerState<CameraGalleryScreen> {
  late DateTime _day;
  bool _timelapse = false;
  int _selected = 0;

  @override
  void initState() {
    super.initState();
    final now = (widget.initialDay ?? DateTime.now()).toLocal();
    _day = DateTime(now.year, now.month, now.day);
  }

  void _changeDay(DateTime day) => setState(() {
    _day = DateTime(day.year, day.month, day.day);
    _selected = 0;
  });

  void _rate(PondCameraFrame frame) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => Scaffold(
          appBar: AppBar(
            title: Text('Rate frame · ${_time(frame.capturedAt)}'),
          ),
          body: SingleChildScrollView(
            padding: const EdgeInsets.all(AppSpace.lg),
            child: AlgaeSeverityRatingCard(userId: widget.userId, frame: frame),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final query = (pond: widget.userId, day: _day);
    final frames = ref.watch(cameraDayProvider(query));
    final drift = ref.watch(ratingContextProvider(widget.userId));
    return Scaffold(
      appBar: AppBar(
        title: const Text('Camera gallery'),
        actions: [
          IconButton(
            tooltip: 'Edit water mask',
            icon: const Icon(Icons.crop_free),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => WaterMaskEditorScreen(userId: widget.userId),
              ),
            ),
          ),
          IconButton(
            tooltip: 'Refresh camera history',
            icon: const Icon(Icons.refresh),
            onPressed: () {
              ref.invalidate(cameraDayProvider(query));
              ref.invalidate(ratingContextProvider(widget.userId));
            },
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpace.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                IconButton(
                  tooltip: 'Previous day',
                  onPressed: () =>
                      _changeDay(DateTime(_day.year, _day.month, _day.day - 1)),
                  icon: const Icon(Icons.chevron_left),
                ),
                Expanded(
                  child: TextButton(
                    onPressed: () async {
                      final picked = await showDatePicker(
                        context: context,
                        initialDate: _day,
                        firstDate: DateTime(2000),
                        lastDate: DateTime.now().isAfter(_day)
                            ? DateTime.now()
                            : _day,
                      );
                      if (picked != null && mounted) _changeDay(picked);
                    },
                    child: Text('${_date(_day)} · local time'),
                  ),
                ),
                IconButton(
                  tooltip: 'Next day',
                  onPressed: _date(_day) == _date(DateTime.now())
                      ? null
                      : () => _changeDay(
                          DateTime(_day.year, _day.month, _day.day + 1),
                        ),
                  icon: const Icon(Icons.chevron_right),
                ),
              ],
            ),
            Text(
              drift.when(
                data: (context) {
                  final verdict = context?.calibration?.drift;
                  if (verdict == null) {
                    return 'Camera drift: unavailable. Try refreshing.';
                  }
                  final label = switch (verdict.verdict) {
                    'stable' => 'stable',
                    'drift_possible' => 'possible drift',
                    'drift_suspected' => 'suspected drift',
                    _ => 'not enough rated frames',
                  };
                  return 'Camera drift: $label. ${verdict.detail}';
                },
                loading: () => 'Camera drift: checking rated frames…',
                error: (_, _) => 'Camera drift: unavailable. Try refreshing.',
              ),
            ),
            const SizedBox(height: AppSpace.md),
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment(value: false, label: Text('Gallery')),
                ButtonSegment(value: true, label: Text('Timelapse')),
              ],
              selected: {_timelapse},
              onSelectionChanged: (value) =>
                  setState(() => _timelapse = value.single),
            ),
            const SizedBox(height: AppSpace.lg),
            frames.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (_, _) => PondErrorState(
                title: 'Camera history unavailable',
                message:
                    'Could not read camera frames. Check the connection and try again.',
                onRetry: () => ref.invalidate(cameraDayProvider(query)),
              ),
              data: (items) {
                if (items.isEmpty) {
                  return const PondEmptyState(
                    title: 'No frames for this day',
                    message:
                        'Choose another day or wait for the camera to report.',
                  );
                }
                return _timelapse
                    ? _scrubber(items)
                    : Column(
                        children: [
                          for (final frame in items)
                            Padding(
                              padding: const EdgeInsets.only(
                                bottom: AppSpace.md,
                              ),
                              child: OutcomeCardShell(
                                child: Column(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.stretch,
                                  children: [
                                    CameraFrameImage(
                                      frame: frame,
                                      thumbnail: true,
                                    ),
                                    _metadata(frame),
                                    TextButton(
                                      onPressed: frame.id == null
                                          ? null
                                          : () => _rate(frame),
                                      child: const Text('Rate this frame'),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                        ],
                      );
              },
            ),
          ],
        ),
      ),
    );
  }

  Widget _metadata(PondCameraFrame frame) => Padding(
    padding: const EdgeInsets.symmetric(vertical: AppSpace.sm),
    child: Text(
      '${_time(frame.capturedAt)} · '
      'Green ratio: ${frame.greenRatio == null ? 'unknown' : '${(frame.greenRatio! * 100).toStringAsFixed(2)}%'}\n'
      'State: ${frame.state ?? 'unknown'} · Quality: ${frame.qualityStatus}'
      '${frame.qualityReasons.isEmpty ? '' : ' (${frame.qualityReasons.map((r) => r.replaceAll('_', ' ')).join(', ')})'}',
    ),
  );

  Widget _scrubber(List<PondCameraFrame> frames) {
    final index = _selected.clamp(0, frames.length - 1);
    final frame = frames[index];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        CameraFrameImage(frame: frame),
        _metadata(frame),
        Text('Frame ${index + 1} of ${frames.length}'),
        Slider(
          key: const ValueKey('frame-scrubber'),
          value: index.toDouble(),
          max: (frames.length - 1).clamp(1, frames.length).toDouble(),
          divisions: frames.length > 1 ? frames.length - 1 : null,
          label: _time(frame.capturedAt),
          onChanged: frames.length == 1
              ? null
              : (value) => setState(() => _selected = value.round()),
        ),
        const Text('Camera green ratio (%)'),
        const Text('Frames with unknown green ratio have no chart point.'),
        SizedBox(
          height: 220,
          child: CameraGreenRatioChart(
            frames: frames,
            selected: index,
            onSelected: (value) => setState(() => _selected = value),
          ),
        ),
        TextButton(
          onPressed: frame.id == null ? null : () => _rate(frame),
          child: const Text('Rate this frame'),
        ),
      ],
    );
  }
}

/// Thumbnail failures fall back to the full frame; missing objects remain
/// visible as a placeholder without hiding their analysis or rating controls.
class CameraFrameImage extends StatelessWidget {
  const CameraFrameImage({
    super.key,
    required this.frame,
    this.thumbnail = false,
  });
  final PondCameraFrame frame;
  final bool thumbnail;

  @override
  Widget build(BuildContext context) {
    Widget missing() => const Center(child: Text('Camera image unavailable'));
    Widget full() => frame.imageUrl == null
        ? missing()
        : Image.network(
            frame.imageUrl!,
            fit: BoxFit.contain,
            errorBuilder: (_, _, _) => missing(),
          );
    return AspectRatio(
      aspectRatio: 4 / 3,
      child: thumbnail && frame.thumbnailUrl != null
          ? Image.network(
              frame.thumbnailUrl!,
              fit: BoxFit.contain,
              errorBuilder: (_, _, _) => full(),
            )
          : full(),
    );
  }
}

class CameraGreenRatioChart extends StatelessWidget {
  const CameraGreenRatioChart({
    super.key,
    required this.frames,
    required this.selected,
    required this.onSelected,
  });
  final List<PondCameraFrame> frames;
  final int selected;
  final ValueChanged<int> onSelected;

  double _x(int index) =>
      frames[index].capturedAt?.millisecondsSinceEpoch.toDouble() ??
      index.toDouble();

  @override
  Widget build(BuildContext context) {
    final accent = AppColors.of(context).water;
    return LineChart(
      LineChartData(
        minX: _x(0),
        maxX: _x(frames.length - 1) > _x(0) ? _x(frames.length - 1) : _x(0) + 1,
        minY: 0,
        maxY: 100,
        titlesData: FlTitlesData(
          topTitles: const AxisTitles(
            sideTitles: SideTitles(showTitles: false),
          ),
          rightTitles: const AxisTitles(
            sideTitles: SideTitles(showTitles: false),
          ),
          leftTitles: const AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 40,
              interval: 25,
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 30,
              getTitlesWidget: (value, meta) => Text(
                _time(DateTime.fromMillisecondsSinceEpoch(value.round())),
                style: AppType.style(
                  color: AppColors.of(context).textMuted,
                  fontSize: AppType.micro,
                ),
              ),
            ),
          ),
        ),
        extraLinesData: ExtraLinesData(
          verticalLines: [
            VerticalLine(x: _x(selected), color: accent, strokeWidth: 2),
          ],
        ),
        lineBarsData: [
          LineChartBarData(
            color: accent,
            isCurved: false,
            spots: [
              for (var i = 0; i < frames.length; i++)
                if (frames[i].greenRatio != null)
                  FlSpot(_x(i), frames[i].greenRatio! * 100)
                else
                  FlSpot.nullSpot,
            ],
            dotData: const FlDotData(show: true),
          ),
        ],
        lineTouchData: LineTouchData(
          handleBuiltInTouches: false,
          touchCallback: (event, response) {
            final spots = response?.lineBarSpots;
            if (event is FlTapUpEvent && spots != null && spots.isNotEmpty) {
              onSelected(spots.first.spotIndex);
            }
          },
        ),
      ),
      duration: Duration.zero,
    );
  }
}
