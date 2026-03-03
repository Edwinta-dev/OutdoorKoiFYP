import 'package:flutter/material.dart';
// import 'home_screen.dart'; // We will route to this later
import 'main_layout.dart';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  // Controllers to grab the text typed by the user
  final TextEditingController _volumeController = TextEditingController();
  final TextEditingController _biomassController = TextEditingController();

  // Default value for the Singapore region dropdown
  String _selectedRegion = 'Central';
  final List<String> _sgRegions = ['North', 'South', 'East', 'West', 'Central'];

  void _saveAndContinue() {
    // TODO: Write these values to shared_preferences or Supabase user_profile here.
    final volume = _volumeController.text;
    final biomass = _biomassController.text;
    final region = _selectedRegion;
    // After saving, navigate to the Home Screen and remove Onboarding from the stack
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (context) => const MainLayout()),
    );
    print("Saved! Transitioning to Dashboard...");
  }

  @override
  void dispose() {
    _volumeController.dispose();
    _biomassController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Koi Pond Setup')),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Let\'s baseline your ecosystem.',
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 24),

            TextField(
              controller: _volumeController,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Tank Volume (Liters)',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 16),

            TextField(
              controller: _biomassController,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Total Fish Biomass (Est. kg)',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 16),

            DropdownButtonFormField<String>(
              value: _selectedRegion,
              decoration: const InputDecoration(
                labelText: 'Location in Singapore',
                border: OutlineInputBorder(),
              ),
              items: _sgRegions.map((String region) {
                return DropdownMenuItem(value: region, child: Text(region));
              }).toList(),
              onChanged: (String? newValue) {
                setState(() {
                  _selectedRegion = newValue!;
                });
              },
            ),

            const Spacer(), // Pushes the button to the bottom

            SizedBox(
              width: double.infinity,
              height: 50,
              child: ElevatedButton(
                onPressed: _saveAndContinue,
                child: const Text(
                  'Save & Go to Dashboard',
                  style: TextStyle(fontSize: 18),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
