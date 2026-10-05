import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/detail_graph/water_buffer_status_card.dart
//
// Displays the WaterChemistryAssessment computed server-side by the
// DigitalTwin Flask engine (TAN -> NO2 -> NO3 nitrogen cycle + buffering
// trend). Sits directly under the pH historical graph on the detail graph
// screen. Fetches GET /assessment/<user_id> via DigitalTwinApi.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/providers.dart';
import '../../utils/digital_twin_api.dart';

class WaterBufferStatusCard extends ConsumerStatefulWidget {
  final int userId;

  const WaterBufferStatusCard({super.key, required this.userId});

  @override
  ConsumerState<WaterBufferStatusCard> createState() =>
      _WaterBufferStatusCardState();
}

class _WaterBufferStatusCardState extends ConsumerState<WaterBufferStatusCard> {
  void _retry() => ref.invalidate(assessmentProvider(widget.userId));

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<WaterChemistryAssessment?>(
      future: ref.watch(assessmentProvider(widget.userId).future),
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
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpace.xxl),
      child: Center(
        child: SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(
            strokeWidth: 2.4,
            color: AppColors.of(context).info,
          ),
        ),
      ),
    );
  }

  Widget _unavailableState() => PondErrorState(
    title: 'Water Buffer & Nitrogen Cycle',
    message:
        'No digital twin assessment yet. This appears once the pond '
        'engine has run at least one poll cycle, or the analysis '
        'service is unreachable from this device.',
    onRetry: _retry,
  );

  // ------------------------------------------------------------------
  // Main content
  // ------------------------------------------------------------------

  Widget _assessmentCard(WaterChemistryAssessment a) {
    final statusColor = _statusColor(a.status);

    return OutcomeCardShell(
      accent: statusColor,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          AdaptiveRow(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: AdaptiveRow(
                  children: [
                    Icon(
                      Icons.science_outlined,
                      color: AppColors.of(context).info,
                      size: 18,
                    ),
                    const SizedBox(width: AppSpace.sm),
                    Expanded(
                      child: Text(
                        'Water Buffer & Nitrogen Cycle',
                        style: AppType.style(
                          color: AppColors.of(context).text,
                          fontWeight: FontWeight.bold,
                          fontSize: AppType.body,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
              StatusChip(
                status: PondStatus.fromWire(a.status),
                label: a.category.toUpperCase(),
              ),
            ],
          ),
          const SizedBox(height: AppSpace.lg),

          // TAN / NO2 / NO3 metric tiles
          AdaptiveRow(
            children: [
              Expanded(
                child: _metricBox(
                  'TAN',
                  '${a.tanPpm.toStringAsFixed(2)} ppm',
                  _levelColor(a.tanPpm, watch: 0.5, danger: 1.0),
                ),
              ),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: _metricBox(
                  'Nitrite (NO₂)',
                  '${a.no2Ppm.toStringAsFixed(2)} ppm',
                  _levelColor(a.no2Ppm, watch: 0.25, danger: 0.5),
                  isHighlight: a.no2Ppm > 0.5,
                ),
              ),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: _metricBox(
                  'Nitrate (NO₃)',
                  '${a.no3Ppm.toStringAsFixed(1)} ppm',
                  _levelColor(a.no3Ppm, watch: 40, danger: 80),
                ),
              ),
            ],
          ),

          const SizedBox(height: AppSpace.lg),
          Divider(color: AppColors.of(context).outline, height: 1),
          const SizedBox(height: AppSpace.md),

          // Advisory
          AdaptiveRow(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.info_outline, color: statusColor, size: 16),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: Text(
                  a.advisory,
                  style: AppType.style(
                    color: AppColors.of(context).textSecondary,
                    fontSize: AppType.label,
                    height: 1.4,
                  ),
                ),
              ),
            ],
          ),

          // Buffering trend readout, if the engine has enough history
          if (a.phReactivity != null || a.tdsTrend != null) ...[
            const SizedBox(height: AppSpace.md),
            AdaptiveRow(
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
            const SizedBox(height: AppSpace.md),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpace.md,
                vertical: AppSpace.sm,
              ),
              decoration: BoxDecoration(
                color: AppColors.of(context).warning.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(AppRadius.control),
                border: Border.all(
                  color: AppColors.of(context).warning.withValues(alpha: 0.4),
                ),
              ),
              child: AdaptiveRow(
                children: [
                  Icon(
                    Icons.science,
                    color: AppColors.of(context).warning,
                    size: 15,
                  ),
                  const SizedBox(width: AppSpace.sm),
                  Expanded(
                    child: Text(
                      'Add KH / Calcium buffer now',
                      style: AppType.style(
                        color: AppColors.of(context).warning,
                        fontSize: AppType.caption,
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
            const SizedBox(height: AppSpace.md),
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

  Widget _shell({required Widget child}) => OutcomeCardShell(child: child);

  Widget _metricBox(
    String label,
    String value,
    Color valueColor, {
    bool isHighlight = false,
  }) => MetricTile(
    label: label,
    value: value,
    color: valueColor,
    estimate: true,
    status: valueColor == AppColors.of(context).danger
        ? PondStatus.danger
        : valueColor == AppColors.of(context).warning
        ? PondStatus.warning
        : null,
  );

  Widget _trendLine(String label, String value, IconData icon) {
    return AdaptiveRow(
      children: [
        Icon(icon, color: AppColors.of(context).textMuted, size: 13),
        const SizedBox(width: AppSpace.xs),
        Expanded(
          child: Text(
            '$label: $value',
            style: AppType.style(
              color: AppColors.of(context).textMuted,
              fontSize: AppType.caption,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  Widget _warningChip(String text) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpace.sm,
        vertical: AppSpace.xs,
      ),
      decoration: BoxDecoration(
        color: AppColors.of(context).text.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(AppRadius.chip),
        border: Border.all(
          color: AppColors.of(context).text.withValues(alpha: 0.08),
        ),
      ),
      child: AdaptiveRow(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.warning_amber_rounded,
            color: AppColors.of(context).textMuted,
            size: 11,
          ),
          const SizedBox(width: AppSpace.xs),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 220),
            child: Text(
              text,
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.micro,
              ),
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
        return AppColors.of(context).danger;
      case 'amber':
        return AppColors.of(context).warning;
      default:
        return AppColors.of(context).healthy;
    }
  }

  Color _levelColor(
    double value, {
    required double watch,
    required double danger,
  }) {
    if (value > danger) return AppColors.of(context).danger;
    if (value > watch) return AppColors.of(context).warning;
    return AppColors.of(context).healthy;
  }
}
