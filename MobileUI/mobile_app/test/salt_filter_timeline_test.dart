import 'package:mobile_app/theme/app_theme.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/widgets/detail_graph/historical_line_chart.dart';
import 'package:mobile_app/widgets/detail_graph/intervention_legend.dart';

void main() {
  testWidgets('salt and filter timeline shows mass, notes and maintenance', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: HistoricalLineChart(
            primarySensorType: 'TDS',
            primaryDomainEvent: 'WATER_TOPUP',
            points: [
              {
                'date': '2026-10-01',
                'sensor_type': 'TDS',
                'avg_value': 200,
                'after_maintenance': true,
              },
              {
                'date': '2026-10-02',
                'sensor_type': 'TDS',
                'avg_value': 210,
                'after_maintenance': false,
              },
            ],
            interventions: [
              {
                'event_type': 'SALT',
                'timestamp': '2026-10-01T00:00:00',
                'salt_grams': 1,
                'event_id': 'salt',
              },
              {
                'event_type': 'FILTER_CLEAN',
                'timestamp': '2026-10-01T00:00:00',
                'notes': 'Rinsed',
                'event_id': 'filter',
              },
            ],
          ),
        ),
      ),
    );
    final data = tester.widget<LineChart>(find.byType(LineChart)).data;
    final lines = data.extraLinesData.verticalLines;
    expect(lines[0].label.labelResolver(lines[0]), 'Salt 1 g');
    expect(lines[1].label.labelResolver(lines[1]), 'Filter cleaning: Rinsed');
    expect(lines[0].color, const AppColors(Brightness.light).intervention);
    expect(lines[1].color, const AppColors(Brightness.light).warning);
    final bar = data.lineBarsData.single;
    final tooltip = data.lineTouchData.touchTooltipData.getTooltipItems([
      LineBarSpot(bar, 0, bar.spots[0]),
      LineBarSpot(bar, 0, bar.spots[1]),
    ]);
    expect(tooltip[0]!.text, contains('After maintenance'));
    expect(tooltip[1]!.text, isNot(contains('After maintenance')));
  });

  testWidgets('salt and filter legend entries are visible', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: InterventionLegend(primaryEventType: 'SALT')),
      ),
    );
    expect(find.text('Salt Addition'), findsOneWidget);
    expect(find.text('Filter Cleaning'), findsOneWidget);
  });
}
