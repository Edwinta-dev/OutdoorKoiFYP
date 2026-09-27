import 'package:flutter/material.dart';
import '../../utils/pond_heuristics.dart';
import '../../utils/digital_twin_api.dart';

// ROUND 3 FIX (the headline MobileUI finding): this card used to ALWAYS
// recompute a full pH/buffer risk assessment client-side via
// PondHeuristics.getPhAdvisory - a from-scratch re-implementation of the
// same kind of reactivity/trend analysis Backend/DigitalTwin/engine.py's
// WaterChemistryEngine already computes server-side (grouped by day,
// least-squares slope, risk scoring - same shape, different units/
// thresholds, arrived at completely independently). That backend result is
// already cached and exposed cheaply via GET /assessment/<user_id> - see
// app.py's own docstring: "/assessment/* cheap cached CURRENT status...
// What the dashboard cards... want." The dashboard was built to read raw
// telemetry and recompute instead, which is why this screen and the Detail
// Graph screen (which DOES call the backend, via
// widgets/detail_graph/water_buffer_status_card.dart) can show two
// different verdicts for the same pond at the same moment - the exact
// mobile/backend "split-brain" flagged in this project's own prior audit.
//
// Fix: prefer the backend's assessment when available (the normal case;
// zero client-side recomputation). Only fall back to the local heuristic
// when it genuinely isn't available yet (new pond, or the DigitalTwin
// service unreachable) - graceful degradation, not a second mainline path.
class PhCardData {
  final String status; // "Green" | "Amber" | "Red"
  final String category;
  final int riskScore;
  final double phReactivity;
  final bool addHardenerNow;
  final String message;
  final AdvisorySeverity severity;
  final bool fromBackend;

  const PhCardData({
    required this.status,
    required this.category,
    required this.riskScore,
    required this.phReactivity,
    required this.addHardenerNow,
    required this.message,
    required this.severity,
    required this.fromBackend,
  });
}

AdvisorySeverity _severityForStatus(String status) {
  if (status == 'Red') return AdvisorySeverity.red;
  if (status == 'Amber') return AdvisorySeverity.amber;
  return AdvisorySeverity.none;
}

/// Pure, independently-testable resolver: backend assessment wins when
/// present; otherwise falls back to the existing client-side heuristic
/// (unchanged behavior for that path - see ph_helpers_test.dart).
PhCardData resolvePhCardData({
  required WaterChemistryAssessment? backendAssessment,
  required double currentPh,
  required double currentTds,
  required String forecast2hr,
  required dynamic rainfallMm,
  required List<PondSample> telemetryHistory,
}) {
  if (backendAssessment != null) {
    return PhCardData(
      status: backendAssessment.status,
      category: backendAssessment.category,
      riskScore: backendAssessment.riskScore,
      phReactivity: backendAssessment.phReactivity ?? 0.0,
      addHardenerNow: backendAssessment.addHardenerNow,
      message: backendAssessment.advisory,
      severity: _severityForStatus(backendAssessment.status),
      fromBackend: true,
    );
  }

  final phAdvisory = PondHeuristics.getPhAdvisory(
    ph: currentPh,
    tds: currentTds,
    forecast2hr: forecast2hr,
    rainfallMm: rainfallMm,
    history: telemetryHistory,
  );
  final buffer = phAdvisory.bufferAssessment;
  return PhCardData(
    status: buffer.status,
    category: buffer.category,
    riskScore: buffer.riskScore,
    phReactivity: buffer.phReactivity,
    addHardenerNow: buffer.addHardenerNow,
    message: phAdvisory.message,
    severity: phAdvisory.severity,
    fromBackend: false,
  );
}

class PhOutcomeCard extends StatelessWidget {
  final Map<String, dynamic> sensorData;
  final Map<String, dynamic> forecastData;
  final List<PondSample> telemetryHistory;
  final VoidCallback onTap;
  // Null when the backend assessment isn't available yet (new pond, or the
  // DigitalTwin service unreachable) - triggers the client-side fallback.
  final WaterChemistryAssessment? backendAssessment;

  // Safe target bounds for visual scale
  final double targetMinPh;
  final double targetMaxPh;

  const PhOutcomeCard({
    super.key,
    required this.sensorData,
    required this.forecastData,
    required this.telemetryHistory,
    required this.onTap,
    this.backendAssessment,
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
    // 1. Extract inputs (currentPh/currentTds are still the live sensor
    // reading for the numeric display - that part was never duplicated,
    // only the RISK ASSESSMENT of it was).
    final double currentPh =
        double.tryParse(sensorData['pH']?.toString() ?? '7.4') ?? 7.4;
    final double currentTds =
        double.tryParse(sensorData['TDS']?.toString() ?? '180') ?? 180.0;
    final String forecast2hr =
        forecastData['two_hr_forecast']?.toString() ?? 'Fair';
    final dynamic rainfallMm = forecastData['rainfall_mm'] ?? 0.0;

    // 2. Prefer the backend's already-computed assessment; fall back to the
    // local heuristic only when it isn't available.
    final cardData = resolvePhCardData(
      backendAssessment: backendAssessment,
      currentPh: currentPh,
      currentTds: currentTds,
      forecast2hr: forecast2hr,
      rainfallMm: rainfallMm,
      telemetryHistory: telemetryHistory,
    );

    final statusColor = _getStatusColor(cardData.severity, cardData.status);

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
                  child: _buildRainVulnerabilityPanel(cardData, statusColor),
                ),
              ],
            ),

            // --- BOTTOM ADVISORY BANNER (If Amber / Red) ---
            if (cardData.severity != AdvisorySeverity.none ||
                cardData.addHardenerNow) ...[
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
                        cardData.message,
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
    PhCardData cardData,
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
            if (cardData.addHardenerNow)
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
              '${cardData.riskScore}',
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
              cardData.category,
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
          cardData.fromBackend
              ? 'Server-computed risk score (WaterChemistryEngine)'
              : 'Reactivity: ${cardData.phReactivity.toStringAsFixed(2)} pH/kLUX',
          style: const TextStyle(color: Colors.white38, fontSize: 10),
        ),
      ],
    );
  }
}
