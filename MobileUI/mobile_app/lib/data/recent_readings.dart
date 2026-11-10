/// One raw sensor reading of GET /v1/ponds/{pond}/sensors/recent.
class RecentReading {
  final DateTime at;
  final double value;
  final int? flags;

  RecentReading.fromJson(Map<String, dynamic> json)
    : at = DateTime.parse(json['at'] as String),
      value = (json['value'] as num).toDouble(),
      flags = (json['flags'] as num?)?.toInt();
}

/// One sensor's raw readings over the last [minutes], oldest first.
class RecentReadings {
  final String sensor;
  final int minutes;
  final List<RecentReading> readings;

  RecentReadings.fromJson(Map<String, dynamic> json)
    : sensor = json['sensor'] as String,
      minutes = (json['minutes'] as num).toInt(),
      readings = (json['readings'] as List? ?? [])
          .map(
            (row) =>
                RecentReading.fromJson(Map<String, dynamic>.from(row as Map)),
          )
          .toList();

  RecentReading? get latest => readings.isEmpty ? null : readings.last;
  double get minimum =>
      readings.map((r) => r.value).reduce((a, b) => a < b ? a : b);
  double get maximum =>
      readings.map((r) => r.value).reduce((a, b) => a > b ? a : b);
}
