// lib/widgets/dashboard/localized_nea_widget.dart

import 'package:flutter/material.dart';

class LocalizedNeaWidget extends StatelessWidget {
  final Map<String, dynamic> telemetryData;
  final Map<String, dynamic> forecastData;

  const LocalizedNeaWidget({
    super.key,
    required this.telemetryData,
    required this.forecastData,
  });

  /// Helper to extract nested values safely
  String _extractValue(dynamic container, String key, {String fallback = '0'}) {
    if (container == null) return fallback;

    if (container is Map) {
      final inner = container[key] ?? container['value'];
      if (inner != null) return inner.toString();
    }

    if (container is List && container.isNotEmpty) {
      final firstItem = container[0];
      if (firstItem is Map) {
        final inner = firstItem[key] ?? firstItem['value'];
        if (inner != null) return inner.toString();
      }
    }

    return container.toString();
  }

  /// Maps 2-hour weather text to icons
  IconData _getWeatherIcon(String forecastText) {
    final lower = forecastText.toLowerCase();
    if (lower.contains('thunder') || lower.contains('tl')) {
      return Icons.thunderstorm_outlined;
    } else if (lower.contains('rain') ||
        lower.contains('shower') ||
        lower.contains('sh')) {
      return Icons.grain_outlined;
    } else if (lower.contains('cloud') || lower.contains('pc')) {
      if (lower.contains('night')) return Icons.nights_stay_outlined;
      return Icons.wb_cloudy_outlined;
    } else if (lower.contains('fair') || lower.contains('clear')) {
      if (lower.contains('night')) return Icons.brightness_3_outlined;
      return Icons.wb_sunny_outlined;
    }
    return Icons.wb_cloudy_outlined;
  }

  @override
  Widget build(BuildContext context) {
    // 1. Live Station Telemetry
    final stationName = telemetryData['station_name'] ?? 'Ang Mo Kio';
    final rainfallVal = _extractValue(
      telemetryData['rainfall'],
      'value',
      fallback: '0.0',
    );
    final rainfallDisplay = "$rainfallVal mm/h";
    final airTempDisplay =
        "${_extractValue(telemetryData['air_temp'], 'value', fallback: '31.0')} °C";

    // 2. UV Index (Safely parsed from forecastData)
    final rawUv = forecastData['uv_index']?['data']?['uv'];
    final uvVal = (rawUv ?? 0).toString();

    // 3. 2-Hour Localized Status Extraction
    final forecast2hrMap = forecastData['forecast_2hr'] is Map
        ? forecastData['forecast_2hr']
        : {};
    final status2hr = forecast2hrMap['forecast'] ?? 'Partly Cloudy';

    // 4. Advisory
    final advisory =
        telemetryData['advisory'] ??
        forecastData['advisory'] ??
        'Normal weather conditions detected.';

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A), // Cohesive dark background
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // -----------------------------------------------------------------
          // 1. HEADER ROW (Station Name & Live Badge)
          // -----------------------------------------------------------------
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: Row(
                  children: [
                    const Icon(
                      Icons.location_on_outlined,
                      color: Colors.lightBlueAccent,
                      size: 18,
                    ),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        'NEA Station: $stationName',
                        style: const TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.w600,
                          fontSize: 13,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: Colors.lightBlueAccent.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(10),
                ),
                child: const Text(
                  'Live NEA',
                  style: TextStyle(color: Colors.lightBlueAccent, fontSize: 10),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // -----------------------------------------------------------------
          // 2. MICRO TELEMETRY ROW (Wrapped in Expanded to distribute width)
          // -----------------------------------------------------------------
          Row(
            children: [
              Expanded(
                child: NeaMicroBadge(
                  icon: Icons.thunderstorm_outlined,
                  label: 'Rainfall',
                  value: rainfallDisplay,
                ),
              ),
              Expanded(
                child: NeaMicroBadge(
                  icon: Icons.thermostat,
                  label: 'Air Temp',
                  value: airTempDisplay,
                ),
              ),
              Expanded(
                child: NeaMicroBadge(
                  icon: Icons.wb_sunny_outlined,
                  label: 'UV Index',
                  value: uvVal,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // -----------------------------------------------------------------
          // 3. YOUR NEXT 2 HOURS SECTION
          // -----------------------------------------------------------------
          Container(
            padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 12),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.03),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              children: [
                Icon(
                  _getWeatherIcon(status2hr),
                  color: Colors.cyanAccent,
                  size: 20,
                ),
                const SizedBox(width: 10),
                const Text(
                  'Your Next 2 Hours: ',
                  style: TextStyle(
                    color: Colors.white54,
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                Expanded(
                  child: Text(
                    status2hr,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),

          // -----------------------------------------------------------------
          // 4. POND ADVISORY BOX
          // -----------------------------------------------------------------
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.amber.withOpacity(0.1),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.amber.withOpacity(0.3)),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(
                  Icons.warning_amber_rounded,
                  color: Colors.amberAccent,
                  size: 22,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Pond Environmental Advisory',
                        style: TextStyle(
                          color: Colors.amberAccent,
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        advisory,
                        style: const TextStyle(
                          color: Colors.white70,
                          fontSize: 11,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class NeaMicroBadge extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;

  const NeaMicroBadge({
    super.key,
    required this.icon,
    required this.label,
    required this.value,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Icon(icon, color: Colors.lightBlueAccent, size: 20),
        const SizedBox(height: 4),
        Text(
          label,
          style: const TextStyle(color: Colors.white54, fontSize: 10),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 2),
        Text(
          value,
          style: const TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
            fontSize: 12,
          ),
          textAlign: TextAlign.center,
          overflow: TextOverflow.ellipsis,
        ),
      ],
    );
  }
}
