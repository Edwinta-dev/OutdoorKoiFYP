import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';

class InterventionLegend extends StatelessWidget {
  final String primaryEventType;

  const InterventionLegend({super.key, required this.primaryEventType});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpace.lg),
      decoration: BoxDecoration(
        color: AppColors.of(context).surface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(
          color: AppColors.of(context).text.withValues(alpha: 0.06),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Timeline Intervention Legend',
            style: AppType.style(
              color: AppColors.of(context).text,
              fontWeight: FontWeight.bold,
              fontSize: AppType.label,
            ),
          ),
          const SizedBox(height: AppSpace.md),
          Wrap(
            spacing: 12,
            runSpacing: 8,
            children: [
              _legendItem(
                context,
                'Salt Addition',
                AppColors.of(context).intervention,
                isHighlighted: primaryEventType == 'SALT',
              ),
              _legendItem(
                context,
                'Filter Cleaning',
                AppColors.of(context).warning,
                isHighlighted: primaryEventType == 'FILTER_CLEAN',
              ),
              _legendItem(
                context,
                'Major Flush Reset',
                AppColors.of(context).healthy,
                isHighlighted: true,
              ),
              _legendItem(
                context,
                'Water Change',
                AppColors.of(context).info,
                isHighlighted: primaryEventType == 'WATER_CHANGE',
              ),
              _legendItem(
                context,
                'Water Top-Up',
                AppColors.of(context).info,
                isHighlighted: primaryEventType == 'WATER_TOPUP',
              ),
              _legendItem(
                context,
                'Algae Scrub',
                AppColors.of(context).water,
                isHighlighted: primaryEventType == 'ALGAE_SCRUB',
              ),
              _legendItem(
                context,
                'Feeding Session',
                AppColors.of(context).feeding,
                isHighlighted: primaryEventType == 'FEEDING',
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _legendItem(
    BuildContext context,
    String label,
    Color color, {
    required bool isHighlighted,
  }) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: isHighlighted ? 10 : 6,
          height: isHighlighted ? 10 : 6,
          decoration: BoxDecoration(
            color: isHighlighted ? color : AppColors.of(context).outline,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: AppSpace.sm),
        Text(
          label,
          style: AppType.style(
            color: isHighlighted
                ? AppColors.of(context).text
                : AppColors.of(context).textMuted,
            fontSize: AppType.micro,
            fontWeight: isHighlighted ? FontWeight.bold : FontWeight.normal,
          ),
        ),
      ],
    );
  }
}
