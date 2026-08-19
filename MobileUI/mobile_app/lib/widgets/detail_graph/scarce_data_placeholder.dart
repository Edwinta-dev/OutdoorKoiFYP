import 'package:flutter/material.dart';

class ScarceDataPlaceholder extends StatelessWidget {
  const ScarceDataPlaceholder({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 280,
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: const Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.query_stats, color: Colors.cyanAccent, size: 36),
          SizedBox(height: 10),
          Text(
            'Establishing Baseline Cycle Averages',
            style: TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.bold,
              fontSize: 14,
            ),
          ),
          SizedBox(height: 4),
          Text(
            'Daily averages will populate automatically as sensor readings collect over the month.',
            style: TextStyle(color: Colors.white54, fontSize: 11),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}
