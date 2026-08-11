// lib/widgets/common/global_quick_action_fab.dart

import 'package:flutter/material.dart';
import '../modals/quick_log_modals.dart';

class GlobalQuickActionFab extends StatelessWidget {
  const GlobalQuickActionFab({super.key});

  @override
  Widget build(BuildContext context) {
    return FloatingActionButton(
      backgroundColor: Colors.cyanAccent,
      elevation: 6,
      child: const Icon(Icons.add, color: Colors.black, size: 28),
      onPressed: () => showQuickActionSelector(context),
    );
  }
}
