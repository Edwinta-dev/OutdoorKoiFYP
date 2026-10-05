import 'rating_card_data.dart';
import 'telemetry_history.dart';
import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../utils/pond_dashboard.dart';
import 'local_profile_repository.dart';
import 'pond_data_source.dart';
import 'repositories.dart';

final pondDataSourceProvider = Provider<PondDataSource>(
  (ref) => LivePondDataSource(),
);
final localProfileRepositoryProvider = Provider<LocalProfileRepository>(
  (ref) => PreferencesProfileRepository(),
);
final localProfileProvider = FutureProvider(
  (ref) => ref.watch(localProfileRepositoryProvider).load(),
);

final pondProfileRepositoryProvider = Provider<PondProfileRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);
final dashboardRepositoryProvider = Provider<DashboardRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);
final telemetryHistoryRepositoryProvider = Provider<TelemetryHistoryRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);
final assessmentsRepositoryProvider = Provider<AssessmentsRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);
final forecastsRepositoryProvider = Provider<ForecastsRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);
final eventsRepositoryProvider = Provider<EventsRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);
final cameraFramesRepositoryProvider = Provider<CameraFramesRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);
final ratingsRepositoryProvider = Provider<RatingsRepository>(
  (ref) => ref.watch(pondDataSourceProvider),
);

final pondProfileProvider = FutureProvider.family(
  (ref, int pond) =>
      ref.watch(pondProfileRepositoryProvider).fetchPondProfile(pond),
);

final speciesProvider = FutureProvider(
  (ref) => ref.watch(pondProfileRepositoryProvider).fetchSpeciesNames(),
);
final fishProfilesProvider = FutureProvider((ref) async {
  final profile = await ref.watch(localProfileProvider.future);
  return ref
      .watch(pondProfileRepositoryProvider)
      .fetchFishProfiles(profile.pondId ?? 0, profile.species);
});
final historyProvider =
    FutureProvider.family<TelemetryHistory?, ({int pond, int days})>((
      ref,
      query,
    ) async {
      final json = await ref
          .watch(telemetryHistoryRepositoryProvider)
          .fetchHistoricalGraphPayload(query.pond, query.days);
      return json == null ? null : TelemetryHistory.fromJson(json);
    });
final assessmentProvider = FutureProvider.family(
  (ref, int pond) =>
      ref.watch(assessmentsRepositoryProvider).fetchLatestAssessment(pond),
);
final evaporationForecastProvider = FutureProvider.family(
  (ref, int pond) => ref
      .watch(forecastsRepositoryProvider)
      .fetchEvaporationForecastOrError(pond),
);
final algaeForecastProvider = FutureProvider.family(
  (ref, int pond) =>
      ref.watch(forecastsRepositoryProvider).fetchAlgaeForecastOrError(pond),
);
final cameraFrameProvider = FutureProvider.family(
  (ref, int pond) =>
      ref.watch(cameraFramesRepositoryProvider).fetchLatestFrame(pond),
);
final ratingContextProvider = FutureProvider.family(
  (ref, int pond) =>
      ref.watch(ratingsRepositoryProvider).fetchAlgaeRatingContext(pond),
);

/// The dashboard shares one cached API request. The card presentation adapter
/// lets the established UI consume the new typed response without extra reads.
final pondDashboardProvider = FutureProvider<PondDashboard?>((ref) async {
  final timer = Timer.periodic(
    const Duration(seconds: 30),
    (_) => ref.invalidateSelf(),
  );
  ref.onDispose(timer.cancel);
  final profile = await ref.watch(localProfileProvider.future);
  final json = await ref
      .watch(dashboardRepositoryProvider)
      .fetchDashboardPayload('${profile.pondId ?? 0}');
  return json == null ? null : PondDashboard.fromJson(json);
});

final dashboardProvider = FutureProvider<Map<String, dynamic>?>((ref) async {
  final d = await ref.watch(pondDashboardProvider.future);
  if (d == null) return null;
  return {
    'raw_sensor': {
      'pH': d.readings.ph.value,
      'TDS': d.readings.tds.value,
      'temp': d.readings.waterTemp.value,
      'LUX': d.readings.lux.value,
    },
    'nea_telemetry': {
      'air_temp': {'value': d.weather.airTemperature.value},
      'rainfall': {'value': d.weather.rainfall.value},
      'wind_speed': {'value': d.weather.windSpeed.value},
    },
    'nea_forecasts': {
      'forecast_2hr': {'forecast': d.forecast.twoHour.text},
      'forecast_24hr': {
        'general': {
          'forecast': d.forecast.twentyFourHourRegional.text,
          'temperature': {
            'low': d.forecast.twentyFourHourGeneral.temperatureLowC,
            'high': d.forecast.twentyFourHourGeneral.temperatureHighC,
          },
        },
      },
      'outlook_4day': {
        'forecasts': [
          for (final day in d.forecast.outlook)
            {
              'date': day.date?.toIso8601String(),
              'day': day.weekday,
              'forecast': day.text,
              'temperature': {
                'low': day.temperatureLowC,
                'high': day.temperatureHighC,
              },
            },
        ],
      },
      'uv_index': {
        'data': {'uv': d.weather.uvIndex.value},
      },
    },
    'telemetry_history': const <Map<String, dynamic>>[],
    'chemistry_assessment': d.assessments.chemistry,
  };
});

final ratingCardProvider = FutureProvider.family((ref, int pond) async {
  final frameFuture = ref.watch(cameraFrameProvider(pond).future);
  final contextFuture = ref.watch(ratingContextProvider(pond).future);
  return RatingCardData(
    frame: (await frameFuture).frame,
    frameError: (await frameFuture).error,
    context: await contextFuture,
  );
});
