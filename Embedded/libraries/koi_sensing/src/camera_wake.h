#pragma once
// Issue #38: the camera node's next wake, from the camera service reply.
// The reply carries sleep_sec, reason and next_at (local ISO time, e.g.
// "2026-10-01T14:00:00+08:00"); the HTTP Date header gives the server's
// clock ("Thu, 01 Oct 2026 06:00:00 GMT"). With both, the node sets its
// clock from Date and sleeps until next_at; otherwise it uses sleep_sec.
// Kept free of the Arduino core so the host tests can build it.
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "sleep_backoff.h"

// Shortest sleep the node accepts from the server. 15 min protects the
// battery from a misconfigured server; a test build may go down to 30 s.
const unsigned long CAMERA_MIN_SLEEP_DEFAULT_SEC = 900;
const unsigned long CAMERA_MIN_SLEEP_TEST_SEC    = 30;

// The configured lower clamp (CAMERA_MIN_SLEEP_SEC in secrets.h), raised to
// 15 min unless this is a test build, and never below 30 s.
constexpr unsigned long cameraMinSleepSeconds(unsigned long configured, bool testBuild) {
  return configured < CAMERA_MIN_SLEEP_TEST_SEC ? CAMERA_MIN_SLEEP_TEST_SEC
       : (!testBuild && configured < CAMERA_MIN_SLEEP_DEFAULT_SEC) ? CAMERA_MIN_SLEEP_DEFAULT_SEC
       : configured;
}

// Days since 1970-01-01 for a proleptic Gregorian date (H. Hinnant's algorithm).
inline int64_t daysFromCivil(int y, int m, int d) {
  y -= m <= 2;
  const int64_t era = (y >= 0 ? y : y - 399) / 400;
  const int64_t yoe = y - era * 400;
  const int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + doe - 719468;
}

inline bool epochFromCivil(int y, int mo, int d, int h, int mi, int s, int64_t& epoch) {
  if (y < 1970 || mo < 1 || mo > 12 || d < 1 || d > 31 || h < 0 || h > 23 ||
      mi < 0 || mi > 59 || s < 0 || s > 60) return false;
  epoch = daysFromCivil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s;
  return true;
}

// HTTP Date header, IMF-fixdate form: "Thu, 01 Oct 2026 06:00:00 GMT".
inline bool parseHttpDate(const char* text, int64_t& epoch) {
  static const char* const MONTHS = "JanFebMarAprMayJunJulAugSepOctNovDec";
  char wkday[4], mon[4], zone[4];
  int d, y, h, mi, s;
  if (!text || sscanf(text, "%3s, %d %3s %d %d:%d:%d %3s", wkday, &d, mon, &y, &h, &mi, &s, zone) != 8)
    return false;
  if (strcmp(zone, "GMT") != 0 || strlen(mon) != 3) return false;
  const char* hit = strstr(MONTHS, mon);
  if (!hit || (hit - MONTHS) % 3 != 0) return false;
  return epochFromCivil(y, (int)(hit - MONTHS) / 3 + 1, d, h, mi, s, epoch);
}

// ISO 8601 time with an offset: "2026-10-01T14:00:00+08:00", "...Z",
// optionally with fractional seconds (ignored).
inline bool parseIsoTime(const char* text, int64_t& epoch) {
  int y, mo, d, h, mi, s, n = 0;
  if (!text || sscanf(text, "%4d-%2d-%2dT%2d:%2d:%2d%n", &y, &mo, &d, &h, &mi, &s, &n) != 6 || n == 0)
    return false;
  const char* zone = text + n;
  if (*zone == '.') {
    zone++;
    while (*zone >= '0' && *zone <= '9') zone++;
  }
  int offsetSec = 0;
  if (zone[0] == 'Z' && zone[1] == '\0') {
    offsetSec = 0;
  } else if ((zone[0] == '+' || zone[0] == '-') && strlen(zone) == 6 && zone[3] == ':') {
    int oh, om;
    if (sscanf(zone + 1, "%2d:%2d", &oh, &om) != 2 || oh > 14 || om > 59) return false;
    offsetSec = (oh * 3600 + om * 60) * (zone[0] == '-' ? -1 : 1);
  } else {
    return false;
  }
  if (!epochFromCivil(y, mo, d, h, mi, s, epoch)) return false;
  epoch -= offsetSec;
  return true;
}

// Seconds to sleep: until next_at by the server's clock when both are
// known and next_at is ahead of it, else the reply's sleep_sec; clamped.
inline unsigned long cameraWakeSleepSeconds(bool haveServerNow, int64_t serverNow,
                                            bool haveNextAt, int64_t nextAt,
                                            unsigned long sleepSec,
                                            unsigned long lo, unsigned long hi) {
  unsigned long s = sleepSec;
  if (haveServerNow && haveNextAt && nextAt > serverNow) {
    const int64_t diff = nextAt - serverNow;
    s = diff > (int64_t)hi ? hi : (unsigned long)diff;
  }
  return clampSleepSeconds(s, lo, hi);
}
