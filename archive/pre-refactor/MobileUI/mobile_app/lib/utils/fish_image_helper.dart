// lib/utils/fish_image_helper.dart

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:image_picker/image_picker.dart';
import 'package:image_cropper/image_cropper.dart';

class FishImageHelper {
  static Future<String> getUserId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('userID') ??
        prefs.getInt('userID')?.toString() ??
        '1';
  }

  static void showLoadingDialog(BuildContext context) {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => const Center(
        child: CircularProgressIndicator(color: Color(0xFF38BDF8)),
      ),
    );
  }

  /// Pick, Crop, and upload a new distinct photo for a species (Supports Multi-Photo Storage)
  static Future<void> pickCropAndUploadImage({
    required BuildContext context,
    required String speciesTitle,
    required VoidCallback onDataRefresh,
  }) async {
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
            toolbarColor: const Color(0xFF131B2A),
            toolbarWidgetColor: Colors.white,
            initAspectRatio: CropAspectRatioPreset.ratio16x9,
            lockAspectRatio: false,
            activeControlsWidgetColor: const Color(0xFF38BDF8),
            statusBarColor: const Color(0xFF070B12),
            backgroundColor: const Color(0xFF070B12),
          ),
          IOSUiSettings(title: 'Refocus Image Viewport'),
        ],
      );

      if (croppedFile == null) return;

      if (!context.mounted) return;
      showLoadingDialog(context);

      final userId = await getUserId();
      final fileBytes = await croppedFile.readAsBytes();
      final formattedTitle = speciesTitle.replaceAll(' ', '_');

      // Generates a unique filename using timestamp, allowing infinite photos per species folder
      final storagePath =
          '$userId/${formattedTitle}_${DateTime.now().millisecondsSinceEpoch}.jpg';

      await Supabase.instance.client.storage
          .from('pond-images')
          .uploadBinary(
            storagePath,
            fileBytes,
            fileOptions: const FileOptions(
              contentType: 'image/jpeg',
              upsert: true,
            ),
          );

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

      await Supabase.instance.client.storage.from('pond-images').remove([
        fullStoragePath,
      ]);

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
      backgroundColor: const Color(0xFF131B2A),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (sheetContext) => Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Manage Species Gallery',
              style: TextStyle(
                color: Colors.white,
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 16),
            ListTile(
              leading: const Icon(
                Icons.add_photo_alternate_outlined,
                color: Color(0xFF38BDF8),
              ),
              title: const Text(
                'Add Another Photo',
                style: TextStyle(color: Colors.white),
              ),
              subtitle: const Text(
                'Upload new image for this species',
                style: TextStyle(color: Colors.white60, fontSize: 12),
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
