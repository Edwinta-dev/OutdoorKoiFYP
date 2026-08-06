// lib/widgets/dashboard/long_term_forecast_widget.dart

import 'package:flutter/material.dart';

class LongTermForecastWidget extends StatelessWidget {
  final Map<String, dynamic> data;

  const LongTermForecastWidget({super.key, required this.data});

  /// Maps NEA weather forecast strings to icons
  IconData _getWeatherIcon(String forecastText) {
    final lower = forecastText.toLowerCase();
    if (lower.contains('thunder') || lower.contains('tl')) {
      return Icons.thunderstorm_outlined;
    } else if (lower.contains('rain') || lower.contains('shower')) {
      return Icons.grain_outlined;
    } else if (lower.contains('cloud')) {
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
    // 1. Safely extract 24-Hour & 2-Hour General Data
    final forecast24hr = data['forecast_24hr'] is Map
        ? data['forecast_24hr']
        : {};
    final general = forecast24hr['general'] is Map
        ? forecast24hr['general']
        : {};

    final forecastText24hr = general['forecast']?['text'] ?? 'Fair';
    final tempLow = general['temperature']?['low'] ?? '--';
    final tempHigh = general['temperature']?['high'] ?? '--';
    final rhLow = general['relativeHumidity']?['low'] ?? '--';
    final rhHigh = general['relativeHumidity']?['high'] ?? '--';
    final windSpeedHigh = general['wind']?['speed']?['high'] ?? '--';
    final windDir = general['wind']?['direction'] ?? '';

    // 2. Safely extract 4-Day Outlook Array
    final List outlook4Day = data['outlook_4day'] is List
        ? data['outlook_4day']
        : [];

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A), // Dark cohesive background
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Section Title
          const Row(
            children: [
              Icon(
                Icons.calendar_today_outlined,
                color: Colors.cyanAccent,
                size: 16,
              ),
              SizedBox(width: 8),
              Text(
                'Extended Weather Outlook',
                style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 14,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // --- 1. 24-HOUR GENERAL OVERVIEW (Header + Micro Stats Bar) ---
          Row(
            children: [
              Icon(
                _getWeatherIcon(forecastText24hr),
                color: Colors.cyanAccent,
                size: 28,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      forecastText24hr,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '24-Hour Forecast • Temp $tempLow–$tempHigh°C',
                      style: const TextStyle(
                        color: Colors.white54,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),

          // Micro Inline Telemetry Strip (No Card Boxes, Clean Spaced Text)
          Container(
            padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 12),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.03),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                _buildMicroStat('Humidity', '$rhLow–$rhHigh%'),
                Text(
                  '|',
                  style: TextStyle(color: Colors.white.withOpacity(0.12)),
                ),
                _buildMicroStat('Wind', '$windSpeedHigh km/h $windDir'),
              ],
            ),
          ),

          const SizedBox(height: 16),
          const Divider(color: Colors.white10, height: 1),
          const SizedBox(height: 14),

          // --- 2. 4-DAY OUTLOOK STRIP (Clean Columns, No Individual Cards) ---
          const Text(
            '4-DAY OUTLOOK',
            style: TextStyle(
              color: Colors.white54,
              fontSize: 10,
              fontWeight: FontWeight.bold,
              letterSpacing: 1,
            ),
          ),
          const SizedBox(height: 12),

          outlook4Day.isEmpty
              ? const Padding(
                  padding: EdgeInsets.symmetric(vertical: 8.0),
                  child: Text(
                    'No 4-day outlook data available',
                    style: TextStyle(color: Colors.white38, fontSize: 11),
                  ),
                )
              : Row(
                  mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: outlook4Day.map((item) {
                    final itemData = item['data'] is Map ? item['data'] : {};
                    final dayName = itemData['day'] ?? item['slot_id'] ?? '--';
                    final shortDay = dayName.length >= 3
                        ? dayName.substring(0, 3)
                        : dayName;

                    final forecastText = itemData['forecast']?['text'] ?? '';
                    final lowTemp = itemData['temperature']?['low'] ?? '--';
                    final highTemp = itemData['temperature']?['high'] ?? '--';

                    return Expanded(
                      child: Column(
                        children: [
                          Text(
                            shortDay.toUpperCase(),
                            style: const TextStyle(
                              color: Colors.white70,
                              fontSize: 10,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          const SizedBox(height: 6),
                          Icon(
                            _getWeatherIcon(forecastText),
                            color: Colors.lightBlueAccent,
                            size: 20,
                          ),
                          const SizedBox(height: 6),
                          Text(
                            '$lowTemp–$highTemp°',
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 11,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ],
                      ),
                    );
                  }).toList(),
                ),
        ],
      ),
    );
  }

  Widget _buildMicroStat(String label, String value) {
    return Row(
      children: [
        Text(
          '$label: ',
          style: const TextStyle(color: Colors.white54, fontSize: 10),
        ),
        Text(
          value,
          style: const TextStyle(
            color: Colors.white70,
            fontSize: 10,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}
