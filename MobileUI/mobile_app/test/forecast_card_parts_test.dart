// The loading / unavailable shell and the urgency mappings shared by the
// three detail-screen lookahead cards.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:mobile_app/widgets/detail_graph/forecast_card_parts.dart';
import 'package:mobile_app/widgets/shared/pond_widgets.dart';

Widget _host(Widget child) => MaterialApp(
  home: Scaffold(body: SingleChildScrollView(child: child)),
);

ForecastCard<int> _card(
  Future<ForecastResult<int>> future, {
  VoidCallback? onRetry,
}) => ForecastCard<int>(
  future: future,
  title: 'Test Outlook',
  loadingColor: Colors.blue,
  onRetry: onRetry ?? () {},
  builder: (value) => Text('forecast $value'),
);

void main() {
  testWidgets('shows a spinner while the forecast is pending', (tester) async {
    await tester.pumpWidget(
      _host(_card(Completer<ForecastResult<int>>().future)),
    );
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.text('forecast 1'), findsNothing);
  });

  testWidgets('builds the card once the forecast arrives', (tester) async {
    await tester.pumpWidget(
      _host(_card(Future.value((forecast: 1, error: null)))),
    );
    await tester.pump();
    expect(find.text('forecast 1'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('shows the reason and retries when the forecast is missing', (
    tester,
  ) async {
    var retries = 0;
    await tester.pumpWidget(
      _host(
        _card(
          Future.value((forecast: null, error: 'Service down')),
          onRetry: () => retries++,
        ),
      ),
    );
    await tester.pump();
    expect(find.byType(PondErrorState), findsOneWidget);
    expect(find.text('Service down'), findsOneWidget);
    await tester.tap(find.byType(TextButton).first);
    expect(retries, 1);
  });

  testWidgets('falls back to a generic reason when none is given', (
    tester,
  ) async {
    await tester.pumpWidget(
      _host(_card(Future.value((forecast: null, error: null)))),
    );
    await tester.pump();
    expect(find.text('Forecast unavailable'), findsOneWidget);
  });

  test('urgency colour and label follow the days until action', () {
    const colors = AppColors(Brightness.light);
    const planned = Colors.purple;
    expect(
      forecastUrgencyColor(colors, null, planned: planned),
      colors.healthy,
    );
    expect(forecastUrgencyColor(colors, 0, planned: planned), colors.danger);
    expect(forecastUrgencyColor(colors, 3, planned: planned), colors.warning);
    expect(forecastUrgencyColor(colors, 4, planned: planned), planned);
    expect(forecastUrgencyLabel(null, clear: 'CLEAR'), 'CLEAR');
    expect(forecastUrgencyLabel(-1, clear: 'CLEAR'), 'DUE NOW');
    expect(forecastUrgencyLabel(2, clear: 'CLEAR'), 'SOON');
    expect(forecastUrgencyLabel(9, clear: 'CLEAR'), 'PLANNED');
  });

  test('only the danger and warning colours map to a status', () {
    const colors = AppColors(Brightness.light);
    expect(pondStatusForColor(colors, colors.danger), PondStatus.danger);
    expect(pondStatusForColor(colors, colors.warning), PondStatus.warning);
    expect(pondStatusForColor(colors, colors.healthy), isNull);
  });
}
