import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/fish/fish_parameter_table.dart

import 'package:flutter/material.dart';

class FishParameterTable extends StatelessWidget {
  final Map<String, String> parameters;
  final String headerTitle;

  const FishParameterTable({
    super.key,
    required this.parameters,
    this.headerTitle = 'Target Parameters & Characteristics',
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.of(context).text.withValues(alpha: 0.02),
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(
          color: AppColors.of(context).text.withValues(alpha: 0.08),
        ),
      ),
      child: Column(
        children: [
          // Section Banner Header
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpace.lg,
              vertical: AppSpace.md,
            ),
            decoration: BoxDecoration(
              color: AppColors.of(context).info.withValues(alpha: 0.1),
              borderRadius: const BorderRadius.vertical(
                top: Radius.circular(AppRadius.card),
              ),
            ),
            child: Text(
              headerTitle,
              style: AppType.style(
                color: AppColors.of(context).info,
                fontSize: AppType.caption,
                fontWeight: FontWeight.bold,
                letterSpacing: 0.8,
              ),
            ),
          ),
          Divider(
            color: AppColors.of(context).text.withValues(alpha: 0.06),
            height: 1,
          ),

          // Parameter Rows
          ListView.separated(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            itemCount: parameters.length,
            separatorBuilder: (_, _) => Divider(
              color: AppColors.of(context).text.withValues(alpha: 0.06),
              height: 1,
              indent: 16,
              endIndent: 16,
            ),
            itemBuilder: (context, index) {
              final key = parameters.keys.elementAt(index);
              final value = parameters[key]!;

              // Long text fields automatically branch into a vertical stack layout
              final bool isLongTextField =
                  key == 'Behaviour' || key == 'Tank Region' || key == 'Gender';

              return Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpace.lg,
                  vertical: AppSpace.lg,
                ),
                child: isLongTextField
                    ? Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            key,
                            style: AppType.style(
                              color: AppColors.of(context).info,
                              fontSize: AppType.label,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          const SizedBox(height: AppSpace.sm),
                          Text(
                            value,
                            style: AppType.style(
                              color: AppColors.of(context).text,
                              fontSize: AppType.body,
                              fontWeight: FontWeight.w500,
                              height: 1.4,
                            ),
                          ),
                        ],
                      )
                    : AdaptiveRow(
                        minWidth: 300,
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            key,
                            style: AppType.style(
                              color: AppColors.of(context).textMuted,
                              fontSize: AppType.body,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                          const SizedBox(width: AppSpace.md),
                          Flexible(
                            child: Text(
                              value,
                              textAlign: TextAlign.right,
                              style: AppType.style(
                                color: AppColors.of(context).text,
                                fontSize: AppType.body,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ],
                      ),
              );
            },
          ),
        ],
      ),
    );
  }
}
