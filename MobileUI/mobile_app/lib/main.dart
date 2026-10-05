import 'theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'data/local_profile_repository.dart';
import 'data/providers.dart';
import 'package:supabase_flutter/supabase_flutter.dart'; // Updated import for Supabase Flutter
import 'config/app_config.dart';
import 'config/error_tracking.dart';
import 'screens/config_error_screen.dart';
// Import your screens
import 'screens/onboarding_screen.dart';
import 'screens/main_layout.dart';

void main() async {
  // Ensure Flutter engine bindings are initialized before async tasks
  WidgetsFlutterBinding.ensureInitialized();
  // Reports crashes to Sentry when the build has a SENTRY_DSN.
  await runWithErrorTracking(ErrorTrackingConfig.build, _startApp);
}

Future<void> _startApp() async {
  const config = AppConfig.environment;
  final problems = config.problems;
  if (problems.isNotEmpty) {
    runApp(ConfigErrorApp(problems: problems));
    return;
  }
  await Supabase.initialize(
    url: config.supabaseUrl,
    publishableKey: config.supabasePublishableKey,
  );
  final repository = PreferencesProfileRepository();
  final profile = await repository.load();
  runApp(
    ProviderScope(
      retry: (_, _) => null,
      overrides: [localProfileRepositoryProvider.overrideWithValue(repository)],
      child: KoiMonitorApp(isOnboarded: profile.isOnboarded),
    ),
  );
}

class KoiMonitorApp extends StatelessWidget {
  const KoiMonitorApp({super.key, required this.isOnboarded});

  final bool isOnboarded;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'OutdoorKoi',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      home: isOnboarded ? const MainLayout() : const OnboardingScreen(),
    );
  }
}
