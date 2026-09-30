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
          padding: const EdgeInsets.symmetric(horizontal: 4.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Row 1 & 4: Main Species Name + Care Level Badge (Attractive UI pill)
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  Expanded(
                    child: Text(
                      title,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 20,
                        fontWeight: FontWeight.w900,
                        letterSpacing: 0.5,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 8),
                  // Attractive Care Level Glass Pill
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 6,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xFF38BDF8).withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(
                        color: const Color(0xFF38BDF8).withValues(alpha: 0.4),
                        width: 1,
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: const Color(0xFF38BDF8).withValues(alpha: 0.1),
                          blurRadius: 8,
                        ),
                      ],
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(
                          Icons.shield_outlined,
                          color: Color(0xFF38BDF8),
                          size: 13,
                        ),
                        const SizedBox(width: 5),
                        Text(
                          'Care: $careLevel',
                          style: const TextStyle(
                            color: Color(0xFF38BDF8),
                            fontWeight: FontWeight.w700,
                            fontSize: 11,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),

              // Row 2: Common Names (Allows wrapping if long)
              Row(
                children: [
                  const Icon(
                    Icons.label_outline_rounded,
                    color: Colors.white54,
                    size: 14,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      commonNames,
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),

              // Row 3: Number of Fishes in Tank + Carousel Position Indicator
              Row(
                children: [
                  const Icon(
                    Icons.phishing_outlined,
                    color: Color(0xFF50C878),
                    size: 14,
                  ),
                  const SizedBox(width: 6),
                  Text(
                    'Owned in Tank: $fishCount specimen(s)',
                    style: const TextStyle(
                      color: Color(0xFF50C878),
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const Spacer(),
                  Text(
                    'Profile ${currentIndex + 1} of ${fishProfiles.length}',
                    style: const TextStyle(
                      color: Colors.white38,
                      fontSize: 11,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),

        // --- 2. CLEAN BORDERLESS IMAGE CAROUSEL VIEWPORT ---
        Container(
          height: 220,
          width: double.infinity,
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.02),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: Colors.white.withValues(alpha: 0.08),
              width: 1,
            ),
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(20),
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
                        : Container(color: const Color(0xFF131B2A));
                  },
                ),

                // --- 3. TOP-LAYER EDIT BUTTON ---
                Positioned(
                  top: 12,
                  right: 12,
                  child: Material(
                    color: Colors.transparent,
                    child: InkWell(
                      onTap: onManageImage,
                      borderRadius: BorderRadius.circular(20),
                      child: Container(
                        padding: const EdgeInsets.all(8),
                        decoration: BoxDecoration(
                          color: Colors.black.withValues(alpha: 0.6),
                          shape: BoxShape.circle,
                          border: Border.all(
                            color: Colors.white.withValues(alpha: 0.2),
                          ),
                        ),
                        child: const Icon(
                          Icons.edit_outlined,
                          color: Colors.white,
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
