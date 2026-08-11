import 'package:flutter/material.dart';

void showNeaFullForecastModal(
  BuildContext context,
  Map<String, dynamic> forecastData,
) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: const Color(0xFF0A0E17),
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
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
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 16, 24, 24),
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
                  color: Colors.white24,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 20),

            // --- HEADER ---
            const Text(
              'Singapore Environmental Outlook',
              style: TextStyle(
                color: Colors.white,
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              validPeriodText,
              style: const TextStyle(color: Colors.white38, fontSize: 12),
            ),
            const SizedBox(height: 16),

            // --- SECTION 1: 24-HOUR MACRO BASELINE (No Card Container) ---
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Expanded(
                  child: Text(
                    summary24,
                    style: const TextStyle(
                      color: Colors.cyanAccent,
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                Text(
                  '$tempLow24–$tempHigh24°C',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),

            // 24-Hour Parameter Row
            Row(
              children: [
                _buildParamTag(
                  Icons.water_drop_outlined,
                  'Humidity: $rhLow24–$rhHigh24%',
                ),
                const SizedBox(width: 16),
                _buildParamTag(
                  Icons.air,
                  'Wind: $windDir24 ($windLow24–$windHigh24 km/h)',
                ),
              ],
            ),
            const SizedBox(height: 20),
            const Divider(color: Colors.white12, height: 1),
            const SizedBox(height: 20),

            // --- SECTION 2: 4-DAY EXTENDED OUTLOOK (Clean Table Stream) ---
            const Text(
              '4-DAY FORECAST BREAKDOWN',
              style: TextStyle(
                color: Colors.white54,
                fontSize: 11,
                fontWeight: FontWeight.bold,
                letterSpacing: 0.8,
              ),
            ),
            const SizedBox(height: 12),

            if (outlook4Day.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Text(
                  'No 4-day outlook available.',
                  style: TextStyle(color: Colors.white38, fontSize: 13),
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
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Row(
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
                              style: const TextStyle(
                                color: Colors.white,
                                fontWeight: FontWeight.bold,
                                fontSize: 13,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              wDir.isNotEmpty
                                  ? '$wDir • $wLow-$wHigh km/h'
                                  : '$wLow-$wHigh km/h',
                              style: const TextStyle(
                                color: Colors.white38,
                                fontSize: 10,
                              ),
                            ),
                          ],
                        ),
                      ),

                      // Column 2: Forecast Summary Text
                      Expanded(
                        child: Text(
                          text,
                          style: const TextStyle(
                            color: Colors.white70,
                            fontSize: 13,
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
                            style: const TextStyle(
                              color: Colors.cyanAccent,
                              fontWeight: FontWeight.w600,
                              fontSize: 13,
                            ),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            'RH $hLow–$hHigh%',
                            style: const TextStyle(
                              color: Colors.white38,
                              fontSize: 10,
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
    );
  }

  Widget _buildParamTag(IconData icon, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: Colors.white54, size: 14),
        const SizedBox(width: 6),
        Text(
          label,
          style: const TextStyle(color: Colors.white70, fontSize: 12),
        ),
      ],
    );
  }
}
