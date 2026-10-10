import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/dashboard/today_action_feed.dart
//
// The "Today" section at the top of the dashboard: the weather rules that
// fired for this pond (the dashboard's next_actions), most urgent first.
// A dismissed action stays hidden on this device until the date changes.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/providers.dart';
import '../../utils/digital_twin_api.dart';
import '../modals/quick_log_modals.dart';

/// Local profile key holding today's dismissals as "yyyy-mm-dd|RULE|action".
const String dismissedActionsKey = 'dismissedActions';

class TodayActionFeed extends ConsumerStatefulWidget {
  /// Null when the dashboard could not be loaded, so the feed does not
  /// claim the pond needs nothing.
  final List<PondAction>? actions;

  /// The device clock; tests pass a fixed one.
  final DateTime Function() clock;

  const TodayActionFeed({
    super.key,
    required this.actions,
    this.clock = DateTime.now,
  });

  @override
  ConsumerState<TodayActionFeed> createState() => _TodayActionFeedState();
}

class _TodayActionFeedState extends ConsumerState<TodayActionFeed> {
  Set<String> _dismissed = {};
  final Set<String> _expanded = {};

  String get _today {
    final d = widget.clock();
    return '${d.year.toString().padLeft(4, '0')}-'
        '${d.month.toString().padLeft(2, '0')}-'
        '${d.day.toString().padLeft(2, '0')}';
  }

  @override
  void initState() {
    super.initState();
    _loadDismissed();
  }

  Future<void> _loadDismissed() async {
    try {
      final profile = await ref.read(localProfileRepositoryProvider).load();
      final stored =
          (profile.values[dismissedActionsKey] as List?)?.cast<String>() ??
          const <String>[];
      if (!mounted) return;
      setState(() => _dismissed = stored.toSet());
    } catch (_) {
      // Unreadable storage only means nothing is hidden.
    }
  }

  Future<void> _dismiss(PondAction action) async {
    final prefix = '$_today|';
    // Earlier days' dismissals have expired; drop them on each write.
    final next = {
      for (final k in _dismissed)
        if (k.startsWith(prefix)) k,
      '$prefix${action.dismissKey}',
    };
    setState(() => _dismissed = next);
    try {
      await ref.read(localProfileRepositoryProvider).save({
        dismissedActionsKey: next.toList(),
      });
    } catch (_) {
      // Still hidden for this session; it may return after a restart.
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    final all = widget.actions;
    final prefix = '$_today|';
    final visible = all == null
        ? const <PondAction>[]
        : PondAction.byUrgency(
            all.where((a) => !_dismissed.contains('$prefix${a.dismissKey}')),
          );
    final hidden = all == null ? 0 : all.length - visible.length;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionHeader(
          icon: Icons.today_outlined,
          title: 'TODAY',
          color: c.text,
        ),
        if (all == null)
          _message(
            context,
            "Today's actions could not be loaded.",
            'Pull down to try again.',
          )
        else if (visible.isEmpty)
          _message(
            context,
            'The pond needs nothing today.',
            hidden > 0
                ? '$hidden dismissed ${hidden == 1 ? 'action is' : 'actions are'} '
                      'hidden until tomorrow.'
                : 'No weather rule has called for action. Rules without '
                      'enough weather data are not counted.',
          )
        else
          for (final action in visible) ...[
            _ActionCard(
              action: action,
              now: widget.clock(),
              expanded: _expanded.contains(action.dismissKey),
              onToggleWhy: () => setState(() {
                if (!_expanded.remove(action.dismissKey)) {
                  _expanded.add(action.dismissKey);
                }
              }),
              onDismiss: () => _dismiss(action),
            ),
            const SizedBox(height: AppSpace.sm),
          ],
      ],
    );
  }

  Widget _message(BuildContext context, String title, String detail) {
    final c = AppColors.of(context);
    return OutcomeCardShell(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: AppType.style(
              color: c.text,
              fontSize: AppType.body,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: AppSpace.xs),
          Text(
            detail,
            style: AppType.style(
              color: c.textMuted,
              fontSize: AppType.label,
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({
    required this.action,
    required this.now,
    required this.expanded,
    required this.onToggleWhy,
    required this.onDismiss,
  });

  final PondAction action;
  final DateTime now;
  final bool expanded;
  final VoidCallback onToggleWhy;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    final accent = action.urgencyRank <= 1 ? c.warning : c.info;
    final log = _logTarget(context, action.logEventType);
    final why = action.evidence.isEmpty
        ? action.ruleLabel
        : '${action.ruleLabel}: ${action.evidence}';

    return OutcomeCardShell(
      accent: accent,
      padding: const EdgeInsets.fromLTRB(
        AppSpace.lg,
        AppSpace.md,
        AppSpace.xs,
        AppSpace.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      action.whenText(now),
                      style: AppType.style(
                        color: accent,
                        fontSize: AppType.label,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: AppSpace.xs),
                    Text(
                      action.action,
                      style: AppType.style(
                        color: c.text,
                        fontSize: AppType.body,
                        fontWeight: FontWeight.w600,
                        height: 1.3,
                      ),
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Dismiss for today',
                icon: Icon(Icons.close, color: c.textMuted, size: 18),
                onPressed: onDismiss,
              ),
            ],
          ),
          const SizedBox(height: AppSpace.xs),
          Padding(
            padding: const EdgeInsets.only(right: AppSpace.md),
            child: InkWell(
              key: ValueKey('why-${action.rule}'),
              onTap: onToggleWhy,
              borderRadius: BorderRadius.circular(AppRadius.small),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      'Why: $why',
                      maxLines: expanded ? null : 1,
                      overflow: expanded
                          ? TextOverflow.visible
                          : TextOverflow.ellipsis,
                      style: AppType.style(
                        color: c.textSecondary,
                        fontSize: AppType.label,
                        height: 1.4,
                      ),
                    ),
                  ),
                  Icon(
                    expanded ? Icons.expand_less : Icons.expand_more,
                    color: c.textMuted,
                    size: 18,
                  ),
                ],
              ),
            ),
          ),
          if (expanded)
            Padding(
              padding: const EdgeInsets.only(
                top: AppSpace.xs,
                right: AppSpace.md,
              ),
              child: Text(
                'Rule ${action.rule}, checked against the NEA weather data '
                'the service held at the time. It is a weather rule, not a '
                'pond measurement.',
                style: AppType.style(
                  color: c.textMuted,
                  fontSize: AppType.caption,
                  height: 1.4,
                ),
              ),
            ),
          if (log != null)
            Padding(
              padding: const EdgeInsets.only(top: AppSpace.sm),
              child: OutlinedButton.icon(
                onPressed: () => showInterventionLogSheet(
                  context,
                  eventType: action.logEventType!,
                  title: log.title,
                  accentColor: log.color,
                ),
                icon: Icon(log.icon, size: 18),
                label: Text(log.button),
              ),
            ),
        ],
      ),
    );
  }

  /// The log sheet each loggable event opens; titles and colours match the
  /// quick-log selector.
  static ({String title, String button, IconData icon, Color color})?
  _logTarget(BuildContext context, String? eventType) {
    final c = AppColors.of(context);
    return switch (eventType) {
      'WATER_CHANGE' => (
        title: 'Water Change',
        button: 'Log water change',
        icon: Icons.water_drop,
        color: c.info,
      ),
      'FEEDING' => (
        title: 'Feeding Session',
        button: 'Log feed',
        icon: Icons.set_meal_outlined,
        color: c.feeding,
      ),
      _ => null,
    };
  }
}
