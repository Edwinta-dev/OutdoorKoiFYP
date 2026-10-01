import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

Future<void> handleImageManagement(
  BuildContext context,
  VoidCallback onImageUpdated,
) async {
  // 1. Retrieve the userID stored locally during onboarding
  final prefs = await SharedPreferences.getInstance();
  final userId =
      prefs.getString('userID') ?? prefs.getInt('userID')?.toString() ?? '1';

  // 2. Present an action sheet to choose between Uploading a new image or Deleting the current one
  showModalBottomSheet(
    context: context,
    backgroundColor: const Color(0xFF1E293B),
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
    ),
    builder: (BuildContext sheetContext) {
      return Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Manage Species Image',
              style: TextStyle(
                color: Colors.white,
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 16),

            // Option A: Pick & Upload / Overwrite Image
            ListTile(
              leading: const Icon(
                Icons.add_photo_alternate_outlined,
                color: Color(0xFF38BDF8),
              ),
              title: const Text(
                'Upload / Change Image',
                style: TextStyle(color: Colors.white),
              ),
              subtitle: const Text(
                'Choose from gallery or camera',
                style: TextStyle(color: Colors.white60, fontSize: 12),
              ),
              onTap: () async {
                Navigator.pop(sheetContext);
                await _pickAndUploadImage(context, userId, onImageUpdated);
              },
            ),

            const Divider(color: Colors.white12),

            // Option B: Delete Current Image Asset
            ListTile(
              leading: const Icon(
                Icons.delete_outline,
                color: Colors.redAccent,
              ),
              title: const Text(
                'Remove Image',
                style: TextStyle(color: Colors.redAccent),
              ),
              subtitle: const Text(
                'Clear current custom image storage',
                style: TextStyle(color: Colors.white60, fontSize: 12),
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

    // Show loading indicator
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (c) => const Center(
        child: CircularProgressIndicator(color: Color(0xFF38BDF8)),
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
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (c) => const Center(
        child: CircularProgressIndicator(color: Colors.redAccent),
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
