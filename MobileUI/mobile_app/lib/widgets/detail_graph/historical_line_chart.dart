import 'package:mobile_app/theme/app_theme.dart';
import 'dart:math';
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

class HistoricalLineChart extends StatelessWidget {
  final List<Map<String, dynamic>> points;
  final List<Map<String, dynamic>> interventions;
  final String primarySensorType;
  final String primaryDomainEvent;

  const HistoricalLineChart({
    super.key,
    required this.points,
    required this.interventions,
    required this.primarySensorType,
    required this.primaryDomainEvent,
  });

  @override
  Widget build(BuildContext context) {
    if (points.isEmpty) {
      return const SizedBox.shrink();
    }

    final List<FlSpot> spots = [];
    final Map<int, String> dateLabels = {};

    for (int i = 0; i < points.length; i++) {
      final double val =
          double.tryParse(points[i]['avg_value'].toString()) ?? 0.0;
      spots.add(FlSpot(i.toDouble(), val));

      final rawDate = points[i]['date']?.toString() ?? '';
      if (rawDate.isNotEmpty) {
        final parsed = DateTime.tryParse(rawDate);
        dateLabels[i] = parsed != null
            ? '${parsed.day}/${parsed.month}'
            : rawDate;
      }
    }

    // --- 1. ROBUST Y-AXIS BOUNDS FOR WILD SWINGS (LUX / ALGAE SAFE) ---
    final yValues = spots.map((s) => s.y).toList();
    double rawMinY = yValues.reduce((a, b) => a < b ? a : b);
    double rawMaxY = yValues.reduce((a, b) => a > b ? a : b);

    double dataSpan = rawMaxY - rawMinY;

    // Guard against Flatline (e.g. night time 0 LUX or flat sensor data)
    if (dataSpan < 1.0) {
      dataSpan = 10.0;
      rawMaxY = rawMinY + dataSpan;
    }

    // Target ~65% vertical occupancy band
    final double padding = dataSpan * 0.20;

    final bool isLuxOrAlgae =
        primarySensorType.toLowerCase().contains('lux') ||
        primarySensorType.toLowerCase().contains('algae');
    final bool canBeNegative = primarySensorType.toLowerCase().contains('temp');

    double minY = rawMinY - padding;
    if (!canBeNegative && minY < 0) {
      minY = 0.0;
    }
    double maxY = rawMaxY + padding;

    // --- 2. SAFE Y-INTERVAL CALCULATION (PREVENT FL_CHART OVERFLOW / NaN) ---
    final double range = maxY - minY;
    double rawInterval = range / 4.0;

    // Determine a "nice" clean step interval (1, 2, 5, 10, 100, 1000, 10000, etc.)
    double exponent = (log(rawInterval) / ln10).floorToDouble();
    double fraction = rawInterval / pow(10, exponent);
    double niceFraction;
    if (fraction < 1.5) {
      niceFraction = 1.0;
    } else if (fraction < 3.0) {
      niceFraction = 2.0;
    } else if (fraction < 7.0) {
      niceFraction = 5.0;
    } else {
      niceFraction = 10.0;
    }
    double yInterval = max(1.0, niceFraction * pow(10, exponent));

    // --- 3. X-AXIS BOUNDS & DYNAMIC INTERVENTIONS ---
    final double minX = 0;
    final double maxX = (points.length - 1).toDouble().clamp(1.0, 30.0);

    final List<VerticalLine> verticalLines = [];
    for (var ev in interventions) {
      final String eType = ev['event_type'] ?? '';
      final bool isPrimary =
          (eType == primaryDomainEvent) ||
          ((eType == 'SALT' || eType == 'FILTER_CLEAN') &&
              ['tds', 'ph'].contains(primarySensorType.toLowerCase()));
      final bool isMajorReset = (ev['is_major_reset'] == true);

      final String rawTs = ev['timestamp'] ?? '';
      final evDate = DateTime.tryParse(rawTs);
      if (evDate != null && points.isNotEmpty) {
        final firstDate =
            DateTime.tryParse(points.first['date']?.toString() ?? '') ?? evDate;
        final dayOffset = evDate
            .difference(firstDate)
            .inDays
            .toDouble()
            .clamp(minX, maxX);

        Color lineCol;
        double strokeW;

        if (isMajorReset) {
          lineCol = AppColors.of(context).healthy;
          strokeW = 2.0;
        } else if (isPrimary) {
          lineCol = _getEventColor(context, eType);
          strokeW = 1.5;
        } else {
          lineCol = AppColors.of(context).text.withValues(alpha: 0.12);
          strokeW = 1.0;
        }

        verticalLines.add(
          VerticalLine(
            x: dayOffset,
            label: VerticalLineLabel(
              show: eType == 'SALT' || eType == 'FILTER_CLEAN',
              style: AppType.style(
                color: AppColors.of(context).textSecondary,
                fontSize: AppType.micro,
              ),
              labelResolver: (_) => eType == 'SALT'
                  ? 'Salt ${ev["salt_grams"] ?? "?"} g'
                  : 'Filter cleaning${ev["notes"] == null ? "" : ": ${ev["notes"]}"}',
            ),
            color: lineCol,
            strokeWidth: strokeW,
            dashArray: isPrimary || isMajorReset ? null : [4, 4],
          ),
        );
      }
    }

    final lineColor = _getMetricLineColor(context, primarySensorType);

    return Container(
      height: 290,
      width: double.infinity,
      padding: const EdgeInsets.only(
        right: AppSpace.xl,
        left: AppSpace.sm,
        top: AppSpace.xxl,
        bottom: AppSpace.md,
      ),
      decoration: BoxDecoration(
        color: AppColors.of(context).surface,
        borderRadius: BorderRadius.circular(AppRadius.panel),
        border: Border.all(
          color: AppColors.of(context).text.withValues(alpha: 0.08),
        ),
        boxShadow: [
          BoxShadow(
            color: AppColors.of(context).shadow.withValues(alpha: 0.25),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: LineChart(
        LineChartData(
          minY: minY,
          maxY: maxY,
          minX: minX,
          maxX: maxX,

          // Touch Tooltips
          lineTouchData: LineTouchData(
            enabled: true,
            touchTooltipData: LineTouchTooltipData(
              getTooltipColor: (spot) =>
                  AppColors.of(context).surfaceInset.withValues(alpha: 0.92),
              tooltipBorder: BorderSide(
                color: lineColor.withValues(alpha: 0.5),
              ),
              tooltipPadding: const EdgeInsets.symmetric(
                horizontal: AppSpace.md,
                vertical: AppSpace.sm,
              ),
              tooltipMargin: 12,
              getTooltipItems: (touchedSpots) {
                return touchedSpots.map((spot) {
                  return LineTooltipItem(
                    '${_formatValue(spot.y, isLuxOrAlgae)} ${_getMetricUnit(primarySensorType)}'
                    '${points[spot.x.toInt()]['after_maintenance'] == true ? '\nAfter maintenance' : ''}',
                    AppType.style(
                      color: lineColor,
                      fontWeight: FontWeight.bold,
                      fontSize: AppType.caption,
                    ),
                  );
                }).toList();
              },
            ),
            getTouchedSpotIndicator:
                (LineChartBarData barData, List<int> spotIndexes) {
                  return spotIndexes.map((spotIndex) {
                    return TouchedSpotIndicatorData(
                      FlLine(
                        color: lineColor.withValues(alpha: 0.5),
                        strokeWidth: 1.5,
                        dashArray: [4, 4],
                      ),
                      FlDotData(
                        show: true,
                        getDotPainter: (spot, percent, barData, index) =>
                            FlDotCirclePainter(
                              radius: 5,
                              color: lineColor,
                              strokeWidth: 2,
                              strokeColor: AppColors.of(context).text,
                            ),
                      ),
                    );
                  }).toList();
                },
          ),

          // Grid
          gridData: FlGridData(
            show: true,
            drawVerticalLine: false,
            getDrawingHorizontalLine: (value) => FlLine(
              color: AppColors.of(context).text.withValues(alpha: 0.03),
              strokeWidth: 1,
            ),
          ),

          // Titles & Axis Formatting
          titlesData: FlTitlesData(
            rightTitles: const AxisTitles(
              sideTitles: SideTitles(showTitles: false),
            ),
            topTitles: const AxisTitles(
              sideTitles: SideTitles(showTitles: false),
            ),
            leftTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 42,
                interval: yInterval,
                getTitlesWidget: (val, meta) {
                  if (val <= minY || val >= maxY) {
                    return const SizedBox.shrink();
                  }
                  return Text(
                    _formatValue(val, isLuxOrAlgae),
                    style: AppType.style(
                      color: AppColors.of(context).text.withValues(alpha: 0.35),
                      fontSize: AppType.micro,
                      fontWeight: FontWeight.w500,
                    ),
                  );
                },
              ),
            ),
            bottomTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 22,
                interval: (spots.length / 5).clamp(1.0, 10.0),
                getTitlesWidget: (val, meta) {
                  final idx = val.toInt();
                  return Padding(
                    padding: const EdgeInsets.only(top: AppSpace.sm),
                    child: Text(
                      dateLabels[idx] ?? '',
                      style: AppType.style(
                        color: AppColors.of(
                          context,
                        ).text.withValues(alpha: 0.35),
                        fontSize: AppType.micro,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
          borderData: FlBorderData(show: false),
          extraLinesData: ExtraLinesData(verticalLines: verticalLines),

          // Line & Area Fill
          lineBarsData: [
            LineChartBarData(
              spots: spots,
              isCurved: true,
              curveSmoothness: 0.35,
              color: lineColor,
              barWidth: 2.8,
              isStrokeCapRound: true,
              dotData: const FlDotData(show: false),
              belowBarData: BarAreaData(
                show: true,
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    lineColor.withValues(alpha: 0.28),
                    lineColor.withValues(alpha: 0.0),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Formats Y-axis values cleanly (e.g., 1000 -> 1k, 50000 -> 50k)
  String _formatValue(double val, bool isLargeMetric) {
    if (isLargeMetric || val >= 1000) {
      if (val >= 1000000) {
        return '${(val / 1000000).toStringAsFixed(1)}M';
      } else if (val >= 1000) {
        return '${(val / 1000).toStringAsFixed(val % 1000 == 0 ? 0 : 1)}k';
      }
    }
    return val.toStringAsFixed(val < 10 ? 1 : 0);
  }

  String _getMetricUnit(String sensorType) {
    final lower = sensorType.toLowerCase();
    if (lower.contains('ph')) return 'pH';
    if (lower.contains('lux')) return 'lx';
    if (lower.contains('tds')) return 'ppm';
    if (lower.contains('temp')) return '°C';
    return '';
  }

  Color _getEventColor(BuildContext context, String eventType) {
    switch (eventType) {
      case 'WATER_CHANGE':
        return AppColors.of(context).info;
      case 'WATER_TOPUP':
        return AppColors.of(context).info;
      case 'ALGAE_SCRUB':
        return AppColors.of(context).water;
      case 'SALT':
        return AppColors.of(context).intervention;
      case 'FILTER_CLEAN':
        return AppColors.of(context).warning;
      case 'FEEDING':
        return AppColors.of(context).feeding;
      default:
        return AppColors.of(context).textMuted;
    }
  }

  Color _getMetricLineColor(BuildContext context, String sensorType) {
    final lower = sensorType.toLowerCase();
    if (lower.contains('ph')) return AppColors.of(context).healthy;
    if (lower.contains('lux')) return AppColors.of(context).feeding;
    if (lower.contains('tds')) return AppColors.of(context).info;
    return AppColors.of(context).info;
  }
}
