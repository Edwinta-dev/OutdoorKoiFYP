import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'collated_fish_tips_view.dart';
import 'package:image_picker/image_picker.dart';

class FishTipsView extends StatefulWidget {
  const FishTipsView({super.key});

  @override
  State<FishTipsView> createState() => _FishTipsViewState();
}

class _FishTipsViewState extends State<FishTipsView> {
  bool _isLoading = true;
  List<Map<String, dynamic>> _fishProfiles = [];

  @override
  void initState() {
    super.initState();
    _fetchOwnedFishData();
  }

  Future<void> _fetchOwnedFishData() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final List<String> ownedSpecies =
          prefs.getStringList('ownedFishSpecies') ?? [];

      if (ownedSpecies.isEmpty) {
        setState(() => _isLoading = false);
        return;
      }

      final response = await Supabase.instance.client
          .from('Fish_Database')
          .select()
          .inFilter('Title', ownedSpecies);

      setState(() {
        _fishProfiles = List<Map<String, dynamic>>.from(response);
        _isLoading = false;
      });
    } catch (e) {
      print('Error fetching fish tips payload: $e');
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return const Scaffold(
        backgroundColor: Color(0xFF0F172A),
        body: Center(
          child: CircularProgressIndicator(color: Color(0xFF38BDF8)),
        ),
      );
    }

    if (_fishProfiles.isEmpty) {
      return Scaffold(
        backgroundColor: const Color(0xFF0F172A),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(32.0),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: const [
                Icon(Icons.phishing_outlined, size: 64, color: Colors.white54),
                SizedBox(height: 16),
                Text(
                  'No Fish Inventory Found',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                SizedBox(height: 8),
                Text(
                  'Complete your tank onboarding or update your fish profile list to view specific maintenance tips.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.white60, fontSize: 14),
                ),
              ],
            ),
          ),
        ),
      );
    }

    // Logic Switch: If multiple fish owned, route to Collated View with refresh callback
    if (_fishProfiles.length > 1) {
      return CollatedFishTipsView(
        fishProfiles: _fishProfiles,
        onDataRefresh: _fetchOwnedFishData, // ✅ Added missing callback argument
      );
    }

    // Single Fish Standard View with refresh callback passed down
    return SingleFishTipsContent(
      fishData: _fishProfiles.first,
      onDataRefresh: _fetchOwnedFishData,
    );
  }
}

// Single Fish View implementation with proper data refresh hooks
class SingleFishTipsContent extends StatefulWidget {
  const SingleFishTipsContent({
    super.key,
    required this.fishData,
    required this.onDataRefresh,
  });
  final Map<String, dynamic> fishData;
  final VoidCallback onDataRefresh;

  @override
  State<SingleFishTipsContent> createState() => _SingleFishTipsContentState();
}

class _SingleFishTipsContentState extends State<SingleFishTipsContent> {
  Future<void> _handleImageManagement() async {
    final prefs = await SharedPreferences.getInstance();
    final userId =
        prefs.getString('userID') ?? prefs.getInt('userID')?.toString() ?? '1';

    if (!mounted) return;

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
                  'Choose from gallery',
                  style: TextStyle(color: Colors.white60, fontSize: 12),
                ),
                onTap: () async {
                  Navigator.pop(sheetContext);
                  await _pickAndUploadImage(userId);
                },
              ),
              const Divider(color: Colors.white12),
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
                  'Clear custom image storage',
                  style: TextStyle(color: Colors.white60, fontSize: 12),
                ),
                onTap: () async {
                  Navigator.pop(sheetContext);
                  await _deleteUserImage(userId);
                },
              ),
            ],
          ),
        );
      },
    );
  }

  Future<void> _pickAndUploadImage(String userId) async {
    try {
      final ImagePicker picker = ImagePicker();
      final XFile? image = await picker.pickImage(
        source: ImageSource.gallery,
        imageQuality: 80,
      );
      if (image == null) return;

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
      final storagePath = '$userId/$fileName';

      await Supabase.instance.client.storage
          .from('pond-images')
          .uploadBinary(
            storagePath,
            fileBytes,
            fileOptions: const FileOptions(upsert: true),
          );

      if (mounted) Navigator.pop(context); // Dismiss loader
      widget.onDataRefresh();

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Image successfully updated and saved to cloud!'),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Failed to upload image: $e')));
      }
    }
  }

  Future<void> _deleteUserImage(String userId) async {
    try {
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (c) => const Center(
          child: CircularProgressIndicator(color: Colors.redAccent),
        ),
      );

      final List<FileObject> objects = await Supabase.instance.client.storage
          .from('pond-images')
          .list(path: userId);

      if (objects.isNotEmpty) {
        final List<String> pathsToDelete = objects
            .map((obj) => '$userId/${obj.name}')
            .toList();
        await Supabase.instance.client.storage
            .from('pond-images')
            .remove(pathsToDelete);
      }

      if (mounted) Navigator.pop(context);
      widget.onDataRefresh();

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Associated user storage files cleared.'),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Deletion failed: $e')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final fish = widget.fishData;
    final title = fish['Title'] ?? 'Unknown Species';
    final commonName = fish['Common Name'] ?? 'Freshwater Specimen';
    final careLevel = fish['Care Level'] ?? 'Standard';
    final imageUrl = fish['Image URL'] ?? '';

    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16.0, 16.0, 16.0, 110.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Image Viewport with Edit Button Overlay
            Container(
              height: 240,
              width: double.infinity,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(24),
                border: Border.all(color: Colors.white.withOpacity(0.12)),
              ),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(24),
                child: Stack(
                  children: [
                    imageUrl.isNotEmpty
                        ? Image.network(
                            imageUrl,
                            fit: BoxFit.cover,
                            width: double.infinity,
                          )
                        : Container(color: const Color(0xFF1E293B)),
                    Positioned.fill(
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          gradient: LinearGradient(
                            begin: Alignment.topCenter,
                            end: Alignment.bottomCenter,
                            colors: [
                              Colors.transparent,
                              Colors.black.withOpacity(0.75),
                            ],
                          ),
                        ),
                      ),
                    ),
                    // Edit Image Button
                    Positioned(
                      top: 12,
                      right: 12,
                      child: InkWell(
                        onTap: _handleImageManagement,
                        borderRadius: BorderRadius.circular(20),
                        child: Container(
                          padding: const EdgeInsets.all(8),
                          decoration: BoxDecoration(
                            color: Colors.black.withOpacity(0.6),
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: Colors.white.withOpacity(0.2),
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
                    // Title Overlay
                    Positioned(
                      bottom: 16,
                      left: 16,
                      right: 16,
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Text(
                                  title,
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 22,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  commonName,
                                  style: const TextStyle(
                                    color: Colors.white70,
                                    fontSize: 13,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 10,
                              vertical: 5,
                            ),
                            decoration: BoxDecoration(
                              color: const Color(0xFF38BDF8).withOpacity(0.25),
                              borderRadius: BorderRadius.circular(16),
                              border: Border.all(
                                color: const Color(0xFF38BDF8).withOpacity(0.4),
                              ),
                            ),
                            child: Text(
                              'Care: $careLevel',
                              style: const TextStyle(
                                color: Color(0xFF38BDF8),
                                fontWeight: FontWeight.w600,
                                fontSize: 11,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 24),
            // Parameter Table
            Container(
              decoration: BoxDecoration(
                color: const Color(0xFF1E293B).withOpacity(0.55),
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: Colors.white.withOpacity(0.08)),
              ),
              child: ListView.separated(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: 7,
                separatorBuilder: (context, index) => Divider(
                  color: Colors.white.withOpacity(0.06),
                  height: 1,
                  indent: 16,
                  endIndent: 16,
                ),
                itemBuilder: (context, index) {
                  final params = [
                    'Maximum Size',
                    fish['Maximum Size'] ?? 'N/A',
                    'Optimal pH',
                    fish['pH'] ?? 'N/A',
                    'Temperature',
                    fish['Temperature'] ?? 'N/A',
                    'Life Span',
                    fish['Life Span'] ?? 'N/A',
                    'Behaviour',
                    fish['Behaviour'] ?? 'N/A',
                    'Tank Region',
                    fish['Tank Region'] ?? 'N/A',
                    'Gender',
                    fish['Gender'] ?? 'N/A',
                  ];
                  return Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 14,
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        SizedBox(
                          width: 120,
                          child: Text(
                            params[index * 2],
                            style: const TextStyle(
                              color: Colors.white60,
                              fontWeight: FontWeight.w500,
                              fontSize: 13,
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Text(
                            params[index * 2 + 1],
                            textAlign: TextAlign.right,
                            style: const TextStyle(
                              color: Colors.white,
                              fontWeight: FontWeight.w600,
                              fontSize: 13,
                              height: 1.3,
                            ),
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}
