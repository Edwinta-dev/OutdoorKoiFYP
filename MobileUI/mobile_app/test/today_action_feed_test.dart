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
    'evidence': '55.2 mm at station S43 on 2026-08-11.',
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

Future<void> pumpFeedWith(
  WidgetTester tester,
  FakeLocalProfileRepository repo,
  List<PondAction> actions,
  DateTime Function() now,
) async {
  tester.view.physicalSize = phoneSize;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      key: UniqueKey(),
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
      expect(a.headline, 'Dry day for pond work');
      expect(a.ruleTag, '24-HOUR FORECAST');
      expect(a.ruleIcon, Icons.wb_sunny_outlined);
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
    testWidgets(
      'lists brief cards most urgent first with timing and evidence',
      (tester) async {
        await pumpFeed(tester, parsed());

        expect(find.text('TODAY'), findsOneWidget);
        final tops = [
          for (final raw in [
            ladderActions[3],
            ladderActions[0],
            ladderActions[1],
            ladderActions[2],
          ])
            topOf(tester, parsed([raw]).single.headline),
        ];
        expect(tops, orderedEquals([...tops]..sort()));
        expect(find.text('Heavy rain yesterday'), findsOneWidget);
        expect(find.text('RAIN GAUGE'), findsOneWidget);
        // The chip appears only once a card is expanded.
        expect(find.textContaining('from now to about'), findsNothing);
        await tester.tap(find.byKey(const ValueKey('expand-NOWCAST')));
        await tester.tap(find.byKey(const ValueKey('expand-REACT')));
        await tester.pumpAndSettle();
        expect(
          find.text('Thundery Showers from now to about 11:30 am'),
          findsOneWidget,
        );
        expect(
          find.text('Rain gauge: 55.2 mm yesterday (S43)'),
          findsOneWidget,
        );
        expect(tester.takeException(), isNull);
      },
    );

    testWidgets('empty: names rules that cannot be assessed', (tester) async {
      await pumpFeed(tester, const []);

      expect(find.text('Weather rules could not be checked.'), findsOneWidget);
      expect(find.textContaining('PREEMPT and WINDOW'), findsOneWidget);
    });

    testWidgets('unavailable: does not claim the pond needs nothing', (
      tester,
    ) async {
      await pumpFeed(tester, null);

      expect(find.text("Today's actions could not be loaded."), findsOneWidget);
      expect(find.text('Weather rules could not be checked.'), findsNothing);
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
      final windowHeadline = parsed([ladderActions[2]]).single.headline;

      await tester.tap(
        find.descendant(
          of: find.ancestor(
            of: find.text(windowHeadline),
            matching: find.byType(OutcomeCardShell),
          ),
          matching: find.byTooltip('Dismiss for today'),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text(window), findsNothing);
      expect(
        find.text(parsed([ladderActions[3]]).single.headline),
        findsOneWidget,
      );
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
      expect(
        find.text(parsed([ladderActions[2]]).single.headline),
        findsOneWidget,
      );
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

      expect(
        find.text(parsed([ladderActions[0]]).single.headline),
        findsOneWidget,
      );
    });

    testWidgets('all dismissed: empty state counts the hidden actions', (
      tester,
    ) async {
      final only = parsed([ladderActions[3]]);
      await pumpFeed(tester, only);

      await tester.tap(find.byTooltip('Dismiss for today'));
      await tester.pumpAndSettle();

      expect(find.text('All actions dismissed.'), findsOneWidget);
      expect(
        find.text('1 dismissed action is hidden until tomorrow.'),
        findsOneWidget,
      );
    });

    testWidgets('the chevron expands to advice, one chip and the info '
        'button', (tester) async {
      await pumpFeed(tester, parsed([ladderActions[1]]));

      expect(find.text('4-DAY OUTLOOK'), findsOneWidget);
      expect(find.byKey(const ValueKey('evidence-PREEMPT')), findsNothing);
      expect(find.byKey(const ValueKey('info-PREEMPT')), findsNothing);
      expect(find.text('Reduce feeding and add aeration'), findsNothing);

      await tester.tap(find.byKey(const ValueKey('expand-PREEMPT')));
      await tester.pumpAndSettle();
      expect(find.byType(Chip), findsNothing);
      expect(find.byKey(const ValueKey('evidence-PREEMPT')), findsOneWidget);
      expect(find.text('Tmax 34.1 °C, outlook 34.0 °C'), findsOneWidget);
      expect(find.text('Reduce feeding and add aeration'), findsOneWidget);
      expect(find.textContaining('Reduce the feed ration'), findsNothing);
      expect(find.textContaining('not a pond measurement'), findsNothing);
      expect(find.textContaining('Rule PREEMPT'), findsNothing);
      expect(find.byKey(const ValueKey('info-PREEMPT')), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('expand-PREEMPT')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('evidence-PREEMPT')), findsNothing);
    });

    testWidgets('info button opens a popup that any tap closes', (
      tester,
    ) async {
      await pumpFeed(tester, parsed([ladderActions[3]]));
      await tester.tap(find.byKey(const ValueKey('expand-NOWCAST')));
      await tester.pumpAndSettle();
      final info = parsed([ladderActions[3]]).single.infoText;

      await tester.tap(find.byKey(const ValueKey('info-NOWCAST')));
      await tester.pumpAndSettle();
      expect(find.text(info), findsOneWidget);
      expect(find.text('Tap anywhere to close'), findsOneWidget);
      final dialog = tester.widget<Dialog>(find.byType(Dialog));
      expect(
        (dialog.shape as RoundedRectangleBorder).borderRadius,
        BorderRadius.circular(AppRadius.control),
      );
      expect(
        tester.getSize(find.byKey(const ValueKey('info-popup-NOWCAST'))).width,
        lessThanOrEqualTo(360),
      );
      await tester.tap(find.text(info));
      await tester.pumpAndSettle();
      expect(find.text(info), findsNothing);

      await tester.tap(find.byKey(const ValueKey('info-NOWCAST')));
      await tester.pumpAndSettle();
      expect(find.text(info), findsOneWidget);
      await tester.tapAt(const Offset(2, 2)); // the barrier
      await tester.pumpAndSettle();
      expect(find.text(info), findsNothing);
    });

    test('each rule has its tag, advice and info text', () {
      final a = {for (final p in parsed()) p.rule: p};
      expect(a['NOWCAST']!.ruleTag, '2-HOUR NOWCAST');
      expect(a['REACT']!.ruleTag, 'RAIN GAUGE');
      expect(a['PREEMPT']!.ruleTag, '4-DAY OUTLOOK');
      expect(a['WINDOW']!.ruleTag, '24-HOUR FORECAST');
      expect(a['NOWCAST']!.advice, 'Pause and postpone outdoor work');
      expect(a['REACT']!.advice, 'Perform a water chemistry check');
      expect(a['PREEMPT']!.advice, 'Reduce feeding and add aeration');
      expect(
        a['WINDOW']!.advice,
        'Good day for a water change or filter clean',
      );
      expect(
        a['NOWCAST']!.infoText,
        'Rule NOWCAST, checked against the NEA 2-hour forecast. Fires when '
        'the latest nowcast for your area says rain, showers or thunder for '
        'now.',
      );
      expect(
        a['REACT']!.infoText,
        'Rule REACT, checked against NEA weather telemetry. Fires when '
        "yesterday's rain total at your rain station is 50 mm or more.",
      );
      expect(
        a['PREEMPT']!.infoText,
        'Rule PREEMPT, checked against the NEA 4-day outlook. Fires when '
        'today was hot and the outlook stays hot for the coming days.',
      );
      expect(
        a['WINDOW']!.infoText,
        'Rule WINDOW, checked against the NEA 24-hour forecast. Fires when '
        "the day's forecast is in the driest band.",
      );
      const unknown = PondAction(
        rule: 'X',
        leadTime: '',
        action: 'Server text',
        evidence: '',
      );
      expect(unknown.advice, 'Server text');
    });

    test('NOWCAST chip: 12-hour clock, rounded down to 15 minutes', () {
      final n = parsed([ladderActions[3]]).single;
      String at(int h, int m) => n.evidenceText(DateTime(2026, 8, 12, h, m));
      expect(at(13, 10), 'Thundery Showers from now to about 3:00 pm');
      expect(at(13, 44), 'Thundery Showers from now to about 3:30 pm');
      expect(at(22, 50), 'Thundery Showers from now to about 12:45 am');
      expect(at(9, 50), 'Thundery Showers from now to about 11:45 am');
      expect(at(10, 5), 'Thundery Showers from now to about 12:00 pm');
    });

    test('REACT line: gauge total with a relative day, else raw', () {
      final r = parsed([ladderActions[0]]).single; // rain on 2026-08-11
      String at(int d) => r.evidenceText(DateTime(2026, 8, d, 9));
      expect(at(12), 'Rain gauge: 55.2 mm yesterday (S43)');
      expect(at(13), 'Rain gauge: 55.2 mm 2 days ago (S43)');
      expect(at(14), 'Rain gauge: 55.2 mm 3 days ago (S43)');
      expect(at(15), 'Rain gauge: 55.2 mm a long time ago (S43)');
      expect(at(20), 'Rain gauge: 55.2 mm a long time ago (S43)');
      expect(r.evidenceText(Fixtures.now), isNot(contains('00:00')));
      expect(r.evidenceText(Fixtures.now), isNot(contains('since')));
      // Month boundary.
      expect(
        r.evidenceText(DateTime(2026, 9, 1)),
        'Rain gauge: 55.2 mm a long time ago (S43)',
      );
      const undated = PondAction(
        rule: 'REACT',
        leadTime: '',
        action: '',
        evidence: '55.2 mm at station S43 on a day.',
      );
      expect(
        undated.evidenceText(DateTime(2026, 8, 20)),
        'Rain gauge: 55.2 mm yesterday (S43)',
      );
      const odd = PondAction(
        rule: 'REACT',
        leadTime: '',
        action: '',
        evidence: 'Gauge total unavailable.',
      );
      expect(odd.evidenceText(Fixtures.now), 'Gauge total unavailable.');
    });

    test('PREEMPT and WINDOW lines are one short plain line', () {
      expect(
        parsed([ladderActions[1]]).single.evidenceText(Fixtures.now),
        'Tmax 34.1 °C, outlook 34.0 °C',
      );
      expect(
        parsed([ladderActions[2]]).single.evidenceText(Fixtures.now),
        'Forecast in the driest band',
      );
    });

    test('rain rules are told apart by icon and tag', () {
      final now = PondAction.fromJson(const {'rule': 'NOWCAST'});
      final react = PondAction.fromJson(const {'rule': 'REACT'});
      expect(now.ruleIcon, isNot(react.ruleIcon));
      expect(now.ruleTag, '2-HOUR NOWCAST');
      expect(react.ruleTag, 'RAIN GAUGE');
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
        final why = find.byKey(ValueKey('expand-$rule'));
        await tester.ensureVisible(why);
        await tester.tap(why);
        await settle(tester);
      }
      expect(find.byType(Chip), findsNothing);
      for (final rule in ['NOWCAST', 'REACT', 'PREEMPT', 'WINDOW']) {
        expect(find.byKey(ValueKey('evidence-$rule')), findsOneWidget);
      }
      expect(find.byTooltip('About this rule'), findsNWidgets(4));
      expect(tester.takeException(), isNull);
    });
  }

  group('REACT is kept until dismissed', () {
    final react = parsed([ladderActions[0]]);
    DateTime Function() on(int day) =>
        () => DateTime(2026, 8, day, 9, 30);

    testWidgets('first sight stores it; later days still show it', (
      tester,
    ) async {
      final repo = await pumpFeed(tester, react);
      expect(repo.values[retainedReactKey], contains('55.2 mm'));

      // Two days later the server no longer returns it.
      final later = FakeLocalProfileRepository(
        Map.of(repo.values)..remove(dismissedActionsKey),
      );
      await pumpFeedWith(tester, later, const [], on(14));
      await tester.tap(find.byKey(const ValueKey('expand-REACT')));
      await tester.pumpAndSettle();
      expect(find.text('Heavy rain yesterday'), findsOneWidget);
      expect(find.text('Rain gauge: 55.2 mm 3 days ago (S43)'), findsOneWidget);
    });

    testWidgets('dismissing clears it for good', (tester) async {
      final repo = await pumpFeed(tester, react);
      await tester.tap(find.byTooltip('Dismiss for today'));
      await tester.pumpAndSettle();
      expect(repo.values[retainedReactKey], '');

      final later = FakeLocalProfileRepository(Map.of(repo.values));
      await pumpFeedWith(tester, later, const [], on(13));
      expect(find.text('Heavy rain yesterday'), findsNothing);
      expect(find.text('Weather rules could not be checked.'), findsOneWidget);
    });

    testWidgets('dismissed today, server still returns it: not re-kept', (
      tester,
    ) async {
      final action = ladderActions[0]['action'] as String;
      final repo = await pumpFeed(
        tester,
        react,
        prefs: {
          ...onboardedPrefs,
          dismissedActionsKey: ['2026-08-12|REACT|$action'],
        },
      );
      expect(repo.values[retainedReactKey], isNull);
    });

    testWidgets('dropped after 7 days', (tester) async {
      final repo = await pumpFeed(tester, react);
      final kept = FakeLocalProfileRepository(Map.of(repo.values));
      await pumpFeedWith(tester, kept, const [], on(18)); // rain on the 11th
      expect(find.text('Heavy rain yesterday'), findsOneWidget);

      final expired = FakeLocalProfileRepository(Map.of(repo.values));
      await pumpFeedWith(tester, expired, const [], on(19));
      expect(find.text('Heavy rain yesterday'), findsNothing);
      expect(expired.values[retainedReactKey], '');
    });

    testWidgets('other rules are not kept', (tester) async {
      final repo = await pumpFeed(tester, parsed([ladderActions[3]]));
      expect(repo.values[retainedReactKey], isNull);
    });
  });

  group('DashboardView', () {
    Future<void> unmount(WidgetTester tester) =>
        tester.pumpWidget(const SizedBox.shrink());

    testWidgets('shows the Today feed above the weather and monitors', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DashboardView(),
        prefs: {...onboardedPrefs, displayModeKey: 'detailed'},
        source: FakePondDataSource(
          dashboardPayload: {
            ...Fixtures.dashboardPayload(),
            'next_actions': [ladderActions[2]],
          },
        ),
      );
      await settle(tester);

      expect(find.byType(TodayActionFeed), findsOneWidget);
      expect(
        find.text(parsed([ladderActions[2]]).single.headline),
        findsOneWidget,
      );
      expect(
        topOf(tester, 'TODAY'),
        lessThan(topOf(tester, 'TEMPERATURE & FEED MONITOR')),
      );
      expect(find.text('Log water change'), findsOneWidget);
      await unmount(tester);
    });

    testWidgets('simple view: the Today feed sits above the pond status', (
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

      expect(
        find.text(parsed([ladderActions[2]]).single.headline),
        findsOneWidget,
      );
      expect(topOf(tester, 'TODAY'), lessThan(topOf(tester, 'POND STATUS')));
      expect(find.text('Log water change'), findsOneWidget);
      await unmount(tester);
    });

    testWidgets('no actions in the payload: the empty state', (tester) async {
      await pumpScreen(tester, const DashboardView());
      await settle(tester);

      expect(find.text('Weather rules could not be checked.'), findsOneWidget);
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
      expect(find.text('Weather rules could not be checked.'), findsNothing);
      await unmount(tester);
    });
  });
}
