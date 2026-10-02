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
    double currentTemp,
    TemperatureAdvisoryResult? advisory,
  ) {
    const Color warningAmber = Colors.amberAccent;
    const Color alertRed = Color(0xFFFF4D4D);

    // If heuristic detected a Red ground-truth alert, force Red
    if (advisory?.severity == AdvisorySeverity.red) {
      return alertRed;
    }

    // If heuristic detected an Amber warning (ground truth near bounds OR forecast high air temp), force Amber
    if (advisory?.severity == AdvisorySeverity.amber) {
      return warningAmber;
    }

    // Default green when well within optimal bounds
    return const Color(0xFF50C878);
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
      waterTempNum,
      advisoryResult,
    );
    final double pointerPosition = _getNormalizedPosition(waterTempNum);

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(16),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        // 1. REMOVE color: const Color(0xFF131B2A)
        // 2. REMOVE border: Border.all(...)
        // 3. REMOVE boxShadow: [...]
        decoration: const BoxDecoration(
          color:
              Colors.transparent, // Lets the HUD parent container show through!
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 10),

            // --- MAIN SECTION: BOLD TEMP (LEFT) | SYNCHRONIZED SCALE (RIGHT) ---
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                // LEFT: Bold Water Temp
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      waterTempStr,
                      style: TextStyle(
                        color: synchronizedColor,
                        fontSize: 36,
                        fontWeight: FontWeight.w900,
                        letterSpacing: -0.5,
                      ),
                    ),
                    const Text(
                      'Water Temp',
                      style: TextStyle(color: Colors.white54, fontSize: 11),
                    ),
                  ],
                ),
                const SizedBox(width: 24),

                // RIGHT: Target Range Dial
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Target Labels
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            '${targetMinTemp.toStringAsFixed(1)}°',
                            style: const TextStyle(
                              color: Colors.white38,
                              fontSize: 10,
                            ),
                          ),
                          const Text(
                            'Target Range',
                            style: TextStyle(
                              color: Colors.white70,
                              fontSize: 10,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          Text(
                            '${targetMaxTemp.toStringAsFixed(1)}°',
                            style: const TextStyle(
                              color: Colors.white38,
                              fontSize: 10,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),

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
                                  borderRadius: BorderRadius.circular(3),
                                ),
                              ),

                              // Track Active Fill
                              Container(
                                height: 6,
                                width: pointerX.clamp(0.0, trackWidth),
                                decoration: BoxDecoration(
                                  color: synchronizedColor,
                                  borderRadius: BorderRadius.circular(3),
                                ),
                              ),

                              // Track Pointer
                              Positioned(
                                left: (pointerX - 6).clamp(
                                  0.0,
                                  trackWidth - 12,
                                ),
                                top: -3,
                                child: Container(
                                  width: 12,
                                  height: 12,
                                  decoration: BoxDecoration(
                                    color: synchronizedColor,
                                    shape: BoxShape.circle,
                                    border: Border.all(
                                      color: Colors.white,
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
                      const SizedBox(height: 8),

                      Center(
                        child: Text(
                          'Optimal: ${((targetMinTemp + targetMaxTemp) / 2).toStringAsFixed(1)}°C',
                          style: TextStyle(
                            color: Colors.white.withValues(alpha: 0.35),
                            fontSize: 9,
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
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: synchronizedColor.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(
                    color: synchronizedColor.withValues(alpha: 0.35),
                  ),
                ),
                child: Row(
                  children: [
                    Icon(
                      advisoryResult.isForecastDriven
                          ? Icons.wb_sunny_outlined
                          : Icons.warning_amber_rounded,
                      color: synchronizedColor,
                      size: 16,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        advisoryResult.message,
                        style: TextStyle(
                          color: synchronizedColor,
                          fontSize: 10,
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
      ),
    );
  }
}
