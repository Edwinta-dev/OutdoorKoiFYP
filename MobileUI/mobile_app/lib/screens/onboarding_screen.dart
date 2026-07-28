import 'package:flutter/material.dart';
import 'main_layout.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase/supabase.dart';
import 'package:geolocator/geolocator.dart';
import 'dart:convert';

// --- Data Model for Fish Inhabitant Items ---
class FishEntry {
  final String speciesName;
  final int count;
  final double avgWeightKg;

  FishEntry({
    required this.speciesName,
    required this.count,
    required this.avgWeightKg,
  });

  double get totalWeight => count * avgWeightKg;

  Map<String, dynamic> toJson() => {
    'speciesName': speciesName,
    'count': count,
    'avgWeightKg': avgWeightKg,
    'totalWeightKg': totalWeight,
  };
}

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  bool useCurrentLocation = true;
  final TextEditingController _volumeController = TextEditingController();
  final TextEditingController _locationcontroller = TextEditingController();
  final TextEditingController _userIDcontroller = TextEditingController();
  // Fish Stock Input Controllers
  final TextEditingController _fishCountController = TextEditingController();
  final TextEditingController _fishWeightController = TextEditingController();
  String _currentSelectedSpecies = '';

  // Fish Species Dictionary downloaded from Supabase
  List<String> _speciesDictionary = [];
  bool _isLoadingSpecies = true;

  // Active user's added fish inventory
  final List<FishEntry> _addedFishList = [];

  @override
  void initState() {
    super.initState();
    _fetchFishSpeciesDictionary();
  }

  /// Opens a full-screen/bottom-sheet modal with a live search bar
  /// Opens a full-screen/bottom-sheet modal with a live search bar
  void _openSearchableFishPicker() {
    final TextEditingController searchController = TextEditingController();
    List<String> filteredList = List.from(_speciesDictionary);

    showModalBottomSheet(
      context: context,
      isScrollControlled: true, // Key for keyboard responsiveness
      backgroundColor: const Color(0xFF1A323C), // Dark aquatic panel color
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (BuildContext modalContext) {
        return StatefulBuilder(
          builder: (context, setModalState) {
            return Padding(
              padding: EdgeInsets.only(
                bottom: MediaQuery.of(modalContext).viewInsets.bottom,
                top: 16,
                left: 16,
                right: 16,
              ),
              child: SafeArea(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // 1. Header Row
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Text(
                          'Select Fish Species',
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                            color: Colors.white,
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.close, color: Colors.white70),
                          onPressed: () => Navigator.pop(modalContext),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),

                    // 2. Search Field
                    TextField(
                      controller: searchController,
                      autofocus: true,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        hintText: 'Search or type custom species name...',
                        hintStyle: const TextStyle(color: Colors.white54),
                        prefixIcon: const Icon(
                          Icons.search,
                          color: Colors.tealAccent,
                        ),
                        suffixIcon: searchController.text.isNotEmpty
                            ? IconButton(
                                icon: const Icon(
                                  Icons.clear,
                                  color: Colors.white54,
                                ),
                                onPressed: () {
                                  searchController.clear();
                                  setModalState(() {
                                    filteredList = List.from(
                                      _speciesDictionary,
                                    );
                                  });
                                },
                              )
                            : null,
                        filled: true,
                        fillColor: Colors.white.withOpacity(0.08),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      onChanged: (query) {
                        setModalState(() {
                          filteredList = _speciesDictionary
                              .where(
                                (item) => item.toLowerCase().contains(
                                  query.toLowerCase(),
                                ),
                              )
                              .toList();
                        });
                      },
                    ),
                    const SizedBox(height: 12),

                    // 3. FLEXIBLE SCROLLABLE CONTAINER (Prevents 4.4px Overflow)
                    Flexible(
                      child: Container(
                        constraints: BoxConstraints(
                          maxHeight:
                              MediaQuery.of(modalContext).size.height * 0.35,
                        ),
                        child: filteredList.isEmpty
                            ? SingleChildScrollView(
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    vertical: 16,
                                  ),
                                  child: Center(
                                    child: Column(
                                      children: [
                                        const Text(
                                          'No matching species found in database.',
                                          style: TextStyle(
                                            color: Colors.white70,
                                          ),
                                        ),
                                        const SizedBox(height: 10),
                                        if (searchController.text.isNotEmpty)
                                          ElevatedButton.icon(
                                            style: ElevatedButton.styleFrom(
                                              backgroundColor: const Color(
                                                0xFF00E676,
                                              ),
                                            ),
                                            icon: const Icon(
                                              Icons.add,
                                              color: Colors.black,
                                            ),
                                            label: Text(
                                              'Use "${searchController.text.trim()}"',
                                              style: const TextStyle(
                                                color: Colors.black,
                                              ),
                                            ),
                                            onPressed: () {
                                              setState(() {
                                                _currentSelectedSpecies =
                                                    searchController.text
                                                        .trim();
                                              });
                                              Navigator.pop(modalContext);
                                            },
                                          ),
                                      ],
                                    ),
                                  ),
                                ),
                              )
                            : ListView.separated(
                                shrinkWrap: true,
                                itemCount: filteredList.length,
                                separatorBuilder: (_, __) => Divider(
                                  color: Colors.white.withOpacity(0.1),
                                ),
                                itemBuilder: (context, index) {
                                  final speciesName = filteredList[index];
                                  return ListTile(
                                    dense: true,
                                    leading: const Icon(
                                      Icons.set_meal_rounded,
                                      color: Color(0xFF00E676),
                                    ),
                                    title: Text(
                                      speciesName,
                                      style: const TextStyle(
                                        color: Colors.white,
                                      ),
                                    ),
                                    onTap: () {
                                      setState(() {
                                        _currentSelectedSpecies = speciesName;
                                      });
                                      Navigator.pop(modalContext);
                                    },
                                  );
                                },
                              ),
                      ),
                    ),
                    const SizedBox(height: 10),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  /// Fetch species dictionary list from Supabase
  Future<void> _fetchFishSpeciesDictionary() async {
    const String supabaseURL = String.fromEnvironment('SUPABASE_URL');
    const String supabaseAnonKey = String.fromEnvironment(
      'SUPABASE_SERVICEROLE_KEY',
    );

    if (supabaseURL.isEmpty || supabaseAnonKey.isEmpty) {
      // Fallback default list if Supabase env parameters are not configured during testing
      if (mounted) {
        setState(() {
          _speciesDictionary = [
            'Japanese Koi (Kohaku)',
            'Japanese Koi (Taisho Sanke)',
            'Japanese Koi (Showa Sanshoku)',
            'Butterfly Koi',
            'Comet Goldfish',
            'Shubunkin Goldfish',
            'Fantail Goldfish',
            'Plecostomus (Algae Eater)',
          ];
          _isLoadingSpecies = false;
        });
      }
      return;
    }

    try {
      final supabaseClient = SupabaseClient(supabaseURL, supabaseAnonKey);
      // Query fish dictionary table
      final response = await supabaseClient
          .from('Fish_Database')
          .select('Title')
          .order('Title', ascending: true);
      print("Fish Dictionary: $response");
      final List<dynamic> data = response as List<dynamic>;
      final List<String> fetchedNames = data
          .map((item) => item['Title'].toString())
          .toList();

      if (mounted) {
        setState(() {
          _speciesDictionary = fetchedNames;
          _isLoadingSpecies = false;
        });
      }
    } catch (e) {
      print("Failed to fetch species dictionary from Supabase: $e");
      if (mounted) {
        setState(() {
          _isLoadingSpecies = false;
        });
      }
    }
  }

  /// Calculates sum of total fish biomass across all added entries
  double get _totalCalculatedBiomass {
    return _addedFishList.fold(0.0, (sum, item) => sum + item.totalWeight);
  }

  void _addFishEntry() {
    final species = _currentSelectedSpecies.trim();
    final count = int.tryParse(_fishCountController.text) ?? 0;
    final weight = double.tryParse(_fishWeightController.text) ?? 0.0;

    if (species.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Please enter or select a fish species.')),
      );
      return;
    }

    if (count <= 0 || weight <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please enter valid fish count and weight.'),
        ),
      );
      return;
    }

    setState(() {
      _addedFishList.add(
        FishEntry(speciesName: species, count: count, avgWeightKg: weight),
      );
      _fishCountController.clear();
      _fishWeightController.clear();
      _currentSelectedSpecies = '';
    });
  }

  void _removeFishEntry(int index) {
    setState(() {
      _addedFishList.removeAt(index);
    });
  }

  Future<void> _saveAndContinue() async {
    final prefs = await SharedPreferences.getInstance();
    final volume = _volumeController.text;
    final totalBiomassKg = _totalCalculatedBiomass.toStringAsFixed(2);
    final userID = _userIDcontroller.text;
    int manualpostallocation = 0;
    if (!useCurrentLocation && _locationcontroller.text.isNotEmpty) {
      manualpostallocation = int.tryParse(_locationcontroller.text) ?? 0;
    }

    List<String> locationData = [];

    // --- Geolocation Exception & State Defences ---
    if (useCurrentLocation) {
      try {
        locationData = await _getCurrentLocation();
      } catch (e) {
        print("Location retrieval failed: $e");
      }
    }

    if (!mounted) return;

    String latitude = '0';
    String longitude = '0';

    if (locationData.isNotEmpty) {
      latitude = locationData[0];
      longitude = locationData[1];
    }

    // 1. EXTRACT UNIQUE SPECIES NAMES ONLY (Filters out duplicate entries)
    final List<String> uniqueOwnedSpecies = _addedFishList
        .map((f) => f.speciesName)
        .toSet() // Removes duplicate names if user added same species twice
        .toList();

    // Writing onboarding data locally into Shared Preferences
    await prefs.setString('tankVolume', volume);
    await prefs.setString('fishBiomass', totalBiomassKg);
    await prefs.setStringList('ownedFishSpecies', uniqueOwnedSpecies);
    await prefs.setBool('isOnboarded', true);
    await prefs.setString('latitude', latitude);
    await prefs.setString('longitude', longitude);
    await prefs.setString('userID', userID);
    print("Tank Volume: $volume");
    print("Total Biomass: $totalBiomassKg");
    print("Owned Fish Species: $uniqueOwnedSpecies");
    print("Longitude: $longitude");
    print("Latitude: $latitude");
    print("User ID: $userID");
    // Writing to Supabase
    const String supabaseURL = String.fromEnvironment('SUPABASE_URL');
    const String supabaseAnonKey = String.fromEnvironment(
      'SUPABASE_SERVICEROLE_KEY',
    );

    if (supabaseURL.isNotEmpty && supabaseAnonKey.isNotEmpty) {
      final supabaseClient = SupabaseClient(supabaseURL, supabaseAnonKey);

      try {
        final response = await supabaseClient.from('UserData').insert({
          'volume': volume,
          'biomass': totalBiomassKg,
          'latitude': latitude,
          'longitude': longitude,
          'manualpostallocation': manualpostallocation,
          'userID': userID,
        });
      } catch (supabaseError) {
        print("Supabase write failure: $supabaseError");
      }
    }

    if (!mounted) return;

    // Route out cleanly, replacing Onboarding screen
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (context) => const MainLayout()),
    );
  }

  @override
  void dispose() {
    _volumeController.dispose();
    _locationcontroller.dispose();
    _fishCountController.dispose();
    _fishWeightController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Koi Pond Setup')),
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

              // Tank Volume Input
              TextField(
                controller: _volumeController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Tank Volume (Liters)',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.water_rounded),
                ),
              ),
              const SizedBox(height: 24),

              // --- AUGMENTED FISH BIOMASS & STOCK SECTION ---
              const Text(
                "Pond Inhabitants & Fish Stock",
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 4),
              const Text(
                "Search or manually enter species, quantities, and average weights.",
                style: TextStyle(fontSize: 12, color: Colors.white70),
              ),
              const SizedBox(height: 12),

              // 1. Searchable Autocomplete Fish Species Input
              _isLoadingSpecies
                  ? const LinearProgressIndicator()
                  : // 1. Searchable Dropdown Selector Button
                    InkWell(
                      onTap: _isLoadingSpecies
                          ? null
                          : _openSearchableFishPicker,
                      borderRadius: BorderRadius.circular(12),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 16,
                        ),
                        decoration: BoxDecoration(
                          color: Colors.white.withOpacity(0.07),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(
                            color: _currentSelectedSpecies.isNotEmpty
                                ? Colors.tealAccent
                                : Colors.white.withOpacity(0.2),
                          ),
                        ),
                        child: Row(
                          children: [
                            const Icon(
                              Icons.search_rounded,
                              color: Colors.tealAccent,
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Text(
                                _currentSelectedSpecies.isEmpty
                                    ? 'Tap to Search or Select Fish Species'
                                    : _currentSelectedSpecies,
                                style: TextStyle(
                                  color: _currentSelectedSpecies.isEmpty
                                      ? Colors.white54
                                      : Colors.white,
                                  fontSize: 16,
                                  fontWeight: _currentSelectedSpecies.isEmpty
                                      ? FontWeight.normal
                                      : FontWeight.bold,
                                ),
                              ),
                            ),
                            const Icon(
                              Icons.arrow_drop_down_rounded,
                              color: Colors.white70,
                            ),
                          ],
                        ),
                      ),
                    ),
              const SizedBox(height: 12),

              // 2. Quantity & Average Weight Input Row
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _fishCountController,
                      keyboardType: TextInputType.number,
                      decoration: const InputDecoration(
                        labelText: 'Quantity',
                        hintText: 'e.g. 5',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: TextField(
                      controller: _fishWeightController,
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                      ),
                      decoration: const InputDecoration(
                        labelText: 'Avg Weight (kg)',
                        hintText: 'e.g. 0.8',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),

              // 3. Add Fish Button
              OutlinedButton.icon(
                onPressed: _addFishEntry,
                icon: const Icon(Icons.add_circle_outline_rounded),
                label: const Text('Add Fish to Stock'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(double.infinity, 45),
                ),
              ),
              const SizedBox(height: 16),

              // 4. Added Fish Entries List & Total Calculated Biomass Card
              if (_addedFishList.isNotEmpty) ...[
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.teal.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: Colors.teal.withOpacity(0.4)),
                  ),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text(
                        "Total Biomass Estimate:",
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 15,
                        ),
                      ),
                      Text(
                        "${_totalCalculatedBiomass.toStringAsFixed(2)} kg",
                        style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 18,
                          color: Color(0xFF00E676),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 10),
                ListView.builder(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: _addedFishList.length,
                  itemBuilder: (context, index) {
                    final item = _addedFishList[index];
                    return Card(
                      margin: const EdgeInsets.symmetric(vertical: 4),
                      child: ListTile(
                        dense: true,
                        leading: const CircleAvatar(
                          backgroundColor: Colors.teal,
                          child: Icon(
                            Icons.set_meal_rounded,
                            size: 18,
                            color: Colors.white,
                          ),
                        ),
                        title: Text(
                          item.speciesName,
                          style: const TextStyle(fontWeight: FontWeight.bold),
                        ),
                        subtitle: Text(
                          "${item.count} fish × ${item.avgWeightKg} kg = ${item.totalWeight.toStringAsFixed(2)} kg total",
                        ),
                        trailing: IconButton(
                          icon: const Icon(
                            Icons.delete_outline,
                            color: Colors.redAccent,
                          ),
                          onPressed: () => _removeFishEntry(index),
                        ),
                      ),
                    );
                  },
                ),
              ],
              const SizedBox(height: 24),

              // Location Mode Toggle
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
              TextField(
                controller: _userIDcontroller,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Find user ID on the hardware for syncing',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              // Save Button
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
