import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';

class FishImageHeader extends StatelessWidget {
  final Map<String, dynamic> fishData;
  final VoidCallback onManageImage;

  const FishImageHeader({
    super.key,
    required this.fishData,
    required this.onManageImage,
  });

  @override
  Widget build(BuildContext context) {
    final title = fishData['Title'] ?? 'Unknown Species';
    final commonName = fishData['Common Name'] ?? 'Freshwater Specimen';
    final careLevel = fishData['Care Level'] ?? 'Standard';
    final imageUrl = fishData['Image URL'] ?? '';

    return Container(
      height: 230 + (MediaQuery.textScalerOf(context).scale(20) - 20) * 10,
      width: double.infinity,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AppRadius.panel),
        border: Border.all(
          color: AppColors.of(context).onImage.withValues(alpha: 0.12),
        ),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AppRadius.panel),
        child: Stack(
          children: [
            imageUrl.isNotEmpty
                ? Image.network(
                    imageUrl,
                    fit: BoxFit.cover,
                    width: double.infinity,
                  )
                : Container(color: AppColors.of(context).surface),
            Positioned.fill(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      AppColors.transparent,
                      AppColors.of(context).shadow.withValues(alpha: 0.8),
                    ],
                  ),
                ),
              ),
            ),
            // Edit Button Overlay
            Positioned(
              top: 12,
              right: 12,
              child: InkWell(
                onTap: onManageImage,
                borderRadius: BorderRadius.circular(AppRadius.panel),
                child: Container(
                  padding: const EdgeInsets.all(AppSpace.sm),
                  decoration: BoxDecoration(
                    color: AppColors.of(context).shadow.withValues(alpha: 0.6),
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: AppColors.of(
                        context,
                      ).onImage.withValues(alpha: 0.2),
                    ),
                  ),
                  child: Icon(
                    Icons.edit_outlined,
                    color: AppColors.of(context).onImage,
                    size: 18,
                  ),
                ),
              ),
            ),
            // Title Overlay
            Positioned(
              bottom: 16,
              left: 16,
              right: 16,
              child: AdaptiveRow(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          title,
                          style: AppType.style(
                            color: AppColors.of(context).onImage,
                            fontSize: AppType.heading,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        Text(
                          commonName,
                          style: AppType.style(
                            color: AppColors.of(context).onImage,
                            fontSize: AppType.label,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: AppSpace.md,
                      vertical: AppSpace.xs,
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.of(context).info.withValues(alpha: 0.2),
                      borderRadius: BorderRadius.circular(AppRadius.control),
                      border: Border.all(
                        color: AppColors.of(
                          context,
                        ).info.withValues(alpha: 0.4),
                      ),
                    ),
                    child: Text(
                      'Care: $careLevel',
                      style: AppType.style(
                        color: AppColors.of(context).info,
                        fontWeight: FontWeight.w600,
                        fontSize: AppType.caption,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
