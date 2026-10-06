import 'package:mobile_app/theme/app_theme.dart';
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
      backgroundColor: AppColors.of(context).canvas,
      extendBody: true,

      appBar: AppBar(
        toolbarHeight: 60 + (MediaQuery.textScalerOf(context).scale(14) - 14),
        backgroundColor: AppColors.of(context).canvas,
        title: Row(
          children: [
            Image.asset('lib/assets/koi_icon.png', width: 50, height: 40),
            const SizedBox(width: AppSpace.sm),
            Expanded(
              child: Text(
                currentTitle,
                style: AppType.style(
                  color: AppColors.of(context).text,
                  fontSize: AppType.body,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.6,
                ),
              ),
            ),
          ],
        ),
        actions: [
          if (_currentIndex == 0)
            IconButton(
              tooltip: 'Refresh pond readings',
              icon: const Icon(Icons.refresh),
              onPressed: () => _dashboardKey.currentState?.refreshData(),
            ),
        ],
      ),

      // ROUND 2 FIX: was `body: _screens[_currentIndex]`, which swaps in a
      // whole different widget subtree on every tab switch. Since the
      // outgoing screen is a different widget type at that tree position,
      // Flutter disposes its entire State - for DashboardView that means
      // its 30s poll Timer is cancelled and its cached _dashboardData is
      // thrown away, then initState() reruns (a fresh network fetch) every
      // single time the user comes back to the Dashboard tab, no matter how
      // recently it last polled. IndexedStack keeps all three screens
      // mounted (just hides the inactive ones), so switching tabs is free
      // and the Dashboard's poll cadence/cache actually mean something.
      body: IndexedStack(index: _currentIndex, children: _screens),

      // DOCKED FLOATING ISLAND NAVIGATION BAR
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpace.xl,
            vertical: AppSpace.lg,
          ),
          child: Container(
            constraints: const BoxConstraints(minHeight: 66),
            padding: const EdgeInsets.symmetric(horizontal: AppSpace.sm),
            decoration: BoxDecoration(
              color: AppColors.of(
                context,
              ).surfaceRaised.withValues(alpha: 0.90),
              borderRadius: BorderRadius.circular(AppRadius.pill),
              border: Border.all(
                color: AppColors.of(context).text.withValues(alpha: 0.12),
                width: 1.0,
              ),
              boxShadow: [
                BoxShadow(
                  color: AppColors.of(context).shadow.withValues(alpha: 0.35),
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
                      Expanded(
                        child: _buildNavBarItem(
                          0,
                          Icons.water_drop_outlined,
                          Icons.water_drop,
                          'Dashboard',
                        ),
                      ),
                      Expanded(
                        child: _buildNavBarItem(
                          1,
                          Icons.phishing_outlined,
                          Icons.phishing,
                          'Fish Tips',
                        ),
                      ),
                      Expanded(
                        child: _buildNavBarItem(
                          2,
                          Icons.settings_outlined,
                          Icons.settings,
                          'Settings',
                        ),
                      ),
                    ],
                  ),
                ),
                Container(
                  height: 28,
                  width: 1,
                  margin: const EdgeInsets.symmetric(horizontal: AppSpace.sm),
                  color: AppColors.of(context).text.withValues(alpha: 0.15),
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
    final activeColor = AppColors.of(context).info;
    final inactiveColor = AppColors.of(context).textSecondary;

    return InkWell(
      onTap: () => setState(() => _currentIndex = index),
      borderRadius: BorderRadius.circular(AppRadius.sheet),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpace.xs,
          vertical: AppSpace.sm,
        ),
        decoration: BoxDecoration(
          color: isSelected
              ? activeColor.withValues(alpha: 0.12)
              : AppColors.transparent,
          borderRadius: BorderRadius.circular(AppRadius.sheet),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              isSelected ? activeIcon : icon,
              color: isSelected ? activeColor : inactiveColor,
              size: 20,
            ),
            if (isSelected) ...[
              const SizedBox(height: AppSpace.xs),
              Text(
                label,
                style: AppType.style(
                  color: activeColor,
                  fontWeight: FontWeight.w600,
                  fontSize: AppType.caption,
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
      color: AppColors.transparent,
      child: InkWell(
        onTap: () => _openQuickLogModal(context),
        borderRadius: BorderRadius.circular(AppRadius.sheet),
        child: Container(
          height: 46,
          padding: const EdgeInsets.symmetric(horizontal: AppSpace.lg),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [AppColors.of(context).info, AppColors.of(context).info],
            ),
            borderRadius: BorderRadius.circular(AppRadius.sheet),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.add, color: AppColors.of(context).onInfo, size: 18),
              const SizedBox(width: AppSpace.xs),
              Text(
                'Log',
                style: AppType.style(
                  color: AppColors.of(context).onInfo,
                  fontWeight: FontWeight.bold,
                  fontSize: AppType.label,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
