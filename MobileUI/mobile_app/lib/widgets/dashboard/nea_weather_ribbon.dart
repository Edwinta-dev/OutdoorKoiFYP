import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';

class NeaWeatherRibbon extends StatelessWidget {
  final Map<String, dynamic> forecastData;
  final Map<String, dynamic> telemetryData;
  final VoidCallback onOpenFullForecast;

  const NeaWeatherRibbon({
    super.key,
    required this.forecastData,
    required this.telemetryData,
    required this.onOpenFullForecast,
  });

  /// Maps official Singapore NEA forecast strings directly to custom PNG assets.
  String _getWeatherAssetPath(String text) {
    final lower = text.trim().toLowerCase();
    switch (lower) {
      case 'fair':
        return 'lib/assets/fair.png';
      case 'fair (day)':
        return 'lib/assets/fair_day.png';
      case 'fair (night)':
        return 'lib/assets/fair_night.png';
      case 'fair and warm':
        return 'lib/assets/fair_and_warm.png';
      case 'partly cloudy':
        return 'lib/assets/partly_cloudy.png';
      case 'partly cloudy (day)':
        return 'lib/assets/partly_cloudy_day.png';
      case 'partly cloudy (night)':
        return 'lib/assets/partly_cloudy_night.png';
      case 'cloudy':
        return 'lib/assets/cloudy.png';
      case 'hazy':
        return 'lib/assets/hazy.png';
      case 'slightly hazy':
        return 'lib/assets/slightly_hazy.png';
      case 'windy':
        return 'lib/assets/windy.png';
      case 'mist':
        return 'lib/assets/mist.png';
      case 'fog':
        return 'lib/assets/fog.png';
      case 'light rain':
        return 'lib/assets/light_rain.png';
      case 'moderate rain':
        return 'lib/assets/moderate_rain.png';
      case 'heavy rain':
        return 'lib/assets/heavy_rain.png';
      case 'passing showers':
        return 'lib/assets/passing_showers.png';
      case 'light showers':
        return 'lib/assets/light_showers.png';
      case 'showers':
        return 'lib/assets/showers.png';
      case 'heavy showers':
        return 'lib/assets/heavy_showers.png';
      case 'thundery showers':
        return 'lib/assets/thundery_showers.png';
      case 'heavy thundery showers':
        return 'lib/assets/heavy_thundery_showers.png';
      case 'heavy thundery showers with gusty winds':
        return 'lib/assets/heavy_thundery_showers_with_gusty_winds.png';
      default:
        if (lower.contains('thunder')) {
          return 'lib/assets/thundery_showers.png';
        } else if (lower.contains('rain') || lower.contains('shower')) {
          return 'lib/assets/showers.png';
        } else if (lower.contains('cloud')) {
          return 'lib/assets/cloudy.png';
        }
        return 'lib/assets/fair_day.png';
    }
  }

  @override
  Widget build(BuildContext context) {
    // 1. Extract 2-hour forecast & UV
    final String forecast2hr =
        forecastData['forecast_2hr']?['forecast']?.toString() ?? 'Fair (Day)';
    final int uvVal =
        int.tryParse(
          forecastData['uv_index']?['data']?['uv']?.toString() ?? '0',
        ) ??
        0;

    // 2. Extract live station telemetry
    final double airTemp =
        double.tryParse(
          telemetryData['air_temp']?['value']?.toString() ?? '',
        ) ??
        30.0;
    final double rainfall =
        double.tryParse(
          telemetryData['rainfall']?['value']?.toString() ?? '',
        ) ??
        0.0;
    // Safely parse UV to an integer before building the layout
    final int uvInt = int.tryParse(uvVal.toString()) ?? 0;

    return GestureDetector(
      onTap: onOpenFullForecast,
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpace.lg,
          vertical: AppSpace.md,
        ),
        decoration: BoxDecoration(
          color: AppColors.of(context).surfaceRaised,
          borderRadius: BorderRadius.circular(AppRadius.card),
          border: Border.all(
            color: AppColors.of(context).text.withValues(alpha: 0.1),
            width: 1,
          ),
        ),
        child: AdaptiveRow(
          minWidth: 300,
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            // 1. Weather Icon
            Image.asset(
              _getWeatherAssetPath(forecast2hr),
              width: 24,
              height: 24,
              fit: BoxFit.contain,
              errorBuilder: (_, _, _) => Icon(
                Icons.cloud_queue,
                color: AppColors.of(context).info,
                size: 20,
              ),
            ),
            const SizedBox(width: AppSpace.sm),

            // 2. Sky Condition (Flexible prevents overflow on long NEA strings)
            Flexible(
              child: Text(
                forecast2hr,
                style: AppType.style(
                  color: AppColors.of(context).text,
                  fontSize: AppType.body,
                  fontWeight: FontWeight.bold,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const SizedBox(width: AppSpace.sm),

            // 3. Air Temperature
            Text(
              '•  ${airTemp.toStringAsFixed(1)}°C',
              style: AppType.style(
                color: AppColors.of(context).text,
                fontSize: AppType.label,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(width: AppSpace.sm),

            // 4. UV Index Badge (Safe integer comparison)
            _buildMiniBadge(
              context,
              '•  UV',
              uvVal.toString(),
              color: uvInt > 3
                  ? AppColors.of(context).warning
                  : AppColors.of(context).textSecondary,
            ),
            const SizedBox(width: AppSpace.sm),

            // 5. Rainfall Metric
            _buildMiniStat(
              context,
              Icons.water_drop,
              '${rainfall.toStringAsFixed(1)} mm',
              color: rainfall > 0
                  ? AppColors.of(context).info
                  : AppColors.of(context).textSecondary,
            ),
            const SizedBox(width: AppSpace.sm),

            // 6. Navigation Chevron
            Icon(
              Icons.arrow_forward_ios,
              color: AppColors.of(context).textMuted,
              size: 12,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMiniBadge(
    BuildContext context,
    String label,
    String value, {
    Color? color,
  }) {
    return Row(
      children: [
        Text(
          '$label ',
          style: AppType.style(
            color: AppColors.of(context).textMuted,
            fontSize: AppType.caption,
          ),
        ),
        Text(
          value,
          style: AppType.style(
            color: color,
            fontSize: AppType.caption,
            fontWeight: FontWeight.bold,
          ),
        ),
      ],
    );
  }

  Widget _buildMiniStat(
    BuildContext context,
    IconData icon,
    String text, {
    Color? color,
  }) {
    return Row(
      children: [
        Icon(icon, color: AppColors.of(context).textMuted, size: 13),
        const SizedBox(width: AppSpace.xs),
        Text(
          text,
          style: AppType.style(
            color: color,
            fontSize: AppType.caption,
            fontWeight: FontWeight.w500,
          ),
        ),
      ],
    );
  }
}
