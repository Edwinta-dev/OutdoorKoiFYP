// lib/utils/event_id.dart

import 'dart:math';

final Random _random = Random.secure();

/// A random (version 4) UUID for one logged pond event. The app writes it
/// to pondInterventions.event_id and posts the same value to the twin,
/// which applies each event_id once (backend issue #19).
String newEventId() {
  final bytes = List<int>.generate(16, (_) => _random.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}
