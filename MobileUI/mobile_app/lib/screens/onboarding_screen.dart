import 'package:flutter/material.dart';
import 'main_layout.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase/supabase.dart';
import 'package:geolocator/geolocator.dart';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  bool useCurrentLocation = true;
  final TextEditingController _volumeController = TextEditingController();
  final TextEditingController _biomassController = TextEditingController();
  final TextEditingController _locationcontroller = TextEditingController();

  String _selectedRegion = 'Central';
  final List<String> _sgRegions = ['North', 'South', 'East', 'West', 'Central'];

  Future<void> _saveAndContinue() async {
    final prefs = await SharedPreferences.getInstance();
    final volume = _volumeController.text;
    final biomass = _biomassController.text;

    int manualpostallocation = 0;
    if (!useCurrentLocation && _locationcontroller.text.isNotEmpty) {
      manualpostallocation = int.tryParse(_locationcontroller.text) ?? 0;
    }

    final region = _selectedRegion;
    List<String> locationData = [];

    // --- Geolocation Exception & State Defences ---
    if (useCurrentLocation) {
      try {
        locationData = await _getCurrentLocation();
      } catch (e) {
        print("Location retrieval failed: $e");
      }
    }

    // CRITICAL GUARD: Check if widget is still alive in the widget tree after async gap
    if (!mounted) return;

    String latitude = '0';
    String longitude = '0';

    if (locationData.isNotEmpty) {
      latitude = locationData[0];
      longitude = locationData[1];
    }

    // Writing onboarding data locally into shared preferences
    await prefs.setString('tankVolume', volume);
    await prefs.setString('fishBiomass', biomass);
    await prefs.setString('sgRegion', region);
    await prefs.setBool('isOnboarded', true);
    await prefs.setString('latitude', latitude);
    await prefs.setString('longitude', longitude);

    // Writing to Supabase
    const String supabaseURL = String.fromEnvironment('SUPABASE_URL');
    const String supabaseAnonKey = String.fromEnvironment(
      'SUPABASE_PUBLISHABLE_KEY',
    );

    final supabaseClient = SupabaseClient(supabaseURL, supabaseAnonKey);

    try {
      final response = await supabaseClient
          .from('UserData')
          .insert({
            'volume': volume,
            'biomass': biomass,
            'region': region,
            'latitude': latitude,
            'longitude': longitude,
            'manualpostallocation': manualpostallocation,
          })
          .select('id')
          .single();

      final int newId = response['id'];
      await prefs.setInt('userId', newId);
      print("Data saved to Supabase with ID: $newId");
    } catch (supabaseError) {
      print("Supabase write failure: $supabaseError");
    }

    if (!mounted) return;

    // Route out cleanly, wiping Onboarding away
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (context) => const MainLayout()),
    );
  }

  @override
  void dispose() {
    _volumeController.dispose();
    _biomassController.dispose();
    _locationcontroller.dispose(); // Added missing cleanup dispose call
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Koi Pond Setup')),
      // FIX: SingleChildScrollView prevents standard keyboard pixel overflows
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                "Let's baseline your ecosystem.",
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

              SwitchListTile(
                title: const Text("Use Smartphone Current Location"),
                subtitle: const Text(
                  "Automatically find your nearest NEA station",
                ),
                value: useCurrentLocation,
                activeThumbColor: Colors.teal,
                onChanged: (bool value) {
                  setState(() {
                    useCurrentLocation = value;
                  });
                },
              ),
              const SizedBox(height: 16),

              if (!useCurrentLocation) ...[
                TextField(
                  controller: _locationcontroller,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Manual Location Input (Postal Code)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
              ],

              DropdownButtonFormField<String>(
                initialValue:
                    _selectedRegion, // Fixed initialValue warning issue
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

              // FIX: Replaced Spacer() with a concrete margin boundary
              // for safe rendering inside a scroll layout
              const SizedBox(height: 40),

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
      ),
    );
  }
}

Future<List<String>> _getCurrentLocation() async {
  bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
  if (!serviceEnabled) {
    return Future.error('Location services are disabled.');
  }

  LocationPermission permission = await Geolocator.checkPermission();
  if (permission == LocationPermission.denied) {
    permission = await Geolocator.requestPermission();
    if (permission == LocationPermission.denied) {
      return Future.error('Location permissions are denied');
    }
  }

  if (permission == LocationPermission.deniedForever) {
    return Future.error('Location permissions are permanently denied.');
  }

  const LocationSettings locationSettings = LocationSettings(
    accuracy: LocationAccuracy.high,
    distanceFilter: 100,
  );

  Position position = await Geolocator.getCurrentPosition(
    locationSettings: locationSettings,
  );

  return [position.latitude.toString(), position.longitude.toString()];
}
