import 'package:flutter/material.dart';
// Import your individual screen contents
import 'dashboard_view.dart';
import 'historical_view.dart';
import 'settings_view.dart';

class MainLayout extends StatefulWidget {
  const MainLayout({super.key});

  @override
  State<MainLayout> createState() => _MainLayoutState();
}

class _MainLayoutState extends State<MainLayout> {
  int _currentIndex = 0;

  // The list of screens that will be swapped into the body
  final List<Widget> _screens = [
    const DashboardView(), // Index 0
    const HistoricalView(), // Index 1
    const SettingsView(), // Index 2
  ];

  // The feeding logic now lives globally in the shell
  void _logFeeding() {
    // TODO: Push timestamp to Supabase
    print("Feeding logged! Updating models...");
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Feeding event logged successfully.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Koi Monitor'), // Persistent title
        // You can remove the settings action from here since it has its own tab now!
      ),

      // 1. THE BODY: Swaps out based on the selected tab
      body: _screens[_currentIndex],

      // 2. THE PERSISTENT FAB
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _logFeeding,
        icon: const Icon(Icons.restaurant),
        label: const Text('Feed Koi Now'),
        backgroundColor: Colors.teal,
      ),

      // 3. THE PERSISTENT BOTTOM NAV
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _currentIndex,
        onTap: (index) {
          setState(() {
            _currentIndex = index; // Rebuilds the shell with the new screen
          });
        },
        items: const [
          BottomNavigationBarItem(
            icon: Icon(Icons.dashboard),
            label: 'Dashboard',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.auto_graph),
            label: 'History',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}
