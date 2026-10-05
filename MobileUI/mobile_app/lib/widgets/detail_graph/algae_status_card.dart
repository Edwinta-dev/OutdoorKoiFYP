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
// ratio, stored in imageTable. The card makes that visible - it shows the
// latest camera frame, says whether the growth rate was fitted from that
// history or fell back to a literature default, and surfaces how many
// frames were rejected as obstructed. A forecast the user can see the
// evidence for is one they can calibrate their trust against.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/providers.dart';
import '../../utils/digital_twin_api.dart';

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
    final days = f.predictedScrubDaysFromNow;
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
                      Icons.grass_outlined,
                      color: AppColors.of(context).water,
                      size: 18,
                    ),
                    const SizedBox(width: AppSpace.sm),
                    Expanded(
                      child: Text(
                        'Algae Growth Outlook',
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

          // --- Camera frame + headline, side by side. The thumbnail is
          //     the whole point: this forecast has a photo behind it.
          AdaptiveRow(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _cameraThumbnail(f),
              const SizedBox(width: AppSpace.lg),
              Expanded(child: _headline(days, accent)),
            ],
          ),

          const SizedBox(height: AppSpace.lg),

          // --- Coverage meter against this pond's own threshold ---
          _coverageMeter(f, accent),

          const SizedBox(height: AppSpace.lg),
          Divider(color: AppColors.of(context).outline, height: 1),
          const SizedBox(height: AppSpace.md),

          AdaptiveRow(
            children: [
              Expanded(
                child: _metricBox(
                  'Coverage',
                  '${(f.currentGreenRatio * 100).toStringAsFixed(2)}%',
                  AppColors.of(context).water,
                ),
              ),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: _metricBox(
                  'Growth',
                  f.realisedRatePerDay != null
                      ? '${(f.realisedRatePerDay! * 100).toStringAsFixed(1)}%/d'
                      : '--',
                  f.realisedRatePerDay != null && f.realisedRatePerDay! > 0
                      ? AppColors.of(context).warning
                      : AppColors.of(context).healthy,
                ),
              ),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: _metricBox(
                  'Frames',
                  '${f.sampleCount}',
                  AppColors.of(context).textSecondary,
                ),
              ),
            ],
          ),

          // --- "Scrubbing today buys you N days" ---
          if (f.scrubDaysBought != null) ...[
            const SizedBox(height: AppSpace.md),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpace.md,
                vertical: AppSpace.sm,
              ),
              decoration: BoxDecoration(
                color: AppColors.of(context).water.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(AppRadius.control),
                border: Border.all(
                  color: AppColors.of(context).water.withValues(alpha: 0.35),
                ),
              ),
              child: AdaptiveRow(
                children: [
                  Icon(
                    Icons.cleaning_services_outlined,
                    color: AppColors.of(context).water,
                    size: 15,
                  ),
                  const SizedBox(width: AppSpace.sm),
                  Expanded(
                    child: Text(
                      'Scrubbing today buys about ${f.scrubDaysBought} '
                      'clear day${f.scrubDaysBought == 1 ? '' : 's'}',
                      style: AppType.style(
                        color: AppColors.of(context).water,
                        fontSize: AppType.caption,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],

          // --- Grounding provenance ---
          const SizedBox(height: AppSpace.md),
          _groundingAdaptiveRow(f),

          // --- Threshold-mode disclosure ---
          const SizedBox(height: AppSpace.md),
          Text(
            f.thresholds.isBaselineRelative
                ? 'Alert level is set relative to this pond\'s own observed '
                      'baseline (${((f.thresholds.baseline ?? 0) * 100).toStringAsFixed(2)}% '
                      'coverage), because green-pixel ratio depends on how the '
                      'camera is framed.'
                : 'Alert level uses absolute coverage thresholds - not enough '
                      'camera history yet to establish this pond\'s own baseline.',
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

  Widget _cameraThumbnail(AlgaeForecast f) {
    final double size = 72;
    return ClipRRect(
      borderRadius: BorderRadius.circular(AppRadius.control),
      child: SizedBox(
        width: size,
        height: size,
        child: f.latestImageUrl == null
            ? Container(
                color: AppColors.of(context).text.withValues(alpha: 0.04),
                child: Icon(
                  Icons.photo_camera_outlined,
                  color: AppColors.of(context).outline,
                  size: 24,
                ),
              )
            : Image.network(
                f.latestImageUrl!,
                fit: BoxFit.cover,
                // The pond camera is on the LAN and the bucket is public,
                // but a phone on mobile data may not reach either - fail
                // to a placeholder rather than a broken-image glyph.
                errorBuilder: (_, _, _) => Container(
                  color: AppColors.of(context).text.withValues(alpha: 0.04),
                  child: Icon(
                    Icons.broken_image_outlined,
                    color: AppColors.of(context).outline,
                    size: 22,
                  ),
                ),
                loadingBuilder: (context, child, progress) {
                  if (progress == null) return child;
                  return Container(
                    color: AppColors.of(context).text.withValues(alpha: 0.04),
                    child: Center(
                      child: SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: AppColors.of(context).water,
                        ),
                      ),
                    ),
                  );
                },
              ),
      ),
    );
  }

  Widget _headline(int? days, Color accent) {
    final String big;
    final String sub;
    if (days == null) {
      big = 'No scrub needed';
      sub = 'Within the forecast window';
    } else if (days == 0) {
      big = 'Scrub now';
      sub = 'Already past your alert level';
    } else {
      big = 'in $days day${days == 1 ? '' : 's'}';
      sub = 'Estimated next scrub';
    }

    return Column(
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
            fontSize: AppType.heading,
            fontWeight: FontWeight.w900,
            letterSpacing: -0.5,
          ),
        ),
      ],
    );
  }

  Widget _coverageMeter(AlgaeForecast f, Color accent) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AdaptiveRow(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Green coverage vs alert level',
              style: AppType.style(
                color: AppColors.of(context).textMuted,
                fontSize: AppType.micro,
              ),
            ),
            Text(
              '${(f.currentGreenRatio * 100).toStringAsFixed(2)}% '
              'of ${(f.thresholds.action * 100).toStringAsFixed(1)}%',
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
            value: f.thresholdProgress,
            minHeight: 6,
            backgroundColor: AppColors.of(context).text.withValues(alpha: 0.06),
            valueColor: AlwaysStoppedAnimation<Color>(accent),
          ),
        ),
      ],
    );
  }

  Widget _groundingAdaptiveRow(AlgaeForecast f) {
    final grounded = f.isCameraGrounded;
    final color = grounded
        ? AppColors.of(context).healthy
        : (f.rateSource == 'measured_declining'
              ? AppColors.of(context).info
              : AppColors.of(context).textMuted);

    final String label;
    switch (f.rateSource) {
      case 'fitted_from_camera':
        label = 'Growth rate measured from ${f.sampleCount} pond camera frames';
        break;
      case 'measured_declining':
        label = 'Camera shows algae currently receding';
        break;
      default:
        label = 'Using default growth rate - not enough camera history yet';
    }

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
            grounded ? Icons.verified_outlined : Icons.photo_camera_outlined,
            color: color,
            size: 14,
          ),
          const SizedBox(width: AppSpace.sm),
          Expanded(
            child: Text(
              label,
              style: AppType.style(
                color: color,
                fontSize: AppType.caption,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          if (f.obstructedSampleCount > 0)
            Tooltip(
              message:
                  '${f.obstructedSampleCount} frame(s) rejected as obstructed',
              child: AdaptiveRow(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.visibility_off_outlined,
                    color: AppColors.of(context).textMuted,
                    size: 12,
                  ),
                  const SizedBox(width: AppSpace.xs),
                  Text(
                    '${f.obstructedSampleCount}',
                    style: AppType.style(
                      color: AppColors.of(context).textMuted,
                      fontSize: AppType.micro,
                    ),
                  ),
                ],
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
    return AppColors.of(context).water;
  }

  String _urgencyLabel(int? days) {
    if (days == null) return 'CLEAR';
    if (days <= 0) return 'DUE NOW';
    if (days <= 3) return 'SOON';
    return 'PLANNED';
  }
}
