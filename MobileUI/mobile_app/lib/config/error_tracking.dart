// lib/config/error_tracking.dart
//
// Crash and error reports to Sentry, on only when the build was given a
// DSN:
//   "SENTRY_DSN": "https://...ingest.sentry.io/..."   in env/*.json
// Optional SENTRY_ENVIRONMENT (default production for release builds,
// development otherwise) and SENTRY_RELEASE (default
// sg.edu.ntu.outdoorkoi@<pubspec version>).
//
// Personal data stays on the phone: no IP address or screenshots are
// sent (the view hierarchy is off by default too), and scrubEvent /
// scrubBreadcrumb remove location (latitude, longitude, postal code),
// email addresses and tokens from whatever is left before a report
// leaves the app.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:sentry_flutter/sentry_flutter.dart';

/// The app's application ID on Android and iOS.
const String appPackage = 'sg.edu.ntu.outdoorkoi';

/// Must equal `version:` in pubspec.yaml (test/error_tracking_test.dart
/// checks it).
const String appVersion = '1.0.0+1';

class ErrorTrackingConfig {
  const ErrorTrackingConfig({
    required this.dsn,
    required this.environment,
    required this.release,
  });

  /// The values compiled into this build.
  static const ErrorTrackingConfig build = ErrorTrackingConfig(
    dsn: String.fromEnvironment('SENTRY_DSN'),
    environment: String.fromEnvironment(
      'SENTRY_ENVIRONMENT',
      defaultValue: kReleaseMode ? 'production' : 'development',
    ),
    release: String.fromEnvironment(
      'SENTRY_RELEASE',
      defaultValue: '$appPackage@$appVersion',
    ),
  );

  final String dsn;
  final String environment;
  final String release;

  bool get enabled => dsn.trim().isNotEmpty;

  /// Applies this configuration and the privacy settings to [options].
  void apply(SentryFlutterOptions options) {
    options
      ..dsn = dsn.trim()
      ..environment = environment.trim().isEmpty ? null : environment
      ..release = release.trim().isEmpty ? null : release
      ..sendDefaultPii = false
      ..attachScreenshot = false
      ..beforeSend = scrubEvent
      ..beforeBreadcrumb = scrubBreadcrumb;
  }
}

/// Runs [appRunner], inside Sentry when [config] has a DSN.
Future<void> runWithErrorTracking(
  ErrorTrackingConfig config,
  FutureOr<void> Function() appRunner,
) async {
  if (!config.enabled) {
    await appRunner();
    return;
  }
  await SentryFlutter.init(config.apply, appRunner: appRunner);
}

const String filtered = '[Filtered]';

// A key containing any of these (lower case, ignoring '_' and '-') has
// its value replaced.
const List<String> _sensitiveKeyParts = [
  'latitude',
  'longitude',
  'location',
  'postal',
  'closeststations',
  'coord',
  'geo',
  'email',
  'ipaddress',
  'token',
  'secret',
  'password',
  'authorization',
  'cookie',
  'apikey',
  'publishablekey',
  'jwt',
  'session',
  'credential',
];
// Keys that are sensitive only as a whole word ("lat" but not "latency").
const Set<String> _sensitiveKeys = {
  'lat',
  'lon',
  'lng',
  'long',
  'key',
  'auth',
  'apikey',
};

final List<RegExp> _valuePatterns = [
  RegExp(r'bearer\s+[A-Za-z0-9._~+/=-]+', caseSensitive: false),
  RegExp(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*'),
  RegExp(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
  RegExp(r'(?<=://)[^/\s:@]+:[^/\s@]+(?=@)'),
];
final RegExp _queryParam = RegExp(r'\b([A-Za-z0-9_.-]+)=([^&\s]*)');

bool isSensitiveKey(String key) {
  final name = key.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]'), '');
  return _sensitiveKeys.contains(name) ||
      _sensitiveKeyParts.any((part) => name.contains(part));
}

String scrubText(String text) {
  var out = text;
  for (final pattern in _valuePatterns) {
    out = out.replaceAll(pattern, filtered);
  }
  return out.replaceAllMapped(
    _queryParam,
    (m) =>
        isSensitiveKey(m.group(1)!) ? '${m.group(1)}=$filtered' : m.group(0)!,
  );
}

/// [value] with sensitive keys and values replaced, recursively.
dynamic scrubValue(dynamic value) {
  if (value is Map) {
    return {
      for (final entry in value.entries)
        entry.key: isSensitiveKey('${entry.key}')
            ? filtered
            : scrubValue(entry.value),
    };
  }
  if (value is List) return [for (final v in value) scrubValue(v)];
  if (value is String) return scrubText(value);
  return value;
}

Map<String, String> _scrubStrings(Map<String, String> map) => {
  for (final entry in map.entries)
    entry.key: isSensitiveKey(entry.key) ? filtered : scrubText(entry.value),
};

/// beforeSend: removes personal data from an event.
SentryEvent? scrubEvent(SentryEvent event, Hint hint) {
  final userId = event.user?.id;
  event.user = userId == null ? null : SentryUser(id: userId);
  final request = event.request;
  if (request != null) {
    event.request = SentryRequest(
      url: request.url == null ? null : scrubText(request.url!),
      method: request.method,
      queryString: request.queryString == null
          ? null
          : scrubText(request.queryString!),
      headers: _scrubStrings(request.headers),
    );
  }
  final message = event.message;
  if (message != null) {
    message.formatted = scrubText(message.formatted);
    message.params = message.params == null
        ? null
        : [for (final p in message.params!) scrubValue(p)];
  }
  for (final exception in event.exceptions ?? const <SentryException>[]) {
    if (exception.value != null) exception.value = scrubText(exception.value!);
  }
  if (event.tags != null) event.tags = _scrubStrings(event.tags!);
  // ignore: deprecated_member_use
  final extra = event.extra;
  if (extra != null) {
    // ignore: deprecated_member_use
    event.extra = Map<String, dynamic>.from(scrubValue(extra) as Map);
  }
  event.breadcrumbs = event.breadcrumbs
      ?.map((crumb) => scrubBreadcrumb(crumb, Hint()))
      .whereType<Breadcrumb>()
      .toList();
  return event;
}

/// beforeBreadcrumb: the HTTP and navigation breadcrumbs carry URLs and
/// route arguments.
Breadcrumb? scrubBreadcrumb(Breadcrumb? crumb, Hint hint) {
  if (crumb == null) return null;
  if (crumb.message != null) crumb.message = scrubText(crumb.message!);
  if (crumb.data != null) {
    crumb.data = Map<String, dynamic>.from(scrubValue(crumb.data) as Map);
  }
  return crumb;
}
