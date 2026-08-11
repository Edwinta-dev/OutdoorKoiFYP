import 'package:flutter/material.dart';
import '../../utils/pond_heuristics.dart';

class PhOutcomeCard extends StatelessWidget {
  final Map<String, dynamic> sensorData;
  final Map<String, dynamic> forecastData;
  final List<PondSample> telemetryHistory;
  final VoidCallback onTap;

  // Safe target bounds for visual scale
  final double targetMinPh;
  final double targetMaxPh;

  const PhOutcomeCard({
    super.key,
    required this.sensorData,
    required this.forecastData,
    required this.telemetryHistory,
    required this.onTap,
    this.targetMinPh = 6.8,
    this.targetMaxPh = 8.2,
  });

  Color _getStatusColor(AdvisorySeverity severity, String status) {
    if (severity == AdvisorySeverity.red || status == 'Red') {
      return const Color(0xFFFF4D4D);
    }
    if (severity == AdvisorySeverity.amber || status == 'Amber') {
      return Colors.amberAccent;
    }
    return const Color(0xFF50C878); // Optimal Green
  }

  @override
  Widget build(BuildContext context) {
    // 1. Extract inputs
    final double currentPh =
        double.tryParse(sensorData['pH']?.toString() ?? '7.4') ?? 7.4;
    final double currentTds =
        double.tryParse(sensorData['TDS']?.toString() ?? '180') ?? 180.0;
    final String forecast2hr =
        forecastData['two_hr_forecast']?.toString() ?? 'Fair';
    final dynamic rainfallMm = forecastData['rainfall_mm'] ?? 0.0;

    // 2. Evaluate Heuristics & Rain Vulnerability
    final phAdvisory = PondHeuristics.getPhAdvisory(
      ph: currentPh,
      tds: currentTds,
      forecast2hr: forecast2hr,
      rainfallMm: rainfallMm,
      history: telemetryHistory,
    );

    final buffer = phAdvisory.bufferAssessment;
    final statusColor = _getStatusColor(phAdvisory.severity, buffer.status);

    return GestureDetector(
      onTap: onTap,
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
            // --- HEADER ROW ---
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.water_drop_outlined,
                      color: statusColor,
                      size: 20,
                    ),
                    const SizedBox(width: 8),
                    const Text(
                      'pH & BUFFER HEALTH',
                      style: TextStyle(
                        color: Colors.white70,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.8,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 16),

            // --- MAIN TWO-COLUMN BODY ---
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                // LEFT COLUMN: pH Display + Vertical Scale
                Expanded(
                  flex: 5,
                  child: Row(
                    children: [
                      // Numeric pH
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            currentPh.toStringAsFixed(2),
                            style: TextStyle(
                              color: statusColor,
                              fontSize: 34,
                              fontWeight: FontWeight.bold,
                              height: 1.0,
                            ),
                          ),
                          const SizedBox(height: 4),
                          const Text(
                            'Current pH',
                            style: TextStyle(
                              color: Colors.white54,
                              fontSize: 12,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(width: 16),

                      // Vertical pH Scale Bar (5.5 to 9.5 range)
                      _buildVerticalPhScale(currentPh, statusColor),
                    ],
                  ),
                ),

                // DIVIDER
                Container(
                  width: 1,
                  height: 64,
                  color: Colors.white12,
                  margin: const EdgeInsets.symmetric(horizontal: 12),
                ),

                // RIGHT COLUMN: Rain Vulnerability Score Panel
                Expanded(
                  flex: 6,
                  child: _buildRainVulnerabilityPanel(buffer, statusColor),
                ),
              ],
            ),

            // --- BOTTOM ADVISORY BANNER (If Amber / Red) ---
            if (phAdvisory.severity != AdvisorySeverity.none ||
                buffer.addHardenerNow) ...[
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 10,
                ),
                decoration: BoxDecoration(
                  color: statusColor.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Row(
                  children: [
                    Icon(
                      Icons.warning_amber_rounded,
                      color: statusColor,
                      size: 18,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        phAdvisory.message,
                        style: TextStyle(
                          color: statusColor,
                          fontSize: 12,
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

  /// Builds the Vertical pH Target Scale (Range: 5.5 to 9.5)
  Widget _buildVerticalPhScale(double ph, Color activeColor) {
    const double minPhScale = 5.5;
    const double maxPhScale = 9.5;
    final double clampedPh = ph.clamp(minPhScale, maxPhScale);

    // Convert pH value to a normalized 0.0 - 1.0 height factor
    final double progress =
        (clampedPh - minPhScale) / (maxPhScale - minPhScale);

    return SizedBox(
      height: 64,
      width: 18,
      child: Stack(
        alignment: Alignment.bottomCenter,
        children: [
          // Background Track
          Container(
            width: 6,
            decoration: BoxDecoration(
              color: Colors.white10,
              borderRadius: BorderRadius.circular(4),
            ),
          ),
          // Green Safe Zone Band (6.8 to 8.2)
          Positioned(
            bottom:
                64 * ((targetMinPh - minPhScale) / (maxPhScale - minPhScale)),
            child: Container(
              width: 6,
              height:
                  64 *
                  ((targetMaxPh - targetMinPh) / (maxPhScale - minPhScale)),
              decoration: BoxDecoration(
                color: const Color(0xFF50C878).withOpacity(0.35),
                borderRadius: BorderRadius.circular(4),
              ),
            ),
          ),
          // Current pH Marker Indicator
          Positioned(
            bottom: (64 * progress) - 4, // 4px offset for center alignment
            child: Container(
              width: 14,
              height: 6,
              decoration: BoxDecoration(
                color: activeColor,
                borderRadius: BorderRadius.circular(3),
                boxShadow: [
                  BoxShadow(color: activeColor.withOpacity(0.6), blurRadius: 4),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// Right-side panel detailing the Rain Vulnerability Score
  Widget _buildRainVulnerabilityPanel(
    BufferAssessment buffer,
    Color statusColor,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text(
              'Rain Vulnerability',
              style: TextStyle(color: Colors.white54, fontSize: 11),
            ),
            if (buffer.addHardenerNow)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: const Color(0xFFFF4D4D).withOpacity(0.2),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Text(
                  'BUFFER NOW',
                  style: TextStyle(
                    color: Color(0xFFFF4D4D),
                    fontSize: 9,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 4),

        // Risk Score Display
        Row(
          crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: [
            Text(
              '${buffer.riskScore}',
              style: TextStyle(
                color: statusColor,
                fontSize: 26,
                fontWeight: FontWeight.bold,
              ),
            ),
            const Text(
              ' / 10',
              style: TextStyle(color: Colors.white38, fontSize: 13),
            ),
            const Spacer(),
            Text(
              buffer.category,
              style: TextStyle(
                color: statusColor,
                fontSize: 11,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),

        // Proxy Info Caption
        Text(
          'Reactivity: ${buffer.phReactivity.toStringAsFixed(2)} pH/kLUX',
          style: const TextStyle(color: Colors.white38, fontSize: 10),
        ),
      ],
    );
  }
}
