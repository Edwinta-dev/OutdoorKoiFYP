import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
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
import '../widgets/dashboard/today_action_feed.dart';
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
    final List<PondSample> telemetryHistory = [];
    final phAssessment =
        dashboardData['chemistry_assessment'] as WaterChemistryAssessment?;
    final Map<String, dynamic> forecastData =
        dashboardData['nea_forecasts'] ?? {};
    final Map<String, dynamic> telemetryData =
        dashboardData['nea_telemetry'] ?? {};
    // Null when the dashboard did not load: the feed then says so instead
    // of reporting that the pond needs nothing.
    final pondDashboard = ref.watch(pondDashboardProvider).value;
    final todayActions = pondDashboard?.nextActions
        .map(PondAction.fromJson)
        .toList();

    return Scaffold(
      backgroundColor: AppColors.of(
        context,
      ).canvas, // Deeper aerospace HUD black
      body: SafeArea(
        child: state.isLoading && dashboardData.isEmpty
            ? Center(
                child: CircularProgressIndicator(
                  color: AppColors.of(context).info,
                ),
              )
            : RefreshIndicator(
                color: AppColors.of(context).info,
                backgroundColor: AppColors.of(context).surface,
                onRefresh: refreshData,
                child: SingleChildScrollView(
                  physics: const AlwaysScrollableScrollPhysics(
                    parent: BouncingScrollPhysics(),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppSpace.xl,
                    vertical: AppSpace.md,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TodayActionFeed(actions: todayActions),
                      const SizedBox(height: AppSpace.lg),
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

                      const SizedBox(height: AppSpace.lg),

                      // 2. pH & BUFFER HEALTH
                      _buildHudSectionHeader(
                        icon: Icons.water_drop_outlined,
                        title: 'pH & Buffer Health',
                        color: AppColors.of(
                          context,
                        ).text, // Optimal green default
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

                      const SizedBox(height: AppSpace.lg),

                      // 3. ALGAL & SOLAR MONITOR
                      _buildHudSectionHeader(
                        icon: Icons.wb_sunny_outlined,
                        title: 'Algal & Solar Monitor',
                        color: AppColors.of(
                          context,
                        ).text, // Aquatic cyan default
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
  }) => SectionHeader(
    icon: icon,
    title: title.toUpperCase(),
    color: color,
    onTap: onTap,
  );
  // ROUND 2 FIX: _buildBorderlessInstrumentWrap was dead code - defined but
  // never called anywhere in this file. Removed rather than left to imply a
  // "Cockpit HUD wrap" is actually applied somewhere.
}
