// lib/screens/dashboard_view.dart

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../utils/pond_heuristics.dart';
import '../widgets/dashboard/temperature_outcome_card.dart';
import '../widgets/dashboard/solar_outcome_card.dart';
import '../widgets/dashboard/ph_outcome_card.dart';
import '../widgets/dashboard/four_day_outlook_card.dart';
import 'detail_graph_screen.dart';

class DashboardView extends StatefulWidget {
  const DashboardView({super.key});

  @override
  State<DashboardView> createState() => _DashboardViewState();
}

class _DashboardViewState extends State<DashboardView> {
  Timer? _pollingTimer;
  bool _isLoading = true;
  bool _isFetching = false;
  Map<String, dynamic> _dashboardData = {};

  @override
  void initState() {
    super.initState();
    // 1. Initial immediate RPC Fetch
    _fetchBundledPayload(isBackgroundPoll: false);

    // 2. Schedule periodic polling loop (every 30 seconds)
    _pollingTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      _fetchBundledPayload(isBackgroundPoll: true);
    });
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  /// RPC HTTP call to Supabase to fetch bundled dashboard payload
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
        setState(() {
          _isLoading = false;
        });
      }
    } finally {
      _isFetching = false;
    }
  }

  /// Navigates to the Dynamic Detailed Graph Page for a specific metric
  void _navigateToDetailGraph(String metricType, String title) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) =>
            DetailGraphScreen(metricType: metricType, title: title),
      ),
    );
  }

  /// Safely parses raw RPC JSON list into strongly-typed PondSample objects
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
        // Skip malformed timestamp rows without crashing UI
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
    return Scaffold(
      backgroundColor: const Color(0xFF0A0E17), // Deep Dark Aquatic Theme
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Row(
          children: [
            Icon(Icons.water_drop_outlined, color: Colors.cyanAccent),
            SizedBox(width: 8),
            Text(
              'Pond Dashboard Center',
              style: TextStyle(
                fontWeight: FontWeight.bold,
                fontSize: 18,
                color: Colors.white,
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: _isLoading
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.cyanAccent,
                    ),
                  )
                : const Icon(Icons.refresh, color: Colors.white70),
            onPressed: () => _fetchBundledPayload(isBackgroundPoll: false),
          ),
        ],
      ),
      body: _isLoading && _dashboardData.isEmpty
          ? const Center(
              child: CircularProgressIndicator(color: Colors.cyanAccent),
            )
          : RefreshIndicator(
              color: Colors.cyanAccent,
              backgroundColor: const Color(0xFF131B2A),
              onRefresh: () => _fetchBundledPayload(isBackgroundPoll: false),
              child: SingleChildScrollView(
                physics: const AlwaysScrollableScrollPhysics(
                  parent: BouncingScrollPhysics(),
                ),
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // 1. TEMPERATURE & METABOLIC CARD
                    TemperatureOutcomeCard(
                      sensorData: _dashboardData['raw_sensor'] ?? {},
                      telemetryData: _dashboardData['nea_telemetry'] ?? {},
                      forecastData: _dashboardData['nea_forecasts'] ?? {},
                      onTap: () => _navigateToDetailGraph(
                        'temperature',
                        'Water Temperature Analytics',
                      ),
                      targetMinTemp: 15,
                      targetMaxTemp: 33,
                    ),
                    const SizedBox(height: 16),

                    // 2. pH STABILITY & ACID CRASH CARD
                    PhOutcomeCard(
                      sensorData: _dashboardData['raw_sensor'] ?? {},
                      forecastData: _dashboardData['nea_forecasts'] ?? {},
                      telemetryHistory: telemetryHistory,
                      onTap: () =>
                          _navigateToDetailGraph('ph', 'pH & Buffer Stability'),
                    ),
                    const SizedBox(height: 16),

                    // 3. SOLAR RADIATION & ALGAE BLOOM CARD
                    SolarOutcomeCard(
                      sensorData: _dashboardData['raw_sensor'] ?? {},
                      forecastData: _dashboardData['nea_forecasts'] ?? {},
                      onTap: () => _navigateToDetailGraph(
                        'lux',
                        'Solar & Algae Risk Analysis',
                      ),
                    ),
                    const SizedBox(height: 16),

                    // 4. 4-DAY EXTENDED FORECAST OUTLOOK CARD
                    FourDayOutlookCard(
                      forecastData: _dashboardData['nea_forecasts'] ?? {},
                    ),
                    const SizedBox(height: 24),
                  ],
                ),
              ),
            ),
    );
  }
}
