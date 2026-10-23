import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/dashboard/today_action_feed.dart
//
// The "Today" section at the top of the dashboard: the weather rules that
// fired for this pond (the dashboard's next_actions), most urgent first.
// A dismissed action stays hidden on this device until the date changes.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/providers.dart';
import '../../utils/digital_twin_api.dart';
import '../modals/quick_log_modals.dart';

/// Local profile key holding today's dismissals as "yyyy-mm-dd|RULE|action".
const String dismissedActionsKey = 'dismissedActions';

/// Local profile key holding the first-seen REACT action as JSON, so a heavy
/// rain alert outlives the server's one-day window until it is dismissed.
/// An empty string means none is kept.
const String retainedReactKey = 'retainedReact';

/// How many days after its rain date a kept REACT alert is still shown.
const int reactRetentionDays = 7;

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
  PondAction? _retained;
  DateTime? _retainedSeen;
  bool _loaded = false;
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

  @override
  void didUpdateWidget(TodayActionFeed old) {
    super.didUpdateWidget(old);
    if (_loaded) _syncRetained();
  }

  static DateTime _day(DateTime d) => DateTime(d.year, d.month, d.day);

  bool _expired(PondAction a, DateTime? seen) {
    final anchor = a.rainDate ?? seen;
    if (anchor == null) return true;
    final n = widget.clock();
    final age = DateTime.utc(
      n.year,
      n.month,
      n.day,
    ).difference(DateTime.utc(anchor.year, anchor.month, anchor.day)).inDays;
    return age > reactRetentionDays;
  }

  /// Keeps the server's REACT as the retained alert (unless dismissed
  /// today or already kept) and drops a retained one that has expired.
  void _syncRetained() {
    final server = widget.actions?.where((a) => a.rule == 'REACT');
    final fromServer = (server == null || server.isEmpty) ? null : server.first;
    if (fromServer != null &&
        !_dismissed.contains('$_today|${fromServer.dismissKey}') &&
        (_retained == null || _retained!.evidence != fromServer.evidence)) {
      _retained = fromServer;
      _retainedSeen = _day(widget.clock());
      _persistRetained();
    } else if (_retained != null && _expired(_retained!, _retainedSeen)) {
      _retained = null;
      _retainedSeen = null;
      _persistRetained();
    }
  }

  Future<void> _persistRetained() async {
    final a = _retained;
    final value = a == null
        ? ''
        : jsonEncode({
            'rule': a.rule,
            'lead_time': a.leadTime,
            'action': a.action,
            'evidence': a.evidence,
            'seen': _retainedSeen?.toIso8601String(),
          });
    try {
      await ref.read(localProfileRepositoryProvider).save({
        retainedReactKey: value,
      });
    } catch (_) {
      // Kept in memory for this session only.
    }
  }

  Future<void> _loadDismissed() async {
    try {
      final profile = await ref.read(localProfileRepositoryProvider).load();
      final stored =
          (profile.values[dismissedActionsKey] as List?)?.cast<String>() ??
          const <String>[];
      final raw = profile.values[retainedReactKey];
      PondAction? kept;
      DateTime? seen;
      if (raw is String && raw.isNotEmpty) {
        final m = jsonDecode(raw) as Map<String, dynamic>;
        kept = PondAction.fromJson(m);
        seen = DateTime.tryParse('${m['seen'] ?? ''}');
      }
      if (!mounted) return;
      setState(() {
        _dismissed = stored.toSet();
        _retained = kept;
        _retainedSeen = seen;
      });
    } catch (_) {
      // Unreadable storage only means nothing is hidden or kept.
    }
    if (!mounted) return;
    _loaded = true;
    setState(_syncRetained);
  }

  Future<void> _dismiss(PondAction action) async {
    final prefix = '$_today|';
    // Earlier days' dismissals have expired; drop them on each write.
    final next = {
      for (final k in _dismissed)
        if (k.startsWith(prefix)) k,
      '$prefix${action.dismissKey}',
    };
    final clearReact = action.rule == 'REACT';
    setState(() {
      _dismissed = next;
      if (clearReact) {
        _retained = null;
        _retainedSeen = null;
      }
    });
    try {
      await ref.read(localProfileRepositoryProvider).save({
        dismissedActionsKey: next.toList(),
        if (clearReact) retainedReactKey: '',
      });
    } catch (_) {
      // Still hidden for this session; it may return after a restart.
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    final served = widget.actions;
    // A kept REACT alert stands in on later days while the server has
    // stopped returning it.
    final keep = _retained;
    final all = served == null
        ? null
        : [
            ...served,
            if (keep != null &&
                !_expired(keep, _retainedSeen) &&
                !served.any((a) => a.rule == 'REACT'))
              keep,
          ];
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
            hidden > 0
                ? 'All actions dismissed.'
                : 'Weather rules could not be checked.',
            hidden > 0
                ? '$hidden dismissed ${hidden == 1 ? 'action is' : 'actions are'} '
                      'hidden until tomorrow.'
                : 'PREEMPT and WINDOW do not have enough weather data to be assessed.',
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

  /// Rain alerts are blue, heat amber, a dry window green.
  static Color _accent(AppColors c, String rule) => switch (rule) {
    'NOWCAST' || 'REACT' => c.info,
    'PREEMPT' => c.warning,
    'WINDOW' => c.healthy,
    _ => c.textSecondary,
  };

  @override
  Widget build(BuildContext context) {
    final c = AppColors.of(context);
    final accent = _accent(c, action.rule);
    final log = _logTarget(context, action.logEventType);

    return OutcomeCardShell(
      accent: accent,
      padding: const EdgeInsets.fromLTRB(
        AppSpace.md,
        AppSpace.sm,
        AppSpace.xs,
        AppSpace.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            key: ValueKey('expand-${action.rule}'),
            onTap: onToggleWhy,
            borderRadius: BorderRadius.circular(AppRadius.small),
            child: Row(
              children: [
                Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: accent.withValues(alpha: 0.16),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(action.ruleIcon, color: accent, size: 20),
                ),
                const SizedBox(width: AppSpace.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        action.ruleTag,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppType.style(
                          color: accent,
                          fontSize: AppType.caption,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 0.6,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        action.headline,
                        style: AppType.style(
                          color: c.text,
                          fontSize: AppType.body,
                          fontWeight: FontWeight.w600,
                          height: 1.2,
                        ),
                      ),
                    ],
                  ),
                ),
                Icon(
                  expanded ? Icons.expand_less : Icons.expand_more,
                  semanticLabel: expanded ? 'Hide details' : 'Show details',
                  color: c.textMuted,
                  size: 22,
                ),
                IconButton(
                  tooltip: 'Dismiss for today',
                  icon: Icon(Icons.close, color: c.textMuted, size: 18),
                  onPressed: onDismiss,
                ),
              ],
            ),
          ),
          if (expanded)
            Padding(
              padding: const EdgeInsets.only(
                top: AppSpace.sm,
                right: AppSpace.md,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (action.advice.isNotEmpty)
                    Text(
                      action.advice,
                      style: AppType.style(
                        color: c.text,
                        fontSize: AppType.label,
                        fontWeight: FontWeight.w700,
                        height: 1.3,
                      ),
                    ),
                  const SizedBox(height: AppSpace.xs),
                  _evidenceBullet(context, action.evidenceText(now)),
                  Align(
                    alignment: Alignment.centerRight,
                    child: IconButton(
                      key: ValueKey('info-${action.rule}'),
                      tooltip: 'About this rule',
                      visualDensity: VisualDensity.compact,
                      icon: Icon(
                        Icons.info_outline,
                        color: c.textMuted,
                        size: 18,
                      ),
                      onPressed: () => _showInfo(context),
                    ),
                  ),
                ],
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

  void _showInfo(BuildContext context) {
    showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (ctx) {
        final c = AppColors.of(ctx);
        return Dialog(
          backgroundColor: c.surfaceRaised,
          constraints: const BoxConstraints(maxWidth: 360),
          clipBehavior: Clip.antiAlias,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.control),
          ),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 360),
            child: InkWell(
              key: ValueKey('info-popup-${action.rule}'),
              onTap: () => Navigator.of(ctx).pop(),
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(AppSpace.xxl),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      action.infoText,
                      style: AppType.style(
                        color: c.text,
                        fontSize: AppType.body,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: AppSpace.lg),
                    Text(
                      'Tap anywhere to close',
                      style: AppType.style(
                        color: c.textMuted,
                        fontSize: AppType.caption,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _evidenceBullet(BuildContext context, String text) => Padding(
    key: ValueKey('evidence-${action.rule}'),
    padding: const EdgeInsets.only(left: AppSpace.lg),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('\u2022  ', style: _bulletStyle(context)),
        Expanded(child: Text(text, style: _bulletStyle(context))),
      ],
    ),
  );

  TextStyle _bulletStyle(BuildContext context) => AppType.style(
    color: AppColors.of(context).textSecondary,
    fontSize: AppType.caption,
    height: 1.35,
  );

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
