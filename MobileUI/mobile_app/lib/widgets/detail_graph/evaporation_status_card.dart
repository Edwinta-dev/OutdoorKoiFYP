import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/detail_graph/evaporation_status_card.dart
//
// Lookahead card for the Temperature & Feed detail graph screen.
// Answers "when do I next need to top up?" and "should I be feeding
// less?" from GET /forecast/evaporation/<user_id>.
//
// Deliberately mirrors WaterBufferStatusCard's structure (same shell,
// same loading/unavailable/content states, same 0xFF131B2A surface and
// 20px radius) so the three detail screens read as one system.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/providers.dart';
import '../../utils/digital_twin_api.dart';
import 'model_outlook_card.dart';

class EvaporationStatusCard extends ConsumerStatefulWidget {
  final int userId;

  const EvaporationStatusCard({super.key, required this.userId});

  @override
  ConsumerState<EvaporationStatusCard> createState() =>
      _EvaporationStatusCardState();
}

class _EvaporationStatusCardState extends ConsumerState<EvaporationStatusCard> {
  void _retry() => ref.invalidate(evaporationForecastProvider(widget.userId));

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<({EvaporationForecast? forecast, String? error})>(
      future: ref.watch(evaporationForecastProvider(widget.userId).future),
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
          color: AppColors.of(context).info,
        ),
      ),
    ),
  );

  Widget _unavailableState(String reason) => PondErrorState(
    title: 'Evaporation & Feed Outlook',
    message: reason,
    onRetry: _retry,
  );

  // ------------------------------------------------------------------

  Widget _forecastCard(EvaporationForecast f) {
    final c = AppColors.of(context);
    final days = f.predictedTopupDaysFromNow;
    final accent = _urgencyColor(days);
    final String headline;
    final String sub;
    if (days == null) {
      headline = 'No top-up needed';
      sub = 'Within the forecast window';
    } else if (days == 0) {
      headline = 'Top up now';
      sub = 'Already past the recommended loss threshold';
    } else {
      headline = 'Next top-up in $days day${days == 1 ? '' : 's'}';
      sub = 'Estimated next top-up';
    }
    final pct = f.startingLossPct;
    final cc = f.tdsCrossCheck;
    final unknown = cc.verdict == 'insufficient_data';
    final slope = cc.observedSlope == null
        ? ''
        : ' (${cc.observedSlope! >= 0 ? '+' : ''}'
              '${cc.observedSlope!.toStringAsFixed(2)} ppm/d)';

    return ModelOutlookCard(
      headline: headline,
      statusColor: accent,
      statusLabel: _urgencyLabel(days),
      subtitle: sub,
      progress: ModelOutlookProgress(
        label: 'Estimated loss since last top-up',
        valueText: '${pct.toStringAsFixed(1)}% of 10%',
        value: (pct / 10.0).clamp(0.0, 1.0),
        footnote: f.lastTopupAt == null
            ? 'No top-up ever logged - starting from "assumed full". Log one '
                  'to anchor this estimate.'
            : null,
      ),
      rows: [
        ModelOutlookRow(
          'Evaporation',
          '${f.avgEvaporationMmPerDay.toStringAsFixed(1)} mm/d',
          c.info,
        ),
        ModelOutlookRow(
          'Water loss',
          '${f.avgLossLitresPerDay.toStringAsFixed(0)} L/d',
          c.info,
        ),
        ModelOutlookRow(
          'Feed cap',
          f.feedCapTodayGrams != null
              ? '${f.feedCapTodayGrams!.toStringAsFixed(0)} g/d'
              : '--',
          c.feeding,
        ),
      ],
      alerts: [
        if (!unknown && !cc.isCorroborated)
          ModelOutlookNote(
            '${cc.humanLabel}$slope',
            icon: Icons.warning_amber_rounded,
            color: c.warning,
          ),
      ],
      infoNotes: [
        if (unknown || cc.isCorroborated) '${cc.humanLabel}$slope',
        if (f.feedNoteToday != null && f.feedNoteToday!.isNotEmpty)
          f.feedNoteToday!,
        'Assumes ${f.assumedDepthM.toStringAsFixed(1)} m depth '
            '(${f.surfaceAreaM2.toStringAsFixed(1)} m² surface) on a '
            '${f.volumeLitres.toStringAsFixed(0)} L pond. '
            '${f.daysUsingRealForecast} day(s) use live NEA forecast; '
            'beyond that the last forecast day is held steady.',
      ],
    );
  }

  // ------------------------------------------------------------------

  Widget _shell({required Widget child}) => OutcomeCardShell(child: child);

  Color _urgencyColor(int? days) {
    if (days == null) return AppColors.of(context).healthy;
    if (days <= 0) return AppColors.of(context).danger;
    if (days <= 3) return AppColors.of(context).warning;
    return AppColors.of(context).info;
  }

  String _urgencyLabel(int? days) {
    if (days == null) return 'No top-up needed';
    if (days <= 0) return 'Top-up due now';
    if (days <= 3) return 'Top-up due soon';
    return 'Top-up planned';
  }
}
