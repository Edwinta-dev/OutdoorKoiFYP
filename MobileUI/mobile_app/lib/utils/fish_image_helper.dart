import 'package:mobile_app/theme/app_theme.dart';
// lib/utils/fish_image_helper.dart

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/providers.dart';

import 'package:image_picker/image_picker.dart';
import 'package:image_cropper/image_cropper.dart';

class FishImageHelper {
  static Future<String> getUserId(BuildContext context) async {
    final profile = await ProviderScope.containerOf(
      context,
      listen: false,
    ).read(localProfileProvider.future);
    return '${profile.pondId ?? 0}';
  }

  static void showLoadingDialog(BuildContext context) {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => Center(
        child: CircularProgressIndicator(color: AppColors.of(context).info),
      ),
    );
  }

  /// Pick, Crop, and upload a new distinct photo for a species (Supports Multi-Photo Storage)
  static Future<void> pickCropAndUploadImage({
    required BuildContext context,
    required String speciesTitle,
    required VoidCallback onDataRefresh,
  }) async {
    final colors = AppColors.of(context);
    try {
      final picker = ImagePicker();
      final XFile? pickedFile = await picker.pickImage(
        source: ImageSource.gallery,
        imageQuality: 90,
      );
      if (pickedFile == null) return;

      final CroppedFile? croppedFile = await ImageCropper().cropImage(
        sourcePath: pickedFile.path,
        uiSettings: [
          AndroidUiSettings(
            toolbarTitle: 'Refocus Image Viewport',
            toolbarColor: colors.surface,
            toolbarWidgetColor: colors.text,
            initAspectRatio: CropAspectRatioPreset.ratio16x9,
            lockAspectRatio: false,
            activeControlsWidgetColor: colors.info,
            statusBarLight: !colors.isDark,
            backgroundColor: colors.canvas,
          ),
          IOSUiSettings(title: 'Refocus Image Viewport'),
        ],
      );

      if (croppedFile == null) return;

      if (!context.mounted) return;
      showLoadingDialog(context);

      final userId = await getUserId(context);
      final fileBytes = await croppedFile.readAsBytes();
      final formattedTitle = speciesTitle.replaceAll(' ', '_');

      // Generates a unique filename using timestamp, allowing infinite photos per species folder
      final storagePath =
          '$userId/${formattedTitle}_${DateTime.now().millisecondsSinceEpoch}.jpg';

      if (!context.mounted) return;
      await ProviderScope.containerOf(context, listen: false)
          .read(pondProfileRepositoryProvider)
          .uploadSpeciesPhoto(storagePath, fileBytes);

      if (context.mounted) Navigator.pop(context);
      onDataRefresh();

      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('New species photo successfully added to gallery!'),
          ),
        );
      }
    } catch (e) {
      if (context.mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Failed to process image: $e')));
      }
    }
  }

  /// Deletes a specific image or all user files if needed
  static Future<void> deleteSpecificImage({
    required BuildContext context,
    required String fullStoragePath,
    required VoidCallback onDataRefresh,
  }) async {
    try {
      showLoadingDialog(context);

      await ProviderScope.containerOf(
        context,
        listen: false,
      ).read(pondProfileRepositoryProvider).deleteSpeciesPhoto(fullStoragePath);

      if (context.mounted) Navigator.pop(context);
      onDataRefresh();

      if (context.mounted) {
        const SnackBar(
          content: Text('Selected photo removed from cloud storage.'),
        );
      }
    } catch (e) {
      if (context.mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Deletion failed: $e')));
      }
    }
  }

  static void showImageManagementSheet({
    required BuildContext context,
    required String speciesTitle,
    required VoidCallback onDataRefresh,
  }) {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.of(context).surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(AppRadius.sheet),
        ),
      ),
      builder: (sheetContext) => Padding(
        padding: const EdgeInsets.all(AppSpace.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              'Manage Species Gallery',
              style: AppType.style(
                color: AppColors.of(context).text,
                fontSize: AppType.title,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: AppSpace.lg),
            ListTile(
              leading: Icon(
                Icons.add_photo_alternate_outlined,
                color: AppColors.of(context).info,
              ),
              title: Text(
                'Add Another Photo',
                style: AppType.style(color: AppColors.of(context).text),
              ),
              subtitle: Text(
                'Upload new image for this species',
                style: AppType.style(
                  color: AppColors.of(context).textSecondary,
                  fontSize: AppType.label,
                ),
              ),
              onTap: () async {
                Navigator.pop(sheetContext);
                await pickCropAndUploadImage(
                  context: context,
                  speciesTitle: speciesTitle,
                  onDataRefresh: onDataRefresh,
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}
