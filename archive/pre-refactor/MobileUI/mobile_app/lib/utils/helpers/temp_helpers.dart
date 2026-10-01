import '../pond_heuristics.dart';

class TemperatureAdvisoryResult {
  final String message;
  final AdvisorySeverity severity;
  final bool isForecastDriven;

  const TemperatureAdvisoryResult({
    required this.message,
    required this.severity,
    required this.isForecastDriven,
  });
}

class TempHelpers {
  /// Evaluates Temperature Risk (Ground-Truth IoT Water Temp vs Predictive Air Forecast)
  static TemperatureAdvisoryResult? evaluateTemperatureAdvisory({
    required dynamic waterTemp,
    required dynamic airTemp,
    required dynamic windSpeed,
    required double targetMinTemp,
    required double targetMaxTemp,
  }) {
    final wTemp = double.tryParse(waterTemp.toString()) ?? 26.0;
    final aTemp = double.tryParse(airTemp.toString()) ?? 30.0;

    // A. Ground Truth Critical Breach -> RED
    if (wTemp > targetMaxTemp || wTemp < targetMinTemp) {
      return const TemperatureAdvisoryResult(
        message: 'Control food quantity and aeration!',
        severity: AdvisorySeverity.red,
        isForecastDriven: false,
      );
    }

    // B. Ground Truth Approaching Bounds -> AMBER
    final double midPoint = (targetMinTemp + targetMaxTemp) / 2.0;
    final double halfRange = (targetMaxTemp - targetMinTemp) / 2.0;
    final double deviation = (wTemp - midPoint).abs();

    if (deviation > halfRange * 0.7) {
      return const TemperatureAdvisoryResult(
        message:
            'Water temperature approaching harmful levels. Consider providing shade or adjusting feed.',
        severity: AdvisorySeverity.amber,
        isForecastDriven: false,
      );
    }

    // C. Predictive Forecast (NEA Air Temp) -> AMBER ONLY (Never Red)
    if (aTemp >= 31.5) {
      return TemperatureAdvisoryResult(
        message:
            'Forecasted air temp is high (${aTemp.toStringAsFixed(1)}°C). Consider adjusting feed.',
        severity: AdvisorySeverity.amber,
        isForecastDriven: true,
      );
    }

    return null; // Safe conditions
  }
}
