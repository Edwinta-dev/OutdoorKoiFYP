import 'package:flutter/material.dart';
// Import your screens
import 'screens/onboarding_screen.dart';

void main() {
  runApp(const KoiMonitorApp());
}

class KoiMonitorApp extends StatelessWidget {
  const KoiMonitorApp({super.key});

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
      // For now, we hardcode the app to start on the Onboarding screen.
      // Later, this is where we will add a check to see if the user has already onboarded!
      home: const OnboardingScreen(),
    );
  }
}
