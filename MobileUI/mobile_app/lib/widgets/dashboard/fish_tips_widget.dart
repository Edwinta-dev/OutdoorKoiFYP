// lib/widgets/dashboard/fish_tips_widget.dart

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

const Color kEmeraldGreen = Color(0xFF50C878);

class FishTipsWidget extends StatefulWidget {
  final List dynamicData; // Retained constructor compatibility

  const FishTipsWidget({super.key, required List data}) : dynamicData = data;

  @override
  State<FishTipsWidget> createState() => _FishTipsWidgetState();
}

class _FishTipsWidgetState extends State<FishTipsWidget> {
  final PageController _pageController = PageController();
  int _currentPage = 0;
  bool _isLoading = true;

  List<Map<String, dynamic>> _fishProfiles = [];
  Map<String, dynamic>? _communitySummary;
  bool _hasClash = false;

  @override
  void initState() {
    super.initState();
    _loadFishData();
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  /// Reads owned species list from SharedPreferences and queries public."Fish_Database"
  Future<void> _loadFishData() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final List<String> ownedSpecies =
          prefs.getStringList('ownedFishSpecies') ?? [];

      if (ownedSpecies.isEmpty) {
        if (mounted) setState(() => _isLoading = false);
        return;
      }

      // Supabase Query: Select matching rows for all unique owned species
      final response = await Supabase.instance.client
          .from('Fish_Database')
          .select()
          .filter('Title', 'in', ownedSpecies);

      final List<Map<String, dynamic>> fetchedFish =
          List<Map<String, dynamic>>.from(response as List);

      _evaluateCommunityParams(fetchedFish);

      if (mounted) {
        setState(() {
          _fishProfiles = fetchedFish;
          _isLoading = false;
        });
      }
    } catch (e) {
      debugPrint('Error loading fish profiles: $e');
      if (mounted) setState(() => _isLoading = false);
    }
  }

  /// Calculates overlapping pH & Temp ranges. Detects clashes across species.
  void _evaluateCommunityParams(List<Map<String, dynamic>> fishList) {
    if (fishList.isEmpty) return;

    double maxLowPh = 0.0;
    double minHighPh = 14.0;
    double maxLowTemp = 0.0;
    double minHighTemp = 100.0;

    Set<String> careLevels = {};
    Set<String> regions = {};

    for (var fish in fishList) {
      // 1. Parse pH range (e.g. "6.5 - 7.8")
      final phStr = fish['pH']?.toString() ?? '';
      final phMatch = RegExp(
        r'(\d+\.?\d*)\s*-\s*(\d+\.?\d*)',
      ).firstMatch(phStr);
      if (phMatch != null) {
        double low = double.tryParse(phMatch.group(1)!) ?? 6.5;
        double high = double.tryParse(phMatch.group(2)!) ?? 7.8;
        if (low > maxLowPh) maxLowPh = low;
        if (high < minHighPh) minHighPh = high;
      }

      // 2. Parse Temperature range (e.g. "24 - 28")
      final tempStr = fish['Temperature']?.toString() ?? '';
      final tempMatch = RegExp(
        r'(\d+\.?\d*)\s*-\s*(\d+\.?\d*)',
      ).firstMatch(tempStr);
      if (tempMatch != null) {
        double low = double.tryParse(tempMatch.group(1)!) ?? 22.0;
        double high = double.tryParse(tempMatch.group(2)!) ?? 28.0;
        if (low > maxLowTemp) maxLowTemp = low;
        if (high < minHighTemp) minHighTemp = high;
      }

      if (fish['Care Level'] != null) careLevels.add(fish['Care Level']);
      if (fish['Tank Region'] != null) regions.add(fish['Tank Region']);
    }

    // A clash occurs if lower required bound exceeds upper bound
    if (maxLowPh > minHighPh || maxLowTemp > minHighTemp) {
      _hasClash = true;
      _communitySummary = null;
    } else {
      _hasClash = false;
      _communitySummary = {
        'phRange':
            '${maxLowPh.toStringAsFixed(1)} – ${minHighPh.toStringAsFixed(1)}',
        'tempRange':
            '${maxLowTemp.toStringAsFixed(0)} – ${minHighTemp.toStringAsFixed(0)}°C',
        'careLevel': careLevels.join(' / '),
        'regions': regions.join(', '),
      };
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return Container(
        height: 220,
        alignment: Alignment.center,
        child: const CircularProgressIndicator(color: Colors.cyanAccent),
      );
    }

    if (_fishProfiles.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFF131B2A),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: Colors.white.withOpacity(0.08)),
        ),
        child: const Text(
          'No owned species logged in preferences.',
          style: TextStyle(color: Colors.white38, fontSize: 11),
        ),
      );
    }

    // Build Card Queue:
    // General Card is ONLY added IF there is NO clash AND there is MORE THAN 1 fish species.
    final List<Widget> cardPages = [];

    final bool showGeneralCard =
        !_hasClash && _communitySummary != null && _fishProfiles.length > 1;

    if (showGeneralCard) {
      cardPages.add(_buildGeneralCard());
    }

    for (var fish in _fishProfiles) {
      cardPages.add(_buildIndividualFishCard(fish));
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Horizontal Swipeable Viewport
        SizedBox(
          height: 235,
          child: PageView.builder(
            controller: _pageController,
            itemCount: cardPages.length,
            onPageChanged: (idx) => setState(() => _currentPage = idx),
            itemBuilder: (context, idx) => cardPages[idx],
          ),
        ),

        const SizedBox(height: 8),

        // Page Dot Indicators
        if (cardPages.length > 1)
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: List.generate(cardPages.length, (idx) {
              return AnimatedContainer(
                duration: const Duration(milliseconds: 250),
                margin: const EdgeInsets.symmetric(horizontal: 3),
                width: _currentPage == idx ? 16 : 6,
                height: 6,
                decoration: BoxDecoration(
                  color: _currentPage == idx
                      ? Colors.orangeAccent
                      : Colors.white24,
                  borderRadius: BorderRadius.circular(3),
                ),
              );
            }),
          ),
      ],
    );
  }

  /// 1. GENERAL COMMUNITY CARD (Tabular Format)
  Widget _buildGeneralCard() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.orangeAccent.withOpacity(0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Row(
                children: [
                  Icon(
                    Icons.diversity_3_outlined,
                    color: Colors.orangeAccent,
                    size: 18,
                  ),
                  SizedBox(width: 8),
                  Text(
                    'Tank Biological Target',
                    style: TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                      fontSize: 13,
                    ),
                  ),
                ],
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: kEmeraldGreen.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Text(
                  'HARMONIOUS',
                  style: TextStyle(
                    color: kEmeraldGreen,
                    fontSize: 9,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Combined parameters for all ${_fishProfiles.length} species in tank:',
            style: const TextStyle(color: Colors.white54, fontSize: 10),
          ),
          const SizedBox(height: 12),

          // TABULAR DISPLAY: General Community Metrics
          Table(
            border: TableBorder(
              horizontalInside: BorderSide(
                color: Colors.white.withOpacity(0.06),
                width: 1,
              ),
            ),
            columnWidths: const {
              0: FlexColumnWidth(1.2),
              1: FlexColumnWidth(2.0),
            },
            children: [
              _buildTableRow(
                'Target pH',
                _communitySummary!['phRange'],
                kEmeraldGreen,
              ),
              _buildTableRow(
                'Target Temp',
                _communitySummary!['tempRange'],
                Colors.cyanAccent,
              ),
              _buildTableRow(
                'Care Level',
                _communitySummary!['careLevel'],
                Colors.orangeAccent,
              ),
              _buildTableRow(
                'Occupied Zones',
                _communitySummary!['regions'],
                Colors.lightBlueAccent,
              ),
            ],
          ),

          const Spacer(),
          Align(
            alignment: Alignment.centerRight,
            child: Text(
              'Swipe left for species profiles ➔',
              style: TextStyle(
                color: Colors.white.withOpacity(0.35),
                fontSize: 9,
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 2. INDIVIDUAL FISH CARD (Tabular Characteristics & Image Request)
  Widget _buildIndividualFishCard(Map<String, dynamic> fish) {
    final title = fish['Title'] ?? fish['Common Name'] ?? 'Unknown Species';
    final imageUrl = fish['Image URL'] ?? '';
    final ph = fish['pH'] ?? 'N/A';
    final temp = fish['Temperature'] ?? 'N/A';
    final care = fish['Care Level'] ?? 'N/A';
    final size = fish['Maximum Size'] ?? 'N/A';
    final region = fish['Tank Region'] ?? 'N/A';
    final behavior = fish['Behaviour'] ?? 'N/A';

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF131B2A),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Network Image Fetching with Progress Loader and Fallback
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Container(
              width: 100,
              height: 185,
              color: Colors.black26,
              child: imageUrl.isNotEmpty
                  ? Image.network(
                      imageUrl,
                      fit: BoxFit.cover,
                      loadingBuilder: (context, child, loadingProgress) {
                        if (loadingProgress == null) return child;
                        return const Center(
                          child: SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.cyanAccent,
                            ),
                          ),
                        );
                      },
                      errorBuilder: (_, __, ___) => const Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.broken_image_outlined,
                            color: Colors.white38,
                            size: 28,
                          ),
                          SizedBox(height: 4),
                          Text(
                            'No Image',
                            style: TextStyle(
                              color: Colors.white38,
                              fontSize: 9,
                            ),
                          ),
                        ],
                      ),
                    )
                  : const Icon(
                      Icons.set_meal_outlined,
                      color: Colors.white38,
                      size: 32,
                    ),
            ),
          ),
          const SizedBox(width: 12),

          // TABULAR DISPLAY: Individual Characteristics Table
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 13,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  behavior,
                  style: const TextStyle(color: Colors.white54, fontSize: 10),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 8),

                // Table Layout for Parameters
                Table(
                  border: TableBorder(
                    horizontalInside: BorderSide(
                      color: Colors.white.withOpacity(0.05),
                      width: 1,
                    ),
                  ),
                  columnWidths: const {
                    0: FlexColumnWidth(1.0),
                    1: FlexColumnWidth(1.5),
                  },
                  children: [
                    _buildTableRow('pH Range', ph, kEmeraldGreen),
                    _buildTableRow('Temperature', temp, Colors.cyanAccent),
                    _buildTableRow('Care Level', care, Colors.orangeAccent),
                    _buildTableRow('Max Size', size, Colors.white70),
                    _buildTableRow(
                      'Tank Region',
                      region,
                      Colors.lightBlueAccent,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Helper method for creating clean rows inside Table
  TableRow _buildTableRow(String label, String value, Color valueColor) {
    return TableRow(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 5),
          child: Text(
            label,
            style: const TextStyle(color: Colors.white54, fontSize: 10),
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 5),
          child: Text(
            value,
            style: TextStyle(
              color: valueColor,
              fontSize: 10,
              fontWeight: FontWeight.w600,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}
