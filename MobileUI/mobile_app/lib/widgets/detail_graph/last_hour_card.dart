import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_app/theme/app_theme.dart';

import '../../data/providers.dart';
import '../../data/recent_readings.dart';
import '../shared/pond_widgets.dart';

/// The raw readings of one sensor over the last hour: a line trace, the
/// latest value with its time, and the window's minimum and maximum.
/// [recentReadingsProvider] re-fetches every 30 s; the readings already
/// shown stay up while a refresh is in flight or has failed.
class LastHourCard extends ConsumerWidget {
  const LastHourCard({
    super.key,
    required this.userId,
    required this.sensorType,
  });

  final int userId;

  /// The SensorData sensor_type: 'pH', 'temp', 'TDS' or 'LUX'.
  final String sensorType;

  static const emptyMessage =
      'No readings in the last hour. Check the sensor is online.';

  /// "7.42" for pH, "28.4 °C", "212 ppm", "11500 lx".
  static String formatValue(String sensorType, double value) {
    final lower = sensorType.toLowerCase();
    if (lower.contains('ph')) return value.toStringAsFixed(2);
    if (lower.contains('temp')) return '${value.toStringAsFixed(1)} °C';
    if (lower.contains('tds')) return '${value.toStringAsFixed(0)} ppm';
    return '${value.toStringAsFixed(0)} lx';
  }

  /// Local wall-clock time, "21:05:30".
  static String formatTime(DateTime at) {
    final t = at.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final c = AppColors.of(context);
    final query = (pond: userId, sensor: sensorType);
    final state = ref.watch(recentReadingsProvider(query));
    final data = state.value;
    final latest = data?.latest;

    final Widget body;
    if (data == null) {
      body = state.hasError
          ? PondErrorState(
              title: 'Last hour could not be loaded',
              message: 'Check your connection and try again.',
              onRetry: () => ref.invalidate(recentReadingsProvider(query)),
            )
          : Padding(
              padding: const EdgeInsets.symmetric(vertical: AppSpace.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Loading the last hour of readings',
                    style: AppType.style(
                      color: c.textSecondary,
                      fontSize: AppType.label,
                    ),
                  ),
                  const SizedBox(height: AppSpace.sm),
                  LinearProgressIndicator(color: c.info),
                ],
              ),
            );
    } else if (latest == null) {
      body = Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpace.md),
        child: Text(
          emptyMessage,
          style: AppType.style(color: c.textSecondary, fontSize: AppType.body),
        ),
      );
    } else {
      body = Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: 140,
            child: _Trace(readings: data.readings, sensorType: sensorType),
          ),
          const SizedBox(height: AppSpace.sm),
          Wrap(
            spacing: AppSpace.lg,
            runSpacing: AppSpace.xs,
            children: [
              for (final text in [
                'Min ${formatValue(sensorType, data.minimum)}',
                'Max ${formatValue(sensorType, data.maximum)}',
                'From ${formatTime(data.readings.first.at)}',
              ])
                Text(
                  text,
                  style: AppType.style(
                    color: c.textSecondary,
                    fontSize: AppType.label,
                  ),
                ),
            ],
          ),
          if (state.hasError) ...[
            const SizedBox(height: AppSpace.xs),
            Text(
              'Update failed. Showing the last readings received.',
              style: AppType.style(color: c.danger, fontSize: AppType.label),
            ),
          ],
        ],
      );
    }

    return OutcomeCardShell(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            alignment: WrapAlignment.spaceBetween,
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: AppSpace.md,
            runSpacing: AppSpace.xs,
            children: [
              Text(
                'Last hour',
                style: AppType.style(
                  color: c.text,
                  fontWeight: FontWeight.bold,
                  fontSize: AppType.body,
                ),
              ),
              if (latest != null)
                Text(
                  '${formatValue(sensorType, latest.value)} · ${formatTime(latest.at)}',
                  key: const ValueKey('last-hour-latest'),
                  style: AppType.style(
                    color: c.text,
                    fontWeight: FontWeight.bold,
                    fontSize: AppType.title,
                  ),
                ),
            ],
          ),
          const SizedBox(height: AppSpace.sm),
          body,
        ],
      ),
    );
  }
}

class _Trace extends StatelessWidget {
  const _Trace({required this.readings, required this.sensorType});

  final List<RecentReading> readings;
  final String sensorType;

  /// The smallest value range the chart spans, so sensor noise on a
  /// steady reading does not fill the card.
  double get _minimumSpan {
    final lower = sensorType.toLowerCase();
    if (lower.contains('ph')) return 0.2;
    if (lower.contains('temp')) return 1;
    return 10;
  }

  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    final lower = sensorType.toLowerCase();
    final lineColor = lower.contains('ph')
        ? c.healthy
        : lower.contains('lux')
        ? c.feeding
        : c.info;

    // x is seconds since the first reading.
    final start = readings.first.at;
    final spots = [
      for (final r in readings)
        FlSpot(r.at.difference(start).inMilliseconds / 1000, r.value),
    ];
    final values = readings.map((r) => r.value);
    final low = values.reduce((a, b) => a < b ? a : b);
    final high = values.reduce((a, b) => a > b ? a : b);
    final span = (high - low) < _minimumSpan ? _minimumSpan : high - low;
    final middle = (high + low) / 2;
    final lastX = spots.last.x;

    return LineChart(
      LineChartData(
        minX: 0,
        maxX: lastX > 0 ? lastX : 1,
        minY: middle - span * 0.65,
        maxY: middle + span * 0.65,
        titlesData: const FlTitlesData(show: false),
        borderData: FlBorderData(show: false),
        gridData: FlGridData(
          drawVerticalLine: false,
          horizontalInterval: span * 1.3 / 4,
          getDrawingHorizontalLine: (_) =>
              FlLine(color: c.text.withValues(alpha: 0.08), strokeWidth: 1),
        ),
        lineTouchData: LineTouchData(
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (_) => c.surfaceInset.withValues(alpha: 0.92),
            getTooltipItems: (touched) => [
              for (final spot in touched)
                LineTooltipItem(
                  '${LastHourCard.formatValue(sensorType, spot.y)}\n'
                  '${LastHourCard.formatTime(readings[spot.spotIndex].at)}',
                  AppType.style(
                    color: lineColor,
                    fontWeight: FontWeight.bold,
                    fontSize: AppType.caption,
                  ),
                ),
            ],
          ),
        ),
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            color: lineColor,
            barWidth: 2,
            isCurved: false,
            dotData: FlDotData(
              show: true,
              checkToShowDot: (spot, bar) =>
                  spots.length == 1 || spot == bar.spots.last,
            ),
            belowBarData: BarAreaData(
              show: true,
              color: lineColor.withValues(alpha: 0.10),
            ),
          ),
        ],
      ),
      duration: Duration.zero,
    );
  }
}
