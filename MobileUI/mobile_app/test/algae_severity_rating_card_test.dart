// AlgaeSeverityRatingCard in each state: loading, empty (no frame, no
// analysis service), data, error; plus submit and undo.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/widgets/detail_graph/algae_severity_rating_card.dart';

import 'helpers/fake_pond_data_source.dart';
import 'helpers/fixtures.dart';
import 'helpers/pump_screen.dart';

Widget _card({VoidCallback? onRatingChanged}) => Scaffold(
  body: SingleChildScrollView(
    padding: const EdgeInsets.all(16),
    child: AlgaeSeverityRatingCard(userId: 7, onRatingChanged: onRatingChanged),
  ),
);

FakePondDataSource _dataSource() => FakePondDataSource(
  frame: Fixtures.frame(),
  ratingContext: Fixtures.ratingContext(),
  ratingResult: Fixtures.ratingResult(),
);

void main() {
  testWidgets('loading: spinner while the frame is pending', (tester) async {
    await pumpScreen(
      tester,
      _card(),
      source: FakePondDataSource(hanging: {'fetchLatestFrame'}),
    );
    await settle(tester);

    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.text('Rate What You See'), findsNothing);
  });

  testWidgets('empty: no frame and no analysis service', (tester) async {
    await pumpScreen(
      tester,
      _card(),
      source: FakePondDataSource(
        frameError: 'The pond camera has not reported yet.',
      ),
    );
    await settle(tester);

    expect(find.text('Rate What You See'), findsOneWidget);
    expect(find.text('No camera frame yet'), findsOneWidget);
    expect(find.text('The pond camera has not reported yet.'), findsOneWidget);
    expect(find.text('How does the pond look right now?'), findsOneWidget);
    expect(find.textContaining('Analysis service unreachable'), findsOneWidget);
    expect(find.text('Submit rating'), findsNothing);
  });

  testWidgets('data: options, previous rating and calibration progress', (
    tester,
  ) async {
    await pumpScreen(tester, _card(), source: _dataSource());
    await settle(tester);

    for (final s in AlgaeSeverity.values) {
      expect(find.text(s.label), findsOneWidget);
    }
    expect(
      find.text('Last time you rated this "Minor growth"'),
      findsOneWidget,
    );
    expect(find.text('Submit rating'), findsOneWidget);
    // Nothing selected yet, so submit is disabled.
    final submit = tester.widget<FilledButton>(find.byType(FilledButton));
    expect(submit.onPressed, isNull);
  });

  testWidgets('error: a failed load offers a retry', (tester) async {
    final fake = await pumpScreen(
      tester,
      _card(),
      source: FakePondDataSource(failing: {'fetchLatestFrame'}),
    );
    await settle(tester);

    expect(
      find.textContaining('Could not load the pond camera'),
      findsOneWidget,
    );

    fake.failing.clear();
    await tester.tap(find.text('Retry'));
    await settle(tester);
    expect(find.text('Rate What You See'), findsOneWidget);
    expect(fake.callsTo('fetchLatestFrame'), hasLength(2));
  });

  testWidgets('submit pins the rated frame, shows the effect, and undo '
      'reverses it', (tester) async {
    var changed = 0;
    final fake = await pumpScreen(
      tester,
      _card(onRatingChanged: () => changed++),
      source: _dataSource(),
    );
    await settle(tester);

    await tester.tap(find.text(AlgaeSeverity.moderate.label));
    await tester.pump();
    await tester.tap(find.text('Submit rating'));
    await settle(tester);

    final call = fake.callsTo('submitAlgaeRating').single.args;
    expect(call['severity'], AlgaeSeverity.moderate);
    expect(call['imageId'], 501);
    expect(call['greenRatio'], 0.12);
    expect(
      find.text('Estimate moved up from 12.00% to 17.00% coverage.'),
      findsOneWidget,
    );
    expect(changed, 1);

    await tester.tap(find.text('Undo'));
    await settle(tester);
    expect(fake.callsTo('undoAlgaeRating').single.args['ratingId'], 42);
    expect(find.textContaining('Estimate moved up'), findsNothing);
    expect(changed, 2);
  });

  testWidgets('a rejected rating shows the server reason', (tester) async {
    final fake = _dataSource()
      ..ratingResult = null
      ..ratingError = 'That frame is no longer the newest.';
    await pumpScreen(tester, _card(), source: fake);
    await settle(tester);

    await tester.tap(find.text(AlgaeSeverity.none.label));
    await tester.pump();
    await tester.tap(find.text('Submit rating'));
    await settle(tester);

    expect(find.text('That frame is no longer the newest.'), findsOneWidget);
  });
}
