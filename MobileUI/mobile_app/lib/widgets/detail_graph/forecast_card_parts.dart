// lib/widgets/detail_graph/forecast_card_parts.dart
//
// The pieces the three detail-screen lookahead cards (algae, evaporation,
// water buffer) have in common: the loading / unavailable shell, the title
// row, and the urgency and status mappings. Each card keeps its own content.

import 'package:flutter/material.dart';
import 'package:mobile_app/theme/app_theme.dart';
import 'package:mobile_app/widgets/shared/pond_widgets.dart';

/// What a forecast provider yields: the forecast, or the reason it is missing.
typedef ForecastResult<T> = ({T? forecast, String? error});

/// A centred progress spinner for a card that is still loading.
class ForecastCardLoading extends StatelessWidget {
  const ForecastCardLoading({super.key, required this.color});

  final Color color;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: AppSpace.xxl),
    child: Center(
      child: SizedBox(
        width: 22,
        height: 22,
        child: CircularProgressIndicator(strokeWidth: 2.4, color: color),
      ),
    ),
  );
}

/// The loading and unavailable states around a forecast future; [builder]
/// draws the card once a forecast is there.
class ForecastCard<T> extends StatelessWidget {
  const ForecastCard({
    super.key,
    required this.future,
    required this.title,
    required this.loadingColor,
    required this.onRetry,
    required this.builder,
  });

  final Future<ForecastResult<T>> future;
  final String title;
  final Color loadingColor;
  final VoidCallback onRetry;
  final Widget Function(T forecast) builder;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<ForecastResult<T>>(
      future: future,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return OutcomeCardShell(
            child: ForecastCardLoading(color: loadingColor),
          );
        }
        final forecast = snapshot.data?.forecast;
        if (forecast == null) {
          return OutcomeCardShell(
            child: PondErrorState(
              title: title,
              message: snapshot.data?.error ?? 'Forecast unavailable',
              onRetry: onRetry,
            ),
          );
        }
        return builder(forecast);
      },
    );
  }
}

/// The card's icon and title on the left, [trailing] (a status chip) on the right.
class ForecastCardHeader extends StatelessWidget {
  const ForecastCardHeader({
    super.key,
    required this.icon,
    required this.iconColor,
    required this.title,
    required this.trailing,
  });

  final IconData icon;
  final Color iconColor;
  final String title;
  final Widget trailing;

  @override
  Widget build(BuildContext context) {
    return AdaptiveRow(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Expanded(
          child: AdaptiveRow(
            children: [
              Icon(icon, color: iconColor, size: 18),
              const SizedBox(width: AppSpace.sm),
              Expanded(
                child: Text(
                  title,
                  style: AppType.style(
                    color: AppColors.of(context).text,
                    fontWeight: FontWeight.bold,
                    fontSize: AppType.body,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
        trailing,
      ],
    );
  }
}

/// danger or warning when [color] is the palette's danger or warning colour,
/// otherwise null.
PondStatus? pondStatusForColor(AppColors colors, Color color) {
  if (color == colors.danger) return PondStatus.danger;
  if (color == colors.warning) return PondStatus.warning;
  return null;
}

/// The accent for a days-until-action figure: [planned] when it is more than
/// three days out.
Color forecastUrgencyColor(
  AppColors colors,
  int? days, {
  required Color planned,
}) {
  if (days == null) return colors.healthy;
  if (days <= 0) return colors.danger;
  if (days <= 3) return colors.warning;
  return planned;
}

/// The chip text for a days-until-action figure; [clear] is the wording for
/// "nothing predicted".
String forecastUrgencyLabel(int? days, {required String clear}) {
  if (days == null) return clear;
  if (days <= 0) return 'DUE NOW';
  if (days <= 3) return 'SOON';
  return 'PLANNED';
}
