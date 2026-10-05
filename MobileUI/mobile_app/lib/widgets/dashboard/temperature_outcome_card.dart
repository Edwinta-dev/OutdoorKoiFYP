import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/dashboard/temperature_outcome_card.dart

import 'package:flutter/material.dart';
import '../../utils/pond_heuristics.dart';

class TemperatureOutcomeCard extends StatelessWidget {
  final Map<String, dynamic> sensorData;
  final Map<String, dynamic> telemetryData;
  final Map<String, dynamic> forecastData;
  final VoidCallback onTap;

  final double targetMinTemp;
  final double targetMaxTemp;

  const TemperatureOutcomeCard({
    super.key,
    required this.sensorData,
    required this.telemetryData,
    required this.forecastData,
    required this.onTap,
    required this.targetMinTemp,
    required this.targetMaxTemp,
  });

  double _getNormalizedPosition(double currentTemp) {
    final double scaleMin = targetMinTemp - 2.0;
    final double scaleMax = targetMaxTemp + 2.0;
    final double position = (currentTemp - scaleMin) / (scaleMax - scaleMin);
    return position.clamp(0.0, 1.0);
  }

  /// Computes scale color based on current water temp or forecast pressure
  Color _computeThemeColor(
    BuildContext context,
    double currentTemp,
    TemperatureAdvisoryResult? advisory,
  ) {
    final Color warningAmber = AppColors.of(context).warning;
    final Color alertRed = AppColors.of(context).danger;

    // If heuristic detected a Red ground-truth alert, force Red
    if (advisory?.severity == AdvisorySeverity.red) {
      return alertRed;
    }

    // If heuristic detected an Amber warning (ground truth near bounds OR forecast high air temp), force Amber
    if (advisory?.severity == AdvisorySeverity.amber) {
      return warningAmber;
    }

    // Default green when well within optimal bounds
    return AppColors.of(context).healthy;
  }

  @override
  Widget build(BuildContext context) {
    // 1. Water Temp Parsing (Ground Truth IoT Sensor)
    final rawWaterTemp = sensorData['temp'] ?? 28.2;
    final double waterTempNum =
        double.tryParse(rawWaterTemp.toString()) ?? 28.2;
    final String waterTempStr = "${waterTempNum.toStringAsFixed(1)}°C";

    // 2. Air Temp Parsing (NEA Telemetry / Forecast)
    final airTempRaw =
        telemetryData['air_temp']?['value'] ??
        telemetryData['air_temp'] ??
        31.5;
    final double airTempNum = double.tryParse(airTempRaw.toString()) ?? 31.5;
    final windSpeed = telemetryData['wind_speed']?['value'] ?? '12';

    // 3. Evaluate Advisory with Ground Truth vs Forecast Hierarchy
    final TemperatureAdvisoryResult? advisoryResult =
        PondHeuristics.getTemperatureAdvisory(
          waterTemp: waterTempNum,
          airTemp: airTempNum,
          windSpeed: windSpeed,
          targetMinTemp: targetMinTemp,
          targetMaxTemp: targetMaxTemp,
        );

    // 4. Synchronized Scale & Advisory Color
    final Color synchronizedColor = _computeThemeColor(
      context,
      waterTempNum,
      advisoryResult,
    );
    final double pointerPosition = _getNormalizedPosition(waterTempNum);

    return OutcomeCardShell(
      onTap: onTap,
      transparent: true,
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpace.lg,
        vertical: AppSpace.lg,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          StatusChip(
            status: advisoryResult?.severity == AdvisorySeverity.red
                ? PondStatus.danger
                : advisoryResult?.severity == AdvisorySeverity.amber
                ? PondStatus.warning
                : PondStatus.healthy,
          ),
          const SizedBox(height: AppSpace.md),

          // --- MAIN SECTION: BOLD TEMP (LEFT) | SYNCHRONIZED SCALE (RIGHT) ---
          AdaptiveRow(
            minWidth: 300,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // LEFT: Bold Water Temp
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    waterTempStr,
                    style: AppType.style(
                      color: synchronizedColor,
                      fontSize: AppType.display,
                      fontWeight: FontWeight.w900,
                      letterSpacing: -0.5,
                    ),
                  ),
                  Text(
                    'Water Temp',
                    style: AppType.style(
                      color: AppColors.of(context).textMuted,
                      fontSize: AppType.caption,
                    ),
                  ),
                ],
              ),
              const SizedBox(width: AppSpace.xxl),

              // RIGHT: Target Range Dial
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Target Labels
                    AdaptiveRow(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          '${targetMinTemp.toStringAsFixed(1)}°',
                          style: AppType.style(
                            color: AppColors.of(context).textMuted,
                            fontSize: AppType.micro,
                          ),
                        ),
                        Text(
                          'Target Range',
                          style: AppType.style(
                            color: AppColors.of(context).textSecondary,
                            fontSize: AppType.micro,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        Text(
                          '${targetMaxTemp.toStringAsFixed(1)}°',
                          style: AppType.style(
                            color: AppColors.of(context).textMuted,
                            fontSize: AppType.micro,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: AppSpace.sm),

                    // Synchronized Track and Pointer
                    LayoutBuilder(
                      builder: (context, constraints) {
                        final double trackWidth = constraints.maxWidth;
                        final double pointerX = trackWidth * pointerPosition;

                        return Stack(
                          clipBehavior: Clip.none,
                          alignment: Alignment.centerLeft,
                          children: [
                            // Track Background
                            Container(
                              height: 6,
                              width: trackWidth,
                              decoration: BoxDecoration(
                                color: synchronizedColor.withValues(alpha: 0.2),
                                borderRadius: BorderRadius.circular(
                                  AppRadius.small,
                                ),
                              ),
                            ),

                            // Track Active Fill
                            Container(
                              height: 6,
                              width: pointerX.clamp(0.0, trackWidth),
                              decoration: BoxDecoration(
                                color: synchronizedColor,
                                borderRadius: BorderRadius.circular(
                                  AppRadius.small,
                                ),
                              ),
                            ),

                            // Track Pointer
                            Positioned(
                              left: (pointerX - 6).clamp(0.0, trackWidth - 12),
                              top: -3,
                              child: Container(
                                width: 12,
                                height: 12,
                                decoration: BoxDecoration(
                                  color: synchronizedColor,
                                  shape: BoxShape.circle,
                                  border: Border.all(
                                    color: AppColors.of(context).text,
                                    width: 2,
                                  ),
                                  boxShadow: [
                                    BoxShadow(
                                      color: synchronizedColor.withValues(
                                        alpha: 0.6,
                                      ),
                                      blurRadius: 6,
                                      spreadRadius: 1,
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        );
                      },
                    ),
                    const SizedBox(height: AppSpace.sm),

                    Center(
                      child: Text(
                        'Optimal: ${((targetMinTemp + targetMaxTemp) / 2).toStringAsFixed(1)}°C',
                        style: AppType.style(
                          color: AppColors.of(
                            context,
                          ).text.withValues(alpha: 0.35),
                          fontSize: AppType.micro,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),

          // --- ADVISORY BANNER (SYNCHRONIZED COLOR & HIERARCHY-AWARE TEXT) ---
          if (advisoryResult != null) ...[
            const SizedBox(height: AppSpace.lg),
            Container(
              padding: const EdgeInsets.all(AppSpace.md),
              decoration: BoxDecoration(
                color: synchronizedColor.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(AppRadius.control),
                border: Border.all(
                  color: synchronizedColor.withValues(alpha: 0.35),
                ),
              ),
              child: AdaptiveRow(
                children: [
                  Icon(
                    advisoryResult.isForecastDriven
                        ? Icons.wb_sunny_outlined
                        : Icons.warning_amber_rounded,
                    color: synchronizedColor,
                    size: 16,
                  ),
                  const SizedBox(width: AppSpace.sm),
                  Expanded(
                    child: Text(
                      advisoryResult.message,
                      style: AppType.style(
                        color: synchronizedColor,
                        fontSize: AppType.micro,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}
