import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../data/pond_data_source.dart';
import '../widgets/detail_graph/algae_severity_rating_card.dart';
import '../widgets/detail_graph/algae_status_card.dart';
import '../widgets/detail_graph/evaporation_status_card.dart';
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

  /// Bumped when a severity rating is submitted or undone. A rating moves
  /// the engine's modelled level, so the algae forecast card underneath is
  /// immediately stale - but the historical chart is not, so this is kept
  /// separate from _chemistryRefreshTick to avoid re-fetching the whole
  /// graph payload for something only one card cares about.
  int _algaeRefreshTick = 0;

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
    final source = PondDataScope.of(context);

    try {
      final prefs = await SharedPreferences.getInstance();
      final userId = int.tryParse(prefs.getString('userID') ?? '0') ?? 0;
      _userId = userId;

      final response = await source.fetchHistoricalGraphPayload(
        userId,
        _selectedDays,
      );

      if (mounted && response != null) {
        final Map<String, dynamic> data = response;
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

    final String lowerMetric = widget.metricType.toLowerCase();

    final bool isWaterQualityDomain =
        lowerMetric.contains('ph') || lowerMetric.contains('water_quality');

    // Algal & Solar screen - dashboard_view navigates here with 'lux'.
    final bool isAlgaeDomain =
        lowerMetric.contains('lux') || lowerMetric.contains('algae');

    // Temperature & Feed screen. Checked LAST and only when the other two
    // did not match, mirroring _getTargetSensorTypes()'s else-branch:
    // 'temperature' and 'evaporation' both land on this screen.
    final bool isTemperatureDomain = !isWaterQualityDomain && !isAlgaeDomain;

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

                  // --- 3c. EVAPORATION & FEED LOOKAHEAD (temp domain) ---
                  // "When do I next top up?" + temperature-driven feed cap.
                  // Server-computed by the DigitalTwin Flask engine
                  // (GET /forecast/evaporation/<user_id>).
                  if (isTemperatureDomain) ...[
                    EvaporationStatusCard(
                      key: ValueKey('evaporation-$_chemistryRefreshTick'),
                      userId: _userId,
                    ),
                    const SizedBox(height: 20),
                  ],

                  // --- 3d. ALGAE DOMAIN ---
                  // Ordered deliberately: forecast first (the outcome the
                  // user came for), then the camera frame + rating control
                  // that grounds it. Putting the rating control last means
                  // the user has already seen what the model currently
                  // believes before being asked to correct it, which makes
                  // the correction meaningful rather than a blind survey.
                  if (isAlgaeDomain) ...[
                    AlgaeStatusCard(
                      key: ValueKey(
                        'algae-$_chemistryRefreshTick-$_algaeRefreshTick',
                      ),
                      userId: _userId,
                    ),
                    const SizedBox(height: 20),
                    AlgaeSeverityRatingCard(
                      key: ValueKey('algae-rating-$_algaeRefreshTick'),
                      userId: _userId,
                      // A rating changes the engine's modelled level, so
                      // the forecast card above must be rebuilt against
                      // the new state rather than left showing the
                      // pre-rating projection.
                      onRatingChanged: () {
                        if (mounted) {
                          setState(() => _algaeRefreshTick++);
                        }
                      },
                    ),
                    const SizedBox(height: 20),
                  ],
                ],
              ),
            ),
    );
  }
}
