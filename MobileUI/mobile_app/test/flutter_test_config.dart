// Runs before every test file in test/.
//
// Font setup: the default test font draws every glyph as a full-width
// square, so text is about twice as wide as on a phone and the dashboard
// cards overflow at phone widths. This loads Roboto and the Material
// icon font from test/fonts (copied from the Flutter SDK's
// bin/cache/artifacts/material_fonts, licenses alongside), so layout is
// realistic and goldens render real text and icons. They are committed
// because the CI runner's SDK cache lacks Roboto even after
// `flutter precache`; the SDK cache is only a fallback.
//
// Limit: text whose style names no font family and does not inherit the
// theme's (DropdownButton's own `style`, as in the log sheet's time
// pickers) still uses the test font and renders as squares. That is
// deterministic, so goldens stay stable; it does not happen on a device.
//
// Golden comparison: text anti-aliasing differs slightly between the
// Windows machine that records goldens and the Linux CI runner, so a
// golden passes when at most [goldenTolerance] of its pixels differ.

import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const double goldenTolerance = 0.005; // 0.5 % of pixels

Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  TestWidgetsFlutterBinding.ensureInitialized();
  await _loadFonts();
  if (goldenFileComparator is LocalFileComparator) {
    goldenFileComparator = _TolerantComparator(
      (goldenFileComparator as LocalFileComparator).basedir.resolve('x.dart'),
    );
  }
  await testMain();
}

Directory _materialFontsDir() {
  final candidates = <String>[
    'test/fonts', // flutter test runs from the package root
    if (Platform.environment['FLUTTER_ROOT'] case final root?)
      '$root/bin/cache/artifacts/material_fonts',
  ];
  // flutter_tester lives in <root>/bin/cache/artifacts/engine/<platform>/.
  var dir = File(Platform.resolvedExecutable).parent;
  for (var i = 0; i < 6; i++) {
    candidates.add('${dir.path}/bin/cache/artifacts/material_fonts');
    candidates.add('${dir.path}/material_fonts');
    dir = dir.parent;
  }
  for (final c in candidates) {
    final d = Directory(c);
    if (File('${d.path}/roboto-regular.ttf').existsSync()) return d;
  }
  throw StateError(
    'Roboto not found (looked in: ${candidates.join(', ')}).',
  );
}

Future<void> _loadFonts() async {
  final dir = _materialFontsDir();
  Future<ByteData> read(String name) async =>
      ByteData.sublistView(await File('${dir.path}/$name').readAsBytes());

  final roboto = FontLoader('Roboto');
  for (final name in const [
    'roboto-light.ttf',
    'roboto-regular.ttf',
    'roboto-italic.ttf',
    'roboto-medium.ttf',
    'roboto-bold.ttf',
    'roboto-black.ttf',
  ]) {
    roboto.addFont(read(name));
  }
  await roboto.load();

  final icons = FontLoader('MaterialIcons')
    ..addFont(read('materialicons-regular.otf'));
  await icons.load();
}

class _TolerantComparator extends LocalFileComparator {
  _TolerantComparator(super.testFile);

  @override
  Future<bool> compare(Uint8List imageBytes, Uri golden) async {
    final result = await GoldenFileComparator.compareLists(
      imageBytes,
      await getGoldenBytes(golden),
    );
    if (result.passed || result.diffPercent <= goldenTolerance) {
      result.dispose();
      return true;
    }
    final error = await generateFailureOutput(result, golden, basedir);
    result.dispose();
    throw FlutterError(error);
  }
}
