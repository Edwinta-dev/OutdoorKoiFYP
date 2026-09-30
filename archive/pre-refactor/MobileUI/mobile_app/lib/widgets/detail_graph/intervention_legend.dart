import 'package:flutter/material.dart';

class InterventionLegend extends StatelessWidget {
  final String primaryEventType;

  const InterventionLegend({super.key, required this.primaryEventType});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.06)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Timeline Intervention Legend',
            style: TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.bold,
              fontSize: 12,
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 12,
            runSpacing: 8,
            children: [
              _legendItem(
                'Major Flush Reset',
                Colors.greenAccent,
                isHighlighted: true,
              ),
              _legendItem(
                'Water Change',
                Colors.lightBlueAccent,
                isHighlighted: primaryEventType == 'WATER_CHANGE',
              ),
              _legendItem(
                'Water Top-Up',
                Colors.cyanAccent,
                isHighlighted: primaryEventType == 'WATER_TOPUP',
              ),
              _legendItem(
                'Algae Scrub',
                Colors.tealAccent,
                isHighlighted: primaryEventType == 'ALGAE_SCRUB',
              ),
              _legendItem(
                'Feeding Session',
                Colors.orangeAccent,
                isHighlighted: primaryEventType == 'FEEDING',
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _legendItem(String label, Color color, {required bool isHighlighted}) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: isHighlighted ? 10 : 6,
          height: isHighlighted ? 10 : 6,
          decoration: BoxDecoration(
            color: isHighlighted ? color : Colors.white24,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 6),
        Text(
          label,
          style: TextStyle(
            color: isHighlighted ? Colors.white : Colors.white38,
            fontSize: 10,
            fontWeight: isHighlighted ? FontWeight.bold : FontWeight.normal,
          ),
        ),
      ],
    );
  }
}
