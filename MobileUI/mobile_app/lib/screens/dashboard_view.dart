// lib/screens/dashboard_view.dart

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../utils/pond_heuristics.dart';
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

    try {
      final prefs = await SharedPreferences.getInstance();
      final String userid = prefs.getString('userID') ?? '0';

      final response = await Supabase.instance.client.rpc(
        'get_bundled_dashboard_payload',
        params: {'p_user_id': userid},
      );

      if (mounted && response != null) {
        setState(() {
          _dashboardData = Map<String, dynamic>.from(response as Map);
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
    final List<PondSample> telemetryHistory = _parseTelemetryHistory(
      _dashboardData['telemetry_history'],
    );
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
                          'Water Temperature Analytics',
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
                          'Water Temperature Analytics',
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

  /// Cockpit HUD wrapper: removes hard box edges and gives instruments a subtle radial canvas glow
  Widget _buildBorderlessInstrumentWrap({required Widget child}) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.02),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: Colors.white.withValues(alpha: 0.06),
          width: 1,
        ),
      ),
      child: child,
    );
  }
}
