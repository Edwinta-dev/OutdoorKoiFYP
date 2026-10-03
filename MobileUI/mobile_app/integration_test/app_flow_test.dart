// Onboarding -> dashboard -> log a feed on a device or desktop build:
//   flutter test integration_test            (picks a connected device)
//   flutter test integration_test -d windows
// Data comes from FakePondDataSource and SharedPreferences is mocked, so
// the run touches neither the live Supabase project nor the device's
// stored settings. The steps are shared with test/onboarding_flow_test.dart.
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/flows/onboarding_to_feed_flow.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('first run: onboarding, dashboard, log a feed', (tester) async {
    await runOnboardingToFeedFlow(tester);
  });
}
