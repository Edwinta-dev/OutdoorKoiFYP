// lib/screens/fish_tips_view.dart

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../utils/fish_image_helper.dart';
import '../widgets/fish/fish_carousel_header.dart';
import '../widgets/fish/fish_parameter_table.dart';

class FishTipsView extends StatefulWidget {
  const FishTipsView({super.key});

  @override
  State<FishTipsView> createState() => FishTipsViewState();
}

class FishTipsViewState extends State<FishTipsView> {
  bool _isLoading = true;
  List<Map<String, dynamic>> _fishProfiles = [];
  int _currentCarouselIndex = 0;
  late final PageController _pageController;

  @override
  void initState() {
    super.initState();
    _pageController = PageController();
    _fetchOwnedFishData();
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  Future<void> refreshData() async => _fetchOwnedFishData();

  Future<void> _fetchOwnedFishData() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final List<String> ownedSpecies =
          prefs.getStringList('ownedFishSpecies') ?? [];
      final userId = await FishImageHelper.getUserId();

      if (ownedSpecies.isEmpty) {
        if (mounted) setState(() => _isLoading = false);
        return;
      }

      // 1. Fetch relational species profiles from database
      final response = await Supabase.instance.client
          .from('Fish_Database')
          .select()
          .inFilter('Title', ownedSpecies);

      // 2. Fetch all user storage images from Supabase Bucket partition folder ($userId)
      List<FileObject> storageFiles = [];
      try {
        storageFiles = await Supabase.instance.client.storage
            .from('pond-images')
            .list(path: userId);
      } catch (_) {
        // Folder might be empty or uninitialized
      }

      // 3. Map profiles and parse multi-photo storage items using RegEx
      List<Map<String, dynamic>> profiles = List<Map<String, dynamic>>.from(
        response,
      );

      for (var profile in profiles) {
        final title = profile['Title']?.toString() ?? '';
        final formattedTitle = title.replaceAll(' ', '_');

        // Regex pattern to match files starting with this species title followed by an underscore and timestamp
        final regex = RegExp(
          '^${RegExp.escape(formattedTitle)}_[0-9]+\\.jpg\$',
        );

        List<String> speciesImageUrls = [];
        for (var file in storageFiles) {
          if (regex.hasMatch(file.name)) {
            final publicUrl = Supabase.instance.client.storage
                .from('pond-images')
                .getPublicUrl('$userId/${file.name}');
            speciesImageUrls.add(publicUrl);
          }
        }

        // Fallback to table 'Image URL' column if no gallery storage photos found yet
        if (speciesImageUrls.isEmpty && profile['Image URL'] != null) {
          speciesImageUrls.add(profile['Image URL'].toString());
        }

        // Inject compiled multi-image list into profile map
        profile['ImageUrls'] = speciesImageUrls;
      }

      if (mounted) {
        setState(() {
          _fishProfiles = profiles;
          _isLoading = false;
          if (_currentCarouselIndex >= _fishProfiles.length) {
            _currentCarouselIndex = (_fishProfiles.length - 1).clamp(0, 99);
          }
        });
      }
    } catch (e) {
      debugPrint('Error loading fish profiles & gallery: $e');
      if (mounted) setState(() => _isLoading = false);
    }
  }

  String _computeWidestNumericalRange(String key) {
    try {
      final List<double> extractedNumbers = [];
      String suffix = '';

      for (var fish in _fishProfiles) {
        final rawVal = fish[key]?.toString() ?? '';
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

      if ((lowest - highest).abs() < 0.01) {
        return '$lowest$suffix';
      }

      return '$lowest - $highest$suffix';
    } catch (_) {
      return 'Compatible Range';
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return const Center(
        child: CircularProgressIndicator(color: Color(0xFF38BDF8)),
      );
    }

    if (_fishProfiles.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
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
                'Complete your tank onboarding to view specific maintenance tips.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white60, fontSize: 14),
              ),
            ],
          ),
        ),
      );
    }

    final currentFish =
        _fishProfiles[_currentCarouselIndex.clamp(0, _fishProfiles.length - 1)];
    final speciesTitle = currentFish['Title'] ?? 'Unknown Species';

    final Map<String, String> parameters = {
      if (_fishProfiles.length > 1) ...{
        'Unified Safe pH': _computeWidestNumericalRange('pH'),
        'Unified Temperature': _computeWidestNumericalRange('Temperature'),
      },
      'Maximum Size': currentFish['Maximum Size']?.toString() ?? 'N/A',
      'Life Span': currentFish['Life Span']?.toString() ?? 'N/A',
      'Tank Region': currentFish['Tank Region']?.toString() ?? 'N/A',
      'Gender': currentFish['Gender']?.toString() ?? 'N/A',
      'Behaviour': currentFish['Behaviour']?.toString() ?? 'N/A',
    };

    return Scaffold(
      backgroundColor: const Color(0xFF070B12),
      body: SafeArea(
        child: SingleChildScrollView(
          physics: const BouncingScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 110),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              FishCarouselHeader(
                fishProfiles: _fishProfiles,
                currentIndex: _currentCarouselIndex,
                pageController: _pageController,
                onPageChanged: (index) =>
                    setState(() => _currentCarouselIndex = index),
                onManageImage: () => FishImageHelper.showImageManagementSheet(
                  context: context,
                  speciesTitle: speciesTitle,
                  onDataRefresh: _fetchOwnedFishData,
                ),
              ),
              const SizedBox(height: 12),

              if (_fishProfiles.length > 1) ...[
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: _fishProfiles.asMap().entries.map((entry) {
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
                              : Colors.white.withValues(alpha: 0.3),
                        ),
                      ),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 20),
              ] else
                const SizedBox(height: 24),

              FishParameterTable(
                parameters: parameters,
                headerTitle: _fishProfiles.length > 1
                    ? 'Target Parameters & Characteristics'
                    : 'Species Parameters & Characteristics',
              ),
            ],
          ),
        ),
      ),
    );
  }
}
