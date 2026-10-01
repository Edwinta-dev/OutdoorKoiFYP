#pragma once
// NEW in Round 2. archive/pre-refactor/Embedded/TempSensor/TempSensor.ino connects with:
//   while (WiFi.status() != WL_CONNECTED) { delay(100); }
// which has no timeout - if the router is ever down, that node spins forever,
// radio on, and never reaches its (nonexistent, in that sketch) sleep cycle.
// esp32cam_pythonanywhere.ino already does this correctly (2 attempts x 12s,
// with backoff on repeated failure via sleep_backoff.h). This pulls that
// policy out as a reusable, host-testable decision so any future sketch
// (including a TempSensor rewrite) can reuse it instead of re-deriving its
// own ad hoc retry loop.

inline bool wifiShouldKeepWaitingThisAttempt(unsigned long elapsedMsThisAttempt,
                                              unsigned long perAttemptTimeoutMs) {
  return elapsedMsThisAttempt < perAttemptTimeoutMs;
}

inline bool wifiShouldStartAnotherAttempt(int attemptsSoFar, int maxAttempts) {
  return attemptsSoFar < maxAttempts;
}
