import 'helpers/ph_helpers.dart';
import 'helpers/temp_helpers.dart';
import 'helpers/algal_helpers.dart';

// Re-export classes so UI cards only ever need to import pond_heuristics.dart
export 'helpers/ph_helpers.dart'
    show PondSample, BufferAssessment, PhAdvisoryResult;
export 'helpers/temp_helpers.dart' show TemperatureAdvisoryResult;

/// Severities for dynamic visual styling (e.g. Card Theme / Advisory Banners)
enum AdvisorySeverity {
  none,
  amber, // Moderate risk, approaching bounds, or forecast prediction
  red, // Critical breach confirmed by IoT ground-truth sensors
}

class PondHeuristics {
  // ==========================================
  // --- 1. NEA OFFICIAL KEYWORD DICTIONARIES ---
  // ==========================================

  /// Keywords indicating high-volume / high-impact precipitation
  static const List<String> heavyRainKeywords = [
    'heavy thundery showers with gusty winds',
    'heavy thundery showers',
    'heavy showers',
    'heavy rain',
    'thundery showers',
    'thundery',
    'gusty',
  ];

  /// Keywords indicating light-to-moderate precipitation
  static const List<String> moderateRainKeywords = [
    'moderate rain',
    'passing showers',
    'light showers',
    'showers',
    'light rain',
    'rain',
    'drizzle',
  ];

  /// Keywords indicating fair, warm, or clear sky conditions
  static const List<String> hotKeywords = [
    'fair and warm',
    'clear',
    'sunny',
    'warm',
  ];

  /// Keywords indicating cloudy, overcast, or low-visibility conditions
  static const List<String> cloudyKeywords = [
    'partly cloudy (day)',
    'partly cloudy (night)',
    'partly cloudy',
    'cloudy',
    'slightly hazy',
    'hazy',
    'windy',
    'mist',
    'fog',
  ];

  // ==========================================
  // --- 2. GENERAL WEATHER & FUSION HELPERS ---
  // ==========================================

  static bool isRainForecast(String forecast) {
    final lower = forecast.toLowerCase();
    return heavyRainKeywords.any((k) => lower.contains(k)) ||
        moderateRainKeywords.any((k) => lower.contains(k));
  }

  static bool isFairForecast(String forecast) {
    final lower = forecast.toLowerCase();
    return hotKeywords.any((k) => lower.contains(k));
  }

  static bool _isRainIncoming(String forecast2hr, dynamic rainfallMm) {
    final double rainAmount = double.tryParse(rainfallMm.toString()) ?? 0.0;
    return isRainForecast(forecast2hr) || rainAmount > 0.0;
  }

  /// Fuses NEA 2-Hour text forecast with live IoT rainfall telemetry (mm)
  /// to determine rain intensity ('heavy', 'moderate', 'light', or 'unknown').
  static String getRainIntensity(String forecast2hr, dynamic rainfallMm) {
    final double rainAmount = double.tryParse(rainfallMm.toString()) ?? 0.0;
    final lower = forecast2hr.toLowerCase();

    // 1. Hardware Ground-Truth Override (Rainfall mm/hr exceeds heavy threshold)
    if (rainAmount >= 10.0) {
      return 'heavy';
    }

    // 2. High-Specificity NEA Text Match (Checked before moderate keywords)
    if (heavyRainKeywords.any((k) => lower.contains(k))) {
      return 'heavy';
    }

    // 3. Moderate Hardware Telemetry OR Moderate NEA Text Match
    if (rainAmount >= 2.5 ||
        moderateRainKeywords.any((k) => lower.contains(k))) {
      return 'moderate';
    }

    // 4. Trace precipitation detected by hardware
    if (rainAmount > 0.0) {
      return 'light';
    }

    return 'unknown';
  }

  // ==========================================
  // --- 2. TELEMETRY MATH & ANALYSIS ---
  // ==========================================

  /// Calculates rate of change dpH/dt over a telemetry window.
  /// Returns derivative rate of change in pH units per hour.
  static double calculatePhRateOfChange(List<dynamic> phTelemetry) {
    return PhHelpers.calculatePhRateOfChange(phTelemetry);
  }

  // ==========================================
  // --- 3. METRIC ADVISORY EVALUATORS ---
  // ==========================================

  /// Evaluates Temperature Risk (Ground-Truth IoT Water Temp vs Predictive Air Forecast)
  static TemperatureAdvisoryResult? getTemperatureAdvisory({
    required dynamic waterTemp,
    required dynamic airTemp,
    required dynamic windSpeed,
    required double targetMinTemp,
    required double targetMaxTemp,
  }) {
    return TempHelpers.evaluateTemperatureAdvisory(
      waterTemp: waterTemp,
      airTemp: airTemp,
      windSpeed: windSpeed,
      targetMinTemp: targetMinTemp,
      targetMaxTemp: targetMaxTemp,
    );
  }

  /// Evaluates pH, Buffer Stability, dpH/dt Decay & Acid Crash Risk
  static PhAdvisoryResult getPhAdvisory({
    required dynamic ph,
    required dynamic tds,
    required String forecast2hr,
    dynamic rainfallMm = 0.0,
    List<dynamic> phTelemetry = const [],
    List<PondSample> history = const [],
  }) {
    final bool rainIncoming = _isRainIncoming(forecast2hr, rainfallMm);
    final String intensity = getRainIntensity(forecast2hr, rainfallMm);

    return PhHelpers.evaluatePhAdvisory(
      ph: ph,
      tds: tds,
      rainIncoming: rainIncoming,
      phTelemetry: phTelemetry,
      history: history,
      maxTargetpH: 8.5,
      minTargetpH: 6.8,
      rainIntensity: intensity,
    );
  }

  /// Evaluates Solar Radiation & Algae Bloom Risk
  static String? getSolarAdvisory({
    required dynamic lux,
    required dynamic uvIndex,
    required String forecast2hr,
  }) {
    return AlgalHelpers.evaluateSolarAdvisory(
      lux: lux,
      uvIndex: uvIndex,
      isFairForecast: isFairForecast(forecast2hr),
    );
  }
}
