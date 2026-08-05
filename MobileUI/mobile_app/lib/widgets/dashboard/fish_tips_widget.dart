// lib/widgets/dashboard/fish_tips_widget.dart

import 'package:flutter/material.dart';

const Color kEmeraldGreen = Color(0xFF50C878);

class FishTipsWidget extends StatelessWidget {
  final List dynamicData;

  const FishTipsWidget({super.key, required List data}) : dynamicData = data;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Row(
                children: [
                  Icon(
                    Icons.set_meal_outlined,
                    color: Colors.orangeAccent,
                    size: 18,
                  ),
                  SizedBox(width: 8),
                  Text(
                    'Fish Care & Biomass Tips',
                    style: TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                      fontSize: 14,
                    ),
                  ),
                ],
              ),
              Text(
                '${dynamicData.length} Species Logged',
                style: TextStyle(
                  color: Colors.orangeAccent.withOpacity(0.8),
                  fontSize: 10,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),

          dynamicData.isEmpty
              ? const Text(
                  'No fish care tips available',
                  style: TextStyle(color: Colors.white38, fontSize: 11),
                )
              : ListView.separated(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: dynamicData.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 10),
                  itemBuilder: (context, index) {
                    final item = dynamicData[index];
                    final species = item['species'] ?? 'General Care';
                    final tip =
                        item['tip'] ?? 'Maintain stable water parameters.';

                    return TipTile(
                      title: species,
                      tip: tip,
                      badgeColor: index.isEven
                          ? Colors.orangeAccent
                          : kEmeraldGreen,
                    );
                  },
                ),
        ],
      ),
    );
  }
}

class TipTile extends StatelessWidget {
  final String title;
  final String tip;
  final Color badgeColor;

  const TipTile({
    super.key,
    required this.title,
    required this.tip,
    required this.badgeColor,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.03),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withOpacity(0.05)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 4,
            height: 36,
            decoration: BoxDecoration(
              color: badgeColor,
              borderRadius: BorderRadius.circular(4),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 12,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  tip,
                  style: const TextStyle(color: Colors.white60, fontSize: 11),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
