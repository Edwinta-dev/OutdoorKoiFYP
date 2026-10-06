import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
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
    return AdaptiveRow(
      minWidth: 300,
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          '${metricType.toUpperCase()} Timeline',
          style: AppType.style(
            color: AppColors.of(context).textSecondary,
            fontSize: AppType.label,
            fontWeight: FontWeight.w600,
          ),
        ),
        SegmentedButton<int>(
          style: ButtonStyle(
            visualDensity: VisualDensity.compact,
            backgroundColor: WidgetStateProperty.resolveWith<Color>((states) {
              if (states.contains(WidgetState.selected)) {
                return AppColors.of(context).info.withValues(alpha: 0.2);
              }
              return AppColors.transparent;
            }),
            foregroundColor: WidgetStateProperty.resolveWith<Color>((states) {
              if (states.contains(WidgetState.selected)) {
                return AppColors.of(context).info;
              }
              return AppColors.of(context).textMuted;
            }),
          ),
          segments: [
            ButtonSegment(
              value: 7,
              label: Text(
                '7D',
                style: AppType.style(fontSize: AppType.caption),
              ),
            ),
            ButtonSegment(
              value: 30,
              label: Text(
                '30D',
                style: AppType.style(fontSize: AppType.caption),
              ),
            ),
          ],
          selected: {selectedDays},
          onSelectionChanged: (val) => onDaysChanged(val.first),
        ),
      ],
    );
  }
}
