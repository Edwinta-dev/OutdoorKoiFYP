import 'package:flutter/material.dart';
import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

class DashboardView extends StatefulWidget {
  const DashboardView({super.key});

  @override
  State<DashboardView> createState() => _DashboardViewState();
}

class _DashboardViewState extends State<DashboardView> {
  Timer? _pollingTimer;

  final String apiUrl = 'http://192.168.68.66:5000/api/current_status';

  String currentPh = "--";
  String currentTemp = "--";
  String currentTds = "--";
  String currentLux = "--";
  bool isLoading = true;

  @override
  void initState() {
    super.initState();
    _fetchCurrentStatus();

    _pollingTimer = Timer.periodic(const Duration(minutes: 1), (timer) {
      _fetchCurrentStatus();
    });
  }

  void _logFeeding() {
    showDialog(
      context: context,
      builder: (BuildContext context) {
        // 1. Initialize our default values for the popup
        DateTime selectedTime = DateTime.now();
        bool isNow = true;
        String selectedVolume = 'Medium';

        // 2. StatefulBuilder allows the UI inside the dialog to update dynamically
        return StatefulBuilder(
          builder: (context, setDialogState) {
            return AlertDialog(
              title: const Text('Log Feeding Event'),
              content: Column(
                mainAxisSize: MainAxisSize.min, // Keeps the dialog compact
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // --- TIME SELECTION ---
                  const Text(
                    'When did you feed them?',
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      // "NOW" Button
                      ChoiceChip(
                        label: const Text('NOW'),
                        selected: isNow,
                        onSelected: (selected) {
                          if (selected) {
                            setDialogState(() {
                              isNow = true;
                              selectedTime = DateTime.now();
                            });
                          }
                        },
                      ),
                      const SizedBox(width: 8),
                      // "Custom Time" Button
                      ChoiceChip(
                        label: Text(
                          isNow
                              ? 'Custom Time'
                              : "${selectedTime.hour.toString().padLeft(2, '0')}:${selectedTime.minute.toString().padLeft(2, '0')}",
                        ),
                        selected: !isNow,
                        onSelected: (selected) async {
                          if (selected) {
                            // Opens the native phone time picker wheel
                            TimeOfDay? picked = await showTimePicker(
                              context: context,
                              initialTime: TimeOfDay.fromDateTime(selectedTime),
                            );

                            if (picked != null) {
                              setDialogState(() {
                                isNow = false;
                                // Merge the picked time with today's date
                                selectedTime = DateTime(
                                  selectedTime.year,
                                  selectedTime.month,
                                  selectedTime.day,
                                  picked.hour,
                                  picked.minute,
                                );
                              });
                            }
                          }
                        },
                      ),
                    ],
                  ),
                  const SizedBox(height: 24),

                  // --- VOLUME SELECTION ---
                  const Text(
                    'Estimated Volume:',
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(value: 'Small', label: Text('Small')),
                      ButtonSegment(value: 'Medium', label: Text('Medium')),
                      ButtonSegment(value: 'Large', label: Text('Large')),
                    ],
                    selected: {selectedVolume},
                    onSelectionChanged: (Set<String> newSelection) {
                      setDialogState(() {
                        selectedVolume = newSelection.first;
                      });
                    },
                  ),
                ],
              ),

              // --- ACTION BUTTONS ---
              actions: [
                TextButton(
                  onPressed: () =>
                      Navigator.pop(context), // Close without saving
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  onPressed: () {
                    Navigator.pop(context); // Close the dialog
                    _submitFeedingData(
                      selectedTime,
                      selectedVolume,
                    ); // Process the data
                  },
                  style: FilledButton.styleFrom(backgroundColor: Colors.teal),
                  child: const Text('Save Log'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  // The method that actually handles the data after the user clicks "Save Log"
  void _submitFeedingData(DateTime time, String volume) {
    // TODO: Push 'time.toIso8601String()' and 'volume' to your Supabase 'feeding_logs' table

    print("Feeding logged! Time: $time, Volume: $volume. Updating models...");

    // Show a success message at the bottom of the screen
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          'Logged $volume feeding at ${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}.',
        ),
        backgroundColor: Colors.teal,
        duration: const Duration(seconds: 3),
      ),
    );
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  Future<void> _fetchCurrentStatus() async {
    try {
      final response = await http.get(Uri.parse(apiUrl));

      if (response.statusCode == 200) {
        final data = json.decode(response.body);

        // Update the UI with the fresh data
        setState(() {
          currentPh = data['pH'].toString();
          currentTemp = data['temp'].toString();
          currentTds = data['TDS'].toString();
          currentLux = data['LUX'].toString();
          isLoading = false;
        });
        print("Successfully updated sensor data at ${DateTime.now()}");
      } else {
        print("Server error: ${response.statusCode}");
      }
    } catch (e) {
      print("Failed to connect to Flask server: $e");
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      // 1. Deep Aquatic Dark Gradient Canvas Background
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              Color(0xFF0F2027), // Deep Navy
              Color(0xFF203A43), // Lagoon Blue
              Color(0xFF2C5364), // Soft Teal-Grey
            ],
          ),
        ),
        child: SafeArea(
          child: SingleChildScrollView(
            physics: const BouncingScrollPhysics(),
            padding: const EdgeInsets.symmetric(
              horizontal: 20.0,
              vertical: 16.0,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Top App Bar / Title Banner
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: const [
                        Text(
                          "Outdoor Koi Pond",
                          style: TextStyle(
                            color: Colors.white70,
                            fontSize: 14,
                            letterSpacing: 1.1,
                          ),
                        ),
                        SizedBox(height: 4),
                        Text(
                          "Ecosystem Health",
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 26,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                    // Live Connectivity Badge
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 6,
                      ),
                      decoration: BoxDecoration(
                        color: Colors.greenAccent.withOpacity(0.2),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(color: Colors.greenAccent, width: 1),
                      ),
                      child: Row(
                        children: const [
                          CircleAvatar(
                            radius: 4,
                            backgroundColor: Colors.greenAccent,
                          ),
                          SizedBox(width: 6),
                          Text(
                            "LIVE",
                            style: TextStyle(
                              color: Colors.greenAccent,
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 20),

                // 2. HERO CARD: Master Health Status Overview
                _buildHeroHealthCard(),

                const SizedBox(height: 24),

                // Section Label
                const Text(
                  'Telemetry Metrics',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                    color: Colors.white,
                    letterSpacing: 0.5,
                  ),
                ),
                const SizedBox(height: 14),

                // 3. Dynamic Sensor Metric Cards Grid
                if (isLoading)
                  const Padding(
                    padding: EdgeInsets.all(40.0),
                    child: Center(
                      child: CircularProgressIndicator(
                        color: Colors.tealAccent,
                      ),
                    ),
                  )
                else
                  GridView.count(
                    crossAxisCount: 2,
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    childAspectRatio: 1.15, // Taller cards for better density
                    crossAxisSpacing: 14,
                    mainAxisSpacing: 14,
                    children: [
                      _buildMetricTile(
                        title: 'pH Level',
                        value: currentPh,
                        unit: 'pH',
                        status: 'Optimal',
                        icon: Icons.water_drop_rounded,
                        accentColor: const Color(0xFF00E676), // Emerald
                      ),
                      _buildMetricTile(
                        title: 'Water Temp',
                        value: currentTemp,
                        unit: '°C',
                        status: 'Normal',
                        icon: Icons.thermostat_rounded,
                        accentColor: const Color(0xFFFF9100), // Vibrant Amber
                      ),
                      _buildMetricTile(
                        title: 'TDS Purity',
                        value: currentTds,
                        unit: 'ppm',
                        status: 'Good',
                        icon: Icons.blur_on_rounded,
                        accentColor: const Color(0xFF00E5FF), // Cyan
                      ),
                      _buildMetricTile(
                        title: 'Sunlight',
                        value: currentLux,
                        unit: 'Lux',
                        status: 'Daylight',
                        icon: Icons.light_mode_rounded,
                        accentColor: const Color(0xFFFFD600), // Gold
                      ),
                    ],
                  ),
                const SizedBox(height: 80), // Padding space for FAB
              ],
            ),
          ),
        ),
      ),

      // 4. Action Button: Floating Action Button with Glass Gradient Style
      floatingActionButtonLocation: FloatingActionButtonLocation.centerFloat,
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _logFeeding,
        elevation: 6,
        icon: const Icon(Icons.set_meal_rounded, color: Colors.black87),
        label: const Text(
          'Log Koi Feeding',
          style: TextStyle(
            color: Colors.black87,
            fontWeight: FontWeight.bold,
            letterSpacing: 0.5,
          ),
        ),
        backgroundColor: const Color(0xFF00E676), // Bright mint green
      ),
    );
  }

  /// Master Hero Status Box
  Widget _buildHeroHealthCard() {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(24),
        gradient: LinearGradient(
          colors: [
            Colors.teal.shade700.withOpacity(0.5),
            Colors.cyan.shade900.withOpacity(0.4),
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        border: Border.all(
          color: Colors.tealAccent.withOpacity(0.3),
          width: 1.5,
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.25),
            blurRadius: 15,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: Row(
        children: [
          // Circular Status Icon Ring
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Colors.tealAccent.withOpacity(0.15),
              border: Border.all(color: Colors.tealAccent, width: 2),
            ),
            child: const Icon(
              Icons.verified_user_rounded,
              color: Colors.tealAccent,
              size: 32,
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: const [
                Text(
                  "Pond Status: Optimal",
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                SizedBox(height: 4),
                Text(
                  "All 4 water quality parameters are within safe ranges for Koi health.",
                  style: TextStyle(
                    color: Colors.white70,
                    fontSize: 12,
                    height: 1.3,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Individual Visual Metric Cards
  Widget _buildMetricTile({
    required String title,
    required String value,
    required String unit,
    required String status,
    required IconData icon,
    required Color accentColor,
  }) {
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        // Translucent "Glassmorphism" panel effect
        color: Colors.white.withOpacity(0.07),
        border: Border.all(color: Colors.white.withOpacity(0.12), width: 1),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.15),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: Padding(
          padding: const EdgeInsets.all(14.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              // Tile Header: Icon & Category Title
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: accentColor.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Icon(icon, color: accentColor, size: 20),
                  ),
                  // Status Pill Badge
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: accentColor.withOpacity(0.2),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      status,
                      style: TextStyle(
                        color: accentColor,
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),

              // Title Label
              Text(
                title,
                style: const TextStyle(
                  color: Colors.white60,
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                ),
              ),

              // Tile Main Value Output
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text(
                    value,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 26,
                      fontWeight: FontWeight.bold,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    unit,
                    style: TextStyle(
                      color: accentColor,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
