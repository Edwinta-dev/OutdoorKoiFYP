import 'dart:developer' as developer;

/// Writes a diagnostic message to the platform log.
void log(Object? message) {
  developer.log('$message', name: 'sg.edu.ntu.outdoorkoi');
}
