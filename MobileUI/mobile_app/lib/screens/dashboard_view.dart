// lib/screens/dashboard_view.dart

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../data/pond_data_source.dart';
import '../utils/pond_heuristics.dart';
import '../utils/digital_twin_api.dart';
import '../widgets/dashboard/nea_weather_ribbon.dart';
import '../widgets/dashboard/temperature_outcome_card.dart';
import '../widgets/dashboard/solar_outcome_card.dart';
import '../widgets/dashboard/ph_outcome_card.dart';
import '../widgets/modals/nea_full_forecast_modal.dart';
import 'detail_graph_screen.dart';

class DashboardView extends StatefulWidget {
  const DashboardView({super.key});

  @override
  State<DashboardView> createState() => DashboardViewState();
}

class DashboardViewState extends State<DashboardView> {
  Timer? _pollingTimer;
  bool _isLoading = true;
  bool _isFetching = false;
  Map<String, dynamic> _dashboardData = {};
  // ROUND 2 FIX: was re-parsed on every build() (see the old
  // _parseTelemetryHistory call site there) - a DateTime.parse + several
  // double.tryParse per sample, over the whole telemetry_history list, on
  // every rebuild, even the ones this data had nothing to do with (a parent
  // rebuild, an animation tick, etc.). build() should be cheap; parsing now
  // happens once, right when new data actually arrives.
  List<PondSample> _telemetryHistory = const [];
  // ROUND 3 FIX: the pH card now prefers this (already computed, already
  // cached server-side) assessment over recomputing an equivalent risk
  // model from raw telemetry on every poll - see ph_outcome_card.dart's
  // resolvePhCardData(). Null until the first successful fetch, or if the
  // DigitalTwin service is unreachable / this pond has no assessment yet -
  // PhOutcomeCard falls back to the old client-side heuristic in that case.
  WaterChemistryAssessment? _phAssessment;

  @override
  void initState() {
    super.initState();
    _fetchBundledPayload(isBackgroundPoll: false);
    _pollingTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      _fetchBundledPayload(isBackgroundPoll: true);
    });
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  /// Public method called by MainLayout refresh button
  Future<void> refreshData() async =>
      _fetchBundledPayload(isBackgroundPoll: false);

  Future<void> _fetchBundledPayload({bool isBackgroundPoll = false}) async {
    if (_isFetching) return;
    _isFetching = true;

    if (!isBackgroundPoll && mounted) {
      setState(() => _isLoading = true);
    }

    final source = PondDataScope.of(context);
    try {
      final prefs = await SharedPreferences.getInstance();
      final String userid = prefs.getString('userID') ?? '0';

      // Independent sources (Supabase RPC vs the DigitalTwin Flask service)
      // - fetched concurrently so one's latency doesn't serialize behind
      // the other. A failure in fetchLatestAssessment resolves to null
      // (see its own doc comment) rather than throwing, so it can never
      // take down the bundled-payload fetch that already worked before
      // this backend assessment existed.
      final results = await Future.wait<dynamic>([
        source.fetchDashboardPayload(userid),
        source.fetchLatestAssessment(int.tryParse(userid) ?? 0),
      ]);
      final response = results[0];
      final assessment = results[1] as WaterChemistryAssessment?;

      if (mounted && response != null) {
        final data = Map<String, dynamic>.from(response);
        // Parse once per actual data refresh (every 30s / on manual
        // refresh), not once per build() - see the field comment above.
        final history = _parseTelemetryHistory(data['telemetry_history']);
        setState(() {
          _dashboardData = data;
          _telemetryHistory = history;
          _phAssessment = assessment;
          _isLoading = false;
        });
      }
    } catch (e) {
      debugPrint('Error fetching bundled payload: $e');
      if (mounted) {
        setState(() => _isLoading = false);
      }
    } finally {
      _isFetching = false;
    }
  }

  void _navigateToDetailGraph(String metricType, String title) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) =>
            DetailGraphScreen(metricType: metricType, title: title),
      ),
    );
  }

  List<PondSample> _parseTelemetryHistory(dynamic rawList) {
    if (rawList is! List) return [];

    final List<PondSample> samples = [];
    for (final item in rawList) {
      if (item is! Map) continue;
      try {
        final timeStr = item['time']?.toString();
        if (timeStr == null) continue;

        samples.add(
          PondSample(
            DateTime.parse(timeStr).toLocal(),
            double.tryParse(item['ph']?.toString() ?? '') ?? 7.4,
            double.tryParse(item['tds']?.toString() ?? '') ?? 180.0,
            double.tryParse(item['tempC']?.toString() ?? '') ?? 26.0,
            double.tryParse(item['lux']?.toString() ?? '') ?? 500.0,
          ),
        );
      } catch (_) {
        continue;
      }
    }
    return samples;
  }

  @override
  Widget build(BuildContext context) {
    final List<PondSample> telemetryHistory = _telemetryHistory;
    final Map<String, dynamic> forecastData =
        _dashboardData['nea_forecasts'] ?? {};
    final Map<String, dynamic> telemetryData =
        _dashboardData['nea_telemetry'] ?? {};

    return Scaffold(
      backgroundColor: const Color(0xFF070B12), // Deeper aerospace HUD black
      body: SafeArea(
        child: _isLoading && _dashboardData.isEmpty
            ? const Center(
                child: CircularProgressIndicator(color: Color(0xFF38BDF8)),
              )
            : RefreshIndicator(
                color: const Color(0xFF38BDF8),
                backgroundColor: const Color(0xFF131B2A),
                onRefresh: () => _fetchBundledPayload(isBackgroundPoll: false),
                child: SingleChildScrollView(
                  physics: const AlwaysScrollableScrollPhysics(
                    parent: BouncingScrollPhysics(),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 12,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      NeaWeatherRibbon(
                        forecastData: forecastData,
                        telemetryData: telemetryData,
                        onOpenFullForecast: () =>
                            showNeaFullForecastModal(context, forecastData),
                      ),

                      const SizedBox(
                        height: 16,
                      ), // 1. TEMPERATURE & FEED MONITOR
                      _buildHudSectionHeader(
                        icon: Icons.thermostat_outlined,
                        title: 'Temperature & Feed Monitor',
                        color: const Color.fromARGB(255, 252, 252, 252),
                        onTap: () => _navigateToDetailGraph(
                          'temperature',
                          'Detailed Temperature',
                        ),
                      ),
                      TemperatureOutcomeCard(
                        sensorData: _dashboardData['raw_sensor'] ?? {},
                        telemetryData: _dashboardData['nea_telemetry'] ?? {},
                        forecastData: _dashboardData['nea_forecasts'] ?? {},
                        targetMinTemp: 24,
                        targetMaxTemp: 28,
                        onTap: () => _navigateToDetailGraph(
                          'temperature',
                          'Detailed Temperature',
                        ),
                      ),

                      const SizedBox(height: 16),

                      // 2. pH & BUFFER HEALTH
                      _buildHudSectionHeader(
                        icon: Icons.water_drop_outlined,
                        title: 'pH & Buffer Health',
                        color: const Color.fromARGB(
                          255,
                          254,
                          255,
                          255,
                        ), // Optimal green default
                        onTap: () => _navigateToDetailGraph(
                          'ph',
                          'pH & Buffer Stability',
                        ),
                      ),
                      PhOutcomeCard(
                        sensorData: _dashboardData['raw_sensor'] ?? {},
                        forecastData: _dashboardData['nea_forecasts'] ?? {},
                        telemetryHistory: telemetryHistory,
                        backendAssessment: _phAssessment,
                        onTap: () => _navigateToDetailGraph(
                          'ph',
                          'pH & Buffer Stability',
                        ),
                      ),

                      const SizedBox(height: 16),

                      // 3. ALGAL & SOLAR MONITOR
                      _buildHudSectionHeader(
                        icon: Icons.wb_sunny_outlined,
                        title: 'Algal & Solar Monitor',
                        color: const Color.fromARGB(
                          255,
                          252,
                          252,
                          253,
                        ), // Aquatic cyan default
                        onTap: () => _navigateToDetailGraph(
                          'lux',
                          'Solar & Algae Risk Analysis',
                        ),
                      ),
                      SolarOutcomeCard(
                        sensorData: _dashboardData['raw_sensor'] ?? {},
                        forecastData: _dashboardData['nea_forecasts'] ?? {},
                        onTap: () => _navigateToDetailGraph(
                          'lux',
                          'Solar & Algae Risk Analysis',
                        ),
                      ),
                    ],
                  ),
                ),
              ),
      ),
    );
  }

  /// Sleek HUD Section Header with optional tap action & trailing arrow
  Widget _buildHudSectionHeader({
    required IconData icon,
    required String title,
    required Color color,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 6),
        child: Row(
          children: [
            Icon(icon, color: color, size: 15),
            const SizedBox(width: 8),
            Text(
              title.toUpperCase(),
              style: TextStyle(
                color: color,
                fontSize: 11,
                fontWeight: FontWeight.w800,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Container(
                height: 1,
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: [color.withValues(alpha: 0.35), Colors.transparent],
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            // Rightward pointing affordance arrow
            Icon(
              Icons.arrow_forward_ios,
              color: Colors.white.withValues(alpha: 0.3),
              size: 12,
            ),
          ],
        ),
      ),
    );
  }
  // ROUND 2 FIX: _buildBorderlessInstrumentWrap was dead code - defined but
  // never called anywhere in this file. Removed rather than left to imply a
  // "Cockpit HUD wrap" is actually applied somewhere.
}
