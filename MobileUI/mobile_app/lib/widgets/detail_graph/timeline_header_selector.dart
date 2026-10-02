import 'package:flutter/material.dart';

class TimelineHeaderSelector extends StatelessWidget {
  final String metricType;
  final int selectedDays;
  final ValueChanged<int> onDaysChanged;

  const TimelineHeaderSelector({
    super.key,
    required this.metricType,
    required this.selectedDays,
    required this.onDaysChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          '${metricType.toUpperCase()} Timeline',
          style: const TextStyle(
            color: Colors.white70,
            fontSize: 12,
            fontWeight: FontWeight.w600,
          ),
        ),
        SegmentedButton<int>(
          style: ButtonStyle(
            visualDensity: VisualDensity.compact,
            backgroundColor: WidgetStateProperty.resolveWith<Color>((states) {
              if (states.contains(WidgetState.selected)) {
                return Colors.cyanAccent.withValues(alpha: 0.2);
              }
              return Colors.transparent;
            }),
            foregroundColor: WidgetStateProperty.resolveWith<Color>((states) {
              if (states.contains(WidgetState.selected)) {
                return Colors.cyanAccent;
              }
              return Colors.white54;
            }),
          ),
          segments: const [
            ButtonSegment(
              value: 7,
              label: Text('7D', style: TextStyle(fontSize: 11)),
            ),
            ButtonSegment(
              value: 30,
              label: Text('30D', style: TextStyle(fontSize: 11)),
            ),
          ],
          selected: {selectedDays},
          onSelectionChanged: (val) => onDaysChanged(val.first),
        ),
      ],
    );
  }
}
