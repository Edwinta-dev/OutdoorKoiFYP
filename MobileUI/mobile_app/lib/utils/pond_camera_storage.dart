// lib/utils/pond_camera_storage.dart
//
// Resolves the pond camera's latest frame straight from Supabase.
//
// The rating card needs three things from the newest ESP32-CAM frame: the
// image itself, and the row's `id` + `green_ratio` so a rating can be
// pinned to the exact frame it describes. All three come from one
// imageTable query - no round-trip through the Flask analysis service,
// which has nothing to add here and would only mean a blank photo
// whenever that process is down.
//
// URL handling: the row already carries an `imageURL` written by
// camera.py, but this rebuilds it locally from the object path rather
// than trusting the stored string. getPublicUrl() is pure string
// construction against the client's configured project - no network call -
// so rebuilding costs nothing and keeps the URL correct if the Supabase
// project or bucket ever changes underneath rows that were written
// earlier. The stored value is the fallback.

import 'package:supabase_flutter/supabase_flutter.dart';

import '../config/app_config.dart';

/// One analysed camera frame.
class PondCameraFrame {
  final int? id;
  final String? imageUrl;
  final double? greenRatio;
  final DateTime? capturedAt;

  /// hsvEngine's own label for this frame - "base", "dynamic" or
  /// "obstruction". Worth surfacing: if the pipeline already suspects the
  /// view is blocked, say so before the user rates a leaf as severe algae.
  final String? state;

  const PondCameraFrame({
    required this.id,
    required this.imageUrl,
    required this.greenRatio,
    required this.capturedAt,
    required this.state,
  });

  factory PondCameraFrame.fromJson(Map<String, dynamic> json) =>
      PondCameraFrame(
        id: (json['id'] as num?)?.toInt(),
        imageUrl: json['imageURL'] as String?,
        greenRatio: (json['green_ratio'] as num?)?.toDouble(),
        capturedAt: DateTime.tryParse('${json['created_at']}'),
        state: PondCameraStorage._stateLabel(json['current_state']),
      );

  bool get isFlaggedObstructed => state == 'obstruction';
}

class PondCameraStorage {
  /// POND_IMAGE_BUCKET from env/*.json; must match POND_IMAGE_BUCKET in
  /// Backend/Camera/.env.
  static final String bucketName = AppConfig.environment.pondImageBucket;

  static const String tableName = 'imageTable';

  /// Newest frame for [userId], defaulting to the logged-in user.
  ///
  /// Returns a null frame with a human-readable reason rather than just
  /// null, so the card can distinguish "camera hasn't reported yet" from
  /// "couldn't reach Supabase" - different problems, different message.
  static Future<({PondCameraFrame? frame, String? error})> fetchLatestFrame({
    required int userId,
    SupabaseClient? client,
  }) async {
    final uid = userId;
    if (uid == 0) {
      return (frame: null, error: 'No user id stored on this device.');
    }

    try {
      // NOTE the column is "user_ID" here - imageTable's spelling.
      // pondInterventions uses "userID" and daily_sensor_averages uses
      // "userid"; all three coexist in this schema.
      final rows = await (client ?? Supabase.instance.client)
          .from(tableName)
          .select('id, created_at, green_ratio, current_state, "imageURL"')
          .eq('user_ID', uid)
          .order('created_at', ascending: false)
          .limit(1);

      final list = (rows as List).cast<Map<String, dynamic>>();
      if (list.isEmpty) {
        return (frame: null, error: 'The pond camera has not reported yet.');
      }
      return (frame: _frameFromRow(list.first, client), error: null);
    } catch (e) {
      return (frame: null, error: 'Could not read camera history: $e');
    }
  }

  // ------------------------------------------------------------------

  static PondCameraFrame _frameFromRow(
    Map<String, dynamic> row,
    SupabaseClient? client,
  ) {
    final stored = _clean(row['imageURL']?.toString());
    return PondCameraFrame(
      id: (row['id'] as num?)?.toInt(),
      imageUrl: _rebuildUrl(stored, client) ?? stored,
      greenRatio: (row['green_ratio'] as num?)?.toDouble(),
      capturedAt: DateTime.tryParse(row['created_at']?.toString() ?? ''),
      state: _stateLabel(row['current_state']),
    );
  }

  /// Pulls the object path back out of a stored public URL and rebuilds
  /// it against the live Supabase client.
  ///
  /// A stored URL looks like:
  ///   `https://<proj>.supabase.co/storage/v1/object/public/<bucket>/<uid>/<ts>_photo.jpg`
  /// Everything after `/public/<bucket>/` is the object path. Returns null
  /// if the URL doesn't match that shape, in which case the caller keeps
  /// the stored value as-is.
  static String? _rebuildUrl(String? storedUrl, SupabaseClient? client) {
    if (storedUrl == null || storedUrl.isEmpty) return null;
    final marker = '/public/$bucketName/';
    final i = storedUrl.indexOf(marker);
    if (i < 0) return null;

    final objectPath = storedUrl.substring(i + marker.length);
    if (objectPath.isEmpty) return null;

    try {
      return _clean(
        (client ?? Supabase.instance.client).storage
            .from(bucketName)
            .getPublicUrl(objectPath),
      );
    } catch (_) {
      return null;
    }
  }

  /// supabase-py's get_public_url() has historically appended a bare "?".
  /// Harmless, but it makes cache keys inconsistent, so strip it.
  static String? _clean(String? url) {
    if (url == null) return null;
    var u = url.trim();
    while (u.endsWith('?') || u.endsWith('&')) {
      u = u.substring(0, u.length - 1);
    }
    return u.isEmpty ? null : u;
  }

  /// `current_state` is stored as a two-element JSON array like
  /// ["base", 0.1235] - element 0 is the state label. Supabase may return
  /// it already decoded or as a raw string depending on the column type.
  static String? _stateLabel(dynamic currentState) {
    if (currentState is List && currentState.isNotEmpty) {
      return currentState.first?.toString();
    }
    if (currentState is String && currentState.trim().isNotEmpty) {
      final t = currentState.trim();
      if (!t.startsWith('[')) return t;
      final first = t.substring(1).split(',').first.trim();
      return first.replaceAll('"', '').replaceAll("'", '');
    }
    return null;
  }
}
