import 'package:flutter/material.dart';
import 'dart:async';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

class DashboardView extends StatefulWidget {
  const DashboardView({super.key});

  @override
  State<DashboardView> createState() => _DashboardViewState();
}

class _DashboardViewState extends State<DashboardView> {
  Timer? _pollingTimer;

  // Raw Sensor Telemetry
  String currentPh = "--";
  String currentTemp = "--";
  String currentTds = "--";
  String currentLux = "--";

  // NEA Microclimate Telemetry
  String ambientTemp = "--";
  String ambientRain = "0";
  String ambientWind = "--";

  // Forecasts & Advisory State
  String forecast2Hr = "Loading...";
  String currentUvIndex = "--";
  Map<String, dynamic> forecast24HrGeneral = {};
  Map<String, dynamic> forecast24HrRegional = {};
  List<dynamic> outlook4Day = [];

  bool isLoading = true;

  @override
  void initState() {
    super.initState();
    _fetchDashboardData();

    _pollingTimer = Timer.periodic(const Duration(minutes: 1), (timer) {
      _fetchDashboardData();
    });
  }

  Future<void> _fetchDashboardData() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final int userID = int.tryParse(prefs.getString('userID') ?? '4') ?? 4;

      final Map<String, dynamic> payload = await Supabase.instance.client.rpc(
        'get_bundled_dashboard_payload',
        params: {'p_user_id': userID},
      );

      if (!mounted) return;

      final rawSensor = payload['raw_sensor'] ?? {};
      final neaTelemetry = payload['nea_telemetry'] ?? {};
      final neaForecasts = payload['nea_forecasts'] ?? {};

      // Filter UV Index for current day's active hour
      final List<dynamic> rawUvList = neaForecasts['uv_index'] ?? [];
      final String todayIsoDate = DateTime.now()
          .toIso8601String()
          .split('T')
          .first;

      String latestUv = "0";
      for (var item in rawUvList) {
        final String? hourStr = item['valid_period']?['hour'];
        if (hourStr != null && hourStr.startsWith(todayIsoDate)) {
          latestUv = item['data']?['uv']?.toString() ?? latestUv;
        }
      }

      setState(() {
        currentPh = rawSensor['pH'] != null
            ? (rawSensor['pH'] as num).toStringAsFixed(2)
            : "--";
        currentTemp = rawSensor['temp'] != null
            ? (rawSensor['temp'] as num).toStringAsFixed(1)
            : "--";
        currentTds = rawSensor['TDS'] != null
            ? (rawSensor['TDS'] as num).toStringAsFixed(0)
            : "--";
        currentLux = rawSensor['LUX'] != null
            ? (rawSensor['LUX'] as num).toStringAsFixed(0)
            : "--";

        ambientTemp = neaTelemetry['air_temp']?['value']?.toString() ?? "--";
        ambientRain = neaTelemetry['rainfall']?['value']?.toString() ?? "0";
        ambientWind = neaTelemetry['wind_speed']?['value']?.toString() ?? "--";

        forecast2Hr = neaForecasts['forecast_2hr']?['forecast'] ?? "Clear";
        forecast24HrRegional = neaForecasts['forecast_24hr']?['regional'] ?? {};
        forecast24HrGeneral = neaForecasts['forecast_24hr']?['general'] ?? {};
        currentUvIndex = latestUv;
        outlook4Day = neaForecasts['outlook_4day'] ?? [];

        isLoading = false;
      });
    } catch (e) {
      debugPrint("Dashboard payload error: $e");
      if (mounted) setState(() => isLoading = false);
    }
  }

  /// Generates contextual helper advisory notes based on live parameters
  List<String> _getAdvisoryNotes() {
    List<String> notes = [];

    // UV Check (Intense if UV >= 6)
    final int uvVal = int.tryParse(currentUvIndex) ?? 0;
    if (uvVal >= 6) {
      notes.add(
        "Intense UV today! Monitor Algal formation over the coming days.",
      );
    }

    // Air Temperature Check (Warm if >= 30°C)
    final double airT = double.tryParse(ambientTemp) ?? 0.0;
    if (airT >= 30.0) {
      notes.add(
        "Warm Air Temperature today! Account for water temperature rising before feeding!",
      );
    }

    // Rainfall / Showers Check
    final double rainVal = double.tryParse(ambientRain) ?? 0.0;
    if (rainVal > 0 ||
        forecast2Hr.toLowerCase().contains('shower') ||
        forecast2Hr.toLowerCase().contains('rain')) {
      notes.add(
        "Rainfall forecasted! Add water hardeners / conduct water change.",
      );
    }

    return notes;
  }

  void _logFeeding() {
    showDialog(
      context: context,
      builder: (BuildContext context) {
        DateTime selectedTime = DateTime.now();
        bool isNow = true;
        String selectedVolume = 'Medium';

        return StatefulBuilder(
          builder: (context, setDialogState) {
            return AlertDialog(
              backgroundColor: const Color(0xFF1E3C45),
              title: const Text(
                'Log Feeding Event',
                style: TextStyle(color: Colors.white),
              ),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'When did you feed them?',
                    style: TextStyle(
                      color: Colors.white70,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
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
                      ChoiceChip(
                        label: Text(
                          isNow
                              ? 'Custom Time'
                              : "${selectedTime.hour.toString().padLeft(2, '0')}:${selectedTime.minute.toString().padLeft(2, '0')}",
                        ),
                        selected: !isNow,
                        onSelected: (selected) async {
                          if (selected) {
                            TimeOfDay? picked = await showTimePicker(
                              context: context,
                              initialTime: TimeOfDay.fromDateTime(selectedTime),
                            );

                            if (picked != null) {
                              setDialogState(() {
                                isNow = false;
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
                  const SizedBox(height: 20),
                  const Text(
                    'Estimated Volume:',
                    style: TextStyle(
                      color: Colors.white70,
                      fontWeight: FontWeight.bold,
                    ),
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
                      setDialogState(() => selectedVolume = newSelection.first);
                    },
                  ),
                ],
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text(
                    'Cancel',
                    style: TextStyle(color: Colors.white54),
                  ),
                ),
                FilledButton(
                  onPressed: () {
                    Navigator.pop(context);
                    _submitFeedingData(selectedTime, selectedVolume);
                  },
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF00E676),
                  ),
                  child: const Text(
                    'Save Log',
                    style: TextStyle(
                      color: Colors.black87,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            );
          },
        );
      },
    );
  }

  Future<void> _submitFeedingData(DateTime time, String volume) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final int userID = prefs.getInt('userID') ?? 4;

      await Supabase.instance.client.from('feeding_logs').insert({
        'userID': userID,
        'fed_at': time.toIso8601String(),
        'volume': volume,
      });

      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Logged $volume feeding at ${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}.',
          ),
          backgroundColor: Colors.teal,
        ),
      );
    } catch (e) {
      debugPrint("Feeding log error: $e");
    }
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final advisoryNotes = _getAdvisoryNotes();

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xFF0F2027), Color(0xFF203A43), Color(0xFF2C5364)],
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
                // Top Banner
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

                // 1. TOP: Raw Sensor Data (Primary Verifiable Accuracy)
                const Text(
                  'Water Quality Metrics',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                    color: Colors.white,
                    letterSpacing: 0.5,
                  ),
                ),
                const SizedBox(height: 14),

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
                    childAspectRatio: 1.15,
                    crossAxisSpacing: 14,
                    mainAxisSpacing: 14,
                    children: [
                      _buildMetricTile(
                        title: 'pH Level',
                        value: currentPh,
                        unit: 'pH',
                        status: 'Optimal',
                        icon: Icons.water_drop_rounded,
                        accentColor: const Color(0xFF00E676),
                      ),
                      _buildMetricTile(
                        title: 'Water Temp',
                        value: currentTemp,
                        unit: '°C',
                        status: 'Normal',
                        icon: Icons.thermostat_rounded,
                        accentColor: const Color(0xFFFF9100),
                      ),
                      _buildMetricTile(
                        title: 'TDS Purity',
                        value: currentTds,
                        unit: 'ppm',
                        status: 'Good',
                        icon: Icons.blur_on_rounded,
                        accentColor: const Color(0xFF00E5FF),
                      ),
                      _buildMetricTile(
                        title: 'Sunlight',
                        value: currentLux,
                        unit: 'Lux',
                        status: 'Daylight',
                        icon: Icons.light_mode_rounded,
                        accentColor: const Color(0xFFFFD600),
                      ),
                    ],
                  ),

                const SizedBox(height: 24),

                // 2. NEXT: Immediate Microclimate & Actionable Indicators
                const Text(
                  'Current Microclimate & Forecast',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                    color: Colors.white,
                    letterSpacing: 0.5,
                  ),
                ),
                const SizedBox(height: 14),
                _buildEnvironmentalBanner(),
                const SizedBox(height: 12),
                _buildNeaMicroclimateStrip(),

                // Actionable Advisory Notes Box
                if (advisoryNotes.isNotEmpty) ...[
                  const SizedBox(height: 16),
                  _buildAdvisoryNotesCard(advisoryNotes),
                ],

                const SizedBox(height: 24),

                // 3. AFTER: Longer Term Forecasts (24-Hour & 4-Day Dropdown/ExpansionTile)
                _buildLongTermForecastsSection(),

                const SizedBox(height: 80),
              ],
            ),
          ),
        ),
      ),
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
        backgroundColor: const Color(0xFF00E676),
      ),
    );
  }

  /// Environmental Banner (2-Hour Forecast & UV Index)
  Widget _buildEnvironmentalBanner() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.07),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.12)),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.wb_twilight_rounded,
            color: Colors.amberAccent,
            size: 30,
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  "2-Hour Forecast",
                  style: TextStyle(color: Colors.white60, fontSize: 11),
                ),
                const SizedBox(height: 2),
                Text(
                  forecast2Hr,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 15,
                  ),
                ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: Colors.purpleAccent.withOpacity(0.2),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.purpleAccent.withOpacity(0.4)),
            ),
            child: Column(
              children: [
                const Text(
                  "UV INDEX",
                  style: TextStyle(
                    color: Colors.purpleAccent,
                    fontSize: 9,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                Text(
                  currentUvIndex,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Microclimate Strip (Air Temp & Wind Speed)
  Widget _buildNeaMicroclimateStrip() {
    return Row(
      children: [
        Expanded(
          child: _buildMiniStatTile(
            "Air Temp",
            "$ambientTemp°C",
            Icons.air_rounded,
            const Color(0xFF00E5FF),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _buildMiniStatTile(
            "Rainfall Rate",
            "${ambientRain}mm",
            Icons.umbrella_rounded,
            const Color(0xFF29B6F6),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _buildMiniStatTile(
            "Wind Speed",
            "$ambientWind kn",
            Icons.waves_rounded,
            const Color(0xFFAB47BC),
          ),
        ),
      ],
    );
  }

  Widget _buildMiniStatTile(
    String label,
    String val,
    IconData icon,
    Color color,
  ) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 10),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.05),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.1)),
      ),
      child: Column(
        children: [
          Icon(icon, color: color, size: 18),
          const SizedBox(height: 6),
          Text(
            val,
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.bold,
              fontSize: 14,
            ),
          ),
          Text(
            label,
            style: const TextStyle(color: Colors.white54, fontSize: 10),
          ),
        ],
      ),
    );
  }

  /// Dynamic Helper Notes Advisory Box
  Widget _buildAdvisoryNotesCard(List<String> notes) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.amber.withOpacity(0.1),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.amberAccent.withOpacity(0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: const [
              Icon(
                Icons.lightbulb_outline_rounded,
                color: Colors.amberAccent,
                size: 18,
              ),
              SizedBox(width: 8),
              Text(
                "Ecosystem Advisories",
                style: TextStyle(
                  color: Colors.amberAccent,
                  fontWeight: FontWeight.bold,
                  fontSize: 13,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ...notes.map(
            (note) => Padding(
              padding: const EdgeInsets.only(bottom: 6.0),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    "• ",
                    style: TextStyle(
                      color: Colors.white70,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  Expanded(
                    child: Text(
                      note,
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 12,
                        height: 1.3,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// ExpansionTile Dropdown for Long-Term Forecasts (24-Hour featured + 4-Day mini cards)
  Widget _buildLongTermForecastsSection() {
    final tempMap = forecast24HrGeneral['temperature'] ?? {};
    final windMap = forecast24HrGeneral['wind'] ?? {};
    final humMap = forecast24HrGeneral['relativeHumidity'] ?? {};
    final periodMap = forecast24HrGeneral['validPeriod'] ?? {};
    final forecastText =
        forecast24HrGeneral['forecast']?['text'] ??
        "Detailed 24-Hour Forecast Unavailable";

    final num tempLow = tempMap['low'] ?? 24;
    final num tempHigh = tempMap['high'] ?? 33;
    final num humLow = humMap['low'] ?? 50;
    final num humHigh = humMap['high'] ?? 90;
    final String windDir = windMap['direction'] ?? 'Var';
    final num windLow = windMap['speed']?['low'] ?? 10;
    final num windHigh = windMap['speed']?['high'] ?? 20;
    final String validPeriodText = periodMap['text'] ?? 'Next 24 Hours';

    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.05),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.1)),
      ),
      child: ExpansionTile(
        title: const Text(
          "Extended Weather Forecasts",
          style: TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
            fontSize: 16,
          ),
        ),
        subtitle: const Text(
          "Tap to view 24-Hour details & 4-Day outlook",
          style: TextStyle(color: Colors.white60, fontSize: 12),
        ),
        iconColor: Colors.tealAccent,
        collapsedIconColor: Colors.white60,
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        children: [
          const Divider(color: Colors.white24),
          const SizedBox(height: 8),

          // Huge Today 24-Hour Featured Card
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              gradient: LinearGradient(
                colors: [
                  Colors.teal.shade800.withOpacity(0.6),
                  Colors.cyan.shade900.withOpacity(0.5),
                ],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: Colors.tealAccent.withOpacity(0.3)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Expanded(
                      child: Text(
                        "24 Hour Outlook",
                        overflow: TextOverflow
                            .ellipsis, // Gracefully truncates if too long
                        maxLines: 1,
                      ),
                    ),
                    Text(
                      validPeriodText,
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 10,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Text(
                  forecastText,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 14),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    _forecastDetailBadge(
                      Icons.thermostat,
                      "Temp",
                      "$tempLow° - $tempHigh°C",
                    ),
                    _forecastDetailBadge(
                      Icons.water_drop,
                      "Humidity",
                      "$humLow% - $humHigh%",
                    ),
                    _forecastDetailBadge(
                      Icons.air,
                      "Wind",
                      "$windDir $windLow-$windHigh km/h",
                    ),
                  ],
                ),
              ],
            ),
          ),

          const SizedBox(height: 16),
          const Align(
            alignment: Alignment.centerLeft,
            child: Text(
              "4-Day Outlook",
              style: TextStyle(
                color: Colors.white70,
                fontWeight: FontWeight.bold,
                fontSize: 14,
              ),
            ),
          ),
          const SizedBox(height: 10),

          // Next 3 Days Mini Cards Grid / Row
          if (outlook4Day.isNotEmpty)
            SizedBox(
              height: 105,
              child: ListView.builder(
                scrollDirection: Axis.horizontal,
                physics: const BouncingScrollPhysics(),
                itemCount: outlook4Day.length,
                itemBuilder: (context, index) {
                  final item = outlook4Day[index];
                  final String dayStr =
                      item['data']?['day'] ?? item['slot_id'] ?? '';
                  final String text =
                      item['data']?['forecast']?['text'] ?? 'Fair';
                  final num low = item['data']?['temperature']?['low'] ?? 25;
                  final num high = item['data']?['temperature']?['high'] ?? 32;

                  return Container(
                    width: 105,
                    margin: const EdgeInsets.only(right: 10),
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.06),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: Colors.white.withOpacity(0.1)),
                    ),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          dayStr,
                          style: const TextStyle(
                            color: Colors.tealAccent,
                            fontWeight: FontWeight.bold,
                            fontSize: 12,
                          ),
                        ),
                        Text(
                          text,
                          style: const TextStyle(
                            color: Colors.white70,
                            fontSize: 10,
                          ),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        Text(
                          "$low° - $high°C",
                          style: const TextStyle(
                            color: Colors.white,
                            fontWeight: FontWeight.w600,
                            fontSize: 11,
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
        ],
      ),
    );
  }

  Widget _forecastDetailBadge(IconData icon, String label, String val) {
    return Row(
      children: [
        Icon(icon, color: Colors.tealAccent, size: 16),
        const SizedBox(width: 6),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              label,
              style: const TextStyle(color: Colors.white54, fontSize: 9),
            ),
            Text(
              val,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 11,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
      ],
    );
  }

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
              Text(
                title,
                style: const TextStyle(
                  color: Colors.white60,
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                ),
              ),
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text(
                    value,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 24,
                      fontWeight: FontWeight.bold,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(width: 4),
                  Text(
                    unit,
                    style: TextStyle(
                      color: accentColor,
                      fontSize: 12,
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
