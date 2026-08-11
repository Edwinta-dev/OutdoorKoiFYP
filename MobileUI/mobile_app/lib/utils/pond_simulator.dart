// lib/utils/unified_pond_simulator.dart

class UnifiedPondSimulator {
  /// 1. Calculates daily TAN (Total Ammonia Nitrogen) in mg
  static double calculateDailyAmmoniaMg({
    required double foodGrams,
    required double proteinPercentage, // e.g. 45.0 for 45%
    double excretionFraction = 0.70,
  }) {
    return foodGrams *
        (proteinPercentage / 100.0) *
        0.16 *
        excretionFraction *
        1000.0;
  }

  /// 2. Calculates daily Nitrate (NO3) generated in mg and ppm
  static Map<String, double> calculateDailyNitrate({
    required double foodGrams,
    required double proteinPercentage,
    required double tankVolumeLitres,
  }) {
    final double tanMg = calculateDailyAmmoniaMg(
      foodGrams: foodGrams,
      proteinPercentage: proteinPercentage,
    );

    // 1 mg TAN oxidizes to 4.43 mg NO3
    final double nitrateMg = tanMg * 4.43;
    final double dailyPpmRise = nitrateMg / tankVolumeLitres;

    return {'nitrateMg': nitrateMg, 'dailyPpmRise': dailyPpmRise};
  }

  /// 3. Back-solves required Water Change % to restore target quality
  static double calculateRequiredWaterChangePct({
    required double currentValue,
    required double targetValue,
    required double tapValue,
  }) {
    if (currentValue <= targetValue) return 0.0; // Already within target
    if (currentValue <= tapValue)
      return 100.0; // Tap water isn't cleaner than tank

    final double requiredPct =
        ((currentValue - targetValue) / (currentValue - tapValue)) * 100.0;
    return requiredPct.clamp(0.0, 100.0);
  }

  /// 4. Simulates N days of parameter drift & calculates required restoration flush
  static Map<String, dynamic> simulatePondDecayAndRestoration({
    required double tankVolumeLitres,
    required double initialNitratePpm,
    required double tapNitratePpm,
    required double foodGramsPerDay,
    required double proteinPct,
    required double targetNitrateLimitPpm,
    int daysToSimulate = 14,
  }) {
    final nitrateData = calculateDailyNitrate(
      foodGrams: foodGramsPerDay,
      proteinPercentage: proteinPct,
      tankVolumeLitres: tankVolumeLitres,
    );

    final double dailyPpm = nitrateData['dailyPpmRise']!;
    final double projectedNitratePpm =
        initialNitratePpm + (dailyPpm * daysToSimulate);

    final double requiredFlushPct = calculateRequiredWaterChangePct(
      currentValue: projectedNitratePpm,
      targetValue: targetNitrateLimitPpm,
      tapValue: tapNitratePpm,
    );

    final double flushVolumeLitres =
        tankVolumeLitres * (requiredFlushPct / 100.0);

    return {
      'dailyNitratePpmRise': dailyPpm,
      'projectedNitratePpm': projectedNitratePpm,
      'breachesThreshold': projectedNitratePpm > targetNitrateLimitPpm,
      'requiredFlushPercentage': requiredFlushPct,
      'flushVolumeLitres': flushVolumeLitres,
    };
  }
}
