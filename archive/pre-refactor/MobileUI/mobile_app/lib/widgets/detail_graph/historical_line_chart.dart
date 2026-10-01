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
      final bool isPrimary = (eType == primaryDomainEvent);
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
          lineCol = Colors.greenAccent;
          strokeW = 2.0;
        } else if (isPrimary) {
          lineCol = _getEventColor(eType);
          strokeW = 1.5;
        } else {
          lineCol = Colors.white.withOpacity(0.12);
          strokeW = 1.0;
        }

        verticalLines.add(
          VerticalLine(
            x: dayOffset,
            color: lineCol,
            strokeWidth: strokeW,
            dashArray: isPrimary || isMajorReset ? null : [4, 4],
          ),
        );
      }
    }

    final lineColor = _getMetricLineColor(primarySensorType);

    return Container(
      height: 290,
      width: double.infinity,
      padding: const EdgeInsets.only(right: 20, left: 8, top: 24, bottom: 12),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.25),
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
                  const Color(0xFF0F172A).withOpacity(0.92),
              tooltipBorder: BorderSide(color: lineColor.withOpacity(0.5)),
              tooltipPadding: const EdgeInsets.symmetric(
                horizontal: 10,
                vertical: 6,
              ),
              tooltipMargin: 12,
              getTooltipItems: (touchedSpots) {
                return touchedSpots.map((spot) {
                  return LineTooltipItem(
                    '${_formatValue(spot.y, isLuxOrAlgae)} ${_getMetricUnit(primarySensorType)}',
                    TextStyle(
                      color: lineColor,
                      fontWeight: FontWeight.bold,
                      fontSize: 11,
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
                        color: lineColor.withOpacity(0.5),
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
                              strokeColor: Colors.white,
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
            getDrawingHorizontalLine: (value) =>
                FlLine(color: Colors.white.withOpacity(0.03), strokeWidth: 1),
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
                  if (val <= minY || val >= maxY)
                    return const SizedBox.shrink();
                  return Text(
                    _formatValue(val, isLuxOrAlgae),
                    style: TextStyle(
                      color: Colors.white.withOpacity(0.35),
                      fontSize: 9,
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
                    padding: const EdgeInsets.only(top: 6.0),
                    child: Text(
                      dateLabels[idx] ?? '',
                      style: TextStyle(
                        color: Colors.white.withOpacity(0.35),
                        fontSize: 9,
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
                    lineColor.withOpacity(0.28),
                    lineColor.withOpacity(0.0),
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

  Color _getEventColor(String eventType) {
    switch (eventType) {
      case 'WATER_CHANGE':
        return Colors.lightBlueAccent;
      case 'WATER_TOPUP':
        return Colors.cyanAccent;
      case 'ALGAE_SCRUB':
        return Colors.tealAccent;
      case 'FEEDING':
        return Colors.orangeAccent;
      default:
        return Colors.white54;
    }
  }

  Color _getMetricLineColor(String sensorType) {
    final lower = sensorType.toLowerCase();
    if (lower.contains('ph')) return const Color(0xFF50C878);
    if (lower.contains('lux')) return const Color(0xFFFF8A65);
    if (lower.contains('tds')) return Colors.lightBlueAccent;
    return Colors.cyanAccent;
  }
}
