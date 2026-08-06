// lib/widgets/dashboard/ph_outcome_card.dart

import 'package:flutter/material.dart';
import '../../utils/pond_heuristics.dart';
import 'ph_sparkline_chart.dart';

const Color kEmeraldGreen = Color(0xFF50C878);

class PhOutcomeCard extends StatelessWidget {
  final Map<String, dynamic> sensorData;
  final Map<String, dynamic> forecastData;
  final List<dynamic> phTelemetry; // Series payload from RPC
  final VoidCallback onTap;

  const PhOutcomeCard({
    super.key,
    required this.sensorData,
    required this.forecastData,
    required this.phTelemetry,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    // 1. Raw Sensor pH & TDS
    final rawPh = sensorData['pH'] ?? 7.35;
    final rawTds = sensorData['TDS'] ?? 185;

    // 2. Forecast Rain Conditions
    final forecast2hr = forecastData['forecast_2hr']?['forecast'] ?? 'Fair';
    final forecast24hr =
        forecastData['forecast_24hr']?['general']?['forecast']?['text'] ??
        'Fair';

    // 3. Heuristic Advisory
    final advisory = PondHeuristics.getPhAdvisory(
      ph: rawPh,
      tds: rawTds,
      forecast2hr: forecast2hr,
      forecast24hr: forecast24hr,
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
            // Header
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Row(
                  children: [
                    Icon(
                      Icons.water_drop_outlined,
                      color: kEmeraldGreen,
                      size: 18,
                    ),
                    SizedBox(width: 8),
                    Text(
                      'pH Stability & Acid Crash Guard',
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
            const SizedBox(height: 14),

            // Primary pH & TDS Metrics
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(
                  rawPh.toString(),
                  style: const TextStyle(
                    color: kEmeraldGreen,
                    fontSize: 32,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(width: 6),
                const Text(
                  'pH',
                  style: TextStyle(color: Colors.white54, fontSize: 11),
                ),
                const SizedBox(width: 24),
                Text(
                  '$rawTds ppm',
                  style: const TextStyle(
                    color: Colors.white70,
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(width: 6),
                const Text(
                  'TDS',
                  style: TextStyle(color: Colors.white54, fontSize: 11),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // --- INLINE pH SPARKLINE GRAPH ---
            PhSparklineChart(
              telemetrySeries: phTelemetry,
              lineColor: kEmeraldGreen,
              height: 44,
            ),
            const SizedBox(height: 12),

            // Weather Forecast Driver Row
            Row(
              children: [
                const Icon(
                  Icons.umbrella_outlined,
                  color: Colors.lightBlueAccent,
                  size: 14,
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    '2h: $forecast2hr • 24h: $forecast24hr',
                    style: const TextStyle(color: Colors.white70, fontSize: 11),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),

            // Advisory Footer
            if (advisory != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.redAccent.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.redAccent.withOpacity(0.3)),
                ),
                child: Row(
                  children: [
                    const Icon(
                      Icons.warning_amber_rounded,
                      color: Colors.redAccent,
                      size: 16,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        advisory,
                        style: const TextStyle(
                          color: Colors.redAccent,
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
}
