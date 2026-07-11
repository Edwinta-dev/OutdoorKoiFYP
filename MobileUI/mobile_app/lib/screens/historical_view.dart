import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'dart:math';

class HistoricalView extends StatefulWidget {
  const HistoricalView({super.key});

  @override
  State<HistoricalView> createState() => _HistoricalViewState();
}

class _HistoricalViewState extends State<HistoricalView> {
  // Toggle state for Day vs. Week/Month view
  bool _isDayView = true;

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 7,
      child: Column(
        children: [
          // 1. View Toggle Switch (Using Wrap to prevent overflow)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: 16.0,
              vertical: 8.0,
            ),
            child: Wrap(
              alignment: WrapAlignment.spaceBetween,
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: 8.0,
              runSpacing: 12.0,
              children: [
                const Text(
                  'Data Range:',
                  style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                ),
                SegmentedButton<bool>(
                  segments: const [
                    ButtonSegment<bool>(
                      value: true,
                      label: Text('Day (30 pts)'),
                    ),
                    ButtonSegment<bool>(
                      value: false,
                      label: Text('Week/Month'),
                    ),
                  ],
                  selected: {_isDayView},
                  onSelectionChanged: (Set<bool> newSelection) {
                    setState(() {
                      _isDayView = newSelection.first;
                    });
                  },
                ),
              ],
            ),
          ),

          // 2. The Scrollable Tab Bar
          const TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            tabs: [
              Tab(text: 'pH'),
              Tab(text: 'Temp'),
              Tab(text: 'TDS'),
              Tab(text: 'Lux'),
              Tab(text: 'Ammonia'),
              Tab(text: 'Nitrate'),
              Tab(text: 'Algae'),
            ],
          ),

          // 3. The Tab Contents
          Expanded(
            child: TabBarView(
              children: [
                SensorChartWidget(
                  endpoint: 'ph',
                  title: 'pH Level',
                  isDayView: _isDayView,
                  color: Colors.blue,
                ),
                SensorChartWidget(
                  endpoint: 'temp',
                  title: 'Temperature (°C)',
                  isDayView: _isDayView,
                  color: Colors.orange,
                ),
                SensorChartWidget(
                  endpoint: 'tds',
                  title: 'TDS (ppm)',
                  isDayView: _isDayView,
                  color: Colors.green,
                ),
                SensorChartWidget(
                  endpoint: 'lux',
                  title: 'Lux',
                  isDayView: _isDayView,
                  color: Colors.amber,
                ),

                // Empty Placeholders
                const Center(
                  child: Text('Ammonia forecasting model pending...'),
                ),
                const Center(
                  child: Text('Nitrate forecasting model pending...'),
                ),
                const Center(child: Text('Algae forecasting model pending...')),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// A Reusable Widget that fetches and graphs data for any given sensor endpoint
class SensorChartWidget extends StatefulWidget {
  final String endpoint;
  final String title;
  final bool isDayView;
  final Color color;

  const SensorChartWidget({
    super.key,
    required this.endpoint,
    required this.title,
    required this.isDayView,
    required this.color,
  });

  @override
  State<SensorChartWidget> createState() => _SensorChartWidgetState();
}

class _SensorChartWidgetState extends State<SensorChartWidget> {
  List<FlSpot> spots = [];
  bool isLoading = true;
  String? errorMessage;

  @override
  void initState() {
    super.initState();
    _fetchData();
  }

  @override
  void didUpdateWidget(SensorChartWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isDayView != widget.isDayView) {
      _fetchData();
    }
  }

  Future<void> _fetchData() async {
    setState(() {
      isLoading = true;
      errorMessage = null;
    });

    if (!widget.isDayView) {
      _loadDummyWeekData();
      return;
    }

    try {
      final url = Uri.parse('http://192.168.68.58:5000/api/${widget.endpoint}');
      final response = await http.get(url);

      if (response.statusCode == 200) {
        final List<dynamic> data = json.decode(response.body);
        final reversedData = data.reversed.toList();

        List<FlSpot> parsedSpots = [];
        for (var item in reversedData) {
          DateTime time = DateTime.parse(item['time']).toLocal();
          double xValue = time.millisecondsSinceEpoch.toDouble();

          double yValue = (item['value'] is int)
              ? (item['value'] as int).toDouble()
              : item['value'] as double;

          parsedSpots.add(FlSpot(xValue, yValue));
        }

        setState(() {
          spots = parsedSpots;
          isLoading = false;
        });
      } else {
        setState(() {
          errorMessage = 'Server error: ${response.statusCode}';
          isLoading = false;
        });
      }
    } catch (e) {
      setState(() {
        errorMessage = 'Failed to connect: $e';
        isLoading = false;
      });
    }
  }

  void _loadDummyWeekData() {
    double now = DateTime.now().millisecondsSinceEpoch.toDouble();
    double dayMs = 86400000;
    List<FlSpot> dummySpots = [];
    for (int i = 0; i < 7; i++) {
      dummySpots.add(FlSpot(now - ((6 - i) * dayMs), 25.0 + sin(i) * 3));
    }
    setState(() {
      spots = dummySpots;
      isLoading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (isLoading) return const Center(child: CircularProgressIndicator());
    if (errorMessage != null)
      return Center(
        child: Text(errorMessage!, style: const TextStyle(color: Colors.red)),
      );
    if (spots.isEmpty) return const Center(child: Text('No data available.'));

    double minX = spots.first.x;
    double maxX = spots.last.x;

    double tenMins = 600000;
    if (maxX - minX < tenMins * 1.5) {
      minX -= tenMins;
      maxX += tenMins;
    }

    double minY = spots.map((s) => s.y).reduce(min) * 0.95;
    double maxY = spots.map((s) => s.y).reduce(max) * 1.05;

    // Integrated Snippet
    return Padding(
      padding: const EdgeInsets.all(16.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.title,
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 24),
          Expanded(
            child: LineChart(
              LineChartData(
                minX: minX,
                maxX: maxX,
                minY: minY,
                maxY: maxY,
                gridData: FlGridData(show: true, drawVerticalLine: false),
                titlesData: FlTitlesData(
                  topTitles: const AxisTitles(
                    sideTitles: SideTitles(showTitles: false),
                  ),
                  rightTitles: const AxisTitles(
                    sideTitles: SideTitles(showTitles: false),
                  ),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 30,
                      interval: widget.isDayView ? 600000 : 86400000,
                      getTitlesWidget: (value, meta) {
                        DateTime date = DateTime.fromMillisecondsSinceEpoch(
                          value.toInt(),
                        );

                        String text = widget.isDayView
                            ? "${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}"
                            : _getWeekday(date.weekday);

                        return Padding(
                          padding: const EdgeInsets.only(top: 8.0),
                          child: Text(
                            text,
                            style: const TextStyle(
                              fontSize: 10,
                              color: Colors.grey,
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                ),
                borderData: FlBorderData(show: false),
                lineBarsData: [
                  LineChartBarData(
                    spots: spots,
                    isCurved: true,
                    color: widget.color,
                    barWidth: 3,
                    isStrokeCapRound: true,
                    dotData: FlDotData(show: spots.length < 15),
                    belowBarData: BarAreaData(
                      show: true,
                      color: widget.color.withOpacity(0.15),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _getWeekday(int day) {
    switch (day) {
      case 1:
        return 'Mon';
      case 2:
        return 'Tue';
      case 3:
        return 'Wed';
      case 4:
        return 'Thu';
      case 5:
        return 'Fri';
      case 6:
        return 'Sat';
      case 7:
        return 'Sun';
      default:
        return '';
    }
  }
}
