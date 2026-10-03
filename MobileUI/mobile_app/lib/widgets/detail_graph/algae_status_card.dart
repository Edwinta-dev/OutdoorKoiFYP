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
import '../../utils/digital_twin_api.dart';

class AlgaeStatusCard extends StatefulWidget {
  final int userId;

  const AlgaeStatusCard({super.key, required this.userId});

  @override
  State<AlgaeStatusCard> createState() => _AlgaeStatusCardState();
}

class _AlgaeStatusCardState extends State<AlgaeStatusCard> {
  late Future<({AlgaeForecast? forecast, String? error})> _future;

  @override
  void initState() {
    super.initState();
    _future = DigitalTwinApi.fetchAlgaeForecastOrError(widget.userId);
  }

  void _retry() {
    setState(() {
      _future = DigitalTwinApi.fetchAlgaeForecastOrError(widget.userId);
    });
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<({AlgaeForecast? forecast, String? error})>(
      future: _future,
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

  Widget _loadingState() => const Padding(
    padding: EdgeInsets.symmetric(vertical: 24),
    child: Center(
      child: SizedBox(
        width: 22,
        height: 22,
        child: CircularProgressIndicator(
          strokeWidth: 2.4,
          color: Colors.tealAccent,
        ),
      ),
    ),
  );

  Widget _unavailableState(String reason) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 16),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Row(
          children: [
            Icon(Icons.grass_outlined, color: Colors.white38, size: 18),
            SizedBox(width: 8),
            Text(
              'Algae Growth Outlook',
              style: TextStyle(
                color: Colors.white70,
                fontWeight: FontWeight.bold,
                fontSize: 13,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Text(
          reason,
          style: const TextStyle(
            color: Colors.white38,
            fontSize: 11,
            height: 1.4,
          ),
        ),
        const SizedBox(height: 10),
        TextButton.icon(
          onPressed: _retry,
          style: TextButton.styleFrom(
            foregroundColor: Colors.tealAccent,
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

  // ------------------------------------------------------------------

  Widget _forecastCard(AlgaeForecast f) {
    final days = f.predictedScrubDaysFromNow;
    final accent = _urgencyColor(days);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: accent.withValues(alpha: 0.3)),
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
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Expanded(
                child: Row(
                  children: [
                    Icon(Icons.grass_outlined, color: Colors.tealAccent, size: 18),
                    SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Algae Growth Outlook',
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
              _pill(_urgencyLabel(days), accent),
            ],
          ),
          const SizedBox(height: 14),

          // --- Camera frame + headline, side by side. The thumbnail is
          //     the whole point: this forecast has a photo behind it.
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _cameraThumbnail(f),
              const SizedBox(width: 14),
              Expanded(child: _headline(days, accent)),
            ],
          ),

          const SizedBox(height: 14),

          // --- Coverage meter against this pond's own threshold ---
          _coverageMeter(f, accent),

          const SizedBox(height: 14),
          const Divider(color: Colors.white10, height: 1),
          const SizedBox(height: 12),

          Row(
            children: [
              Expanded(
                child: _metricBox(
                  'Coverage',
                  '${(f.currentGreenRatio * 100).toStringAsFixed(2)}%',
                  Colors.tealAccent,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _metricBox(
                  'Growth',
                  f.realisedRatePerDay != null
                      ? '${(f.realisedRatePerDay! * 100).toStringAsFixed(1)}%/d'
                      : '--',
                  f.realisedRatePerDay != null && f.realisedRatePerDay! > 0
                      ? Colors.amberAccent
                      : Colors.greenAccent,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _metricBox(
                  'Frames',
                  '${f.sampleCount}',
                  Colors.white70,
                ),
              ),
            ],
          ),

          // --- "Scrubbing today buys you N days" ---
          if (f.scrubDaysBought != null) ...[
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                color: Colors.tealAccent.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: Colors.tealAccent.withValues(alpha: 0.35)),
              ),
              child: Row(
                children: [
                  const Icon(Icons.cleaning_services_outlined,
                      color: Colors.tealAccent, size: 15),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Scrubbing today buys about ${f.scrubDaysBought} '
                      'clear day${f.scrubDaysBought == 1 ? '' : 's'}',
                      style: const TextStyle(
                        color: Colors.tealAccent,
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],

          // --- Grounding provenance ---
          const SizedBox(height: 12),
          _groundingRow(f),

          // --- Threshold-mode disclosure ---
          const SizedBox(height: 10),
          Text(
            f.thresholds.isBaselineRelative
                ? 'Alert level is set relative to this pond\'s own observed '
                      'baseline (${((f.thresholds.baseline ?? 0) * 100).toStringAsFixed(2)}% '
                      'coverage), because green-pixel ratio depends on how the '
                      'camera is framed.'
                : 'Alert level uses absolute coverage thresholds - not enough '
                      'camera history yet to establish this pond\'s own baseline.',
            style: const TextStyle(
              color: Colors.white24,
              fontSize: 9.5,
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------

  Widget _cameraThumbnail(AlgaeForecast f) {
    const double size = 72;
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: SizedBox(
        width: size,
        height: size,
        child: f.latestImageUrl == null
            ? Container(
                color: Colors.white.withValues(alpha: 0.04),
                child: const Icon(
                  Icons.photo_camera_outlined,
                  color: Colors.white24,
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
                  color: Colors.white.withValues(alpha: 0.04),
                  child: const Icon(
                    Icons.broken_image_outlined,
                    color: Colors.white24,
                    size: 22,
                  ),
                ),
                loadingBuilder: (context, child, progress) {
                  if (progress == null) return child;
                  return Container(
                    color: Colors.white.withValues(alpha: 0.04),
                    child: const Center(
                      child: SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.tealAccent,
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
        Text(sub, style: const TextStyle(color: Colors.white54, fontSize: 10.5)),
        const SizedBox(height: 2),
        FittedBox(
          alignment: Alignment.centerLeft,
          child: Text(
            big,
            style: TextStyle(
              color: accent,
              fontSize: 22,
              fontWeight: FontWeight.w900,
              letterSpacing: -0.5,
            ),
          ),
        ),
      ],
    );
  }

  Widget _coverageMeter(AlgaeForecast f, Color accent) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text(
              'Green coverage vs alert level',
              style: TextStyle(color: Colors.white54, fontSize: 10),
            ),
            Text(
              '${(f.currentGreenRatio * 100).toStringAsFixed(2)}% '
              'of ${(f.thresholds.action * 100).toStringAsFixed(1)}%',
              style: TextStyle(
                color: accent,
                fontSize: 10,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: LinearProgressIndicator(
            value: f.thresholdProgress,
            minHeight: 6,
            backgroundColor: Colors.white.withValues(alpha: 0.06),
            valueColor: AlwaysStoppedAnimation<Color>(accent),
          ),
        ),
      ],
    );
  }

  Widget _groundingRow(AlgaeForecast f) {
    final grounded = f.isCameraGrounded;
    final color = grounded
        ? Colors.greenAccent
        : (f.rateSource == 'measured_declining'
              ? Colors.cyanAccent
              : Colors.white38);

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
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          Icon(
            grounded ? Icons.verified_outlined : Icons.photo_camera_outlined,
            color: color,
            size: 14,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              label,
              style: TextStyle(
                color: color,
                fontSize: 10.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          if (f.obstructedSampleCount > 0)
            Tooltip(
              message:
                  '${f.obstructedSampleCount} frame(s) rejected as obstructed',
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.visibility_off_outlined,
                      color: Colors.white38, size: 12),
                  const SizedBox(width: 3),
                  Text(
                    '${f.obstructedSampleCount}',
                    style: const TextStyle(color: Colors.white38, fontSize: 9.5),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------

  Widget _shell({required Widget child}) => Container(
    width: double.infinity,
    padding: const EdgeInsets.symmetric(horizontal: 16),
    decoration: BoxDecoration(
      color: const Color(0xFF131B2A),
      borderRadius: BorderRadius.circular(20),
      border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
    ),
    child: child,
  );

  Widget _pill(String text, Color color) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.15),
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: color.withValues(alpha: 0.5)),
    ),
    child: Text(
      text,
      style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.bold),
    ),
  );

  Widget _metricBox(String label, String value, Color valueColor) => Container(
    padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 8),
    decoration: BoxDecoration(
      color: Colors.white.withValues(alpha: 0.03),
      borderRadius: BorderRadius.circular(10),
      border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
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

  Color _urgencyColor(int? days) {
    if (days == null) return Colors.greenAccent;
    if (days <= 0) return Colors.redAccent;
    if (days <= 3) return Colors.amberAccent;
    return Colors.tealAccent;
  }

  String _urgencyLabel(int? days) {
    if (days == null) return 'CLEAR';
    if (days <= 0) return 'DUE NOW';
    if (days <= 3) return 'SOON';
    return 'PLANNED';
  }
}
