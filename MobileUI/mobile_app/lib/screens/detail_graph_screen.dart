import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../widgets/detail_graph/historical_line_chart.dart';
import '../widgets/detail_graph/intervention_legend.dart';
import '../widgets/detail_graph/scarce_data_placeholder.dart';
import '../widgets/detail_graph/timeline_header_selector.dart';
import '../widgets/detail_graph/water_buffer_status_card.dart';

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
  int _selectedDays = 30;
  int _chemistryRefreshTick = 0;

  int _userId = 0;

  List<Map<String, dynamic>> _dailyTrends = [];
  List<Map<String, dynamic>> _interventions = [];

  @override
  void initState() {
    super.initState();
    _loadUserPreferencesAndPayload();
  }

  /// Loads tank parameters from SharedPreferences & fetches database trends
  Future<void> _loadUserPreferencesAndPayload() async {
    setState(() => _isLoading = true);

    try {
      final prefs = await SharedPreferences.getInstance();
      final userId = int.tryParse(prefs.getString('userID') ?? '0') ?? 0;
      _userId = userId;

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
          _chemistryRefreshTick++;
        });
      }
    } catch (e) {
      debugPrint('Error fetching historical payload: $e');
      if (mounted) setState(() => _isLoading = false);
    }
  }

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
    final targetSensors = _getTargetSensorTypes();
    final primaryType = targetSensors.first;
    final primaryEvent = _getDomainPrimaryEventType();

    final filteredPoints = _dailyTrends.where((e) {
      final st = e['sensor_type']?.toString() ?? '';
      return st.toLowerCase() == primaryType.toLowerCase();
    }).toList();

    final bool isWaterQualityDomain =
        widget.metricType.toLowerCase().contains('ph') ||
        widget.metricType.toLowerCase().contains('water_quality');

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
            onPressed: _loadUserPreferencesAndPayload,
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
                  // --- 2. TIMELINE TIMEFRAME SELECTOR ---
                  TimelineHeaderSelector(
                    metricType: widget.metricType,
                    selectedDays: _selectedDays,
                    onDaysChanged: (days) {
                      setState(() => _selectedDays = days);
                      _loadUserPreferencesAndPayload();
                    },
                  ),
                  const SizedBox(height: 16),

                  // --- 3. HISTORICAL GRAPH WITH INTERVENTIONS ---
                  filteredPoints.isEmpty
                      ? const ScarceDataPlaceholder()
                      : HistoricalLineChart(
                          points: filteredPoints,
                          interventions: _interventions,
                          primarySensorType: primaryType,
                          primaryDomainEvent: primaryEvent,
                        ),
                  const SizedBox(height: 20),

                  // --- 4. INTERVENTION LEGEND ---
                  InterventionLegend(primaryEventType: primaryEvent),
                  const SizedBox(height: 16),

                  // --- 3b. WATER BUFFER / NITROGEN CYCLE (pH domain only) ---
                  // Server-computed by the DigitalTwin Flask engine - sits
                  // directly under the pH graph per product requirement.
                  if (isWaterQualityDomain) ...[
                    WaterBufferStatusCard(
                      key: ValueKey('chemistry-$_chemistryRefreshTick'),
                      userId: _userId,
                    ),
                    const SizedBox(height: 20),
                  ],
                ],
              ),
            ),
    );
  }
}
