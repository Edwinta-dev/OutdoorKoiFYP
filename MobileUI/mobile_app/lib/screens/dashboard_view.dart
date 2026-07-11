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

  final String apiUrl = 'http://127.0.0.1:5000/api/current_status';

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
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16.0),

        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Current Readings',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 12),

            if (isLoading)
              const Center(child: CircularProgressIndicator())
            else
              GridView.count(
                crossAxisCount: 2,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                childAspectRatio: 1.5,
                crossAxisSpacing: 10,
                mainAxisSpacing: 10,
                children: [
                  // Notice these now use our state variables directly!
                  _buildSensorCard('pH Level', currentPh, Colors.blue),
                  _buildSensorCard('Temp (°C)', currentTemp, Colors.orange),
                  _buildSensorCard('TDS (ppm)', currentTds, Colors.green),
                  _buildSensorCard('Lux', currentLux, Colors.amber),
                ],
              ),
          ],
        ),
      ),

      floatingActionButton: FloatingActionButton.extended(
        onPressed: _logFeeding,
        icon: const Icon(Icons.restaurant),
        label: const Text('Feed Koi Now'),
        backgroundColor: Colors.teal,
      ),
    );
  }

  Widget _buildSensorCard(String title, String value, Color color) {
    return Card(
      elevation: 2,
      clipBehavior: Clip.hardEdge,
      child: InkWell(
        onTap: () {},
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(
              title,
              style: TextStyle(fontSize: 16, color: Colors.grey.shade700),
            ),
            const SizedBox(height: 8),
            Text(
              value,
              style: TextStyle(
                fontSize: 28,
                fontWeight: FontWeight.bold,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
