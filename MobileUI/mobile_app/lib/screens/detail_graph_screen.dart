// lib/screens/detail_graph_screen.dart

import 'package:flutter/material.dart';

class DetailGraphScreen extends StatelessWidget {
  final String metricType;
  final String title;

  const DetailGraphScreen({
    super.key,
    required this.metricType,
    required this.title,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A0E17),
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(
          title,
          style: const TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
          ),
        ),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back, color: Colors.white),
          onPressed: () => Navigator.pop(context),
        ),
      ),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Historical Data & Shift Analysis ($metricType)',
              style: const TextStyle(color: Colors.white70, fontSize: 14),
            ),
            const SizedBox(height: 20),

            // Placeholder Container for fl_chart historical curve
            Container(
              height: 260,
              width: double.infinity,
              decoration: BoxDecoration(
                color: const Color(0xFF131B2A),
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: Colors.white.withOpacity(0.08)),
              ),
              child: const Center(
                child: Text(
                  'fl_chart LineChart with Target Band & Rain Annotations',
                  style: TextStyle(color: Colors.cyanAccent, fontSize: 12),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
