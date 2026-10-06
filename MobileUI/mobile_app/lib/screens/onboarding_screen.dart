import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'main_layout.dart';

import 'package:geolocator/geolocator.dart';
import 'dart:convert';
import '../data/providers.dart';
import '../utils/app_log.dart';

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

class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  bool useCurrentLocation = true;
  final TextEditingController _volumeController = TextEditingController();
  final TextEditingController _locationcontroller = TextEditingController();
  final TextEditingController _userIDcontroller = TextEditingController();
  // Fish Stock Input Controllers
  final TextEditingController _fishCountController = TextEditingController();
  final TextEditingController _fishWeightController = TextEditingController();
  String _currentSelectedSpecies = '';

  List<String> get _speciesDictionary =>
      ref.read(speciesProvider).asData?.value ??
      [
        'Japanese Koi (Kohaku)',
        'Japanese Koi (Taisho Sanke)',
        'Japanese Koi (Showa Sanshoku)',
        'Butterfly Koi',
        'Comet Goldfish',
        'Shubunkin Goldfish',
        'Fantail Goldfish',
        'Plecostomus (Algae Eater)',
      ];
  bool get _isLoadingSpecies => ref.read(speciesProvider).isLoading;
  final List<FishEntry> _addedFishList = [];

  /// Opens a full-screen/bottom-sheet modal with a live search bar
  /// Opens a full-screen/bottom-sheet modal with a live search bar
  void _openSearchableFishPicker() {
    final TextEditingController searchController = TextEditingController();
    List<String> filteredList = List.from(_speciesDictionary);

    showModalBottomSheet(
      context: context,
      isScrollControlled: true, // Key for keyboard responsiveness
      backgroundColor: AppColors.of(
        context,
      ).surfaceRaised, // Dark aquatic panel color
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(AppRadius.panel),
        ),
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
                    AdaptiveRow(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          'Select Fish Species',
                          style: AppType.style(
                            fontSize: AppType.title,
                            fontWeight: FontWeight.bold,
                            color: AppColors.of(context).text,
                          ),
                        ),
                        IconButton(
                          icon: Icon(
                            Icons.close,
                            color: AppColors.of(context).textSecondary,
                          ),
                          onPressed: () => Navigator.pop(modalContext),
                        ),
                      ],
                    ),
                    const SizedBox(height: AppSpace.sm),

                    // 2. Search Field
                    TextField(
                      controller: searchController,
                      autofocus: true,
                      style: AppType.style(color: AppColors.of(context).text),
                      decoration: InputDecoration(
                        hintText: 'Search or type custom species name...',
                        hintStyle: AppType.style(
                          color: AppColors.of(context).textMuted,
                        ),
                        prefixIcon: Icon(
                          Icons.search,
                          color: AppColors.of(context).water,
                        ),
                        suffixIcon: searchController.text.isNotEmpty
                            ? IconButton(
                                icon: Icon(
                                  Icons.clear,
                                  color: AppColors.of(context).textMuted,
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
                        fillColor: AppColors.of(
                          context,
                        ).text.withValues(alpha: 0.08),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(
                            AppRadius.control,
                          ),
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
                    const SizedBox(height: AppSpace.md),

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
                                    vertical: AppSpace.lg,
                                  ),
                                  child: Center(
                                    child: Column(
                                      children: [
                                        Text(
                                          'No matching species found in database.',
                                          style: AppType.style(
                                            color: AppColors.of(
                                              context,
                                            ).textSecondary,
                                          ),
                                        ),
                                        const SizedBox(height: AppSpace.md),
                                        if (searchController.text.isNotEmpty)
                                          ElevatedButton.icon(
                                            style: ElevatedButton.styleFrom(
                                              backgroundColor: AppColors.of(
                                                context,
                                              ).primary,
                                            ),
                                            icon: Icon(
                                              Icons.add,
                                              color: AppColors.of(
                                                context,
                                              ).onPrimary,
                                            ),
                                            label: Text(
                                              'Use "${searchController.text.trim()}"',
                                              style: AppType.style(
                                                color: AppColors.of(
                                                  context,
                                                ).onPrimary,
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
                                separatorBuilder: (_, _) => Divider(
                                  color: AppColors.of(
                                    context,
                                  ).text.withValues(alpha: 0.1),
                                ),
                                itemBuilder: (context, index) {
                                  final speciesName = filteredList[index];
                                  return ListTile(
                                    dense: true,
                                    leading: Icon(
                                      Icons.set_meal_rounded,
                                      color: AppColors.of(context).primary,
                                    ),
                                    title: Text(
                                      speciesName,
                                      style: AppType.style(
                                        color: AppColors.of(context).text,
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
                    const SizedBox(height: AppSpace.md),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
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
    final source = ref.read(pondProfileRepositoryProvider);
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
        log("Location retrieval failed: $e");
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

    // Total headcount across all entries (species can repeat if added twice)
    final int totalFishCount = _addedFishList.fold(
      0,
      (sum, item) => sum + item.count,
    );

    // Writing onboarding data locally into Shared Preferences
    await ref.read(localProfileRepositoryProvider).save({
      'tankVolume': volume,
      'fishBiomass': totalBiomassKg,
      'ownedFishSpecies': uniqueOwnedSpecies,
      'fishCount': totalFishCount,
      'isOnboarded': true,
      'latitude': latitude,
      'longitude': longitude,
      'userID': int.parse(userID),
    });
    if (!mounted) return;
    ref.invalidate(localProfileProvider);
    log("Tank Volume: $volume");
    log("Total Biomass: $totalBiomassKg");
    log("Owned Fish Species: $uniqueOwnedSpecies");
    log("Longitude: $longitude");
    log("Latitude: $latitude");
    log("User ID: $userID");

    try {
      final Map<String, dynamic> profile = {
        'volume': volume,
        'biomass': totalBiomassKg,
        'latitude': latitude,
        'longitude': longitude,
        'manualpostallocation': manualpostallocation,
        'userID': userID,
      };
      final response = await source.upsertUserProfile(profile);
      if (!mounted) return;
      final Map<String, dynamic>? closestStations = response['ClosestStations'];
      if (closestStations != null) {
        log("Assigned NEA Stations: $closestStations");
        // 2. Encode the JSON map to a string and persist to local profile storage
        final String jsonString = jsonEncode(closestStations);
        await ref.read(localProfileRepositoryProvider).save({
          'assignedStationsJson': jsonString,
        });
      } else {
        log("No assigned stations returned from database.");
      }
      log("Supabase write success: $response");
    } catch (supabaseError) {
      log("Supabase write failure: $supabaseError");
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
    ref.watch(speciesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Koi Pond Setup')),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(AppSpace.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                "Let's baseline your ecosystem.",
                style: AppType.style(
                  fontSize: AppType.metric,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: AppSpace.xxl),

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
              const SizedBox(height: AppSpace.xxl),

              // --- AUGMENTED FISH BIOMASS & STOCK SECTION ---
              Text(
                "Pond Inhabitants & Fish Stock",
                style: AppType.style(
                  fontSize: AppType.title,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: AppSpace.xs),
              Text(
                "Search or manually enter species, quantities, and average weights.",
                style: AppType.style(
                  fontSize: AppType.label,
                  color: AppColors.of(context).textSecondary,
                ),
              ),
              const SizedBox(height: AppSpace.md),

              // 1. Searchable Autocomplete Fish Species Input
              _isLoadingSpecies
                  ? const LinearProgressIndicator()
                  : // 1. Searchable Dropdown Selector Button
                    InkWell(
                      onTap: _isLoadingSpecies
                          ? null
                          : _openSearchableFishPicker,
                      borderRadius: BorderRadius.circular(AppRadius.control),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: AppSpace.lg,
                          vertical: AppSpace.lg,
                        ),
                        decoration: BoxDecoration(
                          color: AppColors.of(
                            context,
                          ).text.withValues(alpha: 0.07),
                          borderRadius: BorderRadius.circular(
                            AppRadius.control,
                          ),
                          border: Border.all(
                            color: _currentSelectedSpecies.isNotEmpty
                                ? AppColors.of(context).water
                                : AppColors.of(
                                    context,
                                  ).text.withValues(alpha: 0.2),
                          ),
                        ),
                        child: Row(
                          children: [
                            Icon(
                              Icons.search_rounded,
                              color: AppColors.of(context).water,
                            ),
                            const SizedBox(width: AppSpace.md),
                            Expanded(
                              child: Text(
                                _currentSelectedSpecies.isEmpty
                                    ? 'Tap to Search or Select Fish Species'
                                    : _currentSelectedSpecies,
                                style: AppType.style(
                                  color: _currentSelectedSpecies.isEmpty
                                      ? AppColors.of(context).textMuted
                                      : AppColors.of(context).text,
                                  fontSize: AppType.body,
                                  fontWeight: _currentSelectedSpecies.isEmpty
                                      ? FontWeight.normal
                                      : FontWeight.bold,
                                ),
                              ),
                            ),
                            Icon(
                              Icons.arrow_drop_down_rounded,
                              color: AppColors.of(context).textSecondary,
                            ),
                          ],
                        ),
                      ),
                    ),
              const SizedBox(height: AppSpace.md),

              // 2. Quantity & Average Weight Input Row
              AdaptiveRow(
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
                  const SizedBox(width: AppSpace.md),
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
              const SizedBox(height: AppSpace.md),

              // 3. Add Fish Button
              OutlinedButton.icon(
                onPressed: _addFishEntry,
                icon: const Icon(Icons.add_circle_outline_rounded),
                label: const Text('Add Fish to Stock'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(double.infinity, 45),
                ),
              ),
              const SizedBox(height: AppSpace.lg),

              // 4. Added Fish Entries List & Total Calculated Biomass Card
              if (_addedFishList.isNotEmpty) ...[
                Container(
                  padding: const EdgeInsets.all(AppSpace.md),
                  decoration: BoxDecoration(
                    color: AppColors.of(context).water.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(AppRadius.control),
                    border: Border.all(
                      color: AppColors.of(context).water.withValues(alpha: 0.4),
                    ),
                  ),
                  child: AdaptiveRow(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        "Total Biomass Estimate:",
                        style: AppType.style(
                          fontWeight: FontWeight.bold,
                          fontSize: AppType.body,
                        ),
                      ),
                      Text(
                        "${_totalCalculatedBiomass.toStringAsFixed(2)} kg",
                        style: AppType.style(
                          fontWeight: FontWeight.bold,
                          fontSize: AppType.title,
                          color: AppColors.of(context).primary,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: AppSpace.md),
                ListView.builder(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: _addedFishList.length,
                  itemBuilder: (context, index) {
                    final item = _addedFishList[index];
                    return Card(
                      margin: const EdgeInsets.symmetric(vertical: AppSpace.xs),
                      child: ListTile(
                        dense: true,
                        leading: CircleAvatar(
                          backgroundColor: AppColors.of(context).water,
                          child: Icon(
                            Icons.set_meal_rounded,
                            size: 18,
                            color: AppColors.of(
                              context,
                            ).foregroundOn(AppColors.of(context).water),
                          ),
                        ),
                        title: Text(
                          item.speciesName,
                          style: AppType.style(fontWeight: FontWeight.bold),
                        ),
                        subtitle: Text(
                          "${item.count} fish × ${item.avgWeightKg} kg = ${item.totalWeight.toStringAsFixed(2)} kg total",
                        ),
                        trailing: IconButton(
                          icon: Icon(
                            Icons.delete_outline,
                            color: AppColors.of(context).danger,
                          ),
                          onPressed: () => _removeFishEntry(index),
                        ),
                      ),
                    );
                  },
                ),
              ],
              const SizedBox(height: AppSpace.xxl),

              // Location Mode Toggle
              SwitchListTile(
                title: const Text("Use Smartphone Current Location"),
                subtitle: const Text(
                  "Automatically find your nearest NEA station",
                ),
                value: useCurrentLocation,
                activeThumbColor: AppColors.of(context).water,
                onChanged: (bool value) {
                  setState(() {
                    useCurrentLocation = value;
                  });
                },
              ),
              const SizedBox(height: AppSpace.lg),

              if (!useCurrentLocation) ...[
                TextField(
                  controller: _locationcontroller,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Manual Location Input (Postal Code)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: AppSpace.lg),
              ],
              TextField(
                controller: _userIDcontroller,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Find user ID on the hardware for syncing',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: AppSpace.lg),
              // Save Button
              Container(
                width: double.infinity,
                constraints: const BoxConstraints(minHeight: 50),
                child: ElevatedButton(
                  onPressed: _saveAndContinue,
                  child: Text(
                    'Save & Go to Dashboard',
                    style: AppType.style(fontSize: AppType.title),
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
