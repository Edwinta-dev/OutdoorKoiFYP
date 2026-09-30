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
      theme: ThemeData(useMaterial3: true, brightness: Brightness.dark),
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
          padding: const EdgeInsets.all(20),
          children: [
            const Text(
              'This build of the app is missing its server settings, so it '
              'cannot load pond readings or log interventions.',
              style: TextStyle(fontSize: 16),
            ),
            const SizedBox(height: 16),
            for (final problem in problems)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text('- $problem'),
              ),
            const SizedBox(height: 16),
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
