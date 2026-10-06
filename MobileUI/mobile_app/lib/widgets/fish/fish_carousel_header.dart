import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/widgets/fish/fish_carousel_header.dart

import 'package:flutter/material.dart';

class FishCarouselHeader extends StatelessWidget {
  final List<Map<String, dynamic>> fishProfiles;
  final int currentIndex;
  final PageController pageController;
  final ValueChanged<int> onPageChanged;
  final VoidCallback onManageImage;

  const FishCarouselHeader({
    super.key,
    required this.fishProfiles,
    required this.currentIndex,
    required this.pageController,
    required this.onPageChanged,
    required this.onManageImage,
  });

  @override
  Widget build(BuildContext context) {
    final currentFish = fishProfiles.isNotEmpty
        ? fishProfiles[currentIndex.clamp(0, fishProfiles.length - 1)]
        : {};

    final String title = currentFish['Title'] ?? 'Unknown Species';
    final String commonNames =
        currentFish['Common Name'] ?? 'Freshwater Specimen';
    final String careLevel = currentFish['Care Level'] ?? 'Standard';
    // Pull quantity if available in your fish inventory map, defaults to '1'
    final String fishCount = currentFish['Quantity']?.toString() ?? '1';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // --- 1. MINIMALIST HUD HEADER BLOCK (Min 4 rows, highly structured) ---
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpace.xs),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Row 1 & 4: Main Species Name + Care Level Badge (Attractive UI pill)
              AdaptiveRow(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  Expanded(
                    child: Text(
                      title,
                      style: AppType.style(
                        color: AppColors.of(context).text,
                        fontSize: AppType.heading,
                        fontWeight: FontWeight.w900,
                        letterSpacing: 0.5,
                      ),
                    ),
                  ),
                  const SizedBox(width: AppSpace.sm),
                  // Attractive Care Level Glass Pill
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: AppSpace.md,
                      vertical: AppSpace.sm,
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.of(context).info.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(AppRadius.panel),
                      border: Border.all(
                        color: AppColors.of(
                          context,
                        ).info.withValues(alpha: 0.4),
                        width: 1,
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: AppColors.of(
                            context,
                          ).info.withValues(alpha: 0.1),
                          blurRadius: 8,
                        ),
                      ],
                    ),
                    child: AdaptiveRow(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          Icons.shield_outlined,
                          color: AppColors.of(context).info,
                          size: 13,
                        ),
                        const SizedBox(width: AppSpace.xs),
                        Text(
                          'Care: $careLevel',
                          style: AppType.style(
                            color: AppColors.of(context).info,
                            fontWeight: FontWeight.w700,
                            fontSize: AppType.caption,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpace.sm),

              // Row 2: Common Names (Allows wrapping if long)
              AdaptiveRow(
                children: [
                  Icon(
                    Icons.label_outline_rounded,
                    color: AppColors.of(context).textMuted,
                    size: 14,
                  ),
                  const SizedBox(width: AppSpace.sm),
                  Expanded(
                    child: Text(
                      commonNames,
                      style: AppType.style(
                        color: AppColors.of(context).textSecondary,
                        fontSize: AppType.body,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpace.sm),

              // Row 3: Number of Fishes in Tank + Carousel Position Indicator
              AdaptiveRow(
                children: [
                  Icon(
                    Icons.phishing_outlined,
                    color: AppColors.of(context).healthy,
                    size: 14,
                  ),
                  const SizedBox(width: AppSpace.sm),
                  Text(
                    'Owned in Tank: $fishCount specimen(s)',
                    style: AppType.style(
                      color: AppColors.of(context).healthy,
                      fontSize: AppType.label,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const Spacer(),
                  Text(
                    'Profile ${currentIndex + 1} of ${fishProfiles.length}',
                    style: AppType.style(
                      color: AppColors.of(context).textMuted,
                      fontSize: AppType.caption,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: AppSpace.lg),

        // --- 2. CLEAN BORDERLESS IMAGE CAROUSEL VIEWPORT ---
        Container(
          height: 220,
          width: double.infinity,
          decoration: BoxDecoration(
            color: AppColors.of(context).text.withValues(alpha: 0.02),
            borderRadius: BorderRadius.circular(AppRadius.panel),
            border: Border.all(
              color: AppColors.of(context).text.withValues(alpha: 0.08),
              width: 1,
            ),
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(AppRadius.panel),
            child: Stack(
              children: [
                PageView.builder(
                  controller: pageController,
                  itemCount: fishProfiles.length,
                  onPageChanged: onPageChanged,
                  itemBuilder: (context, index) {
                    final imageUrl = fishProfiles[index]['Image URL'] ?? '';
                    return imageUrl.isNotEmpty
                        ? Image.network(
                            imageUrl,
                            fit: BoxFit.cover,
                            alignment: Alignment.center,
                            width: double.infinity,
                          )
                        : Container(color: AppColors.of(context).surface);
                  },
                ),

                // --- 3. TOP-LAYER EDIT BUTTON ---
                Positioned(
                  top: 12,
                  right: 12,
                  child: Material(
                    color: AppColors.transparent,
                    child: InkWell(
                      onTap: onManageImage,
                      borderRadius: BorderRadius.circular(AppRadius.panel),
                      child: Container(
                        padding: const EdgeInsets.all(AppSpace.sm),
                        decoration: BoxDecoration(
                          color: AppColors.of(
                            context,
                          ).shadow.withValues(alpha: 0.6),
                          shape: BoxShape.circle,
                          border: Border.all(
                            color: AppColors.of(
                              context,
                            ).text.withValues(alpha: 0.2),
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
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
