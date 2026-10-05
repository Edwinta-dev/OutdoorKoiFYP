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
    final days = f.predictedTopupDaysFromNow;
    final accent = _urgencyColor(days);

    return OutcomeCardShell(
      accent: accent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AdaptiveRow(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: AdaptiveRow(
                  children: [
                    Icon(
                      Icons.water_damage_outlined,
                      color: AppColors.of(context).info,
                      size: 18,
                    ),
                    const SizedBox(width: AppSpace.sm),
                    Expanded(
                      child: Text(
                        'Evaporation & Feed Outlook',
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
              _pill(_urgencyLabel(days), accent),
            ],
          ),
          const SizedBox(height: AppSpace.lg),

          // --- HEADLINE: the outcome the user actually came for ---
          _headline(days, accent),

          const SizedBox(height: AppSpace.lg),

          // --- Loss-to-threshold meter ---
          _lossMeter(f, accent),

          const SizedBox(height: AppSpace.lg),
          Divider(color: AppColors.of(context).outline, height: 1),
          const SizedBox(height: AppSpace.md),

          // --- Supporting metrics ---
          AdaptiveRow(
            children: [
              Expanded(
                child: _metricBox(
                  'Evaporation',
                  '${f.avgEvaporationMmPerDay.toStringAsFixed(1)} mm/d',
                  AppColors.of(context).info,
                ),
              ),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: _metricBox(
                  'Water Loss',
                  '${f.avgLossLitresPerDay.toStringAsFixed(0)} L/d',
                  AppColors.of(context).info,
                ),
              ),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: _metricBox(
                  'Feed Cap',
                  f.feedCapTodayGrams != null
                      ? '${f.feedCapTodayGrams!.toStringAsFixed(0)} g/d'
                      : '--',
                  AppColors.of(context).feeding,
                ),
              ),
            ],
          ),

          // --- Feed guidance, straight from the temperature band ---
          if (f.feedNoteToday != null && f.feedNoteToday!.isNotEmpty) ...[
            const SizedBox(height: AppSpace.md),
            AdaptiveRow(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  Icons.set_meal_outlined,
                  color: AppColors.of(context).feeding,
                  size: 15,
                ),
                const SizedBox(width: AppSpace.sm),
                Expanded(
                  child: Text(
                    f.feedNoteToday!,
                    style: AppType.style(
                      color: AppColors.of(context).textSecondary,
                      fontSize: AppType.label,
                      height: 1.4,
                    ),
                  ),
                ),
              ],
            ),
          ],

          // --- Grounding badge: does the measured TDS trend agree? ---
          const SizedBox(height: AppSpace.md),
          _groundingAdaptiveRow(f.tdsCrossCheck),

          // --- Assumption disclosure ---
          const SizedBox(height: AppSpace.md),
          Text(
            'Assumes ${f.assumedDepthM.toStringAsFixed(1)} m depth '
            '(${f.surfaceAreaM2.toStringAsFixed(1)} m² surface) on a '
            '${f.volumeLitres.toStringAsFixed(0)} L pond. '
            '${f.daysUsingRealForecast} day(s) use live NEA forecast; '
            'beyond that the last forecast day is held steady.',
            style: AppType.style(
              color: AppColors.of(context).textMuted,
              fontSize: AppType.micro,
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------

  Widget _headline(int? days, Color accent) {
    final String big;
    final String sub;
    if (days == null) {
      big = 'No top-up needed';
      sub = 'Within the forecast window';
    } else if (days == 0) {
      big = 'Top up now';
      sub = 'Already past the recommended loss threshold';
    } else {
      big = 'in $days day${days == 1 ? '' : 's'}';
      sub = 'Estimated next top-up';
    }

    return AdaptiveRow(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Icon(
          days == null ? Icons.check_circle_outline : Icons.schedule,
          color: accent,
          size: 30,
        ),
        const SizedBox(width: AppSpace.md),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                sub,
                style: AppType.style(
                  color: AppColors.of(context).textMuted,
                  fontSize: AppType.caption,
                ),
              ),
              const SizedBox(height: AppSpace.xxs),
              Text(
                big,
                style: AppType.style(
                  color: accent,
                  fontSize: AppType.metric,
                  fontWeight: FontWeight.w900,
                  letterSpacing: -0.5,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _lossMeter(EvaporationForecast f, Color accent) {
    // Progress toward the 10% action threshold, from loss already accrued
    // since the last logged top-up.
    final pct = f.startingLossPct;
    final progress = (pct / 10.0).clamp(0.0, 1.0);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AdaptiveRow(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Estimated loss since last top-up',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.micro,
              ),
            ),
            Text(
              '${pct.toStringAsFixed(1)}% of 10%',
              style: AppType.style(
                color: accent,
                fontSize: AppType.micro,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpace.sm),
        ClipRRect(
          borderRadius: BorderRadius.circular(AppRadius.small),
          child: LinearProgressIndicator(
            value: progress,
            minHeight: 6,
            backgroundColor: AppColors.of(context).text.withValues(alpha: 0.06),
            valueColor: AlwaysStoppedAnimation<Color>(accent),
          ),
        ),
        if (f.lastTopupAt == null) ...[
          const SizedBox(height: AppSpace.sm),
          Text(
            'No top-up ever logged - starting from "assumed full". Log one '
            'to anchor this estimate.',
            style: AppType.style(
              color: AppColors.of(context).textMuted,
              fontSize: AppType.micro,
            ),
          ),
        ],
      ],
    );
  }

  Widget _groundingAdaptiveRow(TdsCrossCheck cc) {
    final ok = cc.isCorroborated;
    final unknown = cc.verdict == 'insufficient_data';
    final color = unknown
        ? AppColors.of(context).textMuted
        : (ok ? AppColors.of(context).healthy : AppColors.of(context).warning);

    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpace.md,
        vertical: AppSpace.sm,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(AppRadius.control),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: AdaptiveRow(
        children: [
          Icon(
            unknown
                ? Icons.help_outline
                : (ok ? Icons.verified_outlined : Icons.info_outline),
            color: color,
            size: 14,
          ),
          const SizedBox(width: AppSpace.sm),
          Expanded(
            child: Text(
              cc.humanLabel,
              style: AppType.style(
                color: color,
                fontSize: AppType.caption,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          if (cc.observedSlope != null)
            Text(
              '${cc.observedSlope! >= 0 ? '+' : ''}'
              '${cc.observedSlope!.toStringAsFixed(2)} ppm/d',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.micro,
              ),
            ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------

  Widget _shell({required Widget child}) => OutcomeCardShell(child: child);

  Widget _pill(String text, Color color) => StatusChip(
    status: color == AppColors.of(context).danger
        ? PondStatus.danger
        : color == AppColors.of(context).warning
        ? PondStatus.warning
        : PondStatus.healthy,
    label: text,
  );

  Widget _metricBox(String label, String value, Color valueColor) => MetricTile(
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

  Color _urgencyColor(int? days) {
    if (days == null) return AppColors.of(context).healthy;
    if (days <= 0) return AppColors.of(context).danger;
    if (days <= 3) return AppColors.of(context).warning;
    return AppColors.of(context).info;
  }

  String _urgencyLabel(int? days) {
    if (days == null) return 'STABLE';
    if (days <= 0) return 'DUE NOW';
    if (days <= 3) return 'SOON';
    return 'PLANNED';
  }
}
