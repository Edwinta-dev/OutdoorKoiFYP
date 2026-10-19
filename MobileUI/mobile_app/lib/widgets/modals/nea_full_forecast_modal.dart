import 'package:mobile_app/widgets/shared/pond_widgets.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:mobile_app/utils/pond_dashboard.dart';

String _value(double? value, String fallback) =>
    value?.toString().replaceAll(RegExp(r'\.0$'), '') ?? fallback;

String _validPeriod(ForecastPeriod period) {
  final from = period.validFrom;
  final to = period.validTo;
  if (from == null || to == null) return 'Next 24 Hours';
  return '${from.day}/${from.month} ${from.hour.toString().padLeft(2, '0')}:00'
      ' – ${to.day}/${to.month} ${to.hour.toString().padLeft(2, '0')}:00';
}

void showNeaFullForecastModal(
  BuildContext context,
  DashboardForecast forecast,
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
    builder: (ctx) => _NeaForecastSheetContent(forecast: forecast),
  );
}

class _NeaForecastSheetContent extends StatelessWidget {
  final DashboardForecast forecast;

  const _NeaForecastSheetContent({required this.forecast});

  @override
  Widget build(BuildContext context) {
    final general24 = forecast.twentyFourHourGeneral;
    final summary24 = forecast.twentyFourHourRegional.text ?? 'Fair (Day)';
    final tempLow24 = _value(general24.temperatureLowC, '26');
    final tempHigh24 = _value(general24.temperatureHighC, '34');
    final rhLow24 = _value(general24.humidityLowPct, '50');
    final rhHigh24 = _value(general24.humidityHighPct, '85');
    final windLow24 = _value(general24.windLowKmh, '10');
    final windHigh24 = _value(general24.windHighKmh, '20');
    final validPeriodText = _validPeriod(general24.period);

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
                    'Wind: $windLow24–$windHigh24 km/h',
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

              if (forecast.outlook.isEmpty)
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
                ...forecast.outlook.map((day) {
                  final dayName = day.weekday ?? '--';
                  final text = day.text ?? 'Fair';
                  final tLow = _value(day.temperatureLowC, '--');
                  final tHigh = _value(day.temperatureHighC, '--');
                  final hLow = _value(day.humidityLowPct, '60');
                  final hHigh = _value(day.humidityHighPct, '95');
                  final wLow = _value(day.windLowKmh, '10');
                  final wHigh = _value(day.windHighKmh, '20');

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
                                '$wLow-$wHigh km/h',
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
