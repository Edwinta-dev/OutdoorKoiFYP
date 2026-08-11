import 'dart:math';
import '../pond_heuristics.dart';

class PondSample {
  final DateTime time;
  final double ph;
  final double tds; // ppm
  final double tempC;
  final double lux;

  const PondSample(this.time, this.ph, this.tds, this.tempC, this.lux);
}

class BufferAssessment {
  final String status; // 'Green' | 'Amber' | 'Red' | 'Unknown'
  final String category;
  final double phReactivity; // Daily pH swing per kLUX — inverse-KH proxy
  final double reactivityTrend; // Trailing slope of reactivity
  final double tdsTrend; // Trailing slope of TDS (ppm/day)
  final double nightLowPh; // Recent low-light pH minimum
  final double estimatedDGH; // Mineral-load display value
  final int riskScore; // 0 to 10+
  final String advisory;
  final bool addHardenerNow;

  const BufferAssessment({
    required this.status,
    required this.category,
    required this.phReactivity,
    required this.reactivityTrend,
    required this.tdsTrend,
    required this.nightLowPh,
    required this.estimatedDGH,
    required this.riskScore,
    required this.advisory,
    required this.addHardenerNow,
  });
}

class PhAdvisoryResult {
  final String message;
  final AdvisorySeverity severity;
  final BufferAssessment bufferAssessment; // Always non-nullable now!

  const PhAdvisoryResult({
    required this.message,
    required this.severity,
    required this.bufferAssessment,
  });
}

class PhHelpers {
  static double calculatePhRateOfChange(List<dynamic> phTelemetry) {
    if (phTelemetry.length < 2) return 0.0;
    try {
      final List<Map<String, dynamic>> sortedList =
          phTelemetry.whereType<Map<String, dynamic>>().toList()..sort(
            (a, b) => DateTime.parse(
              a['timestamp'].toString(),
            ).compareTo(DateTime.parse(b['timestamp'].toString())),
          );

      final first = sortedList.first;
      final last = sortedList.last;
      final double firstVal = double.tryParse(first['value'].toString()) ?? 7.4;
      final double lastVal = double.tryParse(last['value'].toString()) ?? 7.4;

      final DateTime firstTime = DateTime.parse(first['timestamp'].toString());
      final DateTime lastTime = DateTime.parse(last['timestamp'].toString());

      final double hoursDiff =
          lastTime.difference(firstTime).inSeconds / 3600.0;
      if (hoursDiff <= 0) return 0.0;

      return (lastVal - firstVal) / hoursDiff;
    } catch (_) {
      return 0.0;
    }
  }

  static BufferAssessment evaluateRainVulnerabilityRefined({
    required List<PondSample> history,
    required bool rainIncoming,
    String rainIntensity = 'unknown',
  }) {
    if (history.length < 24) {
      final latestPh = history.isNotEmpty ? history.last.ph : 7.4;
      final latestTds = history.isNotEmpty ? history.last.tds : 180.0;
      return BufferAssessment(
        status: 'Unknown',
        category: 'Insufficient History',
        phReactivity: 0,
        reactivityTrend: 0,
        tdsTrend: 0,
        nightLowPh: latestPh,
        estimatedDGH: latestTds / 20.0,
        riskScore: 0,
        advisory: 'Collecting baseline data — need at least 24h of samples.',
        addHardenerNow: false,
      );
    }

    final days = _groupByDay(history);
    final List<double> dailyReactivity = [];
    final List<double> dailyTds = [];

    for (final day in days.values) {
      if (day.length < 12) continue;

      final phs = day.map((s) => s.ph).toList();
      final luxs = day.map((s) => s.lux).toList();
      final tds = day.map((s) => s.tds).toList();

      final maxLux = luxs.reduce(max);
      final avgLux = luxs.reduce((a, b) => a + b) / luxs.length;

      if (maxLux < 500.0 && dailyReactivity.isNotEmpty) {
        dailyReactivity.add(dailyReactivity.last);
        dailyTds.add(tds.reduce((a, b) => a + b) / tds.length);
        continue;
      }

      final phSwing = phs.reduce(max) - phs.reduce(min);
      final avgKLux = avgLux / 1000.0;
      final reactivity = phSwing / (avgKLux + 0.5);

      dailyReactivity.add(reactivity);
      dailyTds.add(tds.reduce((a, b) => a + b) / tds.length);
    }

    if (dailyReactivity.length < 3) {
      final latest = history.last;
      return BufferAssessment(
        status: 'Unknown',
        category: 'Insufficient History',
        phReactivity: 0,
        reactivityTrend: 0,
        tdsTrend: 0,
        nightLowPh: latest.ph,
        estimatedDGH: latest.tds / 20.0,
        riskScore: 0,
        advisory: 'Need at least 3 valid days to compute trends.',
        addHardenerNow: false,
      );
    }

    final currentReactivity = dailyReactivity.last;
    final reactivityTrend = _slope(dailyReactivity);
    final tdsTrend = _slope(dailyTds);
    final latest = history.last;
    final estimatedDGH = latest.tds / 20.0;
    final nightLowPh = _recentNightMinima(history);

    int riskScore = 0;

    if (currentReactivity > 0.40) {
      riskScore += 2;
    } else if (currentReactivity > 0.25) {
      riskScore += 1;
    }

    if (reactivityTrend > 0.05) {
      riskScore += 2;
    } else if (reactivityTrend > 0.02) {
      riskScore += 1;
    }

    if (tdsTrend < -5.0 && reactivityTrend > 0) riskScore += 1;
    if (tdsTrend > 5.0 && reactivityTrend > 0) riskScore += 1;

    if (nightLowPh < 6.8) {
      riskScore += 2;
    } else if (nightLowPh < 7.2) {
      riskScore += 1;
    }

    if (rainIncoming) {
      riskScore += (rainIntensity == 'heavy') ? 2 : 1;
    }

    String status, category, advisory;
    bool addHardener = false;

    if (riskScore >= 6) {
      status = 'Red';
      category = 'High Rain-Crash Risk';
      advisory =
          'Buffer severely depleted. High risk of rain-induced pH crash. '
          'Add Carbonate/Calcium hardener prior to rain.';
      addHardener = true;
    } else if (riskScore >= 3) {
      status = 'Amber';
      category = rainIncoming
          ? 'Moderate Risk — Rain Incoming'
          : 'Buffer Declining';
      advisory = rainIncoming
          ? 'Weakened buffer capacity with incoming rain. Preemptive buffer dosing recommended.'
          : 'Buffer capacity is declining. Monitor closely.';
      addHardener = rainIncoming;
    } else {
      status = 'Green';
      category = 'Stable Buffer';
      advisory = 'Buffering capacity is adequate for incoming weather events.';
      addHardener = false;
    }

    return BufferAssessment(
      status: status,
      category: category,
      phReactivity: currentReactivity,
      reactivityTrend: reactivityTrend,
      tdsTrend: tdsTrend,
      nightLowPh: nightLowPh,
      estimatedDGH: estimatedDGH,
      riskScore: riskScore,
      advisory: advisory,
      addHardenerNow: addHardener,
    );
  }

  /// Always returns a non-nullable PhAdvisoryResult with a valid BufferAssessment
  static PhAdvisoryResult evaluatePhAdvisory({
    required dynamic ph,
    required dynamic tds,
    required bool rainIncoming,
    required double maxTargetpH,
    required double minTargetpH,
    List<dynamic> phTelemetry = const [],
    List<PondSample> history = const [],
    String rainIntensity = 'unknown',
  }) {
    final phVal = double.tryParse(ph.toString()) ?? 7.4;
    final tdsVal = double.tryParse(tds.toString()) ?? 180.0;

    // 1. Run Rain Vulnerability Assessor (always returns a valid BufferAssessment)
    final bufferAssessment = evaluateRainVulnerabilityRefined(
      history: history,
      rainIncoming: rainIncoming,
      rainIntensity: rainIntensity,
    );

    // 2. Immediate Ground-Truth Boundary Breach -> RED
    if (phVal < minTargetpH || phVal > maxTargetpH) {
      return PhAdvisoryResult(
        message:
            'Critical pH reading (${phVal.toStringAsFixed(2)})! Perform immediate partial water change or buffer addition.',
        severity: AdvisorySeverity.red,
        bufferAssessment: bufferAssessment,
      );
    }

    // 3. Rate of Change Decay (dpH/dt) -> AMBER
    final double dpdt = calculatePhRateOfChange(phTelemetry);
    if (dpdt < -0.15) {
      return PhAdvisoryResult(
        message:
            'Rapid pH drop detected (${dpdt.toStringAsFixed(2)} pH/hr). Consider adding Calcium Hardener or KH Buffer to stabilize water hardness.',
        severity: AdvisorySeverity.amber,
        bufferAssessment: bufferAssessment,
      );
    }

    // 4. Rain Incoming + Low Buffering Capacity -> AMBER
    if (rainIncoming && (phVal < 7.2 || tdsVal < 150)) {
      return PhAdvisoryResult(
        message:
            'Risk of pH acid crash from rain! Consider adding KH buffer or Calcium hardener ($tdsVal ppm TDS).',
        severity: AdvisorySeverity.amber,
        bufferAssessment: bufferAssessment,
      );
    }

    // 5. Buffer-Driven Advisory if Amber/Red from Rain Assessor
    if (bufferAssessment.status == 'Red' ||
        bufferAssessment.status == 'Amber') {
      return PhAdvisoryResult(
        message: bufferAssessment.advisory,
        severity: bufferAssessment.status == 'Red'
            ? AdvisorySeverity.red
            : AdvisorySeverity.amber,
        bufferAssessment: bufferAssessment,
      );
    }

    // 6. Completely Safe baseline -> severity: none
    return PhAdvisoryResult(
      message: 'pH parameters and mineral buffers are stable.',
      severity: AdvisorySeverity.none,
      bufferAssessment: bufferAssessment,
    );
  }

  static Map<String, List<PondSample>> _groupByDay(List<PondSample> history) {
    final Map<String, List<PondSample>> map = {};
    for (final s in history) {
      final shiftedTime = s.time.subtract(const Duration(hours: 6));
      final key =
          '${shiftedTime.year}-${shiftedTime.month.toString().padLeft(2, '0')}-${shiftedTime.day.toString().padLeft(2, '0')}';
      map.putIfAbsent(key, () => []).add(s);
    }
    return map;
  }

  static double _slope(List<double> values) {
    final int n = values.length;
    if (n < 2) return 0.0;
    final List<double> xs = List.generate(n, (i) => i.toDouble());
    final double xMean = xs.reduce((a, b) => a + b) / n;
    final double yMean = values.reduce((a, b) => a + b) / n;
    double numerator = 0.0, denominator = 0.0;
    for (int i = 0; i < n; i++) {
      final double xDiff = xs[i] - xMean;
      numerator += xDiff * (values[i] - yMean);
      denominator += xDiff * xDiff;
    }
    return denominator == 0.0 ? 0.0 : numerator / denominator;
  }

  static double _recentNightMinima(List<PondSample> history) {
    final nightSamples = history.where((s) => s.lux < 20.0).toList();
    if (nightSamples.isEmpty) return 7.5;
    final recentNight = nightSamples.length > 24
        ? nightSamples.sublist(nightSamples.length - 24)
        : nightSamples;
    return recentNight.map((s) => s.ph).reduce(min);
  }
}
