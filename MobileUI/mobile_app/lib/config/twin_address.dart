// lib/config/twin_address.dart
//
// The digital twin runs on the owner's laptop, whose IP changes with each
// hotspot connection. DIGITAL_TWIN_BASE_URL from env/*.json is the default;
// Settings can override it at run time, stored on the device, so a new IP
// needs no rebuild. Every DigitalTwinApi request reads [TwinAddress.current].

import 'package:shared_preferences/shared_preferences.dart';

import 'app_config.dart';

class TwinAddress {
  TwinAddress._();

  static const prefsKey = 'digital_twin_base_url_override';
  static const defaultPort = 8080;

  static String? _override;

  /// The override from Settings, or null when the build's value is used.
  static String? get override => _override;

  /// The base URL requests go to, without a trailing slash.
  static String get current =>
      (_override ?? AppConfig.environment.digitalTwinBaseUrl).replaceFirst(
        RegExp(r'/+$'),
        '',
      );

  /// Reads the stored override; call once at start-up.
  static Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getString(prefsKey);
    _override = stored == null ? null : normalize(stored);
  }

  /// Stores [input] as the override, or clears it when [input] is empty.
  /// Throws [FormatException] for an address that is not usable.
  static Future<void> save(String input) async {
    final prefs = await SharedPreferences.getInstance();
    if (input.trim().isEmpty) {
      _override = null;
      await prefs.remove(prefsKey);
      return;
    }
    final url = normalize(input);
    if (url == null) {
      throw FormatException('Not an address: $input');
    }
    _override = url;
    await prefs.setString(prefsKey, url);
  }

  /// Accepts "192.168.1.5", "192.168.1.5:8080" or a full http(s) URL and
  /// returns a full URL (http and port 8080 when omitted), or null.
  static String? normalize(String input) {
    var text = input.trim();
    if (text.isEmpty) return null;
    if (!text.contains('://')) text = 'http://$text';
    final uri = Uri.tryParse(text);
    if (uri == null ||
        (uri.scheme != 'http' && uri.scheme != 'https') ||
        uri.host.isEmpty) {
      return null;
    }
    final withPort = uri.hasPort || uri.scheme == 'https'
        ? uri
        : uri.replace(port: defaultPort);
    return withPort.toString().replaceFirst(RegExp(r'/+$'), '');
  }

  /// For tests.
  static void debugSet(String? url) => _override = url;
}
