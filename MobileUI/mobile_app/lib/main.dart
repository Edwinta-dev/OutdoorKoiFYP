import 'package:flutter/material.dart';
// Import your screens
import 'screens/onboarding_screen.dart';
import 'screens/main_layout.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() async {
  // Ensure Flutter engine bindings are initialized before async tasks
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  final bool isOnboarded = prefs.getBool('isOnboarded') ?? false;

  runApp(KoiMonitorApp(isOnboarded: isOnboarded));
}

class KoiMonitorApp extends StatelessWidget {
  const KoiMonitorApp({super.key, required this.isOnboarded});

  final bool isOnboarded;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Koi Monitor',
      debugShowCheckedModeBanner:
          false, // Removes the "DEBUG" banner in the corner
      theme: ThemeData(
        // Using teal as a seed color fits the aquatic theme nicely
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        useMaterial3: true,
      ),
      home: isOnboarded ? const MainLayout() : const OnboardingScreen(),
    );
  }
}
