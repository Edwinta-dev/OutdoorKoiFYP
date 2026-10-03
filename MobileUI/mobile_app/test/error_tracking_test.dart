import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/config/error_tracking.dart';
import 'package:sentry_flutter/sentry_flutter.dart';

// Error tracking from issue #66: off without a DSN, environment and
// release set, and personal data scrubbed before a report is sent.
void main() {
  const config = ErrorTrackingConfig(
    dsn: 'https://public@sentry.koi-test.invalid/1',
    environment: 'staging',
    release: '$appPackage@$appVersion',
  );

  test('a build without SENTRY_DSN runs the app without Sentry', () async {
    // flutter test passes no dart-defines.
    expect(ErrorTrackingConfig.build.enabled, isFalse);
    expect(ErrorTrackingConfig.build.release, '$appPackage@$appVersion');
    var ran = false;
    await runWithErrorTracking(ErrorTrackingConfig.build, () => ran = true);
    expect(ran, isTrue);
    expect(Sentry.isEnabled, isFalse);
  });

  test('a blank DSN counts as not set', () {
    const blank = ErrorTrackingConfig(
      dsn: '  ',
      environment: 'x',
      release: 'y',
    );
    expect(blank.enabled, isFalse);
    expect(config.enabled, isTrue);
  });

  test('appVersion matches the pubspec version', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final version = RegExp(
      r'^version:\s*(\S+)',
      multiLine: true,
    ).firstMatch(pubspec)!.group(1);
    expect(appVersion, version);
  });

  test('apply sets environment, release and the privacy options', () {
    final options = SentryFlutterOptions();
    config.apply(options);
    expect(options.dsn, config.dsn);
    expect(options.environment, 'staging');
    expect(options.release, 'sg.edu.ntu.outdoorkoi@$appVersion');
    expect(options.sendDefaultPii, isFalse);
    expect(options.attachScreenshot, isFalse);
    expect(options.beforeSend, isNotNull);
    expect(options.beforeBreadcrumb, isNotNull);
  });

  test(
    'scrubText removes emails, bearer tokens, JWTs and secret query values',
    () {
      const jwt =
          'eyJhbGciOiJIUzI1NiJ9'
          '.eyJzdWIiOiIxMjMifQ'
          '.c2lnbmF0dXJl';
      final out = scrubText(
        'owner@example.com sent Bearer abc.def and $jwt to '
        'https://user:pass@host.invalid/v1/ponds?latitude=eq.1.35&volume=1200',
      );
      expect(out, isNot(contains('owner@example.com')));
      expect(out, isNot(contains('abc.def')));
      expect(out, isNot(contains(jwt)));
      expect(out, isNot(contains('user:pass')));
      expect(out, contains('latitude=$filtered'));
      expect(out, contains('volume=1200'));
      const plain = 'Pond 455: water temperature 31.2 C, latency=120 ms';
      expect(scrubText(plain), plain);
    },
  );

  test('scrubValue replaces location, email and token keys at any depth', () {
    final out =
        scrubValue({
              'latitude': 1.35,
              'lng': 103.8,
              'manualPostalLocation': '123456',
              'user': {'email': 'owner@example.com', 'id': '455'},
              'headers': [
                {'Authorization': 'Bearer x'},
              ],
              'SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_x',
              'volume_l': 1200,
            })
            as Map;
    expect(out['latitude'], filtered);
    expect(out['lng'], filtered);
    expect(out['manualPostalLocation'], filtered);
    expect(out['user'], {'email': filtered, 'id': '455'});
    expect(out['headers'], [
      {'Authorization': filtered},
    ]);
    expect(out['SUPABASE_PUBLISHABLE_KEY'], filtered);
    expect(out['volume_l'], 1200);
  });

  test('scrubEvent keeps only the user id and drops request data', () {
    final event = SentryEvent(
      user: SentryUser(
        id: '455',
        email: 'owner@example.com',
        ipAddress: '10.0.0.5',
        geo: SentryGeo(city: 'Singapore'),
      ),
      message: SentryMessage('save failed for owner@example.com'),
      request: SentryRequest(
        url: 'https://abc.supabase.co/rest/v1/UserData?longitude=eq.103.8',
        method: 'GET',
        data: {'latitude': 1.35},
        headers: {'Authorization': 'Bearer x', 'Accept': 'application/json'},
      ),
      exceptions: [
        SentryException(type: 'StateError', value: 'token eyJa.eyJb.c'),
      ],
      breadcrumbs: [
        Breadcrumb.http(
          url: Uri.parse('https://abc.supabase.co/x?apikey=k&id=1'),
          method: 'GET',
        ),
      ],
    );
    final out = scrubEvent(event, Hint())!;
    expect(out.user!.id, '455');
    expect(out.user!.email, isNull);
    expect(out.user!.ipAddress, isNull);
    expect(out.user!.geo, isNull);
    expect(out.message!.formatted, 'save failed for $filtered');
    expect(out.request!.url, contains('longitude=$filtered'));
    expect(out.request!.data, isNull);
    expect(out.request!.headers['Authorization'], filtered);
    expect(out.request!.headers['Accept'], 'application/json');
    expect(out.exceptions!.single.value, 'token $filtered');
    expect(out.breadcrumbs!.single.data!['url'], contains('apikey=$filtered'));
    expect(out.breadcrumbs!.single.data!['url'], contains('id=1'));
  });
}
