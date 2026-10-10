// The Today action feed: PondAction parsing and ordering, and the feed's
// list, empty, unavailable, dismiss, "why" and log-button behaviour.
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/data/local_profile_repository.dart';
import 'package:mobile_app/data/providers.dart';
import 'package:mobile_app/screens/dashboard_view.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/widgets/dashboard/today_action_feed.dart';
import 'package:mobile_app/widgets/modals/quick_log_modals.dart';
import 'package:mobile_app/widgets/shared/pond_widgets.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

/// The four rules as Backend/koi/models/ladder.py words them, in the
/// service's order (REACT, PREEMPT, WINDOW, NOWCAST).
const List<Map<String, dynamic>> ladderActions = [
  {
    'rule': 'REACT',
    'lead_time': 'next morning',
    'action':
        'Check the pond after yesterday\'s heavy rain; verify water level '
        'and fish behaviour.',
    'evidence': '62.4 mm at station S24 on 2026-08-11.',
  },
  {
    'rule': 'PREEMPT',
    'lead_time': 'ahead of the hot stretch',
    'action':
        'Reduce the feed ration and raise aeration before the hot '
        'stretch.',
    'evidence': 'Measured Tmax 34.1 °C and outlook high 34.0 °C.',
  },
  {
    'rule': 'WINDOW',
    'lead_time': 'today',
    'action':
        'Schedule a water change or scrub during this dry weather '
        'window.',
    'evidence': 'Pooled forecast rank 0.050 is in the calibrated driest band.',
  },
  {
    'rule': 'NOWCAST',
    'lead_time': 'within 2 hours',
    'action':
        'Rain is forecast soon; secure exposed feed and pause outdoor pond '
        'work.',
    'evidence': 'Thundery Showers',
  },
];

List<PondAction> parsed([List<Map<String, dynamic>> raw = ladderActions]) =>
    raw.map(PondAction.fromJson).toList();

DateTime clock() => Fixtures.now; // 2026-08-12 09:30, device local time

Future<FakeLocalProfileRepository> pumpFeed(
  WidgetTester tester,
  List<PondAction>? actions, {
  Map<String, Object> prefs = onboardedPrefs,
  DateTime Function() now = clock,
}) async {
  tester.view.physicalSize = phoneSize;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final repo = FakeLocalProfileRepository(prefs);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        pondDataSourceProvider.overrideWithValue(FakePondDataSource()),
        localProfileRepositoryProvider.overrideWithValue(repo),
      ],
      child: MaterialApp(
        theme: AppTheme.dark,
        home: Scaffold(
          body: SingleChildScrollView(
            child: TodayActionFeed(actions: actions, clock: now),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return repo;
}

double topOf(WidgetTester tester, String text) =>
    tester.getTopLeft(find.text(text)).dy;

void main() {
  group('PondAction', () {
    test('parses the ladder action shape', () {
      final a = PondAction.fromJson(ladderActions[2]);
      expect(a.rule, 'WINDOW');
      expect(a.leadTime, 'today');
      expect(a.action, startsWith('Schedule a water change'));
      expect(a.evidence, contains('0.050'));
    });

    test('missing fields parse to empty text, not a crash', () {
      final a = PondAction.fromJson(const {});
      expect(a.rule, '');
      expect(a.ruleLabel, 'Weather rule');
      expect(a.whenText(Fixtures.now), 'Today');
      expect(a.logEventType, isNull);
    });

    test('byUrgency: NOWCAST, REACT, PREEMPT, WINDOW, then unknown rules '
        'in service order', () {
      final ordered = PondAction.byUrgency([
        ...parsed(),
        const PondAction(rule: 'ZETA', leadTime: '', action: 'z', evidence: ''),
        const PondAction(
          rule: 'ALPHA',
          leadTime: '',
          action: 'a',
          evidence: '',
        ),
      ]);
      expect(ordered.map((a) => a.rule), [
        'NOWCAST',
        'REACT',
        'PREEMPT',
        'WINDOW',
        'ZETA',
        'ALPHA',
      ]);
    });

    test('whenText puts each lead time in plain words', () {
      final a = {for (final p in parsed()) p.rule: p};
      expect(
        a['NOWCAST']!.whenText(DateTime(2026, 8, 12, 13, 10)),
        'Now, until about 15:00',
      );
      expect(
        a['NOWCAST']!.whenText(DateTime(2026, 8, 12, 22, 50)),
        'Now, until about 00:45',
      );
      expect(
        a['REACT']!.whenText(DateTime(2026, 8, 12, 9)),
        "This morning, after yesterday's rain",
      );
      expect(
        a['REACT']!.whenText(DateTime(2026, 8, 12, 15)),
        "Today, after yesterday's rain",
      );
      expect(a['PREEMPT']!.whenText(Fixtures.now), 'Before the hot days ahead');
      expect(a['WINDOW']!.whenText(Fixtures.now), 'Today, while it stays dry');
      expect(
        const PondAction(
          rule: 'X',
          leadTime: 'tomorrow morning',
          action: '',
          evidence: '',
        ).whenText(Fixtures.now),
        'Tomorrow morning',
      );
    });

    test('only the water-change and feed rules have an event to log', () {
      final a = {for (final p in parsed()) p.rule: p.logEventType};
      expect(a, {
        'REACT': null,
        'PREEMPT': 'FEEDING',
        'WINDOW': 'WATER_CHANGE',
        'NOWCAST': null,
      });
    });
  });

  group('TodayActionFeed', () {
    testWidgets('lists every action, most urgent first, with when and why', (
      tester,
    ) async {
      await pumpFeed(tester, parsed());

      expect(find.text('TODAY'), findsOneWidget);
      final tops = [
        for (final raw in [
          ladderActions[3],
          ladderActions[0],
          ladderActions[1],
          ladderActions[2],
        ])
          topOf(tester, raw['action'] as String),
      ];
      expect(tops, orderedEquals([...tops]..sort()));
      expect(find.text('Now, until about 11:30'), findsOneWidget);
      expect(find.text("This morning, after yesterday's rain"), findsOneWidget);
      expect(
        find.text(
          'Why: Heavy rain yesterday: 62.4 mm at station S24 on '
          '2026-08-11.',
        ),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    });

    testWidgets('empty: says the pond needs nothing today', (tester) async {
      await pumpFeed(tester, const []);

      expect(find.text('The pond needs nothing today.'), findsOneWidget);
      expect(find.textContaining('No weather rule has called'), findsOneWidget);
    });

    testWidgets('unavailable: does not claim the pond needs nothing', (
      tester,
    ) async {
      await pumpFeed(tester, null);

      expect(find.text("Today's actions could not be loaded."), findsOneWidget);
      expect(find.text('The pond needs nothing today.'), findsNothing);
    });

    testWidgets('dismiss hides the action and stores it for today only', (
      tester,
    ) async {
      final repo = await pumpFeed(
        tester,
        parsed(),
        prefs: {
          ...onboardedPrefs,
          dismissedActionsKey: ['2026-08-11|REACT|old'],
        },
      );
      final window = ladderActions[2]['action'] as String;

      await tester.tap(
        find.descendant(
          of: find.ancestor(
            of: find.text(window),
            matching: find.byType(OutcomeCardShell),
          ),
          matching: find.byTooltip('Dismiss for today'),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text(window), findsNothing);
      expect(find.text(ladderActions[3]['action'] as String), findsOneWidget);
      // Yesterday's entry has expired and is dropped on write.
      expect(repo.values[dismissedActionsKey], ['2026-08-12|WINDOW|$window']);
    });

    testWidgets('a dismissal from today stays hidden on the next open', (
      tester,
    ) async {
      final react = ladderActions[0]['action'] as String;
      await pumpFeed(
        tester,
        parsed(),
        prefs: {
          ...onboardedPrefs,
          dismissedActionsKey: ['2026-08-12|REACT|$react'],
        },
      );

      expect(find.text(react), findsNothing);
      expect(find.text(ladderActions[2]['action'] as String), findsOneWidget);
    });

    testWidgets('a dismissal from yesterday no longer hides the action', (
      tester,
    ) async {
      final react = ladderActions[0]['action'] as String;
      await pumpFeed(
        tester,
        parsed(),
        prefs: {
          ...onboardedPrefs,
          dismissedActionsKey: ['2026-08-11|REACT|$react'],
        },
      );

      expect(find.text(react), findsOneWidget);
    });

    testWidgets('all dismissed: empty state counts the hidden actions', (
      tester,
    ) async {
      final only = parsed([ladderActions[3]]);
      await pumpFeed(tester, only);

      await tester.tap(find.byTooltip('Dismiss for today'));
      await tester.pumpAndSettle();

      expect(find.text('The pond needs nothing today.'), findsOneWidget);
      expect(
        find.text('1 dismissed action is hidden until tomorrow.'),
        findsOneWidget,
      );
    });

    testWidgets('why expands to the full evidence and rule', (tester) async {
      await pumpFeed(tester, parsed([ladderActions[1]]));
      Text why() => tester.widget<Text>(find.textContaining('Why: '));

      expect(why().maxLines, 1);
      expect(find.textContaining('Rule PREEMPT'), findsNothing);

      await tester.tap(find.byKey(const ValueKey('why-PREEMPT')));
      await tester.pumpAndSettle();
      expect(why().maxLines, isNull);
      expect(find.textContaining('Rule PREEMPT'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('why-PREEMPT')));
      await tester.pumpAndSettle();
      expect(why().maxLines, 1);
    });

    testWidgets('log buttons open the matching log sheet', (tester) async {
      await pumpFeed(tester, parsed());

      expect(find.text('Log water change'), findsOneWidget);
      expect(find.text('Log feed'), findsOneWidget);
      // REACT and NOWCAST have nothing to log.
      expect(find.byType(OutlinedButton), findsNWidgets(2));

      await tester.tap(find.text('Log water change'));
      await tester.pumpAndSettle();
      final sheet = tester.widget<InterventionLogSheet>(
        find.byType(InterventionLogSheet),
      );
      expect(sheet.eventType, 'WATER_CHANGE');
      expect(sheet.title, 'Water Change');
      Navigator.of(tester.element(find.byType(InterventionLogSheet))).pop();
      await tester.pumpAndSettle();

      await tester.tap(find.text('Log feed'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<InterventionLogSheet>(find.byType(InterventionLogSheet))
            .eventType,
        'FEEDING',
      );
    });
  });

  for (final brightness in Brightness.values) {
    testWidgets('all four actions, expanded, fit a narrow phone at 1.6 '
        'text scale ($brightness)', (tester) async {
      await pumpScreen(
        tester,
        Scaffold(
          body: SingleChildScrollView(
            child: TodayActionFeed(actions: parsed(), clock: clock),
          ),
        ),
        size: const Size(320, 800),
        textScale: 1.6,
        brightness: brightness,
      );
      await settle(tester);
      for (final rule in ['NOWCAST', 'REACT', 'PREEMPT', 'WINDOW']) {
        final why = find.byKey(ValueKey('why-$rule'));
        await tester.ensureVisible(why);
        await tester.tap(why);
        await settle(tester);
      }
      expect(find.textContaining('Rule '), findsNWidgets(4));
      expect(tester.takeException(), isNull);
    });
  }

  group('DashboardView', () {
    Future<void> unmount(WidgetTester tester) =>
        tester.pumpWidget(const SizedBox.shrink());

    testWidgets('shows the Today feed above the weather and monitors', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DashboardView(),
        source: FakePondDataSource(
          dashboardPayload: {
            ...Fixtures.dashboardPayload(),
            'next_actions': [ladderActions[2]],
          },
        ),
      );
      await settle(tester);

      final action = ladderActions[2]['action'] as String;
      expect(find.byType(TodayActionFeed), findsOneWidget);
      expect(find.text(action), findsOneWidget);
      expect(
        topOf(tester, 'TODAY'),
        lessThan(topOf(tester, 'TEMPERATURE & FEED MONITOR')),
      );
      expect(find.text('Log water change'), findsOneWidget);
      await unmount(tester);
    });

    testWidgets('no actions in the payload: the empty state', (tester) async {
      await pumpScreen(tester, const DashboardView());
      await settle(tester);

      expect(find.text('The pond needs nothing today.'), findsOneWidget);
      await unmount(tester);
    });

    testWidgets('failed fetch: the feed reports it could not load', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DashboardView(),
        source: FakePondDataSource(failing: {'fetchDashboardPayload'}),
      );
      await settle(tester);

      expect(find.text("Today's actions could not be loaded."), findsOneWidget);
      expect(find.text('The pond needs nothing today.'), findsNothing);
      await unmount(tester);
    });
  });
}
