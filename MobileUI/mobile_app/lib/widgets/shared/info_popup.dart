// lib/widgets/shared/info_popup.dart
//
// Shared "(i)" popup: a plain rounded-rectangle dialog that closes on a tap
// anywhere. Used by the Today action cards and the Digital Twin Model cards.

import 'package:flutter/material.dart';
import 'package:mobile_app/theme/app_theme.dart';

Future<void> showInfoPopup(
  BuildContext context, {
  required Widget content,
  Key? key,
}) {
  return showDialog<void>(
    context: context,
    barrierDismissible: true,
    builder: (ctx) {
      final c = AppColors.of(ctx);
      return Dialog(
        backgroundColor: c.surfaceRaised,
        constraints: const BoxConstraints(maxWidth: 360),
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.control),
        ),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 360),
          child: InkWell(
            key: key,
            onTap: () => Navigator.of(ctx).pop(),
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(AppSpace.xxl),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  content,
                  const SizedBox(height: AppSpace.lg),
                  Text(
                    'Tap anywhere to close',
                    style: AppType.style(
                      color: c.textMuted,
                      fontSize: AppType.caption,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    },
  );
}
