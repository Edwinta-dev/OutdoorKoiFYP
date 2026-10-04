// lib/utils/pond_dashboard.dart
//
// Parser for GET /v1/ponds/{pond}/dashboard (Backend/koi/api/dashboard.py;
// schema Dashboard in docs/api/openapi.yaml, field rules in
// docs/api/README.md). No screen calls it yet: the mobile repositories
// issue moves the dashboard from the Supabase RPC and the /assessment and
// /forecast calls to this one response.
//
// Every value the server could not trust is null, with a status saying
// why ("missing", "stale", "invalid", "expired", "night_derived"); this
// parser keeps those nulls rather than filling in defaults.
//
// Class names match the schema names so tests/api/test_contract.py can
// check every key read here against the OpenAPI document (PondDashboard
// is the schema Dashboard).

import 'digital_twin_api.dart' show WaterChemistryAssessment;

DateTime? _time(dynamic v) => v is String ? DateTime.tryParse(v) : null;
double? _num(dynamic v) => v is num ? v.toDouble() : null;
int? _int(dynamic v) => v is num ? v.toInt() : null;
String? _str(dynamic v) => v is String ? v : null;
Map<String, dynamic> _map(dynamic v) =>
    v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};

/// The newest stored reading of one probe channel, with its own times.
class SensorReading {
  final String channel; // ph | tds | water_temp | lux
  final String status; // ok | stale | missing | invalid
  final double? value;
  final String unit;
  final String? sensorType;
  final int? sourceRowId;

  /// When the database stored the row, not when the probe sampled.
  final DateTime? ingestedAt;

  /// Null until the node sends a sample time (issue #17).
  final DateTime? sampleTime;
  final String sampleTimeBasis;
  final DateTime? freshUntil;
  final String? note;

  const SensorReading({
    required this.channel,
    required this.status,
    required this.value,
    required this.unit,
    required this.sensorType,
    required this.sourceRowId,
    required this.ingestedAt,
    required this.sampleTime,
    required this.sampleTimeBasis,
    required this.freshUntil,
    required this.note,
  });

  bool get isFresh => status == 'ok';

  factory SensorReading.fromJson(Map<String, dynamic> json) => SensorReading(
        channel: _str(json['channel']) ?? '',
        status: _str(json['status']) ?? 'missing',
        value: _num(json['value']),
        unit: _str(json['unit']) ?? '',
        sensorType: _str(json['sensor_type']),
        sourceRowId: _int(json['source_row_id']),
        ingestedAt: _time(json['ingested_at']),
        sampleTime: _time(json['sample_time']),
        sampleTimeBasis: _str(json['sample_time_basis']) ?? 'unknown',
        freshUntil: _time(json['fresh_until']),
        note: _str(json['note']),
      );
}

class DashboardReadings {
  final int freshForSeconds;
  final SensorReading ph;
  final SensorReading tds;
  final SensorReading waterTemp;
  final SensorReading lux;

  const DashboardReadings({
    required this.freshForSeconds,
    required this.ph,
    required this.tds,
    required this.waterTemp,
    required this.lux,
  });

  factory DashboardReadings.fromJson(Map<String, dynamic> json) =>
      DashboardReadings(
        freshForSeconds: _int(json['fresh_for_seconds']) ?? 0,
        ph: SensorReading.fromJson(_map(json['ph'])),
        tds: SensorReading.fromJson(_map(json['tds'])),
        waterTemp: SensorReading.fromJson(_map(json['water_temp'])),
        lux: SensorReading.fromJson(_map(json['lux'])),
      );
}

class HypoxiaFlag {
  final String level; // unknown | none | watch | high
  final bool flagged;
  final String explanation;
  final List<String> advice;

  const HypoxiaFlag({
    required this.level,
    required this.flagged,
    required this.explanation,
    required this.advice,
  });

  factory HypoxiaFlag.fromJson(Map<String, dynamic> json) => HypoxiaFlag(
        level: _str(json['level']) ?? 'unknown',
        flagged: json['flagged'] == true,
        explanation: _str(json['explanation']) ?? '',
        advice: (json['advice'] as List?)?.map((e) => '$e').toList() ??
            const [],
      );
}

/// Model estimates. Evaporation and algae stay as maps until the
/// dashboard cards move to this response.
class DashboardAssessments {
  final WaterChemistryAssessment? chemistry;
  final Map<String, dynamic>? evaporation;
  final Map<String, dynamic>? algae;
  final HypoxiaFlag hypoxia;

  const DashboardAssessments({
    required this.chemistry,
    required this.evaporation,
    required this.algae,
    required this.hypoxia,
  });

  factory DashboardAssessments.fromJson(Map<String, dynamic> json) =>
      DashboardAssessments(
        chemistry: json['chemistry'] is Map
            ? WaterChemistryAssessment.fromJson(_map(json['chemistry']))
            : null,
        evaporation:
            json['evaporation'] is Map ? _map(json['evaporation']) : null,
        algae: json['algae'] is Map ? _map(json['algae']) : null,
        hypoxia: HypoxiaFlag.fromJson(_map(json['hypoxia'])),
      );
}

class StationAssignment {
  final String? airTemperature;
  final String? rainfall;
  final String? windSpeed;
  final String? twoHourArea;
  final String? twentyFourHourRegion;

  const StationAssignment({
    required this.airTemperature,
    required this.rainfall,
    required this.windSpeed,
    required this.twoHourArea,
    required this.twentyFourHourRegion,
  });

  factory StationAssignment.fromJson(Map<String, dynamic> json) =>
      StationAssignment(
        airTemperature: _str(json['air_temperature']),
        rainfall: _str(json['rainfall']),
        windSpeed: _str(json['wind_speed']),
        twoHourArea: _str(json['two_hour_area']),
        twentyFourHourRegion: _str(json['twenty_four_hour_region']),
      );
}

/// One NEA station reading from the pond's assigned station.
class WeatherValue {
  final String metric; // air_temperature | rainfall | wind_speed
  final String status; // ok | stale | missing
  final double? value;
  final String unit; // degC | mm (5 minutes) | knot
  final String? stationId;
  final DateTime? observedAt;
  final DateTime? ingestedAt;
  final DateTime? freshUntil;
  final String? note;

  const WeatherValue({
    required this.metric,
    required this.status,
    required this.value,
    required this.unit,
    required this.stationId,
    required this.observedAt,
    required this.ingestedAt,
    required this.freshUntil,
    required this.note,
  });

  factory WeatherValue.fromJson(Map<String, dynamic> json) => WeatherValue(
        metric: _str(json['metric']) ?? '',
        status: _str(json['status']) ?? 'missing',
        value: _num(json['value']),
        unit: _str(json['unit']) ?? '',
        stationId: _str(json['station_id']),
        observedAt: _time(json['observed_at']),
        ingestedAt: _time(json['ingested_at']),
        freshUntil: _time(json['fresh_until']),
        note: _str(json['note']),
      );
}

class UvIndex {
  /// ok | missing | night_derived (0 by convention, not measured).
  final String status;
  final double? value;
  final DateTime? hourStart;
  final bool daylight;
  final DateTime? ingestedAt;
  final String? note;

  const UvIndex({
    required this.status,
    required this.value,
    required this.hourStart,
    required this.daylight,
    required this.ingestedAt,
    required this.note,
  });

  bool get isMeasured => status == 'ok';

  factory UvIndex.fromJson(Map<String, dynamic> json) => UvIndex(
        status: _str(json['status']) ?? 'missing',
        value: _num(json['value']),
        hourStart: _time(json['hour_start']),
        daylight: json['daylight'] == true,
        ingestedAt: _time(json['ingested_at']),
        note: _str(json['note']),
      );
}

class WeatherNow {
  final WeatherValue airTemperature;
  final WeatherValue rainfall;
  final WeatherValue windSpeed;
  final UvIndex uvIndex;

  const WeatherNow({
    required this.airTemperature,
    required this.rainfall,
    required this.windSpeed,
    required this.uvIndex,
  });

  factory WeatherNow.fromJson(Map<String, dynamic> json) => WeatherNow(
        airTemperature: WeatherValue.fromJson(_map(json['air_temperature'])),
        rainfall: WeatherValue.fromJson(_map(json['rainfall'])),
        windSpeed: WeatherValue.fromJson(_map(json['wind_speed'])),
        uvIndex: UvIndex.fromJson(_map(json['uv_index'])),
      );
}

/// A cached NEA forecast. issuedAt is the provider's issue time (null for
/// rows cached before it was recorded); ingestedAt is when it was cached.
class ForecastPeriod {
  final String status; // ok | expired | missing
  final String? slotId;
  final String? text;
  final String? code;
  final DateTime? validFrom;
  final DateTime? validTo;
  final DateTime? issuedAt;
  final DateTime? ingestedAt;

  const ForecastPeriod({
    required this.status,
    required this.slotId,
    required this.text,
    required this.code,
    required this.validFrom,
    required this.validTo,
    required this.issuedAt,
    required this.ingestedAt,
  });

  factory ForecastPeriod.fromJson(Map<String, dynamic> json) => ForecastPeriod(
        status: _str(json['status']) ?? 'missing',
        slotId: _str(json['slot_id']),
        text: _str(json['text']),
        code: _str(json['code']),
        validFrom: _time(json['valid_from']),
        validTo: _time(json['valid_to']),
        issuedAt: _time(json['issued_at']),
        ingestedAt: _time(json['ingested_at']),
      );
}

class GeneralForecast {
  final ForecastPeriod period;
  final double? temperatureLowC;
  final double? temperatureHighC;
  final double? humidityLowPct;
  final double? humidityHighPct;
  final double? windLowKmh;
  final double? windHighKmh;

  const GeneralForecast({
    required this.period,
    required this.temperatureLowC,
    required this.temperatureHighC,
    required this.humidityLowPct,
    required this.humidityHighPct,
    required this.windLowKmh,
    required this.windHighKmh,
  });

  factory GeneralForecast.fromJson(Map<String, dynamic> json) =>
      GeneralForecast(
        period: ForecastPeriod.fromJson(json),
        temperatureLowC: _num(json['temperature_low_c']),
        temperatureHighC: _num(json['temperature_high_c']),
        humidityLowPct: _num(json['humidity_low_pct']),
        humidityHighPct: _num(json['humidity_high_pct']),
        windLowKmh: _num(json['wind_low_kmh']),
        windHighKmh: _num(json['wind_high_kmh']),
      );
}

class OutlookDay {
  final DateTime? date;
  final String? weekday;
  final String? text;
  final String? code;
  final double? temperatureLowC;
  final double? temperatureHighC;
  final double? humidityLowPct;
  final double? humidityHighPct;
  final double? windLowKmh;
  final double? windHighKmh;
  final DateTime? issuedAt;
  final DateTime? ingestedAt;

  const OutlookDay({
    required this.date,
    required this.weekday,
    required this.text,
    required this.code,
    required this.temperatureLowC,
    required this.temperatureHighC,
    required this.humidityLowPct,
    required this.humidityHighPct,
    required this.windLowKmh,
    required this.windHighKmh,
    required this.issuedAt,
    required this.ingestedAt,
  });

  factory OutlookDay.fromJson(Map<String, dynamic> json) => OutlookDay(
        date: _time(json['date']),
        weekday: _str(json['weekday']),
        text: _str(json['text']),
        code: _str(json['code']),
        temperatureLowC: _num(json['temperature_low_c']),
        temperatureHighC: _num(json['temperature_high_c']),
        humidityLowPct: _num(json['humidity_low_pct']),
        humidityHighPct: _num(json['humidity_high_pct']),
        windLowKmh: _num(json['wind_low_kmh']),
        windHighKmh: _num(json['wind_high_kmh']),
        issuedAt: _time(json['issued_at']),
        ingestedAt: _time(json['ingested_at']),
      );
}

class DashboardForecast {
  final ForecastPeriod twoHour;
  final ForecastPeriod twentyFourHourRegional;
  final GeneralForecast twentyFourHourGeneral;
  final List<OutlookDay> outlook;

  const DashboardForecast({
    required this.twoHour,
    required this.twentyFourHourRegional,
    required this.twentyFourHourGeneral,
    required this.outlook,
  });

  factory DashboardForecast.fromJson(Map<String, dynamic> json) =>
      DashboardForecast(
        twoHour: ForecastPeriod.fromJson(_map(json['two_hour'])),
        twentyFourHourRegional:
            ForecastPeriod.fromJson(_map(json['twenty_four_hour_regional'])),
        twentyFourHourGeneral:
            GeneralForecast.fromJson(_map(json['twenty_four_hour_general'])),
        outlook: (json['outlook'] as List? ?? const [])
            .map((d) => OutlookDay.fromJson(_map(d)))
            .toList(),
      );
}

/// GET /v1/ponds/{pond}/dashboard.
class PondDashboard {
  final int pondId;
  final StationAssignment stations;
  final DashboardReadings readings;
  final DashboardAssessments assessments;
  final List<Map<String, dynamic>> nextActions;
  final WeatherNow weather;
  final DashboardForecast forecast;

  const PondDashboard({
    required this.pondId,
    required this.stations,
    required this.readings,
    required this.assessments,
    required this.nextActions,
    required this.weather,
    required this.forecast,
  });

  factory PondDashboard.fromJson(Map<String, dynamic> json) => PondDashboard(
        pondId: _int(json['pond_id']) ?? 0,
        stations: StationAssignment.fromJson(_map(json['stations'])),
        readings: DashboardReadings.fromJson(_map(json['readings'])),
        assessments: DashboardAssessments.fromJson(_map(json['assessments'])),
        nextActions: (json['next_actions'] as List? ?? const [])
            .map((a) => _map(a))
            .toList(),
        weather: WeatherNow.fromJson(_map(json['weather'])),
        forecast: DashboardForecast.fromJson(_map(json['forecast'])),
      );
}
