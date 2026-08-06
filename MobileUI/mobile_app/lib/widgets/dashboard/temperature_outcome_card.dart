// lib/widgets/dashboard/temperature_outcome_card.dart

import 'package:flutter/material.dart';
import '../../utils/pond_heuristics.dart';

class TemperatureOutcomeCard extends StatelessWidget {
  final Map<String, dynamic> sensorData;
  final Map<String, dynamic> telemetryData;
  final Map<String, dynamic> forecastData;
  final VoidCallback onTap;

  // Baseline target range inputs
  final double targetMinTemp;
  final double targetMaxTemp;

  const TemperatureOutcomeCard({
    super.key,
    required this.sensorData,
    required this.telemetryData,
    required this.forecastData,
    required this.onTap,
    this.targetMinTemp = 25.0, // Hardcoded baseline target min
    this.targetMaxTemp = 28.0, // Hardcoded baseline target max
  });

  /// Dynamically interpolates text color from Green -> Amber -> Red
  /// based on deviation from [targetMinTemp, targetMaxTemp]
  Color _getValueColor(double currentTemp) {
    const Color optimalGreen = Color(0xFF50C878);
    const Color warningAmber = Colors.amberAccent;
    const Color alertRed = Color(0xFFFF4D4D);

    // 1. Within Target Range
    if (currentTemp >= targetMinTemp && currentTemp <= targetMaxTemp) {
      return optimalGreen;
    }

    // 2. Calculate distance away from closest target bound
    final double deviation = currentTemp < targetMinTemp
        ? (targetMinTemp - currentTemp)
        : (currentTemp - targetMaxTemp);

    // Max tolerance offset (e.g. 3.5°C away is max red)
    const double maxTolerance = 3.5;
    final double normalizedFactor = (deviation / maxTolerance).clamp(0.0, 1.0);

    // 3. Lerp Green -> Amber -> Red
    if (normalizedFactor < 0.5) {
      return Color.lerp(optimalGreen, warningAmber, normalizedFactor * 2.0)!;
    } else {
      return Color.lerp(
        warningAmber,
        alertRed,
        (normalizedFactor - 0.5) * 2.0,
      )!;
    }
  }

  /// Evaluates Air Temperature tier to return dynamic styling & icons
  _ThermalConfig _getThermalConfig(double airTemp) {
    if (airTemp >= 33.0) {
      return const _ThermalConfig(
        label: 'Scorching',
        color: Color(0xFFFF4D4D), // Vivid Red
        gradient: [Color(0xFFFF4D4D), Color(0xFFFF8C00)],
        icon: Icons.local_fire_department_outlined,
        percentage: 1.0,
      );
    } else if (airTemp >= 30.0) {
      return const _ThermalConfig(
        label: 'Warm',
        color: Colors.amberAccent,
        gradient: [Colors.amberAccent, Colors.orangeAccent],
        icon: Icons.wb_sunny_outlined,
        percentage: 0.72,
      );
    } else if (airTemp >= 24.0) {
      return const _ThermalConfig(
        label: 'Optimal',
        color: Color(0xFF50C878), // Emerald Green
        gradient: [Color(0xFF50C878), Colors.tealAccent],
        icon: Icons.thermostat_auto,
        percentage: 0.45,
      );
    } else {
      return const _ThermalConfig(
        label: 'Cool',
        color: Colors.cyanAccent,
        gradient: [Colors.cyanAccent, Colors.lightBlueAccent],
        icon: Icons.ac_unit,
        percentage: 0.20,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    // 1. Water Temp (Raw Sensor)
    final rawWaterTemp = sensorData['temp'] ?? 28.2;
    final double waterTempNum =
        double.tryParse(rawWaterTemp.toString()) ?? 28.2;
    final waterTempStr = "${waterTempNum.toStringAsFixed(1)}°C";

    // Dynamic color for primary value text
    final Color valueColor = _getValueColor(waterTempNum);

    // 2. Air Temp & Wind (NEA Station Telemetry)
    final airTempRaw =
        telemetryData['air_temp']?['value'] ??
        telemetryData['air_temp'] ??
        31.5;
    final double airTempNum = double.tryParse(airTempRaw.toString()) ?? 31.5;
    final windSpeed = telemetryData['wind_speed']?['value'] ?? '12';

    // 3. Thermal Configuration according to Air Temp
    final config = _getThermalConfig(airTempNum);

    // 4. Heuristic Advisory
    final advisory = PondHeuristics.getTemperatureAdvisory(
      waterTemp: waterTempNum,
      airTemp: airTempNum,
      windSpeed: windSpeed,
    );

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFF131B2A),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: Colors.white.withOpacity(0.08)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // --- 1. CARD HEADER + AMBIENT STATUS BADGE ---
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Row(
                  children: [
                    Icon(
                      Icons.thermostat_outlined,
                      color: Colors.cyanAccent,
                      size: 18,
                    ),
                    SizedBox(width: 8),
                    Text(
                      'Temperature and Feed Monitor',
                      style: TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
                Row(
                  children: [
                    // Dynamic Thermal Pill
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 3,
                      ),
                      decoration: BoxDecoration(
                        color: config.color.withOpacity(0.12),
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(
                          color: config.color.withOpacity(0.3),
                          width: 1,
                        ),
                      ),
                      child: Row(
                        children: [
                          Icon(config.icon, color: config.color, size: 12),
                          const SizedBox(width: 4),
                          Text(
                            config.label.toUpperCase(),
                            style: TextStyle(
                              color: config.color,
                              fontSize: 9,
                              fontWeight: FontWeight.bold,
                              letterSpacing: 0.5,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 6),
                    Icon(
                      Icons.chevron_right,
                      color: Colors.white.withOpacity(0.3),
                      size: 18,
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 14),

            // --- 2. PRIMARY METRIC DISPLAY (Water Temp with Dynamic Color) ---
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(
                  waterTempStr,
                  style: TextStyle(
                    color: valueColor, // Dynamic Green -> Amber -> Red color
                    fontSize: 32,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Water Temp',
                      style: TextStyle(color: Colors.white54, fontSize: 11),
                    ),
                    Text(
                      'Target: ${targetMinTemp.toInt()}–${targetMaxTemp.toInt()}°C',
                      style: TextStyle(
                        color: Colors.white.withOpacity(0.35),
                        fontSize: 9,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 12),

            // --- 3. AMBIENT HEAT BAR GRAPHIC ---
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Text(
                      'Ambient Thermal Load',
                      style: TextStyle(color: Colors.white38, fontSize: 9),
                    ),
                    Text(
                      '${airTempNum.toStringAsFixed(1)}°C Air',
                      style: TextStyle(
                        color: config.color,
                        fontSize: 10,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 5),
                Stack(
                  children: [
                    // Base Track
                    Container(
                      height: 6,
                      width: double.infinity,
                      decoration: BoxDecoration(
                        color: Colors.white.withOpacity(0.06),
                        borderRadius: BorderRadius.circular(3),
                      ),
                    ),
                    // Active Heat Fill
                    FractionallySizedBox(
                      widthFactor: config.percentage,
                      child: Container(
                        height: 6,
                        decoration: BoxDecoration(
                          gradient: LinearGradient(colors: config.gradient),
                          borderRadius: BorderRadius.circular(3),
                          boxShadow: [
                            BoxShadow(
                              color: config.color.withOpacity(0.4),
                              blurRadius: 6,
                              offset: const Offset(0, 1),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 12),

            // --- 4. SECONDARY DRIVER BAR (Air Temp & Wind) ---
            Row(
              children: [
                Text(
                  'Air Temp: $airTempRaw°C',
                  style: const TextStyle(color: Colors.white70, fontSize: 11),
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  child: Text(
                    '•',
                    style: TextStyle(color: Colors.white.withOpacity(0.2)),
                  ),
                ),
                Text(
                  'Wind: $windSpeed km/h',
                  style: const TextStyle(color: Colors.white70, fontSize: 11),
                ),
              ],
            ),

            // --- 5. ADVISORY BANNER (If Triggered) ---
            if (advisory != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: config.color.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: config.color.withOpacity(0.3)),
                ),
                child: Row(
                  children: [
                    Icon(
                      Icons.warning_amber_rounded,
                      color: config.color,
                      size: 16,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        advisory,
                        style: TextStyle(color: config.color, fontSize: 10),
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

class _ThermalConfig {
  final String label;
  final Color color;
  final List<Color> gradient;
  final IconData icon;
  final double percentage;

  const _ThermalConfig({
    required this.label,
    required this.color,
    required this.gradient,
    required this.icon,
    required this.percentage,
  });
}
