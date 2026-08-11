// lib/widgets/dashboard/solar_outcome_card.dart

import 'package:flutter/material.dart';
import '../../utils/pond_heuristics.dart';

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

  @override
  Widget build(BuildContext context) {
    // 1. Extract LUX & NEA Forecast Data
    final double luxNum =
        double.tryParse(sensorData['LUX']?.toString() ?? '500') ?? 500.0;
    final String forecast2hr =
        forecastData['two_hr_forecast']?.toString() ??
        forecastData['forecast_2hr']?['forecast']?.toString() ??
        'Fair';
    final int uvNum =
        int.tryParse(
          forecastData['uv_index']?['data']?['uv']?.toString() ?? '4',
        ) ??
        4;

    // 2. Determine if Solar/Algae Alert Should Be Triggered
    final bool isFairSky = PondHeuristics.isFairForecast(forecast2hr);
    final bool isHighUvOrSun = (uvNum >= 6 && isFairSky) || luxNum > 15000;

    // 3. Colors & Progress
    const Color kLuxColor = Color(0xFF50C878); // Bright aquatic cyan
    const Color kWarningAmber = Colors.amberAccent;
    final double exposureProgress = (luxNum / 30000.0).clamp(0.0, 1.0);

    final Color statusColor = isHighUvOrSun ? kWarningAmber : kLuxColor;

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFF1A1F26),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: statusColor.withValues(alpha: 0.4),
            width: 1.5,
          ),
          boxShadow: [
            BoxShadow(
              color: statusColor.withValues(alpha: 0.25),
              blurRadius: 12,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // --- 1. CARD HEADER ---
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    Icon(Icons.wb_sunny_outlined, size: 18),
                    const SizedBox(width: 8),
                    const Text(
                      'Algal & Solar Monitor',
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
                // LEFT SIDE: Circular Arc Progress Dial
                SizedBox(
                  width: 96,
                  height: 96,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      CustomPaint(
                        size: const Size(96, 96),
                        painter: _MutedArcPainter(
                          progress: exposureProgress,
                          strokeColor: isHighUvOrSun
                              ? kWarningAmber
                              : kLuxColor,
                        ),
                      ),

                      // Custom Self-Sourced Asset (Algae / Biological icon)
                      Image.asset(
                        'lib/assets/seaweed.png', // Dedicated ecosystem asset
                        width: 48,
                        height: 48,
                        fit: BoxFit.contain,
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
                      // Ambient Lux Reading (Always Shown)
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.baseline,
                        textBaseline: TextBaseline.alphabetic,
                        children: [
                          Text(
                            luxNum.toStringAsFixed(0),
                            style: TextStyle(
                              color: isHighUvOrSun ? kWarningAmber : kLuxColor,
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
                      const SizedBox(height: 8),

                      // --- 3. CONDITIONAL ALERT-BY-EXCEPTION ROW ---
                      if (isHighUvOrSun) ...[
                        // Displays ONLY when sunlight/UV is strong enough to accelerate algae
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 4,
                          ),
                          decoration: BoxDecoration(
                            color: kWarningAmber.withOpacity(0.12),
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                              color: kWarningAmber.withOpacity(0.4),
                            ),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(
                                Icons.warning_amber_rounded,
                                color: kWarningAmber,
                                size: 14,
                              ),
                              const SizedBox(width: 5),
                              Flexible(
                                child: Text(
                                  'UV $uvNum • $forecast2hr • Algal Risk',
                                  style: const TextStyle(
                                    color: kWarningAmber,
                                    fontSize: 11,
                                    fontWeight: FontWeight.w600,
                                  ),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ] else ...[
                        // Normal Baseline View (No UV/Forecast redundancy)
                        Row(
                          children: [
                            Container(
                              width: 6,
                              height: 6,
                              decoration: const BoxDecoration(
                                color: Color(0xFF50C878), // Healthy green dot
                                shape: BoxShape.circle,
                              ),
                            ),
                            const SizedBox(width: 6),
                            const Text(
                              'Algal Photosynthesis Stable',
                              style: TextStyle(
                                color: Colors.white70,
                                fontSize: 11,
                                fontWeight: FontWeight.w500,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 2),
                        const Text(
                          'No intense UV bloom factors',
                          style: TextStyle(color: Colors.white38, fontSize: 10),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Simple open-arc progress painter
class _MutedArcPainter extends CustomPainter {
  final double progress;
  final Color strokeColor;

  _MutedArcPainter({required this.progress, required this.strokeColor});

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.width - 8) / 2;
    const startAngle = 2.4; // Open bottom arc
    const sweepAngle = 4.6;

    final bgPaint = Paint()
      ..color = Colors.white.withOpacity(0.08)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 6
      ..strokeCap = StrokeCap.round;

    final activePaint = Paint()
      ..color = strokeColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 6
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      startAngle,
      sweepAngle,
      false,
      bgPaint,
    );

    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      startAngle,
      sweepAngle * progress,
      false,
      activePaint,
    );
  }

  @override
  bool shouldRepaint(covariant _MutedArcPainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.strokeColor != strokeColor;
}
