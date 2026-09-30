import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/config/app_config.dart';
import 'package:mobile_app/screens/config_error_screen.dart';

AppConfig _config({
  String supabaseUrl = 'https://abc.supabase.co',
  String key = 'sb_publishable_test',
  String twin = 'http://10.0.0.5:8080',
  String bucket = 'imageAnalysisBucket',
}) => AppConfig(
  supabaseUrl: supabaseUrl,
  supabasePublishableKey: key,
  digitalTwinBaseUrl: twin,
  pondImageBucket: bucket,
);

void main() {
  test('a complete configuration has no problems', () {
    expect(_config().problems, isEmpty);
  });

  test('every missing value is reported by name', () {
    final problems = _config(
      supabaseUrl: '',
      key: ' ',
      twin: '',
      bucket: '',
    ).problems;
    expect(problems, [
      'SUPABASE_URL is not set',
      'SUPABASE_PUBLISHABLE_KEY is not set',
      'DIGITAL_TWIN_BASE_URL is not set',
      'POND_IMAGE_BUCKET is not set',
    ]);
  });

  test('a URL value without an http(s) scheme and host is rejected', () {
    expect(_config(twin: '192.168.1.2:8080').problems, [
      'DIGITAL_TWIN_BASE_URL is not an http(s) URL: 192.168.1.2:8080',
    ]);
    expect(_config(supabaseUrl: 'ftp://abc.supabase.co').problems, hasLength(1));
  });

  test('a test build without --dart-define-from-file has no fallback values', () {
    // flutter test passes no dart-defines, so this is the unconfigured build.
    expect(AppConfig.environment.digitalTwinBaseUrl, isEmpty);
    expect(AppConfig.environment.problems, hasLength(4));
  });

  testWidgets('the configuration error screen lists each problem', (
    tester,
  ) async {
    await tester.pumpWidget(
      const ConfigErrorApp(problems: ['DIGITAL_TWIN_BASE_URL is not set']),
    );
    expect(find.text('Configuration error'), findsOneWidget);
    expect(find.text('- DIGITAL_TWIN_BASE_URL is not set'), findsOneWidget);
    expect(
      find.textContaining('--dart-define-from-file=env/dev.json'),
      findsOneWidget,
    );
  });
}
