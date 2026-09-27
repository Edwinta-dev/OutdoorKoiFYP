class AlgalHelpers {
  /// Evaluates Solar Radiation & Algae Bloom Risk
  static String? evaluateSolarAdvisory({
    required dynamic lux,
    required dynamic uvIndex,
    required bool isFairForecast,
  }) {
    final luxVal = double.tryParse(lux.toString()) ?? 500;
    final uvVal = int.tryParse(uvIndex.toString()) ?? 0;

    if (luxVal > 15000 || uvVal >= 8 || isFairForecast) {
      return 'Strong solar radiation! Monitor algae bloom risk.';
    }
    return null;
  }
}
