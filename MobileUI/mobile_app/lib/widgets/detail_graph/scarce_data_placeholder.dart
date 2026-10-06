import 'package:flutter/material.dart';
import '../shared/pond_widgets.dart';

class ScarceDataPlaceholder extends StatelessWidget {
  const ScarceDataPlaceholder({super.key});
  @override
  Widget build(BuildContext context) => const OutcomeCardShell(
    child: PondEmptyState(
      title: 'Establishing Baseline Cycle Averages',
      message:
          'Daily averages will populate automatically as sensor readings collect over the month.',
    ),
  );
}
