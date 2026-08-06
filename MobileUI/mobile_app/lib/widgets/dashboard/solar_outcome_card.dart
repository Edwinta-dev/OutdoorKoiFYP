// lib/widgets/dashboard/solar_outcome_card.dart

import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../utils/pond_heuristics.dart';

const Color kLuxColor = Color(0xFFFF8A65); // Soft Coral
const Color kUvColor = Colors.amberAccent;

class SolarOutcomeCard extends StatelessWidget {
  final Map<String, dynamic> sensorData;
  final Map<String, dynamic> forecastData;
  final VoidCallback onTap;

  const SolarOutcomeCard({
    super.key,
    required this.sensorData,
    required this.forecastData,
    required this.onTap,
  });

  /// Maps the exact 2-hour NEA status string to its corresponding asset path
  String _getWeatherAssetPath(String status) {
    // Standard map for precise asset matching
    const Map<String, String> statusAssetMap = {
      'Fair': 'lib/assets/fair.png',
      'Fair (Day)': 'lib/assets/fair_day.png',
      'Fair (Night)': 'lib/assets/fair_night.png',
      'Fair and Warm': 'lib/assets/fair_and_warm.png',
      'Partly Cloudy': 'lib/assets/partly_cloudy.png',
      'Partly Cloudy (Day)': 'lib/assets/partly_cloudy_day.png',
      'Partly Cloudy (Night)': 'lib/assets/partly_cloudy_night.png',
      'Cloudy': 'lib/assets/cloudy.png',
      'Hazy': 'lib/assets/hazy.png',
      'Slightly Hazy': 'lib/assets/slightly_hazy.png',
      'Windy': 'lib/assets/windy.png',
      'Mist': 'lib/assets/mist.png',
      'Fog': 'lib/assets/fog.png',
      'Light Rain': 'lib/assets/light_rain.png',
      'Moderate Rain': 'lib/assets/moderate_rain.png',
      'Heavy Rain': 'lib/assets/heavy_rain.png',
      'Passing Showers': 'lib/assets/passing_showers.png',
      'Light Showers': 'lib/assets/light_showers.png',
      'Showers': 'lib/assets/showers.png',
      'Heavy Showers': 'lib/assets/heavy_showers.png',
      'Thundery Showers': 'lib/assets/thundery_showers.png',
      'Heavy Thundery Showers': 'lib/assets/heavy_thundery_showers.png',
      'Heavy Thundery Showers with Gusty Winds':
          'lib/assets/heavy_thundery_showers_with_gusty_winds.png',
    };

    if (statusAssetMap.containsKey(status)) {
      return statusAssetMap[status]!;
    }

    // Dynamic fallback transformation for unmapped edge-case strings
    final sanitized = status
        .toLowerCase()
        .replaceAll('(', '')
        .replaceAll(')', '')
        .trim()
        .replaceAll(RegExp(r'\s+'), '_');

    return 'lib/assets/$sanitized.png';
  }

  /// Combines LUX (0–1000 lx) and UV Index (0–12) into a 0.0–1.0 dial sweep ratio
  double _calculateSolarExposureProgress(num lux, num uv) {
    final double normalizedLux = (lux / 1000.0).clamp(0.0, 1.0);
    final double normalizedUv = (uv / 12.0).clamp(0.0, 1.0);

    // Weighted index: 60% LUX intensity + 40% UV Index radiation
    final double combinedIndex = (normalizedLux * 0.60) + (normalizedUv * 0.40);
    return combinedIndex.clamp(0.08, 1.0);
  }

  @override
  Widget build(BuildContext context) {
    // 1. Raw Lux Reading
    final rawLux = sensorData['LUX'] ?? 680;
    final num luxNum = num.tryParse(rawLux.toString()) ?? 680;

    // 2. UV Index (Safely parsed)
    final uvObj = forecastData['uv_index'] ?? forecastData['uv'];
    final dynamic rawUv = (uvObj is Map)
        ? (uvObj['data'] is Map ? uvObj['data']['uv'] : uvObj['uv'])
        : 0;
    final num uvNum = num.tryParse(rawUv.toString()) ?? 0;

    // 3. 2-Hour Forecast Status String
    final String forecast2hr =
        forecastData['forecast_2hr']?['forecast']?.toString() ??
        'Partly Cloudy';

    // 4. Resolve exact asset PNG path
    final String assetPath = _getWeatherAssetPath(forecast2hr);

    // 5. Combined Solar Arc Progress Ratio
    final double exposureProgress = _calculateSolarExposureProgress(
      luxNum,
      uvNum,
    );

    // 6. Heuristic Advisory
    final advisory = PondHeuristics.getSolarAdvisory(
      lux: luxNum,
      uvIndex: uvNum,
      forecast2hr: forecast2hr,
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
            // --- 1. CARD HEADER ---
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Row(
                  children: [
                    Icon(Icons.wb_sunny_outlined, color: kLuxColor, size: 18),
                    SizedBox(width: 8),
                    Text(
                      'Algal Monitor',
                      style: TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
                Icon(
                  Icons.chevron_right,
                  color: Colors.white.withOpacity(0.3),
                  size: 18,
                ),
              ],
            ),
            const SizedBox(height: 16),

            // --- 2. MAIN DIAL + DATA PANEL (LEFT/RIGHT SPLIT) ---
            Row(
              children: [
                // LEFT SIDE: Open Arc Circular Dial with Central PNG Asset
                SizedBox(
                  width: 96,
                  height: 96,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      // Circular Arc Progress Painter
                      CustomPaint(
                        size: const Size(96, 96),
                        painter: MutedArcPainter(
                          progress: exposureProgress,
                          strokeColor: kLuxColor,
                        ),
                      ),

                      // Center Weather Graphic Image with Fallback Vector Icon
                      Image.asset(
                        assetPath,
                        width: 52,
                        height: 52,
                        fit: BoxFit.contain,
                        errorBuilder: (_, __, ___) => Icon(
                          _getFallbackVectorIcon(forecast2hr),
                          color: Colors.white70,
                          size: 38,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 18),

                // RIGHT SIDE: Numerical Metric Displays
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Ambient Lux Reading
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.baseline,
                        textBaseline: TextBaseline.alphabetic,
                        children: [
                          Text(
                            '$luxNum',
                            style: const TextStyle(
                              color: kLuxColor,
                              fontSize: 26,
                              fontWeight: FontWeight.bold,
                              letterSpacing: -0.5,
                            ),
                          ),
                          const SizedBox(width: 4),
                          const Text(
                            'lx Ambient',
                            style: TextStyle(
                              color: Colors.white54,
                              fontSize: 11,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 6),

                      // UV Index Row
                      Row(
                        children: [
                          const Icon(Icons.wb_sunny, color: kUvColor, size: 14),
                          const SizedBox(width: 6),
                          Text(
                            'UV Index: $uvNum',
                            style: const TextStyle(
                              color: kUvColor,
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),

                      // 2-Hour Forecast Status Text
                      Text(
                        'Sky: $forecast2hr',
                        style: const TextStyle(
                          color: Colors.white70,
                          fontSize: 11,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
              ],
            ),

            // --- 3. ADVISORY BANNER (If Triggered) ---
            if (advisory != null) ...[
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.amber.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.amber.withOpacity(0.3)),
                ),
                child: Row(
                  children: [
                    const Icon(
                      Icons.warning_amber_rounded,
                      color: Colors.amberAccent,
                      size: 16,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        advisory,
                        style: const TextStyle(
                          color: Colors.amberAccent,
                          fontSize: 10,
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

  /// Backup vector icon in case a specific PNG asset file is missing from local disk
  IconData _getFallbackVectorIcon(String forecastText) {
    final lower = forecastText.toLowerCase();
    if (lower.contains('thunder')) {
      return Icons.thunderstorm_outlined;
    } else if (lower.contains('rain') || lower.contains('shower')) {
      return Icons.grain_outlined;
    } else if (lower.contains('fair') || lower.contains('sunny')) {
      if (lower.contains('night')) return Icons.brightness_3_outlined;
      return Icons.wb_sunny_outlined;
    } else if (lower.contains('wind')) {
      return Icons.air;
    } else if (lower.contains('hazy') ||
        lower.contains('mist') ||
        lower.contains('fog')) {
      return Icons.cloud_queue_outlined;
    }
    return Icons.wb_cloudy_outlined;
  }
}

/// CustomPainter rendering a 285° clean stroke arc with a 75° open gap at the bottom
class MutedArcPainter extends CustomPainter {
  final double progress;
  final Color strokeColor;

  MutedArcPainter({required this.progress, required this.strokeColor});

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.width - 8) / 2;

    const gapAngleDegrees = 75.0;
    const gapAngleRadians = gapAngleDegrees * (math.pi / 180);

    const startAngle = (math.pi / 2) + (gapAngleRadians / 2);
    final totalSweepAngle = (2 * math.pi) - gapAngleRadians;
    final activeSweepAngle = totalSweepAngle * progress.clamp(0.08, 1.0);

    final rect = Rect.fromCircle(center: center, radius: radius);

    // Track Paint
    final trackPaint = Paint()
      ..color = Colors.white.withOpacity(0.10)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4.0
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(rect, startAngle, totalSweepAngle, false, trackPaint);

    // Progress Fill Arc Paint
    final fillPaint = Paint()
      ..color = strokeColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4.5
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(rect, startAngle, activeSweepAngle, false, fillPaint);
  }

  @override
  bool shouldRepaint(covariant MutedArcPainter oldDelegate) {
    return oldDelegate.progress != progress ||
        oldDelegate.strokeColor != strokeColor;
  }
}
