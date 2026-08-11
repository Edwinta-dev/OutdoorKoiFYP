// lib/screens/detail_graph_screen.dart

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

class DetailGraphScreen extends StatefulWidget {
  final String
  metricType; // 'temperature', 'ph', 'lux', 'water_quality', 'evaporation', 'algae'
  final String title;

  const DetailGraphScreen({
    super.key,
    required this.metricType,
    required this.title,
  });

  @override
  State<DetailGraphScreen> createState() => _DetailGraphScreenState();
}

class _DetailGraphScreenState extends State<DetailGraphScreen> {
  bool _isLoading = true;
  int _selectedDays = 30; // Time horizon: 7D or 30D

  List<Map<String, dynamic>> _dailyTrends = [];
  List<Map<String, dynamic>> _interventions = [];

  @override
  void initState() {
    super.initState();
    _fetchHistoricalPayload();
  }

  /// Fetches historical averages and interventions from Supabase RPC
  Future<void> _fetchHistoricalPayload() async {
    setState(() => _isLoading = true);

    try {
      final prefs = await SharedPreferences.getInstance();
      final userId = int.tryParse(prefs.getString('userID') ?? '0') ?? 0;

      final response = await Supabase.instance.client.rpc(
        'get_historical_graph_payload',
        params: {'p_userid': userId, 'p_days': _selectedDays},
      );

      if (mounted && response != null) {
        final Map<String, dynamic> data = Map<String, dynamic>.from(
          response as Map,
        );
        setState(() {
          _dailyTrends = List<Map<String, dynamic>>.from(
            data['daily_trends'] ?? [],
          );
          _interventions = List<Map<String, dynamic>>.from(
            data['interventions'] ?? [],
          );
          _isLoading = false;
        });
      }
    } catch (e) {
      debugPrint('Error fetching historical payload: $e');
      if (mounted) setState(() => _isLoading = false);
    }
  }

  /// Determines target primary sensor keys for the problem domain
  List<String> _getTargetSensorTypes() {
    final lower = widget.metricType.toLowerCase();
    if (lower.contains('ph') || lower.contains('water_quality')) {
      return ['pH', 'TDS'];
    } else if (lower.contains('lux') || lower.contains('algae')) {
      return ['LUX'];
    } else {
      return ['temp', 'Temperature'];
    }
  }

  /// Target highlighted event type for this specific problem domain
  String _getDomainPrimaryEventType() {
    final lower = widget.metricType.toLowerCase();
    if (lower.contains('ph') || lower.contains('water_quality')) {
      return 'WATER_CHANGE';
    } else if (lower.contains('lux') || lower.contains('algae')) {
      return 'ALGAE_SCRUB';
    } else {
      return 'WATER_TOPUP';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A0E17),
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(
          widget.title,
          style: const TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
          ),
        ),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back, color: Colors.white),
          onPressed: () => Navigator.pop(context),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh, color: Colors.cyanAccent),
            onPressed: _fetchHistoricalPayload,
          ),
        ],
      ),
      body: _isLoading
          ? const Center(
              child: CircularProgressIndicator(color: Colors.cyanAccent),
            )
          : SingleChildScrollView(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // --- 1. TIME HORIZON FILTER SELECTOR ---
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        ' ${widget.metricType.toUpperCase()} Timeline',
                        style: const TextStyle(
                          color: Colors.white70,
                          fontSize: 12,
                        ),
                      ),
                      SegmentedButton<int>(
                        segments: const [
                          ButtonSegment(
                            value: 7,
                            label: Text('7D', style: TextStyle(fontSize: 11)),
                          ),
                          ButtonSegment(
                            value: 30,
                            label: Text('30D', style: TextStyle(fontSize: 11)),
                          ),
                        ],
                        selected: {_selectedDays},
                        onSelectionChanged: (val) {
                          setState(() => _selectedDays = val.first);
                          _fetchHistoricalPayload();
                        },
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),

                  // --- 2. HISTORICAL GRAPH CONTAINER WITH X-AXIS INTERVENTIONS ---
                  _buildGraphContainer(),

                  const SizedBox(height: 20),

                  // --- 3. INTERVENTION LEGEND & RECENT LOGS SUMMARY ---
                  _buildInterventionLegend(),
                ],
              ),
            ),
    );
  }

  /// Builds main fl_chart Box with dynamic bounds and event overlays
  Widget _buildGraphContainer() {
    final targetSensors = _getTargetSensorTypes();
    final primaryType = targetSensors.first;

    // Filter trends matching primary target sensor
    final points = _dailyTrends.where((e) {
      final st = e['sensor_type']?.toString() ?? '';
      return st.toLowerCase() == primaryType.toLowerCase();
    }).toList();

    // SCARCE / INCOMPLETE DATASET GUARD:
    if (points.isEmpty) {
      return Container(
        height: 280,
        width: double.infinity,
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: const Color(0xFF131B2A),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: Colors.white.withOpacity(0.08)),
        ),
        child: const Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.query_stats, color: Colors.cyanAccent, size: 36),
            SizedBox(height: 10),
            Text(
              'Establishing Baseline Cycle Averages',
              style: TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.bold,
                fontSize: 14,
              ),
            ),
            SizedBox(height: 4),
            Text(
              'Daily averages will populate automatically as sensor readings collect over the month.',
              style: TextStyle(color: Colors.white54, fontSize: 11),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      );
    }

    // Convert daily averages to chart spots
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

    // Calculate dynamic Y-axis limits safely
    final yValues = spots.map((s) => s.y).toList();
    double minY = yValues.reduce((a, b) => a < b ? a : b);
    double maxY = yValues.reduce((a, b) => a > b ? a : b);
    if ((maxY - minY) < 1.0) {
      minY -= 1.0;
      maxY += 1.0;
    }

    // Map ALL interventions to chart X positions (domain highlighted vs muted)
    final primaryDomainEvent = _getDomainPrimaryEventType();
    final List<VerticalLine> verticalLines = [];

    final double minX = 0;
    final double maxX = (points.length - 1).toDouble().clamp(1.0, 30.0);

    for (var ev in _interventions) {
      final String eType = ev['event_type'] ?? '';
      final bool isPrimary = (eType == primaryDomainEvent);
      final bool isMajorReset = (ev['is_major_reset'] == true);

      // Estimate X position proportional to timeline
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
          lineCol = Colors.greenAccent; // Highlighted Major Water Change
          strokeW = 2.5;
        } else if (isPrimary) {
          lineCol = _getEventColor(eType); // Domain primary highlighted
          strokeW = 1.8;
        } else {
          lineCol = Colors.white.withOpacity(0.18); // Secondary event muted
          strokeW = 1.0;
        }

        verticalLines.add(
          VerticalLine(
            x: dayOffset,
            color: lineCol,
            strokeWidth: strokeW,
            dashArray: isPrimary ? null : [4, 4],
          ),
        );
      }
    }

    return Container(
      height: 280,
      width: double.infinity,
      padding: const EdgeInsets.only(right: 20, left: 10, top: 20, bottom: 12),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: LineChart(
        LineChartData(
          minY: minY,
          maxY: maxY,
          minX: minX,
          maxX: maxX,
          gridData: FlGridData(
            show: true,
            drawVerticalLine: false,
            getDrawingHorizontalLine: (value) =>
                FlLine(color: Colors.white.withOpacity(0.04), strokeWidth: 1),
          ),
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
                reservedSize: 36,
                getTitlesWidget: (val, meta) => Text(
                  val.toStringAsFixed(1),
                  style: const TextStyle(color: Colors.white38, fontSize: 9),
                ),
              ),
            ),
            bottomTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 22,
                interval: (spots.length / 5).clamp(1.0, 10.0),
                getTitlesWidget: (val, meta) {
                  final idx = val.toInt();
                  return Text(
                    dateLabels[idx] ?? '',
                    style: const TextStyle(color: Colors.white38, fontSize: 9),
                  );
                },
              ),
            ),
          ),
          borderData: FlBorderData(show: false),
          extraLinesData: ExtraLinesData(verticalLines: verticalLines),
          lineBarsData: [
            LineChartBarData(
              spots: spots,
              isCurved: true,
              color: _getMetricLineColor(primaryType),
              barWidth: 2.5,
              isStrokeCapRound: true,
              dotData: const FlDotData(show: false),
              belowBarData: BarAreaData(
                show: true,
                color: _getMetricLineColor(primaryType).withOpacity(0.12),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildInterventionLegend() {
    final primaryType = _getDomainPrimaryEventType();

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.06)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Timeline Intervention Legend',
            style: TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.bold,
              fontSize: 12,
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 12,
            runSpacing: 8,
            children: [
              _legendItem(
                'Major Flush Reset',
                Colors.greenAccent,
                isHighlighted: true,
              ),
              _legendItem(
                'Water Change',
                Colors.lightBlueAccent,
                isHighlighted: primaryType == 'WATER_CHANGE',
              ),
              _legendItem(
                'Water Top-Up',
                Colors.cyanAccent,
                isHighlighted: primaryType == 'WATER_TOPUP',
              ),
              _legendItem(
                'Algae Scrub',
                Colors.tealAccent,
                isHighlighted: primaryType == 'ALGAE_SCRUB',
              ),
              _legendItem(
                'Feeding Session',
                Colors.orangeAccent,
                isHighlighted: primaryType == 'FEEDING',
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _legendItem(String label, Color color, {required bool isHighlighted}) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: isHighlighted ? 10 : 6,
          height: isHighlighted ? 10 : 6,
          decoration: BoxDecoration(
            color: isHighlighted ? color : Colors.white24,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 6),
        Text(
          label,
          style: TextStyle(
            color: isHighlighted ? Colors.white : Colors.white38,
            fontSize: 10,
            fontWeight: isHighlighted ? FontWeight.bold : FontWeight.normal,
          ),
        ),
      ],
    );
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
