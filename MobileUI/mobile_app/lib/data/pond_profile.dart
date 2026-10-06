double? _number(Object? value) =>
    value is num ? value.toDouble() : double.tryParse('$value');

class PondProfileValues {
  final DateTime? effectiveFrom;
  final double volumeL;
  final double? depthM;
  final double biomassG;
  final String? fishType;
  final int? fishCount;
  final double? tapTdsPpm;
  final double? tapNitratePpm;
  final bool? aeration;

  PondProfileValues.fromJson(Map<String, dynamic> json)
    : effectiveFrom = DateTime.tryParse('${json['effective_from']}'),
      volumeL = _number(json['volume_l']) ?? 0,
      depthM = _number(json['depth_m']),
      biomassG = _number(json['biomass_g']) ?? 0,
      fishType = json['fish_type'] as String?,
      fishCount = (json['fish_count'] as num?)?.toInt(),
      tapTdsPpm = _number(json['tap_tds_ppm']),
      tapNitratePpm = _number(json['tap_nitrate_ppm']),
      aeration = json['aeration'] as bool?;
}

class PondProfileRow extends PondProfileValues {
  final int id;
  final int pondId;
  final String? source;
  final DateTime? createdAt;

  PondProfileRow.fromJson(super.json)
    : id = (json['id'] as num).toInt(),
      pondId = (json['pond_id'] as num).toInt(),
      source = json['source'] as String?,
      createdAt = DateTime.tryParse('${json['created_at']}'),
      super.fromJson();
}

class PondProfileResponse {
  final int pondId;
  final String source;
  final PondProfileValues current;
  final List<PondProfileRow> history;
  final PondProfileRow? added;

  PondProfileResponse.fromJson(Map<String, dynamic> json)
    : pondId = (json['pond_id'] as num).toInt(),
      source = json['source'] as String,
      current = PondProfileValues.fromJson(
        Map<String, dynamic>.from(json['current'] as Map),
      ),
      history = (json['history'] as List? ?? [])
          .map(
            (row) =>
                PondProfileRow.fromJson(Map<String, dynamic>.from(row as Map)),
          )
          .toList(),
      added = json['added'] == null
          ? null
          : PondProfileRow.fromJson(
              Map<String, dynamic>.from(json['added'] as Map),
            );
}
