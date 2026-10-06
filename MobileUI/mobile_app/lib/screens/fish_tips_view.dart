import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
// lib/screens/fish_tips_view.dart

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/providers.dart';

import '../utils/fish_image_helper.dart';
import '../widgets/fish/fish_carousel_header.dart';
import '../widgets/fish/fish_parameter_table.dart';

class FishTipsView extends ConsumerStatefulWidget {
  const FishTipsView({super.key});

  @override
  ConsumerState<FishTipsView> createState() => FishTipsViewState();
}

class FishTipsViewState extends ConsumerState<FishTipsView> {
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

  Future<void> refreshData() async => ref.invalidate(fishProfilesProvider);
  List<Map<String, dynamic>> get _fishProfiles =>
      ref.read(fishProfilesProvider).asData?.value ?? [];

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
    final state = ref.watch(fishProfilesProvider);
    if (state.isLoading) {
      return Center(
        child: CircularProgressIndicator(color: AppColors.of(context).info),
      );
    }

    if (state.hasError) {
      return SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpace.xl),
        child: PondErrorState(
          title: 'Fish inventory unavailable',
          message: 'Check the connection and try again.',
          onRetry: refreshData,
        ),
      );
    }
    if (_fishProfiles.isEmpty) {
      return const SingleChildScrollView(
        padding: EdgeInsets.all(AppSpace.xxxl),
        child: PondEmptyState(
          title: 'No Fish Inventory Found',
          message:
              'Complete your tank onboarding to view specific maintenance tips.',
          icon: Icons.phishing_outlined,
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
      backgroundColor: AppColors.of(context).canvas,
      body: SafeArea(
        child: SingleChildScrollView(
          physics: const BouncingScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(
            AppSpace.xl,
            AppSpace.lg,
            AppSpace.xl,
            110,
          ),
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
                  onDataRefresh: refreshData,
                ),
              ),
              const SizedBox(height: AppSpace.md),

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
                        margin: const EdgeInsets.symmetric(
                          horizontal: AppSpace.xs,
                        ),
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(AppRadius.small),
                          color: _currentCarouselIndex == entry.key
                              ? AppColors.of(context).info
                              : AppColors.of(
                                  context,
                                ).text.withValues(alpha: 0.3),
                        ),
                      ),
                    );
                  }).toList(),
                ),
                const SizedBox(height: AppSpace.xl),
              ] else
                const SizedBox(height: AppSpace.xxl),

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
