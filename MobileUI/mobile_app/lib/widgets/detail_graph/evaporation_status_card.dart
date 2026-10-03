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
import '../../utils/digital_twin_api.dart';

class EvaporationStatusCard extends StatefulWidget {
  final int userId;

  const EvaporationStatusCard({super.key, required this.userId});

  @override
  State<EvaporationStatusCard> createState() => _EvaporationStatusCardState();
}

class _EvaporationStatusCardState extends State<EvaporationStatusCard> {
  late Future<({EvaporationForecast? forecast, String? error})> _future;

  @override
  void initState() {
    super.initState();
    _future = DigitalTwinApi.fetchEvaporationForecastOrError(widget.userId);
  }

  void _retry() {
    setState(() {
      _future = DigitalTwinApi.fetchEvaporationForecastOrError(widget.userId);
    });
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<({EvaporationForecast? forecast, String? error})>(
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
          color: Colors.cyanAccent,
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
            Icon(Icons.water_damage_outlined, color: Colors.white38, size: 18),
            SizedBox(width: 8),
            Text(
              'Evaporation & Feed Outlook',
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

  // ------------------------------------------------------------------

  Widget _forecastCard(EvaporationForecast f) {
    final days = f.predictedTopupDaysFromNow;
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
                    Icon(
                      Icons.water_damage_outlined,
                      color: Colors.cyanAccent,
                      size: 18,
                    ),
                    SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Evaporation & Feed Outlook',
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
          const SizedBox(height: 16),

          // --- HEADLINE: the outcome the user actually came for ---
          _headline(days, accent),

          const SizedBox(height: 16),

          // --- Loss-to-threshold meter ---
          _lossMeter(f, accent),

          const SizedBox(height: 16),
          const Divider(color: Colors.white10, height: 1),
          const SizedBox(height: 12),

          // --- Supporting metrics ---
          Row(
            children: [
              Expanded(
                child: _metricBox(
                  'Evaporation',
                  '${f.avgEvaporationMmPerDay.toStringAsFixed(1)} mm/d',
                  Colors.cyanAccent,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _metricBox(
                  'Water Loss',
                  '${f.avgLossLitresPerDay.toStringAsFixed(0)} L/d',
                  Colors.cyanAccent,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _metricBox(
                  'Feed Cap',
                  f.feedCapTodayGrams != null
                      ? '${f.feedCapTodayGrams!.toStringAsFixed(0)} g/d'
                      : '--',
                  Colors.orangeAccent,
                ),
              ),
            ],
          ),

          // --- Feed guidance, straight from the temperature band ---
          if (f.feedNoteToday != null && f.feedNoteToday!.isNotEmpty) ...[
            const SizedBox(height: 12),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(
                  Icons.set_meal_outlined,
                  color: Colors.orangeAccent,
                  size: 15,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    f.feedNoteToday!,
                    style: const TextStyle(
                      color: Colors.white70,
                      fontSize: 11.5,
                      height: 1.4,
                    ),
                  ),
                ),
              ],
            ),
          ],

          // --- Grounding badge: does the measured TDS trend agree? ---
          const SizedBox(height: 12),
          _groundingRow(f.tdsCrossCheck),

          // --- Assumption disclosure ---
          const SizedBox(height: 10),
          Text(
            'Assumes ${f.assumedDepthM.toStringAsFixed(1)} m depth '
            '(${f.surfaceAreaM2.toStringAsFixed(1)} m² surface) on a '
            '${f.volumeLitres.toStringAsFixed(0)} L pond. '
            '${f.daysUsingRealForecast} day(s) use live NEA forecast; '
            'beyond that the last forecast day is held steady.',
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

    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Icon(
          days == null ? Icons.check_circle_outline : Icons.schedule,
          color: accent,
          size: 30,
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                sub,
                style: const TextStyle(color: Colors.white54, fontSize: 10.5),
              ),
              const SizedBox(height: 2),
              FittedBox(
                alignment: Alignment.centerLeft,
                child: Text(
                  big,
                  style: TextStyle(
                    color: accent,
                    fontSize: 24,
                    fontWeight: FontWeight.w900,
                    letterSpacing: -0.5,
                  ),
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
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            const Text(
              'Estimated loss since last top-up',
              style: TextStyle(color: Colors.white54, fontSize: 10),
            ),
            Text(
              '${pct.toStringAsFixed(1)}% of 10%',
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
            value: progress,
            minHeight: 6,
            backgroundColor: Colors.white.withValues(alpha: 0.06),
            valueColor: AlwaysStoppedAnimation<Color>(accent),
          ),
        ),
        if (f.lastTopupAt == null) ...[
          const SizedBox(height: 6),
          const Text(
            'No top-up ever logged - starting from "assumed full". Log one '
            'to anchor this estimate.',
            style: TextStyle(color: Colors.white24, fontSize: 9.5),
          ),
        ],
      ],
    );
  }

  Widget _groundingRow(TdsCrossCheck cc) {
    final ok = cc.isCorroborated;
    final unknown = cc.verdict == 'insufficient_data';
    final color = unknown
        ? Colors.white38
        : (ok ? Colors.greenAccent : Colors.amberAccent);

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
            unknown
                ? Icons.help_outline
                : (ok ? Icons.verified_outlined : Icons.info_outline),
            color: color,
            size: 14,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              cc.humanLabel,
              style: TextStyle(
                color: color,
                fontSize: 10.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          if (cc.observedSlope != null)
            Text(
              '${cc.observedSlope! >= 0 ? '+' : ''}'
              '${cc.observedSlope!.toStringAsFixed(2)} ppm/d',
              style: const TextStyle(color: Colors.white38, fontSize: 9.5),
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
      style: TextStyle(
        color: color,
        fontSize: 10,
        fontWeight: FontWeight.bold,
      ),
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
    return Colors.cyanAccent;
  }

  String _urgencyLabel(int? days) {
    if (days == null) return 'STABLE';
    if (days <= 0) return 'DUE NOW';
    if (days <= 3) return 'SOON';
    return 'PLANNED';
  }
}
