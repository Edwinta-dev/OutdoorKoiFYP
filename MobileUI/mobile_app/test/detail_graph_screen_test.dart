// DetailGraphScreen for each domain the dashboard opens (temperature, pH,
// algae/solar) in each state: loading, empty, data, error.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/screens/detail_graph_screen.dart';
import 'package:mobile_app/widgets/detail_graph/algae_severity_rating_card.dart';
import 'package:mobile_app/widgets/detail_graph/algae_status_card.dart';
import 'package:mobile_app/widgets/detail_graph/evaporation_status_card.dart';
import 'package:mobile_app/widgets/detail_graph/historical_line_chart.dart';
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
        'error: a failed history fetch falls back to the placeholder',
        (tester) async {
          await pumpScreen(
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
          expect(find.byType(cardType), findsOneWidget);
          expect(find.text('Retry'), findsWidgets);
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
      await _scrollTo(tester, find.text('in 4 days'));
      expect(find.text('in 4 days'), findsOneWidget);
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
        expect(find.text('WATCH'), findsOneWidget);
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
      await _scrollTo(tester, find.text('in 6 days'));
      expect(find.text('in 6 days'), findsOneWidget);
      expect(find.byType(AlgaeSeverityRatingCard), findsOneWidget);
    });
  });
}
