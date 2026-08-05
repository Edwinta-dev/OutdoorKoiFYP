// lib/screens/dashboard_view.dart

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../widgets/dashboard/at_a_glance_widget.dart';
import '../widgets/dashboard/localized_nea_widget.dart';
import '../widgets/dashboard/long_term_forecast_widget.dart';
import '../widgets/dashboard/fish_tips_widget.dart';

class DashboardView extends StatefulWidget {
  const DashboardView({super.key});

  @override
  State<DashboardView> createState() => _DashboardViewState();
}

class _DashboardViewState extends State<DashboardView> {
  Timer? _pollingTimer;
  bool _isLoading = true;
  Map<String, dynamic> _dashboardData = {};

  @override
  void initState() {
    super.initState();
    // 1. Initial immediate RPC Fetch
    _fetchBundledPayload();

    // 2. Schedule periodic polling loop (every 30 seconds)
    _pollingTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      _fetchBundledPayload();
    });
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  /// RPC HTTP call to Supabase to fetch bundled dashboard payload
  Future<void> _fetchBundledPayload() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final String userid = prefs.getString('userID') ?? '0';
      final response = await Supabase.instance.client.rpc(
        'get_bundled_dashboard_payload',
        params: {'p_user_id': userid},
      );

      if (mounted) {
        setState(() {
          _dashboardData = Map<String, dynamic>.from(response as Map);
          _isLoading = false;
        });
      }
    } catch (e) {
      debugPrint('Error fetching bundled payload: $e');
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A0E17), // Deep Dark Aquatic Theme
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Row(
          children: [
            Icon(Icons.water_drop_outlined, color: Colors.cyanAccent),
            SizedBox(width: 8),
            Text(
              'Dashboard Center',
              style: TextStyle(
                fontWeight: FontWeight.bold,
                fontSize: 18,
                color: Colors.white,
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: _isLoading
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.cyanAccent,
                    ),
                  )
                : const Icon(Icons.refresh, color: Colors.white70),
            onPressed: () {
              setState(() => _isLoading = true);
              _fetchBundledPayload();
            },
          ),
        ],
      ),
      body: _isLoading && _dashboardData.isEmpty
          ? const Center(
              child: CircularProgressIndicator(color: Colors.cyanAccent),
            )
          : RefreshIndicator(
              color: Colors.cyanAccent,
              backgroundColor: const Color(0xFF131B2A),
              onRefresh: _fetchBundledPayload,
              child: SingleChildScrollView(
                physics: const BouncingScrollPhysics(),
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // 1. TOP PORTION: Central Icon + Orbital Telemetry Bubbles
                    AtAGlanceWidget(data: _dashboardData['raw_sensor'] ?? {}),
                    const SizedBox(height: 24),

                    // 2. LOCALIZED REAL-TIME NEA DATA + ADVISORY
                    LocalizedNeaWidget(
                      data: _dashboardData['nea_telemetry'] ?? {},
                    ),
                    const SizedBox(height: 24),

                    // 3. EXTENDED FORECAST (24H HORIZON + 4-DAY OUTLOOK)
                    LongTermForecastWidget(
                      data: _dashboardData['nea_forecasts'] ?? {},
                    ),
                    const SizedBox(height: 24),

                    // 4. SPECIES-SPECIFIC FISH CARE TIPS
                    FishTipsWidget(data: _dashboardData['fish_tips'] ?? []),
                    const SizedBox(height: 32),
                  ],
                ),
              ),
            ),
    );
  }
}
