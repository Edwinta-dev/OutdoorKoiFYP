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
  bool _isFetching = false;
  Map<String, dynamic> _dashboardData = {};

  @override
  void initState() {
    super.initState();
    // 1. Initial immediate RPC Fetch
    _fetchBundledPayload(isBackgroundPoll: false);

    // 2. Schedule periodic polling loop (every 30 seconds)
    _pollingTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      _fetchBundledPayload(isBackgroundPoll: true);
    });
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  /// RPC HTTP call to Supabase to fetch bundled dashboard payload
  Future<void> _fetchBundledPayload({bool isBackgroundPoll = false}) async {
    if (_isFetching) return;
    _isFetching = true;

    if (!isBackgroundPoll && mounted) {
      setState(() => _isLoading = true);
    }

    try {
      final prefs = await SharedPreferences.getInstance();
      final String userid = prefs.getString('userID') ?? '0';

      final response = await Supabase.instance.client.rpc(
        'get_bundled_dashboard_payload',
        params: {'p_user_id': userid},
      );

      if (mounted && response != null) {
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
    } finally {
      _isFetching = false;
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
            onPressed: () => _fetchBundledPayload(isBackgroundPoll: false),
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
              onRefresh: () => _fetchBundledPayload(isBackgroundPoll: false),
              child: SingleChildScrollView(
                physics: const AlwaysScrollableScrollPhysics(
                  parent: BouncingScrollPhysics(),
                ),
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // =================----------------======================
                    // MUST SEE 1: TOP PORTION (Central Icon + Telemetry Dials)
                    // =================----------------======================
                    AtAGlanceWidget(data: _dashboardData['raw_sensor'] ?? {}),
                    const SizedBox(height: 20),

                    // =================----------------======================
                    // MUST SEE 2: LOCALIZED REAL-TIME NEA DATA + ADVISORY
                    // =================----------------======================
                    LocalizedNeaWidget(
                      telemetryData: _dashboardData['nea_telemetry'] ?? {},
                      forecastData: _dashboardData['nea_forecasts'] ?? {},
                    ),
                    const SizedBox(height: 20),

                    // =================----------------======================
                    // COLLAPSIBLE 1: EXTENDED WEATHER OUTLOOK
                    // =================----------------======================
                    CollapsibleDashboardSection(
                      title: 'Extended Weather Outlook',
                      icon: Icons.calendar_today_outlined,
                      iconColor: Colors.cyanAccent,
                      initiallyExpanded: false,
                      child: LongTermForecastWidget(
                        data: _dashboardData['nea_forecasts'] ?? {},
                      ),
                    ),
                    const SizedBox(height: 16),

                    // =================----------------======================
                    // COLLAPSIBLE 2: SPECIES-SPECIFIC FISH CARE TIPS
                    // =================----------------======================
                    CollapsibleDashboardSection(
                      title: 'Fish Care & Biomass Tips',
                      icon: Icons.set_meal_outlined,
                      iconColor: Colors.orangeAccent,
                      initiallyExpanded: false,
                      child: FishTipsWidget(
                        data: _dashboardData['fish_tips'] ?? [],
                      ),
                    ),
                    const SizedBox(height: 32),
                  ],
                ),
              ),
            ),
    );
  }
}

/// Reusable Collapsible Dropdown Card designed for the dark aquatic theme
class CollapsibleDashboardSection extends StatelessWidget {
  final String title;
  final IconData icon;
  final Color iconColor;
  final Widget child;
  final bool initiallyExpanded;

  const CollapsibleDashboardSection({
    super.key,
    required this.title,
    required this.icon,
    required this.iconColor,
    required this.child,
    this.initiallyExpanded = false,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: Theme(
        // Remove default ExpansionTile borders and dividers
        data: Theme.of(context).copyWith(
          dividerColor: Colors.transparent,
          splashColor: Colors.transparent,
          highlightColor: Colors.transparent,
        ),
        child: ExpansionTile(
          initiallyExpanded: initiallyExpanded,
          tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          childrenPadding: const EdgeInsets.only(bottom: 12),
          iconColor: Colors.white70,
          collapsedIconColor: Colors.white38,
          title: Row(
            children: [
              Icon(icon, color: iconColor, size: 18),
              const SizedBox(width: 10),
              Text(
                title,
                style: const TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 14,
                ),
              ),
            ],
          ),
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: child,
            ),
          ],
        ),
      ),
    );
  }
}
