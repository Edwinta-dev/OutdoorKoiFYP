import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';

/// Shown instead of the app when the build is missing its settings (see
/// lib/config/app_config.dart), so a misconfigured build says so rather
/// than showing an empty dashboard.
class ConfigErrorApp extends StatelessWidget {
  const ConfigErrorApp({super.key, required this.problems});

  final List<String> problems;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Koi Monitor',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.system,
      home: ConfigErrorScreen(problems: problems),
    );
  }
}

class ConfigErrorScreen extends StatelessWidget {
  const ConfigErrorScreen({super.key, required this.problems});

  final List<String> problems;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Configuration error')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AppSpace.xl),
          children: [
            Text(
              'This build of the app is missing its server settings, so it '
              'cannot load pond readings or log interventions.',
              style: AppType.style(fontSize: AppType.body),
            ),
            const SizedBox(height: AppSpace.lg),
            for (final problem in problems)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpace.sm),
                child: Text('- $problem'),
              ),
            const SizedBox(height: AppSpace.lg),
            const Text(
              'To fix: copy env/dev.json.example to env/dev.json, fill in '
              'every value, then rebuild with\n'
              'flutter run --dart-define-from-file=env/dev.json',
            ),
          ],
        ),
      ),
    );
  }
}
