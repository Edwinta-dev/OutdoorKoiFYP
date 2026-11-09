import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/providers.dart';
import '../../theme/app_theme.dart';
import '../shared/info_popup.dart';

class CameraCardReadingCard extends ConsumerWidget {
  const CameraCardReadingCard({super.key, required this.userId});
  final int userId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = AppColors.of(context);
    final masks = ref.watch(cameraCardMasksProvider(userId));
    final reading = ref.watch(cameraCardReadingProvider(userId));
    final masksData = masks.asData?.value;
    final data = reading.asData?.value;
    String? empty;
    if (masksData != null && masksData['version'] == null) {
      empty = 'Place the card masks in Camera gallery';
    } else if (masksData != null && masksData['reference_image_id'] == null) {
      empty = 'Choose a clear-water reference frame in Camera gallery';
    } else if (data?.quality == 'unusable') {
      empty = 'This camera frame is unusable for a card reading';
    } else if (reading.hasError || masks.hasError) {
      empty = 'Card reading unavailable';
    } else if (data == null) {
      empty = 'No card reading is available for the latest frame';
    }

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpace.lg),
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: colors.outline.withValues(alpha: .35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Card reading',
                  style: AppType.style(
                    color: colors.text,
                    fontWeight: FontWeight.bold,
                    fontSize: AppType.body,
                  ),
                ),
              ),
              IconButton(
                key: const ValueKey('card-reading-info'),
                tooltip: 'About card readings',
                visualDensity: VisualDensity.compact,
                icon: Icon(
                  Icons.info_outline,
                  size: 18,
                  color: colors.textMuted,
                ),
                onPressed: () => showInfoPopup(
                  context,
                  content: Text(
                    'Adjacent pH segments can be too similar to separate. The comparison also needs a clear-water reference frame; changes in that reference can affect both readings and the turbidity estimate.',
                    style: AppType.style(
                      color: colors.textSecondary,
                      fontSize: AppType.label,
                      height: 1.4,
                    ),
                  ),
                ),
              ),
            ],
          ),
          if (reading.isLoading || masks.isLoading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: AppSpace.md),
              child: Center(
                child: SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            )
          else if (empty != null)
            Padding(
              padding: const EdgeInsets.only(top: AppSpace.xs),
              child: Text(
                empty,
                style: AppType.style(
                  color: colors.textSecondary,
                  fontSize: AppType.label,
                ),
              ),
            )
          else if (data != null) ...[
            _segment(context, 'pH', data.ph),
            _segment(context, 'Ammonia', data.nh3),
            const SizedBox(height: AppSpace.sm),
            Text(
              data.frameAgreement == 'clear'
                  ? 'Both methods agree'
                  : data.frameAgreement == 'disagree'
                  ? 'Methods disagree'
                  : 'Methods could not be compared',
              style: AppType.style(
                color: colors.textSecondary,
                fontSize: AppType.label,
              ),
            ),
            const SizedBox(height: AppSpace.xs),
            Text(
              'Usable dots: ${data.usableDots} of ${data.totalDots}',
              style: AppType.style(
                color: colors.textSecondary,
                fontSize: AppType.label,
              ),
            ),
            if (data.turbidityIndex case final index?) ...[
              const SizedBox(height: AppSpace.sm),
              Text(
                'Turbidity Index: ${index.toStringAsFixed(2)} · ${data.turbidityLabel(index)}',
                style: AppType.style(
                  color: colors.text,
                  fontSize: AppType.label,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }

  Widget _segment(
    BuildContext context,
    String label,
    Map<String, dynamic>? result,
  ) {
    final colors = AppColors.of(context);
    final segment = result?['segment']?.toString();
    final confidence = result?['confidence']?.toString() ?? 'ambiguous';
    final value = segment == null
        ? 'No match'
        : segment.replaceFirst(RegExp(r'^(ph|nh3)_ref_'), 'segment ');
    return Padding(
      padding: const EdgeInsets.only(top: AppSpace.sm),
      child: Row(
        children: [
          Expanded(
            child: Text(
              '$label: $value',
              style: AppType.style(color: colors.text, fontSize: AppType.label),
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpace.sm,
              vertical: AppSpace.xs,
            ),
            decoration: BoxDecoration(
              color: colors.surfaceRaised,
              borderRadius: BorderRadius.circular(AppRadius.chip),
            ),
            child: Text(
              confidence,
              style: AppType.style(
                color: colors.textSecondary,
                fontSize: AppType.micro,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
