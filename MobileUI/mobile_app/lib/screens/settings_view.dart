import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase/supabase.dart';
import 'onboarding_screen.dart';

class SettingsView extends StatefulWidget {
  const SettingsView({super.key});
  @override
  State<SettingsView> createState() => _SettingsViewState();
}

class _SettingsViewState extends State<SettingsView> {
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: SingleChildScrollView(
        // ✅ 2. Change Row to Column to stack elements vertically
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 1. The Graph Section
            const Placeholder(
              fallbackHeight: 200,
            ), // Temporary stand-in for your graph

            const SizedBox(height: 16), // Adds clean spacing between elements
            // 2. The Menu Section Title
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16.0),
              child: Text(
                'Quick Actions',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
            ),
            // 3. The Menu Items
            ListTile(
              leading: const Icon(Icons.water_drop, color: Colors.teal),
              title: const Text('Water Quality Log'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 16),
              onTap: () {
                /* Navigate to log page */
              },
            ),
            ListTile(
              leading: const Icon(Icons.scale, color: Colors.teal),
              title: const Text('Biomass Tracker'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 16),
              onTap: () {
                /* Navigate to biomass page */
              },
            ),

            TextButton(
              style: ButtonStyle(
                foregroundColor: WidgetStateProperty.all<Color>(Colors.red),
              ),
              onPressed:
                  deletePondData, // Implement this function to handle data deletion

              child: Text('Delete Pond Data', style: TextStyle(fontSize: 16)),
            ),
          ],
        ),
      ),
    );
  }

  void deletePondData() async {
    // Implement the logic to delete pond data here
    // This could involve clearing shared preferences, database entries, etc.
    final prefs = await SharedPreferences.getInstance();
    final int userid = prefs.getInt('userId') ?? 0;
    print('Deleting pond data for user ID: $userid');
    const String supabaseURL = String.fromEnvironment('SUPABASE_URL');
    const String supabaseAnonKey = String.fromEnvironment(
      'SUPABASE_PUBLISHABLE_KEY',
    );
    final supabaseClient = SupabaseClient(supabaseURL, supabaseAnonKey);
    await supabaseClient
        .from('UserData')
        .delete()
        .eq('id', userid); // Example for Supabase
    print('Pond data deleted'); // Placeholder for actual deletion logic
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (context) => const OnboardingScreen()),
    );
  }
}
