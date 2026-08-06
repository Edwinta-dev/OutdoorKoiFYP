// lib/widgets/dashboard/four_day_outlook_card.dart

import 'package:flutter/material.dart';

class FourDayOutlookCard extends StatelessWidget {
  final Map<String, dynamic> forecastData;

  const FourDayOutlookCard({super.key, required this.forecastData});

  IconData _getWeatherIcon(String forecastText) {
    final lower = forecastText.toLowerCase();
    if (lower.contains('thunder') || lower.contains('tl')) {
      return Icons.thunderstorm_outlined;
    } else if (lower.contains('rain') || lower.contains('shower')) {
      return Icons.grain_outlined;
    } else if (lower.contains('cloud')) {
      return Icons.wb_cloudy_outlined;
    }
    return Icons.wb_sunny_outlined;
  }

  @override
  Widget build(BuildContext context) {
    final List outlook4Day = forecastData['outlook_4day'] is List
        ? forecastData['outlook_4day']
        : [];

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(
                Icons.calendar_today_outlined,
                color: Colors.cyanAccent,
                size: 16,
              ),
              SizedBox(width: 8),
              Text(
                '4-Day Extended Outlook',
                style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 13,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          outlook4Day.isEmpty
              ? const Text(
                  'No 4-day forecast available',
                  style: TextStyle(color: Colors.white38, fontSize: 11),
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
}
