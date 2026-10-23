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
import 'model_outlook_card.dart';

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
    final c = AppColors.of(context);
    final statusColor = _statusColor(a.status);
    final stable = statusColor == c.healthy;
    final cat = a.category.toLowerCase();

    return ModelOutlookCard(
      headline: stable ? 'Water chemistry stable' : 'Water chemistry: $cat',
      statusColor: statusColor,
      statusLabel: stable
          ? 'Stable'
          : (statusColor == c.danger ? 'Action needed' : 'Watch'),
      subtitle: a.advisory,
      rows: [
        ModelOutlookRow(
          'TAN',
          '${a.tanPpm.toStringAsFixed(2)} ppm',
          _levelColor(a.tanPpm, watch: 0.5, danger: 1.0),
        ),
        ModelOutlookRow(
          'Nitrite (NO₂)',
          '${a.no2Ppm.toStringAsFixed(2)} ppm',
          _levelColor(a.no2Ppm, watch: 0.25, danger: 0.5),
        ),
        ModelOutlookRow(
          'Nitrate (NO₃)',
          '${a.no3Ppm.toStringAsFixed(1)} ppm',
          _levelColor(a.no3Ppm, watch: 40, danger: 80),
        ),
        if (a.phReactivity != null)
          ModelOutlookRow(
            'pH reactivity',
            a.phReactivity!.toStringAsFixed(3),
            c.textSecondary,
          ),
        if (a.tdsTrend != null)
          ModelOutlookRow(
            'TDS trend',
            '${a.tdsTrend! >= 0 ? '+' : ''}${a.tdsTrend!.toStringAsFixed(1)} ppm/day',
            c.textSecondary,
          ),
      ],
      alerts: [
        if (a.addHardenerNow)
          ModelOutlookNote(
            'Add KH / Calcium buffer now',
            icon: Icons.science,
            color: c.warning,
          ),
      ],
      infoNotes: [...a.sensorWarnings],
    );
  }

  // ------------------------------------------------------------------
  // Small building blocks
  // ------------------------------------------------------------------

  Widget _shell({required Widget child}) => OutcomeCardShell(child: child);

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
