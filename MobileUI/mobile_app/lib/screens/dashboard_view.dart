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
import '../utils/pond_dashboard.dart';
import 'detail_graph_screen.dart';

/// Koi comfort range used by the temperature card and the simple view.
const double comfortMinTempC = 24;
const double comfortMaxTempC = 28;

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
    final detailed =
        ref.watch(displayModeProvider).value == DisplayMode.detailed;
    final safeRange = ref.watch(unifiedSafeRangeProvider);
    final tempRange = safeRange.temperatureC;
    final phRange = safeRange.ph;
    final targetMinTemp = tempRange != null && !tempRange.isEmpty
        ? tempRange.minimum
        : comfortMinTempC;
    final targetMaxTemp = tempRange != null && !tempRange.isEmpty
        ? tempRange.maximum
        : comfortMaxTempC;

    return Scaffold(
      backgroundColor: AppColors.of(
        context,
      ).canvas, // Deeper aerospace HUD black
      body: SafeArea(
        child: state.isLoading && dashboardData.isEmpty
            ? Center(
                child: CircularProgressIndicator(
                  color: AppColors.of(context).accent,
                ),
              )
            : RefreshIndicator(
                color: AppColors.of(context).accent,
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
                      if ((tempRange?.isEmpty ?? false) ||
                          (phRange?.isEmpty ?? false))
                        const Padding(
                          padding: EdgeInsets.all(12),
                          child: Text(
                            'Stocked species have no shared safe range. Review their care requirements.',
                          ),
                        ),
                      if (!detailed)
                        SimplePondStatus(
                          dashboard: pondDashboard,
                          minTempC: targetMinTemp,
                          maxTempC: targetMaxTemp,
                        )
                      else ...[
                        NeaWeatherRibbon(
                          forecastData: forecastData,
                          telemetryData: telemetryData,
                          onOpenFullForecast: () {
                            if (pondDashboard != null) {
                              showNeaFullForecastModal(
                                context,
                                pondDashboard.forecast,
                              );
                            }
                          },
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
                          targetMinTemp: targetMinTemp,
                          targetMaxTemp: targetMaxTemp,
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
                          targetMinPh: phRange != null && !phRange.isEmpty
                              ? phRange.minimum
                              : 6.8,
                          targetMaxPh: phRange != null && !phRange.isEmpty
                              ? phRange.maximum
                              : 8.2,
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

/// The simple view's status list: one plain line per pond concern, with
/// measured values and model estimates labelled as such.
class SimplePondStatus extends StatelessWidget {
  /// Null when the dashboard could not be loaded.
  final PondDashboard? dashboard;
  final double minTempC;
  final double maxTempC;

  const SimplePondStatus({
    super.key,
    required this.dashboard,
    this.minTempC = comfortMinTempC,
    this.maxTempC = comfortMaxTempC,
  });

  static String _reading(SensorReading r, String name) => switch (r.status) {
    'stale' => 'The last $name reading is old; check the sensor is online.',
    'invalid' =>
      'The $name sensor sent a value outside its range; '
          'check the probe.',
    _ => 'No $name reading has arrived from the pond sensor.',
  };

  static String temperatureLine(
    SensorReading r, {
    double min = comfortMinTempC,
    double max = comfortMaxTempC,
  }) {
    final v = r.value;
    if (r.status != 'ok' || v == null) return _reading(r, 'water temperature');
    final measured = 'Water is ${v.toStringAsFixed(1)} °C (measured)';
    final range = '${min ~/ 1}-${max ~/ 1} °C koi comfort range';
    if (v < min) {
      return '$measured, below the $range. Koi eat less in cool water.';
    }
    if (v > max) {
      return '$measured, above the $range. Warm water holds less oxygen.';
    }
    return '$measured, inside the $range.';
  }

  static String phLine(SensorReading r, WaterChemistryAssessment? chemistry) {
    final v = r.value;
    final measured = r.status == 'ok' && v != null
        ? 'pH is ${v.toStringAsFixed(1)} (measured).'
        : _reading(r, 'pH');
    if (chemistry == null) {
      return '$measured No water chemistry estimate yet.';
    }
    final advisory = chemistry.advisory.isEmpty ? '' : ' ${chemistry.advisory}';
    return '$measured Water chemistry: ${chemistry.category} '
        '(model estimate).$advisory';
  }

  static String oxygenLine(HypoxiaFlag h) {
    if (h.level == 'unknown' && h.explanation.isEmpty) {
      return 'Low-oxygen risk could not be estimated yet.';
    }
    final level = switch (h.level) {
      'none' => 'low',
      'watch' => 'raised',
      'high' => 'high',
      _ => 'unknown',
    };
    final explanation = h.explanation.isEmpty ? '' : ' ${h.explanation}';
    return 'Low-oxygen risk is $level (model estimate).$explanation';
  }

  @override
  Widget build(BuildContext context) {
    final colors = AppColors.of(context);
    final d = dashboard;
    final lines = d == null
        ? const [
            (
              Icons.cloud_off_outlined,
              'Pond status could not be loaded. Pull down to try again.',
            ),
          ]
        : [
            (
              Icons.thermostat_outlined,
              temperatureLine(
                d.readings.waterTemp,
                min: minTempC,
                max: maxTempC,
              ),
            ),
            (
              Icons.water_drop_outlined,
              phLine(d.readings.ph, d.assessments.chemistry),
            ),
            (Icons.air, oxygenLine(d.assessments.hypoxia)),
          ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionHeader(
          icon: Icons.checklist_outlined,
          title: 'POND STATUS',
        ),
        for (final (icon, text) in lines)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AppSpace.sm),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(icon, size: 20, color: colors.textSecondary),
                const SizedBox(width: AppSpace.md),
                Expanded(
                  child: Text(
                    text,
                    style: AppType.style(
                      fontSize: AppType.body,
                      color: colors.text,
                    ),
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: AppSpace.md),
        Text(
          'Model values, confidence, evidence and charts are in the detailed '
          'view: turn on Detailed view in Settings.',
          style: AppType.style(
            fontSize: AppType.label,
            color: colors.textSecondary,
          ),
        ),
      ],
    );
  }
}
