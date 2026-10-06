// Synthetic payloads in the shapes the app receives: the two Supabase
// RPCs and the DigitalTwin endpoints. Built through each model's
// fromJson so the tests exercise the same parsing as the app. All
// values are invented; none are copied from the live project.

import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:mobile_app/utils/pond_camera_storage.dart';

class Fixtures {
  Fixtures._();

  /// A fixed instant used wherever a widget shows a time.
  static final DateTime now = DateTime(2026, 8, 12, 9, 30);

  /// get_bundled_dashboard_payload for a pond with a full set of readings.
  static Map<String, dynamic> dashboardPayload() => {
    'pond_id': 7,
    'readings': {
      for (final (channel, value) in [
        ('ph', 7.3),
        ('tds', 185.0),
        ('water_temp', 27.4),
        ('lux', 12000.0),
      ])
        channel: {'channel': channel, 'status': 'ok', 'value': value},
    },
    'assessments': <String, dynamic>{},
    'weather': {
      'air_temperature': {'value': 31.2},
      'rainfall': {'value': 0.0},
      'wind_speed': {'value': 9.0},
      'uv_index': {'value': 6},
    },
    'forecast': {
      'two_hour': {'text': 'Partly Cloudy (Day)'},
    },
    'raw_sensor': {'temp': 27.4, 'pH': '7.3', 'TDS': '185', 'LUX': '12000'},
    'nea_forecasts': {
      'forecast_2hr': {'forecast': 'Partly Cloudy (Day)'},
      'two_hr_forecast': 'Partly Cloudy (Day)',
      'uv_index': {
        'data': {'uv': 6},
      },
      'rainfall_mm': 0.0,
    },
    'nea_telemetry': {
      'air_temp': {'value': 31.2},
      'rainfall': {'value': 0.0},
      'wind_speed': {'value': 9.0},
    },
    'telemetry_history': [
      for (var h = 0; h < 6; h++)
        {
          'time': DateTime.utc(2026, 8, 12, h).toIso8601String(),
          'ph': 7.3,
          'tds': 185,
          'tempC': 27.0 + h * 0.1,
          'lux': 0,
        },
    ],
  };

  /// get_bundled_dashboard_payload for a pond with nothing reported yet.
  static Map<String, dynamic> emptyDashboardPayload() => {
    'raw_sensor': <String, dynamic>{},
    'nea_forecasts': <String, dynamic>{},
    'nea_telemetry': <String, dynamic>{},
    'telemetry_history': <dynamic>[],
  };

  /// get_historical_graph_payload with a week of daily averages for every
  /// sensor type the detail screens chart, and one logged event of each
  /// domain's primary type.
  static Map<String, dynamic> historicalPayload() => {
    'daily_trends': [
      for (final (type, base) in [
        ('temp', 27.0),
        ('pH', 7.3),
        ('TDS', 185.0),
        ('LUX', 11000.0),
      ])
        for (var d = 1; d <= 7; d++)
          {
            'sensor_type': type,
            'date': DateTime(2026, 8, d).toIso8601String().substring(0, 10),
            'avg_value': base + d * base * 0.01,
          },
    ],
    'interventions': [
      {
        'event_type': 'WATER_CHANGE',
        'is_major_reset': true,
        'timestamp': '2026-08-04T10:00:00',
      },
      {
        'event_type': 'WATER_TOPUP',
        'is_major_reset': false,
        'timestamp': '2026-08-05T10:00:00',
      },
      {
        'event_type': 'ALGAE_SCRUB',
        'is_major_reset': true,
        'timestamp': '2026-08-06T10:00:00',
      },
    ],
  };

  static Map<String, dynamic> emptyHistoricalPayload() => {
    'daily_trends': <dynamic>[],
    'interventions': <dynamic>[],
  };

  static WaterChemistryAssessment assessment() =>
      WaterChemistryAssessment.fromJson({
        'status': 'Amber',
        'category': 'Watch',
        'tan_ppm': 0.42,
        'no2_ppm': 0.08,
        'no3_ppm': 31.0,
        'ph_reactivity': 0.018,
        'reactivity_trend': 0.001,
        'tds_trend': 1.6,
        'sensor_warnings': <String>[],
        'advisory':
            'Waste load is building. Feed lightly and plan a partial water '
            'change this week.',
        'add_hardener_now': false,
        'risk_score': 3,
      });

  static EvaporationForecast evaporationForecast() =>
      EvaporationForecast.fromJson({
        'predicted_topup_days_from_now': 4,
        'first_watch_days_from_now': 2,
        'first_action_days_from_now': 4,
        'avg_evaporation_mm_per_day': 4.2,
        'avg_loss_litres_per_day': 8.4,
        'starting_loss_pct': 1.5,
        'surface_area_m2': 2.0,
        'assumed_depth_m': 0.6,
        'volume_litres': 1200.0,
        'days_using_real_forecast': 4,
        'last_topup_at': '2026-08-08T08:00:00+00:00',
        'trajectory': [
          for (var d = 0; d < 7; d++)
            {
              'days_from_now': d,
              'evaporation_mm': 4.2,
              'rain_offset_mm': 0.0,
              'cumulative_loss_litres': 8.4 * (d + 1),
              'cumulative_loss_pct': 0.7 * (d + 1),
              'water_temp_c_assumed': 28.0,
              'feed_cap_grams': 60.0,
              'feed_note': 'Normal ration; water is in the koi comfort range.',
            },
        ],
        'tds_cross_check': {
          'verdict': 'corroborated',
          'predicted_tds_slope_ppm_per_day': 1.5,
          'observed_tds_slope_ppm_per_day': 1.7,
          'confidence': 'medium',
        },
      });

  static AlgaeForecast algaeForecast() => AlgaeForecast.fromJson({
    'current_green_ratio': 0.12,
    'predicted_scrub_days_from_now': 6,
    'first_watch_days_from_now': 3,
    'first_action_days_from_now': 6,
    'thresholds': {
      'watch': 0.18,
      'action': 0.25,
      'baseline': 0.1,
      'mode': 'baseline_relative',
    },
    'intrinsic_rate_per_day': 0.2,
    'realised_rate_per_day': 0.12,
    'rate_source': 'fitted_from_camera',
    'confidence': 'medium',
    'sample_count': 12,
    'obstructed_sample_count': 1,
    'days_using_real_forecast': 4,
    'scrub_benefit': {'days_bought': 9, 'post_scrub_green_ratio': 0.05},
    'last_scrub_at': '2026-08-01T08:00:00+00:00',
    'latest_image_url': null,
    'trajectory': [
      for (var d = 0; d < 7; d++)
        {
          'days_from_now': d,
          'green_ratio': 0.12 + d * 0.02,
          'growth_rate_per_day': 0.12,
          'favourability': 0.6,
          'lux_assumed': 11000.0,
          'temp_c_assumed': 28.0,
          'no3_ppm_assumed': 31.0,
        },
    ],
  });

  /// A frame with no photo URL, so tests never attempt a network image.
  static PondCameraFrame frame() => PondCameraFrame(
    id: 501,
    imageUrl: null,
    greenRatio: 0.12,
    capturedAt: now.subtract(const Duration(hours: 2)),
    state: 'base',
  );

  static AlgaeRatingContext ratingContext() => AlgaeRatingContext.fromJson({
    'ratings': <dynamic>[],
    'latest_rating': {
      'id': 41,
      'severity': 'minor',
      'is_obstructed': false,
      'green_ratio_at_rating': 0.11,
      'image_url': null,
      'image_id': 480,
      'rated_at': null,
    },
    'calibration': {
      'label_counts': {'none': 2, 'minor': 1, 'moderate': 0, 'severe': 0},
      'labels_needed': {'none': 0, 'minor': 1, 'moderate': 2, 'severe': 2},
      'class_targets': {
        'none': 0.05,
        'minor': 0.12,
        'moderate': 0.2,
        'severe': 0.3,
      },
      'thresholds': {
        'mode': 'partially_calibrated',
        'watch': 0.18,
        'action': 0.25,
      },
      'camera_drift': {'verdict': 'stable', 'samples': 3, 'detail': ''},
    },
    'latest_image': {'id': 501, 'green_ratio': 0.12, 'imageURL': null},
  });

  static AlgaeRatingResult ratingResult() => AlgaeRatingResult.fromJson({
    'rating_id': 42,
    'green_ratio_before': 0.12,
    'green_ratio_after': 0.17,
    'removed_frames': 0,
    'label_counts': {'none': 2, 'minor': 1, 'moderate': 1, 'severe': 0},
    'warning': null,
    'image_inferred': false,
  });
}
