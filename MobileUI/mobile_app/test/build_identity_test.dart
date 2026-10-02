import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/utils/app_log.dart';

// Guards the app identity and dependency hygiene from issue #45. Paths are
// relative to the package root, where `flutter test` runs.
void main() {
  const appId = 'sg.edu.ntu.outdoorkoi';

  test('Android uses the OutdoorKoi package ID and label', () {
    final gradle = File('android/app/build.gradle.kts').readAsStringSync();
    expect(gradle, contains('namespace = "$appId"'));
    expect(gradle, contains('applicationId = "$appId"'));

    final manifest = File(
      'android/app/src/main/AndroidManifest.xml',
    ).readAsStringSync();
    expect(manifest, contains('android:label="OutdoorKoi"'));

    final activity = File(
      'android/app/src/main/kotlin/sg/edu/ntu/outdoorkoi/MainActivity.kt',
    ).readAsStringSync();
    expect(activity, startsWith('package $appId'));
  });

  test('iOS uses the OutdoorKoi bundle ID and name', () {
    final pbxproj = File(
      'ios/Runner.xcodeproj/project.pbxproj',
    ).readAsStringSync();
    expect(pbxproj, isNot(contains('com.example')));
    expect(pbxproj, contains('PRODUCT_BUNDLE_IDENTIFIER = $appId;'));

    final plist = File('ios/Runner/Info.plist').readAsStringSync();
    expect(plist, contains('<string>OutdoorKoi</string>'));
  });

  test('pubspec drops the template description and unused packages', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    expect(pubspec, isNot(contains('A new Flutter project.')));
    expect(pubspec, isNot(contains('formula1_data')));
  });

  test('lib/ uses the log helper instead of print', () {
    final printCall = RegExp(r'(^|[^\w.])print\(');
    final offenders = Directory('lib')
        .listSync(recursive: true)
        .whereType<File>()
        .where((f) => f.path.endsWith('.dart'))
        .where((f) => printCall.hasMatch(f.readAsStringSync()))
        .map((f) => f.path)
        .toList();
    expect(offenders, isEmpty);
  });

  test('log accepts any object, including null', () {
    expect(() => log('pond volume: 1200 L'), returnsNormally);
    expect(() => log(null), returnsNormally);
    expect(() => log({'ph': 7.2}), returnsNormally);
  });
}
