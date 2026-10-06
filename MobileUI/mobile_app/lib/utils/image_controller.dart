import 'package:mobile_app/theme/app_theme.dart';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../data/local_profile_repository.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

Future<void> handleImageManagement(
  BuildContext context,
  VoidCallback onImageUpdated,
) async {
  // 1. Retrieve the userID stored locally during onboarding
  final prefs = await PreferencesProfileRepository().load();
  final userId = prefs.pondId?.toString() ?? '0';

  if (!context.mounted) return;

  // 2. Present an action sheet to choose between Uploading a new image or Deleting the current one
  unawaited(
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.of(context).surfaceRaised,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.sheet)),
      ),
      builder: (BuildContext sheetContext) {
        return Padding(
          padding: const EdgeInsets.all(AppSpace.xxl),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                'Manage Species Image',
                style: AppType.style(
                  color: AppColors.of(context).text,
                  fontSize: AppType.title,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: AppSpace.lg),

              // Option A: Pick & Upload / Overwrite Image
              ListTile(
                leading: Icon(
                  Icons.add_photo_alternate_outlined,
                  color: AppColors.of(context).info,
                ),
                title: Text(
                  'Upload / Change Image',
                  style: AppType.style(color: AppColors.of(context).text),
                ),
                subtitle: Text(
                  'Choose from gallery or camera',
                  style: AppType.style(
                    color: AppColors.of(context).textSecondary,
                    fontSize: AppType.label,
                  ),
                ),
                onTap: () async {
                  Navigator.pop(sheetContext);
                  await _pickAndUploadImage(context, userId, onImageUpdated);
                },
              ),

              Divider(color: AppColors.of(context).outline),

              // Option B: Delete Current Image Asset
              ListTile(
                leading: Icon(
                  Icons.delete_outline,
                  color: AppColors.of(context).danger,
                ),
                title: Text(
                  'Remove Image',
                  style: AppType.style(color: AppColors.of(context).danger),
                ),
                subtitle: Text(
                  'Clear current custom image storage',
                  style: AppType.style(
                    color: AppColors.of(context).textSecondary,
                    fontSize: AppType.label,
                  ),
                ),
                onTap: () async {
                  Navigator.pop(sheetContext);
                  await _deleteUserImage(context, userId, onImageUpdated);
                },
              ),
            ],
          ),
        );
      },
    ),
  );
}

Future<void> _pickAndUploadImage(
  BuildContext context,
  String userId,
  VoidCallback onImageUpdated,
) async {
  try {
    final ImagePicker picker = ImagePicker();

    // Allow user to choose source (Camera or Gallery)
    final XFile? image = await picker.pickImage(
      source: ImageSource
          .gallery, // Can be changed or prompted via secondary dialog
      imageQuality: 80, // Compression safeguard for mobile optimization
    );

    if (image == null) return;
    if (!context.mounted) return;

    // Show loading indicator
    unawaited(
      showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (c) => Center(
          child: CircularProgressIndicator(color: AppColors.of(context).info),
        ),
      ),
    );

    final fileBytes = await image.readAsBytes();
    final fileName =
        'fish_profile_${DateTime.now().millisecondsSinceEpoch}.jpg';

    // Dynamically build path using user ID bucket partition
    final storagePath = '$userId/$fileName';

    // Upload to Supabase Bucket (e.g., bucket named 'pond-images' or 'fish-bucket')
    await Supabase.instance.client.storage
        .from('pond-images')
        .uploadBinary(
          storagePath,
          fileBytes,
          fileOptions: const FileOptions(upsert: true),
        );

    if (context.mounted) Navigator.pop(context); // Dismiss loading dialog

    // Trigger callback to refresh parent widget state
    onImageUpdated();

    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Image successfully updated and saved to cloud!'),
        ),
      );
    }
  } catch (e) {
    if (context.mounted) {
      Navigator.pop(context); // Dismiss loader if active
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Failed to upload image: $e')));
    }
  }
}

Future<void> _deleteUserImage(
  BuildContext context,
  String userId,
  VoidCallback onImageUpdated,
) async {
  try {
    unawaited(
      showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (c) => Center(
          child: CircularProgressIndicator(color: AppColors.of(context).danger),
        ),
      ),
    );

    // List files inside the user's custom directory path to locate what to delete
    final List<FileObject> objects = await Supabase.instance.client.storage
        .from('pond-images')
        .list(path: userId);

    if (objects.isNotEmpty) {
      // Build full file paths for deletion
      final List<String> pathsToDelete = objects
          .map((obj) => '$userId/${obj.name}')
          .toList();

      await Supabase.instance.client.storage
          .from('pond-images')
          .remove(pathsToDelete);
    }

    if (context.mounted) Navigator.pop(context); // Dismiss loader
    onImageUpdated();

    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Associated user storage files cleared.')),
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
