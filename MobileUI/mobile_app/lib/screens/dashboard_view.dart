// lib/screens/dashboard_view.dart

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/providers.dart';
import '../utils/pond_heuristics.dart';
import '../utils/digital_twin_api.dart';
import '../widgets/dashboard/nea_weather_ribbon.dart';
import '../widgets/dashboard/temperature_outcome_card.dart';
import '../widgets/dashboard/solar_outcome_card.dart';
import '../widgets/dashboard/ph_outcome_card.dart';
import '../widgets/modals/nea_full_forecast_modal.dart';
import 'detail_graph_screen.dart';

class DashboardView extends ConsumerStatefulWidget {
  const DashboardView({super.key});

  @override
  ConsumerState<DashboardView> createState() => DashboardViewState();
}

class DashboardViewState extends ConsumerState<DashboardView> {
  Future<void> refreshData() async {
    ref.invalidate(pondDashboardProvider);
    await ref.read(pondDashboardProvider.future);
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

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(dashboardProvider);
    final dashboardData = state.asData?.value ?? <String, dynamic>{};
    final List<PondSample> telemetryHistory = const [];
    final phAssessment =
        dashboardData['chemistry_assessment'] as WaterChemistryAssessment?;
    final Map<String, dynamic> forecastData =
        dashboardData['nea_forecasts'] ?? {};
    final Map<String, dynamic> telemetryData =
        dashboardData['nea_telemetry'] ?? {};

    return Scaffold(
      backgroundColor: const Color(0xFF070B12), // Deeper aerospace HUD black
      body: SafeArea(
        child: state.isLoading && dashboardData.isEmpty
            ? const Center(
                child: CircularProgressIndicator(color: Color(0xFF38BDF8)),
              )
            : RefreshIndicator(
                color: const Color(0xFF38BDF8),
                backgroundColor: const Color(0xFF131B2A),
                onRefresh: refreshData,
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
                        sensorData: dashboardData['raw_sensor'] ?? {},
                        telemetryData: dashboardData['nea_telemetry'] ?? {},
                        forecastData: dashboardData['nea_forecasts'] ?? {},
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
                        sensorData: dashboardData['raw_sensor'] ?? {},
                        forecastData: dashboardData['nea_forecasts'] ?? {},
                        telemetryHistory: telemetryHistory,
                        backendAssessment: phAssessment,
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
                        sensorData: dashboardData['raw_sensor'] ?? {},
                        forecastData: dashboardData['nea_forecasts'] ?? {},
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
