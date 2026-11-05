// lib/utils/pond_action.dart
//
// A fired weather rule from the dashboard's next actions, parsed by the
// client in digital_twin_api.dart (which re-exports this file).

import 'package:flutter/material.dart';

/// One fired weather rule from the dashboard's `next_actions`, as built by
/// Backend/koi/models/ladder.py::_action: {rule, lead_time, action,
/// evidence}. The service sends no urgency, so [urgencyRank] orders the
/// known rules by how soon the owner has to act.
class PondAction {
  final String rule;
  final String leadTime;
  final String action;
  final String evidence;

  const PondAction({
    required this.rule,
    required this.leadTime,
    required this.action,
    required this.evidence,
  });

  factory PondAction.fromJson(Map<String, dynamic> json) => PondAction(
    rule: '${json['rule'] ?? ''}'.toUpperCase(),
    leadTime: '${json['lead_time'] ?? ''}',
    action: '${json['action'] ?? ''}',
    evidence: '${json['evidence'] ?? ''}',
  );

  /// Most urgent first: rain within 2 hours, then yesterday's heavy rain,
  /// then the hot stretch, then the dry window. Unknown rules come last.
  static const List<String> urgencyOrder = [
    'NOWCAST',
    'REACT',
    'PREEMPT',
    'WINDOW',
  ];

  int get urgencyRank {
    final i = urgencyOrder.indexOf(rule);
    return i < 0 ? urgencyOrder.length : i;
  }

  /// [actions] most urgent first; equal ranks keep the service's order.
  static List<PondAction> byUrgency(Iterable<PondAction> actions) {
    final indexed = actions.toList().asMap().entries.toList()
      ..sort((a, b) {
        final byRank = a.value.urgencyRank.compareTo(b.value.urgencyRank);
        return byRank != 0 ? byRank : a.key.compareTo(b.key);
      });
    return [for (final e in indexed) e.value];
  }

  /// The rule in pond-keeping words.
  String get ruleLabel => switch (rule) {
    'NOWCAST' => 'Rain in the 2-hour forecast',
    'REACT' => 'Heavy rain yesterday',
    'PREEMPT' => 'Hot stretch ahead',
    'WINDOW' => 'Dry weather window',
    _ => rule.isEmpty ? 'Weather rule' : rule,
  };

  /// Short brief used on the collapsed Today card.
  String get headline => switch (rule) {
    'NOWCAST' => 'Rain soon - secure feed and pause work',
    'REACT' => 'Heavy rain yesterday - check the pond',
    'PREEMPT' => 'Hot stretch ahead - reduce feed',
    'WINDOW' => 'Dry weather window - plan pond work',
    _ => action.isEmpty ? ruleLabel : action,
  };

  IconData get ruleIcon => switch (rule) {
    'NOWCAST' || 'REACT' => Icons.cloud,
    'PREEMPT' => Icons.thermostat,
    'WINDOW' => Icons.wb_sunny_outlined,
    _ => Icons.rule_outlined,
  };

  /// When the action applies, in plain words, as seen at [now].
  String whenText(DateTime now) {
    switch (leadTime) {
      case 'within 2 hours':
        final until = now.add(const Duration(hours: 2));
        final minute = until.minute - until.minute % 15;
        return 'Now, until about ${_hhmm(until.hour, minute)}';
      case 'next morning':
        return now.hour < 12
            ? "This morning, after yesterday's rain"
            : "Today, after yesterday's rain";
      case 'today':
        return 'Today, while it stays dry';
      case 'ahead of the hot stretch':
        return 'Before the hot days ahead';
    }
    if (leadTime.isEmpty) return 'Today';
    return leadTime[0].toUpperCase() + leadTime.substring(1);
  }

  /// The intervention event that carries out this action, or null when
  /// the action has nothing to log (a check, or pausing work).
  String? get logEventType => switch (rule) {
    'WINDOW' => 'WATER_CHANGE',
    'PREEMPT' => 'FEEDING',
    _ => null,
  };

  /// Identifies this action within one day, for dismissing it.
  String get dismissKey => '$rule|$action';

  static String _hhmm(int h, int m) =>
      '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}';
}
