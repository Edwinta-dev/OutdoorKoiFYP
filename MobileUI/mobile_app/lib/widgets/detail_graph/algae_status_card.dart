import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/detail_graph/algae_status_card.dart
//
// Lookahead card for the Algal & Solar detail graph screen.
// Answers "when will I next need to scrub?" from
// GET /forecast/algae/<user_id>.
//
// The distinguishing feature versus the other two lookaheads is that this
// one is GROUNDED on a real measurement: the ESP32-CAM's HSV green-pixel
// ratio, stored in imageTable. It says whether the growth rate was fitted
// from that history or fell back to a literature default, and surfaces how
// many frames were rejected as obstructed.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/providers.dart';
import '../../utils/digital_twin_api.dart';
import 'model_outlook_card.dart';

class AlgaeStatusCard extends ConsumerStatefulWidget {
  final int userId;

  const AlgaeStatusCard({super.key, required this.userId});

  @override
  ConsumerState<AlgaeStatusCard> createState() => _AlgaeStatusCardState();
}

class _AlgaeStatusCardState extends ConsumerState<AlgaeStatusCard> {
  void _retry() => ref.invalidate(algaeForecastProvider(widget.userId));

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<({AlgaeForecast? forecast, String? error})>(
      future: ref.watch(algaeForecastProvider(widget.userId).future),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return _shell(child: _loadingState());
        }
        final f = snapshot.data?.forecast;
        if (f == null) {
          return _shell(
            child: _unavailableState(
              snapshot.data?.error ?? 'Forecast unavailable',
            ),
          );
        }
        return _forecastCard(f);
      },
    );
  }

  // ------------------------------------------------------------------

  Widget _loadingState() => Padding(
    padding: const EdgeInsets.symmetric(vertical: AppSpace.xxl),
    child: Center(
      child: SizedBox(
        width: 22,
        height: 22,
        child: CircularProgressIndicator(
          strokeWidth: 2.4,
          color: AppColors.of(context).water,
        ),
      ),
    ),
  );

  Widget _unavailableState(String reason) => PondErrorState(
    title: 'Algae Growth Outlook',
    message: reason,
    onRetry: _retry,
  );

  // ------------------------------------------------------------------

  Widget _forecastCard(AlgaeForecast f) {
    final c = AppColors.of(context);
    final days = f.predictedScrubDaysFromNow;
    final accent = _urgencyColor(days);
    final String headline;
    final String sub;
    if (days == null) {
      headline = 'No scrub needed';
      sub = 'Within the forecast window';
    } else if (days == 0) {
      headline = 'Scrub now';
      sub = 'Already past your alert level';
    } else {
      headline = 'Scrub in about $days day${days == 1 ? '' : 's'}';
      sub = 'Estimated next scrub';
    }
    final coverage = f.currentSignal == null
        ? 'unknown'
        : '${(f.currentSignal! * 100).toStringAsFixed(2)}%';

    return ModelOutlookCard(
      headline: headline,
      statusColor: accent,
      statusLabel: _urgencyLabel(days),
      subtitle: sub,
      progress: ModelOutlookProgress(
        label: '${f.signalLabel} vs alert level',
        valueText:
            '$coverage of ${(f.thresholds.action * 100).toStringAsFixed(1)}%',
        value: f.thresholdProgress,
      ),
      rows: [
        ModelOutlookRow('Coverage', coverage, c.water),
        ModelOutlookRow(
          'Growth',
          f.realisedRatePerDay != null
              ? '${(f.realisedRatePerDay! * 100).toStringAsFixed(1)}%/d'
              : '--',
          f.realisedRatePerDay != null && f.realisedRatePerDay! > 0
              ? c.warning
              : c.healthy,
        ),
        ModelOutlookRow('Frames', '${f.sampleCount}', c.textSecondary),
      ],
      infoNotes: [
        f.thresholds.isBaselineRelative
            ? "Alert level is set relative to this pond's own observed "
                  'baseline (${((f.thresholds.baseline ?? 0) * 100).toStringAsFixed(2)}% '
                  'coverage), because green-pixel ratio depends on how the '
                  'camera is framed.'
            : 'Alert level uses absolute coverage thresholds - not enough '
                  "camera history yet to establish this pond's own baseline.",
      ],
    );
  }

  // ------------------------------------------------------------------

  Widget _shell({required Widget child}) => OutcomeCardShell(child: child);

  Color _urgencyColor(int? days) {
    if (days == null) return AppColors.of(context).healthy;
    if (days <= 0) return AppColors.of(context).danger;
    if (days <= 3) return AppColors.of(context).warning;
    return AppColors.of(context).healthy;
  }

  String _urgencyLabel(int? days) {
    if (days == null) return 'No scrub needed';
    if (days <= 0) return 'Scrub due now';
    if (days <= 3) return 'Scrub due soon';
    return 'On track';
  }
}
