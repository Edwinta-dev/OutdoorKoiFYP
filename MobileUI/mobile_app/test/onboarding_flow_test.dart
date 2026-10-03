// Onboarding -> dashboard -> log a feed, with fake data sources. The same
// steps run on a device in integration_test/app_flow_test.dart.
import 'package:flutter_test/flutter_test.dart';

import 'flows/onboarding_to_feed_flow.dart';
import 'helpers/pump_screen.dart';

void main() {
  testWidgets('first run: onboarding, dashboard, log a feed', (tester) async {
    tester.view.physicalSize = phoneSize;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await runOnboardingToFeedFlow(tester);
  });
}
