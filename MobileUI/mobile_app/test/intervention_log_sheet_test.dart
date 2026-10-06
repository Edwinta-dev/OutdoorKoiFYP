// InterventionLogSheet (the "Log" sheet) in each state: the empty form,
// saving (loading), saved (data) and a failed save (error).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/widgets/modals/quick_log_modals.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

/// A page with a button that opens the sheet the way the app does, so the
/// sheet's Navigator.pop and SnackBar have somewhere to go.
Widget _host(String eventType, String title) => Scaffold(
  body: Builder(
    builder: (context) => Center(
      child: ElevatedButton(
        onPressed: () => showModalBottomSheet<void>(
          context: context,
          isScrollControlled: true,
          builder: (_) => InterventionLogSheet(
            eventType: eventType,
            title: title,
            accentColor: Colors.orangeAccent,
            initialTimestamp: Fixtures.now,
          ),
        ),
        child: const Text('open'),
      ),
    ),
  ),
);

Future<FakePondDataSource> _openSheet(
  WidgetTester tester,
  String eventType,
  String title, {
  FakePondDataSource? source,
}) async {
  final fake = await pumpScreen(
    tester,
    _host(eventType, title),
    source: source,
  );
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
  return fake;
}

void main() {
  testWidgets('salt: saves grams with the same stable ID in history and API', (
    tester,
  ) async {
    final fake = await _openSheet(tester, 'SALT', 'Salt Addition');
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Salt added (g)'),
      '1',
    );
    await tester.tap(find.text('Save Salt Addition'));
    await tester.pumpAndSettle();
    final insert = fake.callsTo('insertIntervention').single.args;
    final posted = fake.callsTo('logSalt').single.args;
    expect(insert['salt_grams'], 1.0);
    expect(insert['event_type'], 'SALT');
    expect(posted['saltGrams'], 1.0);
    expect(posted['eventId'], insert['event_id']);
  });

  testWidgets('salt: rejects nonpositive mass before writing', (tester) async {
    final fake = await _openSheet(tester, 'SALT', 'Salt Addition');
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Salt added (g)'),
      '0',
    );
    await tester.tap(find.text('Save Salt Addition'));
    await tester.pumpAndSettle();
    expect(find.text('Enter valid value'), findsOneWidget);
    expect(fake.callsTo('insertIntervention'), isEmpty);
  });

  for (final notes in ['', 'Rinsed media']) {
    testWidgets('filter: saves optional notes "$notes" with stable ID', (
      tester,
    ) async {
      final fake = await _openSheet(tester, 'FILTER_CLEAN', 'Filter Cleaning');
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Notes (optional)'),
        notes,
      );
      await tester.tap(find.text('Save Filter Cleaning'));
      await tester.pumpAndSettle();
      final insert = fake.callsTo('insertIntervention').single.args;
      final posted = fake.callsTo('logFilterClean').single.args;
      expect(insert['event_type'], 'FILTER_CLEAN');
      expect(insert['notes'], notes.isEmpty ? null : notes);
      expect(posted['notes'], insert['notes']);
      expect(posted['eventId'], insert['event_id']);
    });
  }

  testWidgets(
    'empty: the feeding form opens with defaults and the fixed time',
    (tester) async {
      await _openSheet(tester, 'FEEDING', 'Feeding Session');

      expect(find.text('Log Feeding Session'), findsOneWidget);
      expect(find.text('Food per session (g)'), findsOneWidget);
      expect(find.text('Protein Content (%)'), findsOneWidget);
      expect(find.widgetWithText(TextFormField, '10'), findsOneWidget);
      expect(find.text('12/8/2026'), findsOneWidget);
      expect(find.text('Save Feeding Session'), findsOneWidget);
    },
  );

  testWidgets('each event type shows its own fields', (tester) async {
    await _openSheet(tester, 'WATER_CHANGE', 'Water Change');
    expect(find.text('Volume Percentage (%)'), findsOneWidget);
    expect(find.text('Volume (Litres) [Optional]'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await _openSheet(tester, 'ALGAE_SCRUB', 'Algae Scrub');
    expect(find.text('Scrub Method'), findsOneWidget);
    expect(find.text('Manual Scrub'), findsOneWidget);
  });

  testWidgets('data: saving a feed writes the record, pushes it to the twin '
      'and reports the buffer status', (tester) async {
    final fake = await _openSheet(
      tester,
      'FEEDING',
      'Feeding Session',
      source: FakePondDataSource(eventAssessment: Fixtures.assessment()),
    );

    await tester.enterText(
      find.widgetWithText(TextFormField, 'Food per session (g)'),
      '15',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Protein Content (%)'),
      '35',
    );
    await tester.tap(find.text('Save Feeding Session'));
    await tester.pumpAndSettle();

    final insert = fake.callsTo('insertIntervention').single.args;
    expect(insert['userID'], 7);
    expect(insert['event_type'], 'FEEDING');
    expect(insert['food_grams'], 15.0);
    expect(insert['protein_percentage'], 35.0);
    expect(insert['event_timestamp'], '2026-08-12T09:30:00.000');

    final push = fake.callsTo('logFeeding').single.args;
    expect(push['userId'], 7);
    expect(push['foodGrams'], 15.0);
    expect(push['proteinPercent'], 35.0);
    expect(push['fishType'], 'Japanese Koi (Kohaku)');
    expect(push['fishCount'], 5);
    // The row and the post carry the same event id, a v4 UUID.
    expect(push['eventId'], insert['event_id']);
    expect(
      insert['event_id'],
      matches(
        RegExp(
          r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
        ),
      ),
    );

    expect(find.text('Log Feeding Session'), findsNothing); // sheet closed
    expect(
      find.text('Feeding Session logged • water buffer status: Watch'),
      findsOneWidget,
    );
  });

  testWidgets('data: an unreachable twin still counts as logged', (
    tester,
  ) async {
    final fake = await _openSheet(tester, 'WATER_TOPUP', 'Water Top-Up');
    await tester.tap(find.text('Save Water Top-Up'));
    await tester.pumpAndSettle();

    expect(
      fake.callsTo('insertIntervention').single.args['volume_percentage'],
      25.0,
    );
    expect(fake.callsTo('logTopUp'), hasLength(1));
    expect(find.text('Water Top-Up logged successfully!'), findsOneWidget);
  });

  testWidgets('loading: the save button shows progress and is disabled', (
    tester,
  ) async {
    final fake = await _openSheet(
      tester,
      'FEEDING',
      'Feeding Session',
      source: FakePondDataSource(hanging: {'insertIntervention'}),
    );
    await tester.tap(find.text('Save Feeding Session'));
    await tester.pump();
    await tester.pump();

    final saveButton = find.descendant(
      of: find.byType(InterventionLogSheet),
      matching: find.byType(ElevatedButton),
    );
    expect(find.text('Save Feeding Session'), findsNothing);
    expect(tester.widget<ElevatedButton>(saveButton).onPressed, isNull);
    expect(
      find.descendant(
        of: saveButton,
        matching: find.byType(CircularProgressIndicator),
      ),
      findsOneWidget,
    );
    expect(fake.callsTo('logFeeding'), isEmpty);
  });

  testWidgets('error: a failed save keeps the sheet open and says why', (
    tester,
  ) async {
    final fake = await _openSheet(
      tester,
      'FEEDING',
      'Feeding Session',
      source: FakePondDataSource(failing: {'insertIntervention'}),
    );
    await tester.tap(find.text('Save Feeding Session'));
    await tester.pumpAndSettle();

    expect(find.text('Log Feeding Session'), findsOneWidget);
    expect(find.text('Save Feeding Session'), findsOneWidget);
    expect(
      find.text('Error: FakeSourceError(insertIntervention)'),
      findsOneWidget,
    );
    expect(fake.callsTo('logFeeding'), isEmpty);
  });

  testWidgets('error: an invalid amount is rejected before anything is sent', (
    tester,
  ) async {
    final fake = await _openSheet(tester, 'FEEDING', 'Feeding Session');
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Food per session (g)'),
      'abc',
    );
    await tester.tap(find.text('Save Feeding Session'));
    await tester.pumpAndSettle();

    expect(find.text('Enter valid value'), findsOneWidget);
    expect(fake.calls, isEmpty);
  });
}
