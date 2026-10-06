import 'package:shared_preferences/shared_preferences.dart';

/// Device settings remain readable from installations that stored numeric
/// values as either strings or integers.
class LocalPondProfile {
  final Map<String, Object> values;
  const LocalPondProfile(this.values);

  int? get pondId => int.tryParse('${values['userID']}');
  bool get isOnboarded => values['isOnboarded'] == true;
  List<String> get species =>
      (values['ownedFishSpecies'] as List?)?.cast<String>() ?? const [];
  int? get fishCount => int.tryParse('${values['fishCount']}');
}

abstract interface class LocalProfileRepository {
  Future<LocalPondProfile> load();
  Future<void> save(Map<String, Object> values);
}

class PreferencesProfileRepository implements LocalProfileRepository {
  Future<SharedPreferences>? _preferences;
  Future<SharedPreferences> get _prefs =>
      _preferences ??= SharedPreferences.getInstance();

  @override
  Future<LocalPondProfile> load() async {
    final prefs = await _prefs;
    return LocalPondProfile({
      for (final key in prefs.getKeys()) key: prefs.get(key)!,
    });
  }

  @override
  Future<void> save(Map<String, Object> values) async {
    final prefs = await _prefs;
    for (final entry in values.entries) {
      final value = entry.value;
      final success = switch (value) {
        bool v => await prefs.setBool(entry.key, v),
        int v => await prefs.setInt(entry.key, v),
        double v => await prefs.setDouble(entry.key, v),
        String v => await prefs.setString(entry.key, v),
        List<String> v => await prefs.setStringList(entry.key, v),
        _ => throw ArgumentError(
          'Unsupported local profile value: ${entry.key}',
        ),
      };
      if (!success) throw StateError('Could not save ${entry.key}');
    }
  }
}

class FakeLocalProfileRepository implements LocalProfileRepository {
  final Map<String, Object> values;
  FakeLocalProfileRepository([Map<String, Object> values = const {}])
    : values = Map.of(values);

  @override
  Future<LocalPondProfile> load() async => LocalPondProfile(Map.of(values));

  @override
  Future<void> save(Map<String, Object> values) async =>
      this.values.addAll(values);
}
