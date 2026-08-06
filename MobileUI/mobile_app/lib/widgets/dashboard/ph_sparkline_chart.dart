// lib/widgets/dashboard/ph_sparkline_chart.dart

import 'package:flutter/material.dart';

class PhSparklineChart extends StatelessWidget {
  final List<dynamic> telemetrySeries;
  final Color lineColor;
  final double height;

  const PhSparklineChart({
    super.key,
    required this.telemetrySeries,
    this.lineColor = const Color(0xFF50C878), // kEmeraldGreen
    this.height = 48.0,
  });

  @override
  Widget build(BuildContext context) {
    if (telemetrySeries.isEmpty) {
      return SizedBox(
        height: height,
        child: const Center(
          child: Text(
            'Collecting pH trend data...',
            style: TextStyle(color: Colors.white24, fontSize: 10),
          ),
        ),
      );
    }

    // Safely extract numeric pH values from the telemetry array
    final List<double> points = telemetrySeries.map((e) {
      if (e is Map) {
        final val = e['value'];
        if (val is Map) return double.tryParse(val['value'].toString()) ?? 7.0;
        return double.tryParse(val.toString()) ?? 7.0;
      }
      return 7.0;
    }).toList();

    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _SparklinePainter(
          points: points,
          lineColor: lineColor,
        ),
      ),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  final List<double> points;
  final Color lineColor;

  _SparklinePainter({required this.points, required this.lineColor});

  @override
  void paint(Canvas canvas, Size size) {
    if (points.length < 2) return;

    // Find min and max for scaling with safety margins
    double minVal = points.reduce((a, b) => a < b ? a : b);
    double maxVal = points.reduce((a, b) => a > b ? a : b);

    // Enforce a min dynamic range gap to prevent flatline exaggeration
    if ((maxVal - minVal) < 0.4) {
      minVal -= 0.2;
      maxVal += 0.2;
    }

    final double widthStep = size.width / (points.length - 1);
    final Path path = Path();
    final Path fillPath = Path();

    for (int i = 0; i < points.length; i++) {
      final double x = i * widthStep;
      // Invert Y axis since 0,0 is top-left in Flutter Canvas
      final double normalizedY = (points[i] - minVal) / (maxVal - minVal);
      final double y = size.height - (normalizedY * (size.height - 8)) - 4;

      if (i == 0) {
        path.moveTo(x, y);
        fillPath.moveTo(x, size.height);
        fillPath.lineTo(x, y);
      } else {
        // Smooth bezier curves between data points
        final double prevX = (i - 1) * widthStep;
        final double prevNormalizedY =
            (points[i - 1] - minVal) / (maxVal - minVal);
        final double prevY =
            size.height - (prevNormalizedY * (size.height - 8)) - 4;

        final double controlX1 = prevX + (widthStep / 2);
        final double controlX2 = x - (widthStep / 2);

        path.cubicTo(controlX1, prevY, controlX2, y, x, y);
        fillPath.cubicTo(controlX1, prevY, controlX2, y, x, y);
      }

      if (i == points.length - 1) {
        fillPath.lineTo(x, size.height);
        fillPath.close();
      }
    }

    // 1. Draw Subtle Background Area Gradient
    final Paint fillPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [
          lineColor.withOpacity(0.25),
          lineColor.withOpacity(0.0),
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height))
      ..style = PaintingStyle.fill;

    canvas.drawPath(fillPath, fillPaint);

    // 2. Draw Main Sparkline
    final Paint linePaint = Paint()
      ..color = lineColor
      ..strokeWidth = 2.0
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    canvas.drawPath(path, linePaint);

    // 3. Highlight Latest Value Dot
    final double lastX = size.width;
    final double lastNormalizedY = (points.last - minVal) / (maxVal - minVal);
    final double lastY =
        size.height - (lastNormalizedY * (size.height - 8)) - 4;

    final Paint dotOuter = Paint()
      ..color = lineColor.withOpacity(0.3)
      ..style = PaintingStyle.fill;
    final Paint dotInner = Paint()
      ..color = lineColor
      ..style = PaintingStyle.fill;

    canvas.drawCircle(Offset(lastX, lastY), 5.0, dotOuter);
    canvas.drawCircle(Offset(lastX, lastY), 2.5, dotInner);
  }

  @override
  bool shouldRepaint(covariant _SparklinePainter oldDelegate) {
    return oldDelegate.points != points || oldDelegate.lineColor != lineColor;
  }
}