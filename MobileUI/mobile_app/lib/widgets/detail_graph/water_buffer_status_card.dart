// lib/widgets/detail_graph/water_buffer_status_card.dart
//
// Displays the WaterChemistryAssessment computed server-side by the
// DigitalTwin Flask engine (TAN -> NO2 -> NO3 nitrogen cycle + buffering
// trend). Sits directly under the pH historical graph on the detail graph
// screen. Fetches GET /assessment/<user_id> via DigitalTwinApi.

import 'package:flutter/material.dart';
import '../../data/pond_data_source.dart';
import '../../utils/digital_twin_api.dart';

class WaterBufferStatusCard extends StatefulWidget {
  final int userId;

  const WaterBufferStatusCard({super.key, required this.userId});

  @override
  State<WaterBufferStatusCard> createState() => _WaterBufferStatusCardState();
}

class _WaterBufferStatusCardState extends State<WaterBufferStatusCard> {
  late Future<WaterChemistryAssessment?> _future;

  @override
  void initState() {
    super.initState();
    _future = PondDataScope.of(context).fetchLatestAssessment(widget.userId);
  }

  void _retry() {
    setState(() {
      _future = PondDataScope.of(context).fetchLatestAssessment(widget.userId);
    });
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<WaterChemistryAssessment?>(
      future: _future,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return _shell(child: _loadingState());
        }
        final assessment = snapshot.data;
        if (assessment == null) {
          return _shell(child: _unavailableState());
        }
        return _assessmentCard(assessment);
      },
    );
  }

  // ------------------------------------------------------------------
  // States
  // ------------------------------------------------------------------

  Widget _loadingState() {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 24),
      child: Center(
        child: SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(
            strokeWidth: 2.4,
            color: Colors.cyanAccent,
          ),
        ),
      ),
    );
  }

  Widget _unavailableState() {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.science_outlined, color: Colors.white38, size: 18),
              SizedBox(width: 8),
              Text(
                'Water Buffer & Nitrogen Cycle',
                style: TextStyle(
                  color: Colors.white70,
                  fontWeight: FontWeight.bold,
                  fontSize: 13,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          const Text(
            'No digital twin assessment yet. This appears once the pond '
            'engine has run at least one poll cycle, or the analysis '
            'service is unreachable from this device.',
            style: TextStyle(color: Colors.white38, fontSize: 11, height: 1.4),
          ),
          const SizedBox(height: 10),
          TextButton.icon(
            onPressed: _retry,
            style: TextButton.styleFrom(
              foregroundColor: Colors.cyanAccent,
              padding: EdgeInsets.zero,
              minimumSize: const Size(0, 32),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Retry', style: TextStyle(fontSize: 12)),
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Main content
  // ------------------------------------------------------------------

  Widget _assessmentCard(WaterChemistryAssessment a) {
    final statusColor = _statusColor(a.status);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: statusColor.withValues(alpha: 0.3)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.2),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Expanded(
                child: Row(
                  children: [
                    Icon(Icons.science_outlined, color: Colors.cyanAccent, size: 18),
                    SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Water Buffer & Nitrogen Cycle',
                        style: TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                          fontSize: 13,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: statusColor.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: statusColor.withValues(alpha: 0.5)),
                ),
                child: Text(
                  a.category.toUpperCase(),
                  style: TextStyle(
                    color: statusColor,
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),

          // TAN / NO2 / NO3 metric tiles
          Row(
            children: [
              Expanded(
                child: _metricBox(
                  'TAN',
                  '${a.tanPpm.toStringAsFixed(2)} ppm',
                  _levelColor(a.tanPpm, watch: 0.5, danger: 1.0),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _metricBox(
                  'Nitrite (NO₂)',
                  '${a.no2Ppm.toStringAsFixed(2)} ppm',
                  _levelColor(a.no2Ppm, watch: 0.25, danger: 0.5),
                  isHighlight: a.no2Ppm > 0.5,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _metricBox(
                  'Nitrate (NO₃)',
                  '${a.no3Ppm.toStringAsFixed(1)} ppm',
                  _levelColor(a.no3Ppm, watch: 40, danger: 80),
                ),
              ),
            ],
          ),

          const SizedBox(height: 14),
          const Divider(color: Colors.white10, height: 1),
          const SizedBox(height: 12),

          // Advisory
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.info_outline, color: statusColor, size: 16),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  a.advisory,
                  style: const TextStyle(
                    color: Colors.white70,
                    fontSize: 11.5,
                    height: 1.4,
                  ),
                ),
              ),
            ],
          ),

          // Buffering trend readout, if the engine has enough history
          if (a.phReactivity != null || a.tdsTrend != null) ...[
            const SizedBox(height: 12),
            Row(
              children: [
                if (a.phReactivity != null)
                  Expanded(
                    child: _trendLine(
                      'pH Reactivity',
                      a.phReactivity!.toStringAsFixed(3),
                      _trendArrow(a.reactivityTrend),
                    ),
                  ),
                if (a.tdsTrend != null)
                  Expanded(
                    child: _trendLine(
                      'TDS Trend',
                      '${a.tdsTrend! >= 0 ? '+' : ''}${a.tdsTrend!.toStringAsFixed(1)} ppm/day',
                      a.tdsTrend! > 0
                          ? Icons.trending_up
                          : (a.tdsTrend! < 0
                                ? Icons.trending_down
                                : Icons.trending_flat),
                    ),
                  ),
              ],
            ),
          ],

          // Add-hardener call to action
          if (a.addHardenerNow) ...[
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                color: Colors.amberAccent.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: Colors.amberAccent.withValues(alpha: 0.4)),
              ),
              child: const Row(
                children: [
                  Icon(Icons.science, color: Colors.amberAccent, size: 15),
                  SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Add KH / Calcium buffer now',
                      style: TextStyle(
                        color: Colors.amberAccent,
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],

          // Sensor gating warnings, if any
          if (a.sensorWarnings.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: a.sensorWarnings
                  .map((w) => _warningChip(w))
                  .toList(growable: false),
            ),
          ],
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // Small building blocks
  // ------------------------------------------------------------------

  Widget _shell({required Widget child}) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
      ),
      child: child,
    );
  }

  Widget _metricBox(String label, String value, Color valueColor, {bool isHighlight = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 8),
      decoration: BoxDecoration(
        color: isHighlight
            ? valueColor.withValues(alpha: 0.1)
            : Colors.white.withValues(alpha: 0.03),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: isHighlight
              ? valueColor.withValues(alpha: 0.3)
              : Colors.white.withValues(alpha: 0.05),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(color: Colors.white38, fontSize: 9),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 4),
          FittedBox(
            child: Text(
              value,
              style: TextStyle(
                color: valueColor,
                fontWeight: FontWeight.bold,
                fontSize: 12,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _trendLine(String label, String value, IconData icon) {
    return Row(
      children: [
        Icon(icon, color: Colors.white38, size: 13),
        const SizedBox(width: 4),
        Expanded(
          child: Text(
            '$label: $value',
            style: const TextStyle(color: Colors.white54, fontSize: 10.5),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  Widget _warningChip(String text) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.warning_amber_rounded, color: Colors.white38, size: 11),
          const SizedBox(width: 4),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 220),
            child: Text(
              text,
              style: const TextStyle(color: Colors.white38, fontSize: 9.5),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }

  IconData _trendArrow(double? trend) {
    if (trend == null || trend.abs() < 0.0005) return Icons.trending_flat;
    return trend > 0 ? Icons.trending_up : Icons.trending_down;
  }

  Color _statusColor(String status) {
    switch (status.toLowerCase()) {
      case 'red':
        return Colors.redAccent;
      case 'amber':
        return Colors.amberAccent;
      default:
        return Colors.greenAccent;
    }
  }

  Color _levelColor(double value, {required double watch, required double danger}) {
    if (value > danger) return Colors.redAccent;
    if (value > watch) return Colors.amberAccent;
    return Colors.greenAccent;
  }
}
