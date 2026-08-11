import 'package:flutter/material.dart';
import 'dashboard_view.dart';
import 'fish_tips_view.dart'; // Mapped to your fish inventory / tips screen
import 'settings_view.dart';
import '../widgets/modals/quick_log_modals.dart'; // Source of your Quick Log Bottom Sheet

class MainLayout extends StatefulWidget {
  const MainLayout({super.key});

  @override
  State<MainLayout> createState() => _MainLayoutState();
}

class _MainLayoutState extends State<MainLayout> {
  int _currentIndex = 0;

  // 1. Updated core views list to include Settings at index 2
  final List<Widget> _screens = [
    const DashboardView(),
    const FishTipsView(),
    const SettingsView(),
  ];

  /// Triggers your existing quick-log modal bottom sheet directly from the navbar

  void _openQuickLogModal(BuildContext context) {
    showQuickActionSelector(context);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      extendBody:
          true, // Allows navbar to float cleanly over background gradients
      body: _screens[_currentIndex],

      // 2. Removed separate floatingActionButton to prevent blocking dashboard data!
      // All actions are now unified inside the bottomNavigationBar pill.

      // 3. Docked Glassmorphic Floating Island Navigation Bar
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20.0, vertical: 14.0),
          child: Container(
            height: 66,
            padding: const EdgeInsets.symmetric(horizontal: 8.0),
            decoration: BoxDecoration(
              color: const Color(
                0xFF1E293B,
              ).withOpacity(0.90), // Dark aquatic translucent tone
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
                // --- LEFT/CENTER GROUP: NAVIGATION TABS ---
                Expanded(
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    children: [
                      // Tab 0: Dashboard
                      _buildNavBarItem(
                        index: 0,
                        icon: Icons.water_drop_outlined,
                        activeIcon: Icons.water_drop,
                        label: 'Dashboard',
                      ),

                      // Tab 1: Fish Tips & Inventory
                      _buildNavBarItem(
                        index: 1,
                        icon: Icons.phishing_outlined,
                        activeIcon: Icons.phishing,
                        label: 'Fish Tips',
                      ),

                      // Tab 2: Settings
                      _buildNavBarItem(
                        index: 2,
                        icon: Icons.settings_outlined,
                        activeIcon: Icons.settings,
                        label: 'Settings',
                      ),
                    ],
                  ),
                ),

                // --- SUBTLE VERTICAL DIVIDER ---
                Container(
                  height: 28,
                  width: 1,
                  margin: const EdgeInsets.symmetric(horizontal: 8.0),
                  color: Colors.white.withOpacity(0.15),
                ),

                // --- RIGHT-DOCKED ACTION TRIGGER: "+ LOG EVENT" ---
                _buildActionPillButton(context),
              ],
            ),
          ),
        ),
      ),
    );
  }

  /// Selectable navigation tab item
  Widget _buildNavBarItem({
    required int index,
    required IconData icon,
    required IconData activeIcon,
    required String label,
  }) {
    final isSelected = _currentIndex == index;
    const activeColor = Color(0xFF38BDF8); // Bright aquatic cyan highlight
    const inactiveColor = Colors.white60;

    return InkWell(
      onTap: () {
        setState(() {
          _currentIndex = index;
        });
      },
      borderRadius: BorderRadius.circular(24),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 8.0),
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
              size: 22,
            ),
            if (isSelected) ...[
              const SizedBox(width: 6),
              Text(
                label,
                style: const TextStyle(
                  color: activeColor,
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  /// Right-docked "+ Log Event" action button (replaces old FAB)
  Widget _buildActionPillButton(BuildContext context) {
    const activeGradient = LinearGradient(
      colors: [Color(0xFF0EA5E9), Color(0xFF0284C7)],
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
    );

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: () => _openQuickLogModal(context),
        borderRadius: BorderRadius.circular(25),
        child: Container(
          height: 46,
          padding: const EdgeInsets.symmetric(horizontal: 14.0),
          decoration: BoxDecoration(
            gradient: activeGradient,
            borderRadius: BorderRadius.circular(25),
            boxShadow: [
              BoxShadow(
                color: const Color(0xFF0EA5E9).withOpacity(0.4),
                blurRadius: 10,
                offset: const Offset(0, 3),
              ),
            ],
          ),
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.add, color: Colors.white, size: 20),
              SizedBox(width: 4),
              Text(
                'Log',
                style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 13,
                  letterSpacing: 0.3,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
