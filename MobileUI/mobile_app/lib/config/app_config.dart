// lib/config/app_config.dart
//
// Every environment-specific value the app needs, read at build time from
//   flutter run --dart-define-from-file=env/dev.json
// (copy env/dev.json.example or env/prod.json.example and fill it in; the
// real env/*.json files are gitignored). There are deliberately no
// defaults: a missing value shows ConfigErrorScreen instead of quietly
// pointing the app at someone's LAN IP or another Supabase project.

class AppConfig {
  const AppConfig({
    required this.supabaseUrl,
    required this.supabasePublishableKey,
    required this.digitalTwinBaseUrl,
    required this.pondImageBucket,
  });

  /// The values compiled into this build.
  static const AppConfig environment = AppConfig(
    supabaseUrl: String.fromEnvironment('SUPABASE_URL'),
    supabasePublishableKey: String.fromEnvironment('SUPABASE_PUBLISHABLE_KEY'),
    digitalTwinBaseUrl: String.fromEnvironment('DIGITAL_TWIN_BASE_URL'),
    pondImageBucket: String.fromEnvironment('POND_IMAGE_BUCKET'),
  );

  final String supabaseUrl;
  final String supabasePublishableKey;

  /// Base URL of the DigitalTwin Flask service (Backend/DigitalTwin).
  final String digitalTwinBaseUrl;

  /// Must match POND_IMAGE_BUCKET in Backend/Camera/.env.
  final String pondImageBucket;

  static const List<String> _urlKeys = [
    'SUPABASE_URL',
    'DIGITAL_TWIN_BASE_URL',
  ];

  Map<String, String> get _values => {
    'SUPABASE_URL': supabaseUrl,
    'SUPABASE_PUBLISHABLE_KEY': supabasePublishableKey,
    'DIGITAL_TWIN_BASE_URL': digitalTwinBaseUrl,
    'POND_IMAGE_BUCKET': pondImageBucket,
  };

  /// One line per missing or malformed value; empty when the build is
  /// usable.
  List<String> get problems => [
    for (final entry in _values.entries)
      if (entry.value.trim().isEmpty)
        '${entry.key} is not set'
      else if (_urlKeys.contains(entry.key) && !_isHttpUrl(entry.value))
        '${entry.key} is not an http(s) URL: ${entry.value}',
  ];

  static bool _isHttpUrl(String value) {
    final uri = Uri.tryParse(value.trim());
    return uri != null &&
        (uri.scheme == 'http' || uri.scheme == 'https') &&
        uri.host.isNotEmpty;
  }
}
