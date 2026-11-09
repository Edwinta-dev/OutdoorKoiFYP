import 'package:mobile_app/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/providers.dart';
import '../config/twin_address.dart';
import 'package:http/http.dart' as http;

import 'onboarding_screen.dart';
import '../utils/app_log.dart';

class SettingsView extends ConsumerStatefulWidget {
  const SettingsView({super.key});
  @override
  ConsumerState<SettingsView> createState() => _SettingsViewState();
}

class _SettingsViewState extends ConsumerState<SettingsView> {
  @override
  Widget build(BuildContext context) {
    final displayMode = ref.watch(displayModeProvider);
    final detailed = displayMode.value == DisplayMode.detailed;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: SingleChildScrollView(
        // ✅ 2. Change Row to Column to stack elements vertically
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 1. The Graph Section
            const Placeholder(
              fallbackHeight: 200,
            ), // Temporary stand-in for your graph

            const SizedBox(
              height: AppSpace.lg,
            ), // Adds clean spacing between elements
            SwitchListTile(
              key: const ValueKey('displayModeSwitch'),
              title: const Text('Detailed view'),
              subtitle: Text(
                detailed
                    ? 'The dashboard shows model values, confidence, '
                          'evidence and charts.'
                    : 'The dashboard shows pond status, what to do today '
                          'and plain explanations.',
              ),
              value: detailed,
              onChanged: displayMode.hasValue ? _setDetailed : null,
            ),
            ListTile(
              key: const ValueKey('twinAddressTile'),
              leading: Icon(Icons.lan, color: AppColors.of(context).water),
              title: const Text('Digital twin address'),
              subtitle: Text(
                TwinAddress.override == null
                    ? '${TwinAddress.current} (build default)'
                    : TwinAddress.current,
              ),
              trailing: const Icon(Icons.edit, size: 16),
              onTap: _editTwinAddress,
            ),
            ListTile(
              key: const ValueKey('pondIdTile'),
              leading: Icon(Icons.tag, color: AppColors.of(context).water),
              title: const Text('Pond ID'),
              subtitle: Text(
                ref.watch(localProfileProvider).asData?.value.pondId
                        ?.toString() ??
                    'Not set',
              ),
              trailing: const Icon(Icons.edit, size: 16),
              onTap: _editPondId,
            ),
            const SizedBox(height: AppSpace.lg),
            // 2. The Menu Section Title
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpace.lg),
              child: Text(
                'Quick Actions',
                style: AppType.style(
                  fontSize: AppType.title,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            // 3. The Menu Items
            ListTile(
              leading: Icon(
                Icons.water_drop,
                color: AppColors.of(context).water,
              ),
              title: const Text('Water Quality Log'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 16),
              onTap: () {
                /* Navigate to log page */
              },
            ),
            ListTile(
              leading: Icon(Icons.scale, color: AppColors.of(context).water),
              title: const Text('Biomass Tracker'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 16),
              onTap: () {
                /* Navigate to biomass page */
              },
            ),

            TextButton(
              style: ButtonStyle(
                foregroundColor: WidgetStateProperty.all<Color>(
                  AppColors.of(context).danger,
                ),
              ),
              onPressed:
                  deletePondData, // Implement this function to handle data deletion

              child: Text(
                'Delete Pond Data',
                style: AppType.style(fontSize: AppType.body),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _editTwinAddress() async {
    final controller = TextEditingController(text: TwinAddress.current);
    String? error;
    String? status;
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialog) => AlertDialog(
          title: const Text('Digital twin address'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              TextField(
                key: const ValueKey('twinAddressField'),
                controller: controller,
                keyboardType: TextInputType.url,
                decoration: InputDecoration(
                  hintText: '192.168.1.5 or http://host:8080',
                  helperText:
                      'Port 8080 is added when left out. '
                      'Empty uses the build default.',
                  errorText: error,
                ),
              ),
              if (status != null)
                Padding(
                  padding: const EdgeInsets.only(top: AppSpace.sm),
                  child: Text(status!),
                ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () async {
                final url = TwinAddress.normalize(controller.text);
                if (url == null) {
                  setDialog(() => error = 'Not an address');
                  return;
                }
                setDialog(() {
                  error = null;
                  status = 'Testing...';
                });
                String result;
                try {
                  final r = await http
                      .get(Uri.parse('$url/health'))
                      .timeout(const Duration(seconds: 5));
                  result = r.statusCode == 200
                      ? 'Reachable'
                      : 'Answered ${r.statusCode}';
                } catch (_) {
                  result = 'Not reachable from this phone';
                }
                setDialog(() => status = result);
              },
              child: const Text('Test'),
            ),
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('Cancel'),
            ),
            FilledButton(
              key: const ValueKey('twinAddressSave'),
              onPressed: () async {
                try {
                  await TwinAddress.save(controller.text);
                } on FormatException {
                  setDialog(() => error = 'Not an address');
                  return;
                }
                if (dialogContext.mounted) Navigator.of(dialogContext).pop();
              },
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );
    controller.dispose();
    if (mounted) setState(() {});
  }

  /// Points the app at another pond: saves userID and invalidates every
  /// pond-keyed provider by refreshing the local profile.
  Future<void> _editPondId() async {
    final current = ref.read(localProfileProvider).asData?.value.pondId;
    final controller = TextEditingController(text: current?.toString() ?? '');
    String? error;
    final saved = await showDialog<int>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialog) => AlertDialog(
          title: const Text('Pond ID'),
          content: TextField(
            key: const ValueKey('pondIdField'),
            controller: controller,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(errorText: error),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child: const Text('Cancel'),
            ),
            FilledButton(
              key: const ValueKey('pondIdSave'),
              onPressed: () {
                final id = int.tryParse(controller.text.trim());
                if (id == null || id <= 0) {
                  setDialog(() => error = 'Enter a positive whole number');
                  return;
                }
                Navigator.of(dialogContext).pop(id);
              },
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );
    controller.dispose();
    if (saved == null) return;
    await ref.read(localProfileRepositoryProvider).save({'userID': saved});
    ref.invalidate(localProfileProvider);
    if (mounted) setState(() {});
  }

  Future<void> _setDetailed(bool detailed) async {
    try {
      await ref
          .read(displayModeProvider.notifier)
          .set(detailed ? DisplayMode.detailed : DisplayMode.simple);
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Could not save the display setting. Try again.'),
        ),
      );
    }
  }

  void deletePondData() async {
    // Implement the logic to delete pond data here
    // This could involve clearing shared preferences, database entries, etc.
    final prefs = await ref.read(localProfileRepositoryProvider).load();
    final String userid = prefs.pondId?.toString() ?? '0';
    final int userIDInt = int.parse(userid);
    await ref.read(pondProfileRepositoryProvider).deletePond(userIDInt);
    await ref.read(localProfileRepositoryProvider).save({'isOnboarded': false});
    ref.invalidate(localProfileProvider);
    log('Pond data deleted'); // Placeholder for actual deletion logic
    if (!mounted) return;
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (context) => const OnboardingScreen()),
    );
  }
}
