// DetailGraphScreen for each domain the dashboard opens (temperature, pH,
// algae/solar) in each state: loading, empty, data, error.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/screens/detail_graph_screen.dart';
import 'package:mobile_app/widgets/detail_graph/algae_severity_rating_card.dart';
import 'package:mobile_app/widgets/detail_graph/algae_status_card.dart';
import 'package:mobile_app/widgets/detail_graph/evaporation_status_card.dart';
import 'package:mobile_app/widgets/detail_graph/historical_line_chart.dart';
import 'package:mobile_app/widgets/detail_graph/model_outlook_card.dart';
import 'package:mobile_app/widgets/detail_graph/scarce_data_placeholder.dart';
import 'package:mobile_app/widgets/detail_graph/water_buffer_status_card.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

const _domains = <(String metric, String title, Type card)>[
  ('temperature', 'Detailed Temperature', EvaporationStatusCard),
  ('ph', 'pH & Buffer Stability', WaterBufferStatusCard),
  ('lux', 'Solar & Algae Risk Analysis', AlgaeStatusCard),
];

/// A source where every DigitalTwin call returns data.
FakePondDataSource _fullSource() => FakePondDataSource(
  historicalPayload: Fixtures.historicalPayload(),
  assessment: Fixtures.assessment(),
  evaporationForecast: Fixtures.evaporationForecast(),
  algaeForecast: Fixtures.algaeForecast(),
  frame: Fixtures.frame(),
  ratingContext: Fixtures.ratingContext(),
);

Future<void> _scrollTo(WidgetTester tester, Finder finder) => tester
    .scrollUntilVisible(finder, 200, scrollable: find.byType(Scrollable).first);

const _tag = 'DIGITAL TWIN MODEL';

Future<void> _expandModel(WidgetTester tester) async {
  final tag = find.text(_tag);
  await _scrollTo(tester, tag);
  await tester.tap(tag);
  await settle(tester);
}

void main() {
  for (final (metric, title, cardType) in _domains) {
    group('$metric screen', () {
      testWidgets('loading: spinner while the history is pending', (
        tester,
      ) async {
        await pumpScreen(
          tester,
          DetailGraphScreen(metricType: metric, title: title),
          source: FakePondDataSource(hanging: {'fetchHistoricalGraphPayload'}),
        );
        await settle(tester);

        expect(find.text(title), findsOneWidget);
        expect(find.byType(CircularProgressIndicator), findsOneWidget);
        expect(find.byType(HistoricalLineChart), findsNothing);
      });

      testWidgets('data: chart, legend and the domain card', (tester) async {
        final fake = await pumpScreen(
          tester,
          DetailGraphScreen(metricType: metric, title: title),
          source: _fullSource(),
        );
        await settle(tester);

        expect(find.byType(HistoricalLineChart), findsOneWidget);
        expect(find.byType(ScarceDataPlaceholder), findsNothing);
        expect(find.byType(cardType), findsOneWidget);
        expect(fake.callsTo('fetchHistoricalGraphPayload').single.args, {
          'userId': 7,
          'days': 30,
        });
      });

      testWidgets('empty: placeholder instead of a chart', (tester) async {
        await pumpScreen(
          tester,
          DetailGraphScreen(metricType: metric, title: title),
          source: FakePondDataSource(
            historicalPayload: Fixtures.emptyHistoricalPayload(),
          ),
        );
        await settle(tester);

        expect(find.byType(ScarceDataPlaceholder), findsOneWidget);
        expect(find.byType(HistoricalLineChart), findsNothing);
        expect(find.byType(cardType), findsOneWidget);
        expect(find.text('Retry'), findsWidgets);
      });

      testWidgets(
        'error: a failed history fetch shows a retryable load failure',
        (tester) async {
          final fake = await pumpScreen(
            tester,
            DetailGraphScreen(metricType: metric, title: title),
            source: FakePondDataSource(
              failing: {
                'fetchHistoricalGraphPayload',
                'fetchLatestAssessment',
                'fetchEvaporationForecastOrError',
                'fetchAlgaeForecastOrError',
              },
            ),
          );
          await settle(tester);

          expect(find.byType(CircularProgressIndicator), findsNothing);
          expect(find.byType(ScarceDataPlaceholder), findsOneWidget);
          expect(find.text('History could not be loaded'), findsOneWidget);
          expect(
            find.text('Establishing Baseline Cycle Averages'),
            findsNothing,
          );
          expect(find.byType(cardType), findsOneWidget);
          expect(find.text('Retry'), findsWidgets);
          final retry = find.descendant(
            of: find.byType(ScarceDataPlaceholder),
            matching: find.text('Retry'),
          );
          await tester.tap(retry);
          await settle(tester);
          expect(fake.callsTo('fetchHistoricalGraphPayload'), hasLength(2));
        },
      );
    });
  }

  group('domain cards', () {
    testWidgets('evaporation card: days to top-up, evaporation and feed cap', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'temperature',
          title: 'Detailed Temperature',
        ),
        source: _fullSource(),
      );
      await settle(tester);
      await _scrollTo(tester, find.text('Next top-up in 4 days'));
      expect(find.text('Next top-up in 4 days'), findsOneWidget);
      // Collapsed by default: the table is not built yet.
      expect(find.text('4.2 mm/d'), findsNothing);
      await _expandModel(tester);
      expect(find.text('Evaporation'), findsOneWidget);
      expect(find.text('Water loss'), findsOneWidget);
      expect(find.text('Feed cap'), findsOneWidget);
      expect(find.text('4.2 mm/d'), findsOneWidget);
      expect(find.text('60 g/d'), findsOneWidget);
    });

    testWidgets('evaporation card: shows the server reason when unavailable', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'temperature',
          title: 'Detailed Temperature',
        ),
        source: FakePondDataSource(
          historicalPayload: Fixtures.historicalPayload(),
          evaporationError: 'Pond volume not configured.',
        ),
      );
      await settle(tester);
      await _scrollTo(tester, find.text('Pond volume not configured.'));
      expect(find.text('Pond volume not configured.'), findsOneWidget);
    });

    testWidgets('evaporation card: loading while the forecast is pending', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'temperature',
          title: 'Detailed Temperature',
        ),
        source: FakePondDataSource(
          historicalPayload: Fixtures.historicalPayload(),
          hanging: {'fetchEvaporationForecastOrError'},
        ),
      );
      await settle(tester);
      expect(
        find.descendant(
          of: find.byType(EvaporationStatusCard),
          matching: find.byType(CircularProgressIndicator),
        ),
        findsOneWidget,
      );
    });

    testWidgets(
      'water buffer card: shows the assessment, retries when absent',
      (tester) async {
        final fake = await pumpScreen(
          tester,
          const DetailGraphScreen(
            metricType: 'ph',
            title: 'pH & Buffer Stability',
          ),
          source: FakePondDataSource(
            historicalPayload: Fixtures.historicalPayload(),
          ),
        );
        await settle(tester);
        final retry = find.descendant(
          of: find.byType(WaterBufferStatusCard),
          matching: find.text('Retry'),
        );
        await _scrollTo(tester, retry);
        expect(
          find.textContaining('No digital twin assessment yet'),
          findsOneWidget,
        );

        fake.assessment = Fixtures.assessment();
        await tester.tap(retry);
        await settle(tester);
        expect(fake.callsTo('fetchLatestAssessment'), hasLength(2));
        expect(find.text('WATCH'), findsNothing);
        await _expandModel(tester);
        expect(find.text('TAN'), findsOneWidget);
        expect(find.text('0.42 ppm'), findsOneWidget);
      },
    );

    testWidgets('algae screen: forecast and rating card both render', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'lux',
          title: 'Solar & Algae Risk Analysis',
        ),
        source: _fullSource(),
      );
      await settle(tester);
      await _scrollTo(tester, find.text('Scrub in about 6 days'));
      expect(find.text('Scrub in about 6 days'), findsOneWidget);
      expect(find.byType(AlgaeSeverityRatingCard), findsOneWidget);
    });

    testWidgets('algae screen: rating card sits above the model card', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'lux',
          title: 'Solar & Algae Risk Analysis',
        ),
        source: _fullSource(),
      );
      await settle(tester);
      await _scrollTo(tester, find.text(_tag));
      final rating = tester.getTopLeft(find.byType(AlgaeSeverityRatingCard));
      final model = tester.getTopLeft(find.byType(AlgaeStatusCard));
      expect(rating.dy, lessThan(model.dy));
    });

    testWidgets('algae screen: rating card collapses and re-expands', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'lux',
          title: 'Solar & Algae Risk Analysis',
        ),
        source: _fullSource(),
      );
      await settle(tester);
      final title = find.text('Rate What You See');
      await _scrollTo(tester, title);
      expect(find.text('Submit rating'), findsOneWidget);
      await tester.tap(title);
      await settle(tester);
      expect(find.text('Submit rating'), findsNothing);
      expect(title, findsOneWidget);
      await tester.tap(title);
      await settle(tester);
      expect(find.text('Submit rating'), findsOneWidget);
    });

    for (final (metric, title, headline, rowLabel) in const [
      (
        'temperature',
        'Detailed Temperature',
        'Next top-up in 4 days',
        'Feed cap',
      ),
      ('ph', 'pH & Buffer Stability', null, 'TAN'),
      ('lux', 'Solar & Algae Risk Analysis', 'Scrub in about 6 days', 'Frames'),
    ]) {
      testWidgets('$metric model card: tag, no pill, collapsed then expands', (
        tester,
      ) async {
        await pumpScreen(
          tester,
          DetailGraphScreen(metricType: metric, title: title),
          source: _fullSource(),
        );
        await settle(tester);
        await _scrollTo(tester, find.text(_tag));
        expect(find.text(_tag), findsOneWidget);
        if (headline != null) expect(find.text(headline), findsOneWidget);
        for (final pill in [
          'PLANNED',
          'DUE NOW',
          'SOON',
          'CLEAR',
          'STABLE',
          'WATCH',
        ]) {
          expect(find.text(pill), findsNothing);
        }
        expect(find.text(rowLabel), findsNothing);
        await tester.tap(find.text(_tag));
        await settle(tester);
        expect(find.text(rowLabel), findsOneWidget);
        expect(find.text('Model estimate'), findsNothing);
        await tester.tap(find.text(_tag));
        await settle(tester);
        expect(find.text(rowLabel), findsNothing);
      });
    }

    testWidgets('temperature card: assumptions live in the (i) popup', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'temperature',
          title: 'Detailed Temperature',
        ),
        source: _fullSource(),
      );
      await settle(tester);
      await _scrollTo(tester, find.text(_tag));
      await _expandModel(tester);
      expect(find.textContaining('Assumes'), findsNothing);
      final info = find.byKey(const ValueKey('model-info'));
      await _scrollTo(tester, info);
      expect(find.byTooltip('About this estimate'), findsOneWidget);
      await tester.tap(info);
      await settle(tester);
      expect(find.textContaining('Assumes'), findsOneWidget);
      expect(find.text('Tap anywhere to close'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('model-info-popup')));
      await settle(tester);
      expect(find.textContaining('Assumes'), findsNothing);
    });

    testWidgets('algae card: no scrub-days or camera provenance text', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const DetailGraphScreen(
          metricType: 'lux',
          title: 'Solar & Algae Risk Analysis',
        ),
        source: _fullSource(),
      );
      await settle(tester);
      await _scrollTo(tester, find.text(_tag));
      await _expandModel(tester);
      expect(find.textContaining('Scrubbing today buys'), findsNothing);
      expect(find.textContaining('camera frames'), findsNothing);
      final info = find.byKey(const ValueKey('model-info'));
      await _scrollTo(tester, info);
      await tester.tap(info);
      await settle(tester);
      expect(find.textContaining('Alert level is set'), findsOneWidget);
      expect(find.textContaining('Scrubbing today buys'), findsNothing);
      expect(find.textContaining('camera frames'), findsNothing);
    });
  });

  group('ModelOutlookCard notes', () {
    Widget host({List<String> info = const []}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: ModelOutlookCard(
            headline: 'h',
            statusColor: Colors.green,
            statusLabel: 's',
            subtitle: 'sub',
            rows: const [],
            initiallyExpanded: true,
            alerts: const [ModelOutlookNote('TDS disagrees with forecast')],
            infoNotes: info,
          ),
        ),
      ),
    );

    testWidgets('alert stays visible; no (i) without infoNotes', (t) async {
      await t.pumpWidget(host());
      expect(find.text('TDS disagrees with forecast'), findsOneWidget);
      expect(find.byKey(const ValueKey('model-info')), findsNothing);
    });

    testWidgets('(i) opens the notes and closes on tap', (t) async {
      await t.pumpWidget(host(info: ['Caveat one', 'Caveat two']));
      expect(find.text('Caveat one'), findsNothing);
      expect(find.text('TDS disagrees with forecast'), findsOneWidget);
      await t.tap(find.byKey(const ValueKey('model-info')));
      await t.pumpAndSettle();
      expect(find.text('Caveat one'), findsOneWidget);
      expect(find.text('Caveat two'), findsOneWidget);
      await t.tap(find.byKey(const ValueKey('model-info-popup')));
      await t.pumpAndSettle();
      expect(find.text('Caveat one'), findsNothing);
    });
  });
}
