// lib/widgets/dashboard/long_term_forecast_widget.dart

import 'package:flutter/material.dart';

class LongTermForecastWidget extends StatelessWidget {
  final Map<String, dynamic> data;

  const LongTermForecastWidget({super.key, required this.data});

  @override
  Widget build(BuildContext context) {
    final List hourlyList = data['hourly'] ?? [];
    final List fourDayList = data['four_day'] ?? [];

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
                size: 18,
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
          const SizedBox(height: 14),
          const Text(
            '24-HOUR HORIZON',
            style: TextStyle(
              color: Colors.white54,
              fontSize: 10,
              letterSpacing: 1,
            ),
          ),
          const SizedBox(height: 8),

          // Hourly Horizontal Scroll List
          SizedBox(
            height: 70,
            child: hourlyList.isEmpty
                ? const Center(
                    child: Text(
                      'No hourly data',
                      style: TextStyle(color: Colors.white38, fontSize: 11),
                    ),
                  )
                : ListView.builder(
                    scrollDirection: Axis.horizontal,
                    physics: const BouncingScrollPhysics(),
                    itemCount: hourlyList.length,
                    itemBuilder: (context, index) {
                      final item = hourlyList[index];
                      return ForecastItem(
                        time: item['time'] ?? '--',
                        temp: "${item['temp'] ?? '--'}°",
                      );
                    },
                  ),
          ),
          const SizedBox(height: 16),
          const Divider(color: Colors.white10),
          const SizedBox(height: 8),

          const Text(
            '4-DAY FORECAST SUMMARY',
            style: TextStyle(
              color: Colors.white54,
              fontSize: 10,
              letterSpacing: 1,
            ),
          ),
          const SizedBox(height: 10),

          // 4-Day Outlook Cards
          fourDayList.isEmpty
              ? const Text(
                  'No 4-day forecast available',
                  style: TextStyle(color: Colors.white38, fontSize: 11),
                )
              : Row(
                  children: fourDayList.map((item) {
                    return Expanded(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 4),
                        child: DayCard(
                          day: item['day'] ?? '--',
                          tempRange: item['temp_range'] ?? '--',
                        ),
                      ),
                    );
                  }).toList(),
                ),
        ],
      ),
    );
  }
}

class ForecastItem extends StatelessWidget {
  final String time;
  final String temp;

  const ForecastItem({super.key, required this.time, required this.temp});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 60,
      margin: const EdgeInsets.only(right: 8),
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.04),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            time,
            style: const TextStyle(color: Colors.white54, fontSize: 10),
          ),
          const SizedBox(height: 2),
          const Icon(
            Icons.wb_cloudy_outlined,
            color: Colors.cyanAccent,
            size: 16,
          ),
          const SizedBox(height: 2),
          Text(
            temp,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 11,
              fontWeight: FontWeight.bold,
            ),
          ),
        ],
      ),
    );
  }
}

class DayCard extends StatelessWidget {
  final String day;
  final String tempRange;

  const DayCard({super.key, required this.day, required this.tempRange});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 4),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.04),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        children: [
          Text(
            day,
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.bold,
              fontSize: 11,
            ),
          ),
          const SizedBox(height: 4),
          const Icon(
            Icons.thunderstorm_outlined,
            color: Colors.lightBlueAccent,
            size: 18,
          ),
          const SizedBox(height: 4),
          Text(
            tempRange,
            style: const TextStyle(color: Colors.white54, fontSize: 9),
          ),
        ],
      ),
    );
  }
}
