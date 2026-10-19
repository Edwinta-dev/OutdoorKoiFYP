import 'package:flutter/material.dart';
import '../shared/pond_widgets.dart';

class ScarceDataPlaceholder extends StatelessWidget {
  final bool loadError;
  final VoidCallback? onRetry;

  const ScarceDataPlaceholder({super.key, this.loadError = false, this.onRetry})
    : assert(!loadError || onRetry != null);

  @override
  Widget build(BuildContext context) => OutcomeCardShell(
    child: loadError
        ? PondErrorState(
            title: 'History could not be loaded',
            message: 'Check your connection and try again.',
            onRetry: onRetry!,
          )
        : const PondEmptyState(
            title: 'Establishing Baseline Cycle Averages',
            message:
                'Daily averages will populate automatically as sensor readings collect over the month.',
          ),
  );
}
