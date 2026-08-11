import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:image_picker/image_picker.dart';

class CollatedFishTipsView extends StatefulWidget {
  const CollatedFishTipsView({
    super.key,
    required this.fishProfiles,
    required this.onDataRefresh,
  });
  final List<Map<String, dynamic>> fishProfiles;
  final VoidCallback onDataRefresh;

  @override
  State<CollatedFishTipsView> createState() => _CollatedFishTipsViewState();
}

class _CollatedFishTipsViewState extends State<CollatedFishTipsView> {
  int _currentCarouselIndex = 0;
  late final PageController _pageController;

  @override
  void initState() {
    super.initState();
    _pageController = PageController();
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

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
                  'Choose from gallery (Auto-centered)',
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

      // ✅ Added resolution framing constraints to maintain correct aspect ratio and framing center
      final XFile? image = await picker.pickImage(
        source: ImageSource.gallery,
        imageQuality: 85,
        maxWidth: 1200,
        maxHeight: 900,
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
            content: Text('Image successfully updated and recentered!'),
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
    final currentFish = widget.fishProfiles.isNotEmpty
        ? widget.fishProfiles[_currentCarouselIndex.clamp(
            0,
            widget.fishProfiles.length - 1,
          )]
        : {};

    // ✅ Computed widest unified range across all species values
    final computedPhRange = _computeWidestNumericalRange('pH');
    final computedTempRange = _computeWidestNumericalRange('Temperature');

    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16.0, 16.0, 16.0, 110.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 1. Combined Image Carousel Viewport
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
                    PageView.builder(
                      controller: _pageController,
                      itemCount: widget.fishProfiles.length,
                      onPageChanged: (index) {
                        setState(() {
                          _currentCarouselIndex = index;
                        });
                      },
                      itemBuilder: (context, index) {
                        final imageUrl =
                            widget.fishProfiles[index]['Image URL'] ?? '';
                        return imageUrl.isNotEmpty
                            ? Image.network(
                                imageUrl,
                                fit: BoxFit.cover,
                                alignment: Alignment
                                    .center, // ✅ Forces center alignment for image framing
                                width: double.infinity,
                                errorBuilder: (context, error, stackTrace) =>
                                    Container(color: const Color(0xFF1E293B)),
                              )
                            : Container(color: const Color(0xFF1E293B));
                      },
                    ),
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
                    // Title Overlay matching current carousel index dynamically
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
                                  currentFish['Title'] ?? 'Unknown',
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 22,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  currentFish['Common Name'] ??
                                      'Community Specimen',
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
                              'Care: ${currentFish['Care Level'] ?? 'Standard'}',
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
            const SizedBox(height: 12),

            // Carousel Indicator Dots
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: widget.fishProfiles.asMap().entries.map((entry) {
                return GestureDetector(
                  onTap: () {
                    _pageController.animateToPage(
                      entry.key,
                      duration: const Duration(milliseconds: 300),
                      curve: Curves.easeInOut,
                    );
                  },
                  child: Container(
                    width: _currentCarouselIndex == entry.key ? 18 : 6,
                    height: 6,
                    margin: const EdgeInsets.symmetric(horizontal: 3),
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(3),
                      color: _currentCarouselIndex == entry.key
                          ? const Color(0xFF38BDF8)
                          : Colors.white.withOpacity(0.3),
                    ),
                  ),
                );
              }).toList(),
            ),
            const SizedBox(height: 20),

            // 2. Unified Computed Table Metrics with "Target Parameters" header
            Container(
              decoration: BoxDecoration(
                color: const Color(0xFF1E293B).withOpacity(0.55),
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: Colors.white.withOpacity(0.08)),
              ),
              child: Column(
                children: [
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 10,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xFF38BDF8).withOpacity(0.1),
                      borderRadius: const BorderRadius.vertical(
                        top: Radius.circular(20),
                      ),
                    ),
                    child: const Text(
                      'Target Parameters', // ✅ Updated header label
                      style: TextStyle(
                        color: Color(0xFF38BDF8),
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  Divider(color: Colors.white.withOpacity(0.06), height: 1),

                  _buildTableRow('Unified Safe pH', computedPhRange),
                  Divider(
                    color: Colors.white.withOpacity(0.06),
                    height: 1,
                    indent: 16,
                    endIndent: 16,
                  ),

                  _buildTableRow('Unified Temperature', computedTempRange),
                  Divider(
                    color: Colors.white.withOpacity(0.06),
                    height: 1,
                    indent: 16,
                    endIndent: 16,
                  ),

                  _buildTableRow(
                    'Maximum Size',
                    currentFish['Maximum Size'] ?? 'N/A',
                  ),
                  Divider(
                    color: Colors.white.withOpacity(0.06),
                    height: 1,
                    indent: 16,
                    endIndent: 16,
                  ),
                  _buildTableRow(
                    'Life Span',
                    currentFish['Life Span'] ?? 'N/A',
                  ),
                  Divider(
                    color: Colors.white.withOpacity(0.06),
                    height: 1,
                    indent: 16,
                    endIndent: 16,
                  ),
                  _buildTableRow(
                    'Behaviour',
                    currentFish['Behaviour'] ?? 'N/A',
                  ),
                  Divider(
                    color: Colors.white.withOpacity(0.06),
                    height: 1,
                    indent: 16,
                    endIndent: 16,
                  ),
                  _buildTableRow(
                    'Tank Region',
                    currentFish['Tank Region'] ?? 'N/A',
                  ),
                  Divider(
                    color: Colors.white.withOpacity(0.06),
                    height: 1,
                    indent: 16,
                    endIndent: 16,
                  ),
                  _buildTableRow('Gender', currentFish['Gender'] ?? 'N/A'),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTableRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          SizedBox(
            width: 130,
            child: Text(
              label,
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
              value,
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
  }

  /// ✅ Algorithmic parser to extract numeric bounds and find the absolute widest range span across all fishes
  String _computeWidestNumericalRange(String key) {
    try {
      final List<double> extractedNumbers = [];
      String suffix = '';

      for (var fish in widget.fishProfiles) {
        final rawVal = fish[key]?.toString() ?? '';
        // Use RegExp to pull all decimals/integers out of strings like "6.0 - 7.0" or "15.5°C - 27°C"
        final matches = RegExp(r'([\d\.]+)').allMatches(rawVal);
        for (var match in matches) {
          final val = double.tryParse(match.group(1) ?? '');
          if (val != null) extractedNumbers.add(val);
        }
        if (suffix.isEmpty && rawVal.contains('°C')) {
          suffix = '°C';
        }
      }

      if (extractedNumbers.isEmpty) return 'Compatible Range';

      extractedNumbers.sort();
      final lowest = extractedNumbers.first;
      final highest = extractedNumbers.last;

      // If min and max are practically the same
      if ((lowest - highest).abs() < 0.01) {
        return '$lowest$suffix';
      }

      return '$lowest - $highest$suffix';
    } catch (_) {
      return 'Compatible Range';
    }
  }
}
