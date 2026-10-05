class DailyTelemetry {
  final String sensorType;
  final DateTime date;
  final double? average;
  final bool afterMaintenance;

  DailyTelemetry.fromJson(Map<String, dynamic> json)
    : sensorType = json['sensor_type'] as String,
      date = DateTime.parse(json['date'] as String),
      average = (json['avg_value'] as num?)?.toDouble(),
      afterMaintenance = json['after_maintenance'] == true;

  Map<String, dynamic> toJson() => {
    'sensor_type': sensorType,
    'date': date.toIso8601String(),
    'avg_value': average,
    'after_maintenance': afterMaintenance,
  };
}

class HistoricalIntervention {
  final String eventType;
  final DateTime timestamp;
  final bool isMajorReset;
  final double? saltGrams;
  final String? notes;

  HistoricalIntervention.fromJson(Map<String, dynamic> json)
    : eventType = json['event_type'] as String,
      timestamp = DateTime.parse(json['timestamp'] as String),
      isMajorReset = json['is_major_reset'] == true,
      saltGrams = (json['salt_grams'] as num?)?.toDouble(),
      notes = json['notes'] as String?;

  Map<String, dynamic> toJson() => {
    'event_type': eventType,
    'timestamp': timestamp.toIso8601String(),
    'is_major_reset': isMajorReset,
    'salt_grams': saltGrams,
    'notes': notes,
  };
}

class TelemetryHistory {
  final List<DailyTelemetry> days;
  final List<HistoricalIntervention> interventions;
  TelemetryHistory.fromJson(Map<String, dynamic> json)
    : days = (json['daily_trends'] as List? ?? [])
          .map(
            (row) =>
                DailyTelemetry.fromJson(Map<String, dynamic>.from(row as Map)),
          )
          .toList(),
      interventions = (json['interventions'] as List? ?? [])
          .map(
            (row) => HistoricalIntervention.fromJson(
              Map<String, dynamic>.from(row as Map),
            ),
          )
          .toList();
}
