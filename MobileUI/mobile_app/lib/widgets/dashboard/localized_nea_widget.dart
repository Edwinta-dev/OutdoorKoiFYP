// lib/widgets/dashboard/localized_nea_widget.dart

import 'package:flutter/material.dart';

class LocalizedNeaWidget extends StatelessWidget {
  final Map<String, dynamic> data;

  const LocalizedNeaWidget({super.key, required this.data});

  @override
  Widget build(BuildContext context) {
    final stationName = data['station_name'] ?? 'Ang Mo Kio';
    final rainfall = "${data['rainfall'] ?? 0.0} mm/h";
    final airTemp = "${data['air_temp'] ?? 31.0} °C";
    final uvIndex = "${data['uv_index'] ?? 0}";
    final advisory = data['advisory'] ?? 'Normal weather conditions detected.';

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
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  const Icon(
                    Icons.location_on_outlined,
                    color: Colors.lightBlueAccent,
                    size: 18,
                  ),
                  const SizedBox(width: 6),
                  Text(
                    'NEA Station: $stationName',
                    style: const TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.w600,
                      fontSize: 13,
                    ),
                  ),
                ],
              ),
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
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              NeaMicroBadge(
                icon: Icons.thunderstorm_outlined,
                label: 'Rainfall',
                value: rainfall,
              ),
              NeaMicroBadge(
                icon: Icons.thermostat,
                label: 'Air Temp',
                value: airTemp,
              ),
              NeaMicroBadge(
                icon: Icons.wb_sunny_outlined,
                label: 'UV Index',
                value: uvIndex,
              ),
            ],
          ),
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.amber.withOpacity(0.1),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.amber.withOpacity(0.3)),
            ),
            child: Row(
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
        ),
        const SizedBox(height: 2),
        Text(
          value,
          style: const TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
            fontSize: 12,
          ),
        ),
      ],
    );
  }
}
