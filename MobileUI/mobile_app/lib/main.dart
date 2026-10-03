import 'package:flutter/material.dart';
import 'package:flutter/services.dart'; // Added for native system bar styling
import 'package:shared_preferences/shared_preferences.dart';
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
  // Configure OS native top status bar & bottom device navigation bar
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent, // Seamless top bar
      statusBarIconBrightness: Brightness.light, // White status icons
      systemNavigationBarColor: Color(0xFF0F2027), // Deep Navy bottom bar
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );

  final prefs = await SharedPreferences.getInstance();
  final bool isOnboarded = prefs.getBool('isOnboarded') ?? false;

  runApp(KoiMonitorApp(isOnboarded: isOnboarded));
}

class KoiMonitorApp extends StatelessWidget {
  const KoiMonitorApp({super.key, required this.isOnboarded});

  final bool isOnboarded;

  @override
  Widget build(BuildContext context) {
    // Theme Palette Constants
    const primaryNavy = Color(0xFF0F2027);
    const surfaceTeal = Color(0xFF1A323C);
    const accentMint = Color(0xFF00E676);

    return MaterialApp(
      title: 'OutdoorKoi',
      debugShowCheckedModeBanner:
          false, // Removes the "DEBUG" banner in the corner
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark, // Switches entire app default to dark mode
        scaffoldBackgroundColor: primaryNavy,

        colorScheme: const ColorScheme.dark(
          primary: accentMint,
          secondary: Colors.tealAccent,
          surface: surfaceTeal,
          onPrimary: Colors.black,
          onSurface: Colors.white,
        ),

        // --- TOP BANNER (APP BAR) STYLING ---
        appBarTheme: const AppBarTheme(
          backgroundColor: primaryNavy,
          elevation: 0,
          scrolledUnderElevation: 0,
          centerTitle: false,
          titleTextStyle: TextStyle(
            color: Colors.white,
            fontSize: 20,
            fontWeight: FontWeight.bold,
          ),
          iconTheme: IconThemeData(color: Colors.white),
        ),

        // --- BOTTOM BANNER (NAVIGATION BAR) STYLING ---
        navigationBarTheme: NavigationBarThemeData(
          backgroundColor: primaryNavy,
          indicatorColor: accentMint.withValues(alpha: 0.2),
          labelTextStyle: WidgetStateProperty.all(
            const TextStyle(color: Colors.white70, fontSize: 12),
          ),
          iconTheme: WidgetStateProperty.resolveWith((states) {
            if (states.contains(WidgetState.selected)) {
              return const IconThemeData(color: accentMint);
            }
            return const IconThemeData(color: Colors.white54);
          }),
        ),

        // Legacy BottomNavigationBar support (if used in main_layout.dart)
        bottomNavigationBarTheme: const BottomNavigationBarThemeData(
          backgroundColor: primaryNavy,
          selectedItemColor: accentMint,
          unselectedItemColor: Colors.white54,
          elevation: 0,
        ),
      ),
      home: isOnboarded ? const MainLayout() : const OnboardingScreen(),
    );
  }
}
