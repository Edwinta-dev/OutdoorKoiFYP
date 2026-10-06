import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
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
    final Color kLuxColor = AppColors.of(
      context,
    ).healthy; // Bright aquatic cyan
    final Color kWarningAmber = AppColors.of(context).warning;
    final double exposureProgress = (luxNum / 30000.0).clamp(0.0, 1.0);

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
            status: isHighUvOrSun ? PondStatus.warning : PondStatus.healthy,
          ),
          const SizedBox(height: AppSpace.md),

          // --- 2. MAIN DIAL + DATA PANEL (LEFT/RIGHT SPLIT) ---
          AdaptiveRow(
            minWidth: 300,
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
                        trackColor: AppColors.of(context).outline,
                        strokeColor: isHighUvOrSun ? kWarningAmber : kLuxColor,
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
              const SizedBox(width: AppSpace.xl),

              // RIGHT SIDE: Numerical Metric Displays
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Ambient Lux Reading (Always Shown)
                    AdaptiveRow(
                      crossAxisAlignment: CrossAxisAlignment.baseline,
                      textBaseline: TextBaseline.alphabetic,
                      children: [
                        Text(
                          luxNum.toStringAsFixed(0),
                          style: AppType.style(
                            color: isHighUvOrSun ? kWarningAmber : kLuxColor,
                            fontSize: AppType.metric,
                            fontWeight: FontWeight.bold,
                            letterSpacing: -0.5,
                          ),
                        ),
                        const SizedBox(width: AppSpace.xs),
                        Text(
                          'lx Ambient',
                          style: AppType.style(
                            color: AppColors.of(context).textMuted,
                            fontSize: AppType.caption,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: AppSpace.sm),

                    // --- 3. CONDITIONAL ALERT-BY-EXCEPTION ROW ---
                    if (isHighUvOrSun) ...[
                      // Displays ONLY when sunlight/UV is strong enough to accelerate algae
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: AppSpace.sm,
                          vertical: AppSpace.xs,
                        ),
                        decoration: BoxDecoration(
                          color: kWarningAmber.withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(AppRadius.small),
                          border: Border.all(
                            color: kWarningAmber.withValues(alpha: 0.4),
                          ),
                        ),
                        child: AdaptiveRow(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              Icons.warning_amber_rounded,
                              color: kWarningAmber,
                              size: 14,
                            ),
                            const SizedBox(width: AppSpace.xs),
                            Flexible(
                              child: Text(
                                'UV $uvNum • $forecast2hr ',
                                style: AppType.style(
                                  color: kWarningAmber,
                                  fontSize: AppType.caption,
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
                      AdaptiveRow(
                        children: [
                          Container(
                            width: 6,
                            height: 6,
                            decoration: BoxDecoration(
                              color: AppColors.of(
                                context,
                              ).healthy, // Healthy green dot
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: AppSpace.sm),
                          Text(
                            'Algal Photosynthesis Stable',
                            style: AppType.style(
                              color: AppColors.of(context).textSecondary,
                              fontSize: AppType.caption,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: AppSpace.xxs),
                      Text(
                        'No intense UV bloom factors',
                        style: AppType.style(
                          color: AppColors.of(context).textMuted,
                          fontSize: AppType.micro,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Simple open-arc progress painter
class _MutedArcPainter extends CustomPainter {
  final double progress;
  final Color strokeColor;
  final Color trackColor;

  _MutedArcPainter({
    required this.progress,
    required this.strokeColor,
    required this.trackColor,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.width - 8) / 2;
    const startAngle = 2.4; // Open bottom arc
    const sweepAngle = 4.6;

    final bgPaint = Paint()
      ..color = trackColor
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
      oldDelegate.strokeColor != strokeColor ||
      oldDelegate.trackColor != trackColor;
}
