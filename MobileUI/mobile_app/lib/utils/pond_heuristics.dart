// lib/utils/pond_heuristics.dart

class PondHeuristics {
  /// Rainfall keywords triggering pH Acid Crash advisories
  static const List<String> rainKeywords = [
    'rain',
    'shower',
    'thundery',
    'gusty',
  ];

  /// Fair sky keywords triggering Algae / High Radiation advisories
  static const List<String> fairKeywords = ['fair', 'clear', 'sunny', 'warm'];

  /// Checks if a string contains any rain forecast keywords
  static bool isRainForecast(String forecast) {
    final lower = forecast.toLowerCase();
    return rainKeywords.any((keyword) => lower.contains(keyword));
  }

  /// Checks if a string contains any fair sky forecast keywords
  static bool isFairForecast(String forecast) {
    final lower = forecast.toLowerCase();
    return fairKeywords.any((keyword) => lower.contains(keyword));
  }

  /// Evaluates Temperature & Metabolic Risk
  static String? getTemperatureAdvisory({
    required dynamic waterTemp,
    required dynamic airTemp,
    required dynamic windSpeed,
  }) {
    final wTemp = double.tryParse(waterTemp.toString()) ?? 26.0;
    final aTemp = double.tryParse(airTemp.toString()) ?? 30.0;

    if (wTemp >= 30.0 || aTemp >= 33.0) {
      return 'High temperatures! Consider reducing feed quantity and providing shade.';
    } else if (wTemp < 20.0) {
      return 'Low water temperature! Feed sparingly.';
    }
    return null; // Normal conditions
  }

  /// Evaluates pH & Acid Crash Risk
  static String? getPhAdvisory({
    required dynamic ph,
    required dynamic tds,
    required String forecast2hr,
    required String forecast24hr,
  }) {
    final phVal = double.tryParse(ph.toString()) ?? 7.2;
    final tdsVal = double.tryParse(tds.toString()) ?? 180;
    final rainIncoming = isRainForecast(forecast2hr);

    if (rainIncoming && (phVal < 7.2 || tdsVal < 150)) {
      return 'Risk of pH acid crash from rain! Consider adding KH buffer or Calcium hardener.';
    } else if (phVal < 6.8) {
      return 'Critically acidic water! Immediate partial water change or buffer addition required.';
    }
    return null;
  }

  /// Evaluates Solar Radiation & Algae Bloom Risk
  static String? getSolarAdvisory({
    required dynamic lux,
    required dynamic uvIndex,
    required String forecast2hr,
  }) {
    final luxVal = double.tryParse(lux.toString()) ?? 500;
    final uvVal = int.tryParse(uvIndex.toString()) ?? 0;
    final isFair = isFairForecast(forecast2hr);

    if (luxVal > 15000 || uvVal >= 8 || isFair) {
      return 'Strong solar radiation! Monitor algae bloom risk.';
    }
    return null;
  }
}
