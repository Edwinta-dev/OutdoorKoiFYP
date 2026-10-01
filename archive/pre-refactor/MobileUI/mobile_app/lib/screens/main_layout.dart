// lib/screens/main_layout.dart

import 'package:flutter/material.dart';
import 'dashboard_view.dart';
import 'fish_tips_view.dart';
import 'settings_view.dart';
import '../widgets/modals/quick_log_modals.dart';

class MainLayout extends StatefulWidget {
  const MainLayout({super.key});

  @override
  State<MainLayout> createState() => _MainLayoutState();
}

class _MainLayoutState extends State<MainLayout> {
  int _currentIndex = 0;

  // 1. Declare a GlobalKey targeting DashboardViewState
  final GlobalKey<DashboardViewState> _dashboardKey =
      GlobalKey<DashboardViewState>();

  // Core views corresponding to the navigation tabs
  late final List<Widget> _screens = [
    DashboardView(key: _dashboardKey),
    const FishTipsView(),
    const SettingsView(),
  ];

  void _openQuickLogModal(BuildContext context) {
    showQuickActionSelector(context);
  }

  @override
  Widget build(BuildContext context) {
    // Determine screen title dynamically based on active tab index
    final String currentTitle = _currentIndex == 0
        ? 'Pond Dashboard Centre'
        : _currentIndex == 1
        ? 'Species Care Guide'
        : 'Settings';

    return Scaffold(
      backgroundColor: const Color(0xFF070B12),
      extendBody: true,

      // SIMPLIFIED REUSABLE TOP HUD HEADER BAR
      appBar: PreferredSize(
        preferredSize: const Size.fromHeight(60),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: 20.0,
              vertical: 8.0,
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                // Standard Brand Title + Koi Icon
                Row(
                  children: [
                    Container(
                      width: 50,
                      height: 40,
                      decoration: const BoxDecoration(
                        image: DecorationImage(
                          image: AssetImage('lib/assets/koi_icon.png'),
                          fit: BoxFit.contain,
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Text(
                      currentTitle,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 14,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 1.5,
                      ),
                    ),
                  ],
                ),

                // Screen-Specific Actions (e.g., Refresh only appears on Dashboard index 0)
                if (_currentIndex == 0)
                  IconButton(
                    icon: const Icon(
                      Icons.refresh,
                      color: Colors.white54,
                      size: 20,
                    ),
                    onPressed: () {
                      // DashboardView handles its own refresh via its internal state or global/stream hooks
                      _dashboardKey.currentState?.refreshData();
                    },
                  ),
              ],
            ),
          ),
        ),
      ),

      body: _screens[_currentIndex],

      // DOCKED FLOATING ISLAND NAVIGATION BAR
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20.0, vertical: 14.0),
          child: Container(
            height: 66,
            padding: const EdgeInsets.symmetric(horizontal: 8.0),
            decoration: BoxDecoration(
              color: const Color(0xFF1E293B).withOpacity(0.90),
              borderRadius: BorderRadius.circular(33),
              border: Border.all(
                color: Colors.white.withOpacity(0.12),
                width: 1.0,
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withOpacity(0.35),
                  blurRadius: 20,
                  offset: const Offset(0, 10),
                ),
              ],
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    children: [
                      _buildNavBarItem(
                        0,
                        Icons.water_drop_outlined,
                        Icons.water_drop,
                        'Dashboard',
                      ),
                      _buildNavBarItem(
                        1,
                        Icons.phishing_outlined,
                        Icons.phishing,
                        'Fish Tips',
                      ),
                      _buildNavBarItem(
                        2,
                        Icons.settings_outlined,
                        Icons.settings,
                        'Settings',
                      ),
                    ],
                  ),
                ),
                Container(
                  height: 28,
                  width: 1,
                  margin: const EdgeInsets.symmetric(horizontal: 8.0),
                  color: Colors.white.withOpacity(0.15),
                ),
                _buildActionPillButton(context),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildNavBarItem(
    int index,
    IconData icon,
    IconData activeIcon,
    String label,
  ) {
    final isSelected = _currentIndex == index;
    const activeColor = Color(0xFF38BDF8);
    const inactiveColor = Colors.white60;

    return InkWell(
      onTap: () => setState(() => _currentIndex = index),
      borderRadius: BorderRadius.circular(24),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(horizontal: 10.0, vertical: 8.0),
        decoration: BoxDecoration(
          color: isSelected
              ? activeColor.withOpacity(0.12)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(24),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              isSelected ? activeIcon : icon,
              color: isSelected ? activeColor : inactiveColor,
              size: 20,
            ),
            if (isSelected) ...[
              const SizedBox(width: 4),
              Text(
                label,
                style: const TextStyle(
                  color: activeColor,
                  fontWeight: FontWeight.w600,
                  fontSize: 11,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildActionPillButton(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: () => _openQuickLogModal(context),
        borderRadius: BorderRadius.circular(25),
        child: Container(
          height: 46,
          padding: const EdgeInsets.symmetric(horizontal: 14.0),
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              colors: [Color(0xFF0EA5E9), Color(0xFF0284C7)],
            ),
            borderRadius: BorderRadius.circular(25),
          ),
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.add, color: Colors.white, size: 18),
              SizedBox(width: 4),
              Text(
                'Log',
                style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
