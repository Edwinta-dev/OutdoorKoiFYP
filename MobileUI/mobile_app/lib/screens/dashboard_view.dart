import 'package:flutter/material.dart';
import 'dart:async'; // Required for Timer
import 'dart:convert'; // Required to decode JSON
import 'package:http/http.dart' as http; // Required for network requests

class DashboardView extends StatefulWidget {
  const DashboardView({super.key});

  @override
  State<DashboardView> createState() => _DashboardViewState();
}

class _DashboardViewState extends State<DashboardView> {
  // 1. Declare your state variables and the Timer
  Timer? _pollingTimer;

  // Replace this IP with your laptop's actual local Wi-Fi IP address!
  final String apiUrl = 'http://192.168.68.64:5000/api/current_status';

  String currentPh = "--";
  String currentTemp = "--";
  String currentTds = "--";
  String currentLux = "--";
  bool isLoading = true;

  @override
  void initState() {
    super.initState();
    // Fetch data immediately when the screen loads
    _fetchCurrentStatus();

    // 2. Start the periodic timer to fetch every 1 minute
    _pollingTimer = Timer.periodic(const Duration(minutes: 1), (timer) {
      _fetchCurrentStatus();
    });
  }

  @override
  void dispose() {
    // 3. CRITICAL: Always cancel the timer when leaving the screen!
    // If you don't do this, it will keep running in the background and drain the battery.
    _pollingTimer?.cancel();
    super.dispose();
  }

  // 4. The function that talks to your Flask server
  Future<void> _fetchCurrentStatus() async {
    try {
      final response = await http.get(Uri.parse(apiUrl));

      if (response.statusCode == 200) {
        // Decode the JSON from your Flask jsonify() response
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
      // Optional: Set variables to "Error" or show a snackbar here
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Current Readings',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 12),

          // Show a loading spinner until the first fetch finishes
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
