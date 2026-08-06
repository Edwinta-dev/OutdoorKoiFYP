// lib/widgets/dashboard/at_a_glance_widget.dart

import 'dart:math' as math;
import 'package:flutter/material.dart';

/// Centralized Helper for Dynamic Health-Based Color Shifts
class MetricColorHelper {
  // Base Palette (Muted, easy on the eyes)
  static const Color phBase = Color(0xFF4EBE96); // Soft Emerald
  static const Color tempBase = Color(0xFF4FC3F7); // Sky Cyan
  static const Color tdsBase = Color(0xFFFFB74D); // Soft Amber
  static const Color luxBase = Color(0xFFFF8A65); // Soft Sunset Coral

  // Warning & Alert Colors
  static const Color warningColor = Color(0xFFFFCA28); // Soft Amber Warning
  static const Color criticalColor = Color(0xFFEF5350); // Soft Red Alert

  /// Smoothly interpolates color based on deviation between [currentVal] and [targetVal]
  static Color getDynamicColor({
    required dynamic currentVal,
    required dynamic targetVal,
    required Color baseColor,
    required double warningThreshold,
    required double criticalThreshold,
  }) {
    final num current = num.tryParse(currentVal.toString()) ?? 0;
    final num target = num.tryParse(targetVal.toString()) ?? 0;

    final double deviation = (current - target).abs().toDouble();

    // 1. Within Optimal Target Bounds -> Base Color
    if (deviation <= warningThreshold) {
      return baseColor;
    }

    // 2. Caution Zone -> Lerp from Base to Warning Color
    if (deviation < criticalThreshold) {
      final double factor =
          (deviation - warningThreshold) /
          (criticalThreshold - warningThreshold);
      return Color.lerp(baseColor, warningColor, factor.clamp(0.0, 1.0))!;
    }

    // 3. Critical Zone -> Lerp from Warning to Critical Red Color
    final double criticalFactor =
        (deviation - criticalThreshold) / criticalThreshold;
    return Color.lerp(
      warningColor,
      criticalColor,
      criticalFactor.clamp(0.0, 1.0),
    )!;
  }
}

class AtAGlanceWidget extends StatelessWidget {
  final Map<String, dynamic> data;
  final String iconPath;

  const AtAGlanceWidget({
    super.key,
    required this.data,
    this.iconPath = 'lib/assets/koi_icon.png', // Unchanged asset path
  });

  /// Formats raw dynamic values to max 2 decimal places (2dp)
  String _formatValue(dynamic rawVal) {
    if (rawVal == null) return '0';
    final num? parsed = num.tryParse(rawVal.toString());
    if (parsed == null) return rawVal.toString();

    // Whole integer formatting
    if (parsed == parsed.roundToDouble()) {
      return parsed.toInt().toString();
    }

    // Limit to max 2dp and strip trailing decimal zeroes
    String formatted = parsed.toStringAsFixed(2);
    if (formatted.contains('.')) {
      formatted = formatted
          .replaceAll(RegExp(r'0+$'), '')
          .replaceAll(RegExp(r'\.$'), '');
    }
    return formatted;
  }

  /// Calculates 0.0 - 1.0 progress ratio for the dial arc fill
  double _calculateHealthProgress(String key, dynamic rawVal) {
    final num val = double.tryParse(rawVal.toString()) ?? 0;
    switch (key) {
      case 'pH':
        final distance = (val - 7.4).abs();
        return (1.0 - (distance / 2.0)).clamp(0.1, 1.0);
      case 'temp':
        if (val >= 24 && val <= 28) return 1.0;
        return (1.0 - ((val - 26).abs() / 10.0)).clamp(0.1, 1.0);
      case 'TDS':
        return (1.0 - (val / 500.0)).clamp(0.1, 1.0);
      case 'LUX':
        return (val / 1000.0).clamp(0.1, 1.0);
      default:
        return 0.85;
    }
  }

  @override
  Widget build(BuildContext context) {
    // Extract raw values safely
    final rawPh = data['pH'] ?? 7.2;
    final rawTemp = data['temp'] ?? 28.2;
    final rawTds = data['TDS'] ?? 185;
    final rawLux = data['LUX'] ?? 680;

    // Target values (Can be dynamically calculated or passed via fish tips later)
    const targetPh = 7.4;
    const targetTemp = 26.0;
    const targetTds = 150.0;
    const targetLux = 500.0;

    // Format 2dp string displays
    final phStr = _formatValue(rawPh);
    final tempStr = "${_formatValue(rawTemp)}°";
    final tdsStr = _formatValue(rawTds);
    final luxStr = _formatValue(rawLux);

    // Calculate health-based dynamic colors per metric
    final Color dynamicPhColor = MetricColorHelper.getDynamicColor(
      currentVal: rawPh,
      targetVal: targetPh,
      baseColor: MetricColorHelper.phBase,
      warningThreshold: 0.4, // Deviation > 0.4 triggers warning
      criticalThreshold: 0.8, // Deviation > 0.8 triggers critical red
    );

    final Color dynamicTempColor = MetricColorHelper.getDynamicColor(
      currentVal: rawTemp,
      targetVal: targetTemp,
      baseColor: MetricColorHelper.tempBase,
      warningThreshold: 2.5, // Deviation > 2.5°C triggers warning
      criticalThreshold: 5.0, // Deviation > 5.0°C triggers critical red
    );

    final Color dynamicTdsColor = MetricColorHelper.getDynamicColor(
      currentVal: rawTds,
      targetVal: targetTds,
      baseColor: MetricColorHelper.tdsBase,
      warningThreshold: 50.0, // Deviation > 50 ppm triggers warning
      criticalThreshold: 100.0, // Deviation > 100 ppm triggers critical
    );

    final Color dynamicLuxColor = MetricColorHelper.getDynamicColor(
      currentVal: rawLux,
      targetVal: targetLux,
      baseColor: MetricColorHelper.luxBase,
      warningThreshold: 300.0,
      criticalThreshold: 600.0,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Padding(
          padding: EdgeInsets.only(left: 4, bottom: 12),
          child: Row(
            children: [
              Icon(
                Icons.bubble_chart_outlined,
                color: Colors.cyanAccent,
                size: 18,
              ),
              SizedBox(width: 8),
              Text(
                'At A Glance',
                style: TextStyle(
                  color: Colors.white70,
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
        ),

        SizedBox(
          height: 330,
          width: double.infinity,
          child: Stack(
            alignment: Alignment.center,
            children: [
              // Central PNG Icon
              Image.asset(
                iconPath,
                width: 105,
                height: 105,
                fit: BoxFit.contain,
              ),

              // --- 4 Circular Arc Dial Nodes ---

              // Top-Left: pH Dial
              Align(
                alignment: const Alignment(-0.88, -0.80),
                child: ArcDialNode(
                  label: 'pH',
                  unit: '',
                  value: phStr,
                  icon: Icons.water_drop_outlined,
                  progress: _calculateHealthProgress('pH', rawPh),
                  accentColor: dynamicPhColor,
                ),
              ),

              // Top-Right: Temp Dial
              Align(
                alignment: const Alignment(0.88, -0.80),
                child: ArcDialNode(
                  label: 'Temp',
                  unit: '°C',
                  value: tempStr,
                  icon: Icons.thermostat_outlined,
                  progress: _calculateHealthProgress('temp', rawTemp),
                  accentColor: dynamicTempColor,
                ),
              ),

              // Bottom-Left: TDS Dial
              Align(
                alignment: const Alignment(-0.88, 0.85),
                child: ArcDialNode(
                  label: 'TDS',
                  unit: 'ppm',
                  value: tdsStr,
                  icon: Icons.grain_outlined,
                  progress: _calculateHealthProgress('TDS', rawTds),
                  accentColor: dynamicTdsColor,
                ),
              ),

              // Bottom-Right: LUX Dial
              Align(
                alignment: const Alignment(0.88, 0.85),
                child: ArcDialNode(
                  label: 'Lux',
                  unit: 'lx',
                  value: luxStr,
                  icon: Icons.wb_sunny_outlined,
                  progress: _calculateHealthProgress('LUX', rawLux),
                  accentColor: dynamicLuxColor,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Dial node widget displaying an open stroke arc with an icon + value inside and white label outside
class ArcDialNode extends StatelessWidget {
  final String label;
  final String unit;
  final String value;
  final IconData icon;
  final double progress;
  final Color accentColor;
  final double diameter;

  const ArcDialNode({
    super.key,
    required this.label,
    required this.unit,
    required this.value,
    required this.icon,
    required this.progress,
    required this.accentColor,
    this.diameter = 120.0,
  });

  @override
  Widget build(BuildContext context) {
    // Format label with unit in brackets if unit is non-empty
    final formattedLabel = unit.isNotEmpty ? '$label ($unit)' : label;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        // 1. Dial Arc Viewport
        SizedBox(
          width: diameter,
          height: diameter,
          child: Stack(
            alignment: Alignment.center,
            children: [
              // Clean Custom Arc Painter (Shortened radians with 75° gap)
              CustomPaint(
                size: Size(diameter, diameter),
                painter: MutedArcPainter(
                  progress: progress,
                  strokeColor: accentColor,
                ),
              ),

              // Center content: Small icon + large 2dp value inside the bottom gap
              Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(icon, size: 16, color: accentColor.withOpacity(0.9)),
                  const SizedBox(height: 2),
                  Text(
                    value,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                      letterSpacing: -0.5,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),

        const SizedBox(height: 6),

        // 2. White Label Outside & Below Circle
        Text(
          formattedLabel,
          style: const TextStyle(
            color: Colors.white70, // Clean white font
            fontSize: 11,
            fontWeight: FontWeight.w500,
            letterSpacing: 0.3,
          ),
        ),
      ],
    );
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

    // 75-degree gap at bottom gives extra breathing space for the icon + text
    const gapAngleDegrees = 75.0;
    const gapAngleRadians = gapAngleDegrees * (math.pi / 180);

    const startAngle = (math.pi / 2) + (gapAngleRadians / 2);
    final totalSweepAngle = (2 * math.pi) - gapAngleRadians;
    final activeSweepAngle = totalSweepAngle * progress.clamp(0.08, 1.0);

    final rect = Rect.fromCircle(center: center, radius: radius);

    // Muted background track
    final trackPaint = Paint()
      ..color = Colors.white.withOpacity(0.10)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3.5
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(rect, startAngle, totalSweepAngle, false, trackPaint);

    // Crisp, eye-friendly foreground stroke
    final fillPaint = Paint()
      ..color = strokeColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4.0
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(rect, startAngle, activeSweepAngle, false, fillPaint);
  }

  @override
  bool shouldRepaint(covariant MutedArcPainter oldDelegate) {
    return oldDelegate.progress != progress ||
        oldDelegate.strokeColor != strokeColor;
  }
}
