import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';

void showNeaFullForecastModal(
  BuildContext context,
  Map<String, dynamic> forecastData,
) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppColors.of(context).surfaceInset,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(
        top: Radius.circular(AppRadius.sheet),
      ),
    ),
    builder: (ctx) => _NeaForecastSheetContent(forecastData: forecastData),
  );
}

class _NeaForecastSheetContent extends StatelessWidget {
  final Map<String, dynamic> forecastData;

  const _NeaForecastSheetContent({required this.forecastData});

  @override
  Widget build(BuildContext context) {
    // 1. Extract 24-Hour General Forecast
    final Map general24 = forecastData['forecast_24hr']?['general'] is Map
        ? forecastData['forecast_24hr']['general']
        : {};
    final String summary24 =
        general24['forecast']?['text']?.toString() ?? 'Fair (Day)';
    final String tempLow24 =
        general24['temperature']?['low']?.toString() ?? '26';
    final String tempHigh24 =
        general24['temperature']?['high']?.toString() ?? '34';
    final String rhLow24 =
        general24['relativeHumidity']?['low']?.toString() ?? '50';
    final String rhHigh24 =
        general24['relativeHumidity']?['high']?.toString() ?? '85';
    final String windDir24 =
        general24['wind']?['direction']?.toString() ?? 'SSE';
    final String windLow24 =
        general24['wind']?['speed']?['low']?.toString() ?? '10';
    final String windHigh24 =
        general24['wind']?['speed']?['high']?.toString() ?? '20';
    final String validPeriodText =
        general24['validPeriod']?['text']?.toString() ?? 'Next 24 Hours';

    // 2. Extract 4-Day Outlook List
    final List outlook4Day = forecastData['outlook_4day'] is List
        ? forecastData['outlook_4day']
        : [];

    return SafeArea(
      child: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpace.xxl,
            AppSpace.lg,
            AppSpace.xxl,
            AppSpace.xxl,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Handle Bar
              Center(
                child: Container(
                  width: 36,
                  height: 4,
                  decoration: BoxDecoration(
                    color: AppColors.of(context).outline,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: AppSpace.xl),

              // --- HEADER ---
              Text(
                'Singapore Environmental Outlook',
                style: AppType.style(
                  color: AppColors.of(context).text,
                  fontSize: AppType.title,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: AppSpace.xs),
              Text(
                validPeriodText,
                style: AppType.style(
                  color: AppColors.of(context).textMuted,
                  fontSize: AppType.label,
                ),
              ),
              const SizedBox(height: AppSpace.lg),

              // --- SECTION 1: 24-HOUR MACRO BASELINE (No Card Container) ---
              AdaptiveRow(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Expanded(
                    child: Text(
                      summary24,
                      style: AppType.style(
                        color: AppColors.of(context).info,
                        fontSize: AppType.heading,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  Text(
                    '$tempLow24–$tempHigh24°C',
                    style: AppType.style(
                      color: AppColors.of(context).text,
                      fontSize: AppType.title,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpace.md),

              // 24-Hour Parameter Row
              AdaptiveRow(
                children: [
                  _buildParamTag(
                    context,
                    Icons.water_drop_outlined,
                    'Humidity: $rhLow24–$rhHigh24%',
                  ),
                  const SizedBox(width: AppSpace.lg),
                  _buildParamTag(
                    context,
                    Icons.air,
                    'Wind: $windDir24 ($windLow24–$windHigh24 km/h)',
                  ),
                ],
              ),
              const SizedBox(height: AppSpace.xl),
              Divider(color: AppColors.of(context).outline, height: 1),
              const SizedBox(height: AppSpace.xl),

              // --- SECTION 2: 4-DAY EXTENDED OUTLOOK (Clean Table Stream) ---
              Text(
                '4-DAY FORECAST BREAKDOWN',
                style: AppType.style(
                  color: AppColors.of(context).textMuted,
                  fontSize: AppType.caption,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 0.8,
                ),
              ),
              const SizedBox(height: AppSpace.md),

              if (outlook4Day.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: AppSpace.lg),
                  child: Text(
                    'No 4-day outlook available.',
                    style: AppType.style(
                      color: AppColors.of(context).textMuted,
                      fontSize: AppType.body,
                    ),
                  ),
                )
              else
                ...outlook4Day.map((item) {
                  final itemData = item['data'] is Map ? item['data'] : {};
                  final String dayName =
                      itemData['day']?.toString() ??
                      item['slot_id']?.toString() ??
                      '--';
                  final String text =
                      itemData['forecast']?['text']?.toString() ?? 'Fair';
                  final String tLow =
                      itemData['temperature']?['low']?.toString() ?? '--';
                  final String tHigh =
                      itemData['temperature']?['high']?.toString() ?? '--';
                  final String hLow =
                      itemData['relativeHumidity']?['low']?.toString() ?? '60';
                  final String hHigh =
                      itemData['relativeHumidity']?['high']?.toString() ?? '95';
                  final String wDir =
                      itemData['wind']?['direction']?.toString() ?? '';
                  final String wLow =
                      itemData['wind']?['speed']?['low']?.toString() ?? '10';
                  final String wHigh =
                      itemData['wind']?['speed']?['high']?.toString() ?? '20';

                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: AppSpace.md),
                    child: AdaptiveRow(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        // Column 1: Day Name & Wind
                        SizedBox(
                          width: 96,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                dayName.toUpperCase(),
                                style: AppType.style(
                                  color: AppColors.of(context).text,
                                  fontWeight: FontWeight.bold,
                                  fontSize: AppType.body,
                                ),
                              ),
                              const SizedBox(height: AppSpace.xxs),
                              Text(
                                wDir.isNotEmpty
                                    ? '$wDir • $wLow-$wHigh km/h'
                                    : '$wLow-$wHigh km/h',
                                style: AppType.style(
                                  color: AppColors.of(context).textMuted,
                                  fontSize: AppType.micro,
                                ),
                              ),
                            ],
                          ),
                        ),

                        // Column 2: Forecast Summary Text
                        Expanded(
                          child: Text(
                            text,
                            style: AppType.style(
                              color: AppColors.of(context).textSecondary,
                              fontSize: AppType.body,
                            ),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),

                        // Column 3: Temp & Humidity Range
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            Text(
                              '$tLow–$tHigh°C',
                              style: AppType.style(
                                color: AppColors.of(context).info,
                                fontWeight: FontWeight.w600,
                                fontSize: AppType.body,
                              ),
                            ),
                            const SizedBox(height: AppSpace.xxs),
                            Text(
                              'RH $hLow–$hHigh%',
                              style: AppType.style(
                                color: AppColors.of(context).textMuted,
                                fontSize: AppType.micro,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  );
                }),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildParamTag(BuildContext context, IconData icon, String label) {
    return AdaptiveRow(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: AppColors.of(context).textMuted, size: 14),
        const SizedBox(width: AppSpace.sm),
        Text(
          label,
          style: AppType.style(
            color: AppColors.of(context).textSecondary,
            fontSize: AppType.label,
          ),
        ),
      ],
    );
  }
}
