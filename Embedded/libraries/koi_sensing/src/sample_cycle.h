#pragma once
// Issue #17: the sensor node's cycle identity, sample clock, flags and
// retry queue for payload v2 (supabase/migrations/0027_sensor_payload_v2.sql).
// Kept free of the Arduino core so the host tests can build it.
//
// Identity. Every cold boot (power-on, reset, brownout, crash; anything but
// a deep-sleep wake) gets a new boot id: the NVS boot counter in the high
// 32 bits and a hardware random number in the low 32. If NVS is erased or
// rolled back the counter can repeat, but the random half makes a repeat of
// the whole id a 1 in 2^32 event. cycle_seq counts wake cycles within that
// boot, in RTC memory, from 1. The database's retry key is
// (device, boot_id, cycle_seq, sensor_type).
//
// Sample clock. The node's clock is set by SNTP and keeps running through
// deep sleep, but not through power loss or reset. A cycle's sample time is
// sent only when the clock was set: either already at the start of the
// cycle, or by SNTP later in the same wake, in which case the sample time
// is worked back from millis(), which does not stop between the two. A
// cycle with no trustworthy time is sent without sampled_at and with
// SAMPLE_FLAG_TIME_UNSYNCED; the database then times it by receipt.
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// SensorData.flags bits (0027).
const uint8_t SAMPLE_FLAG_TIME_UNSYNCED   = 1;  // no sampled_at: clock not set
const uint8_t SAMPLE_FLAG_TEMP_FALLBACK   = 2;  // pH/TDS compensated at 25 C (temp read failed)
const uint8_t SAMPLE_FLAG_PH_UNCALIBRATED = 4;  // fewer than two pH buffers stored
const uint8_t SAMPLE_FLAG_LUX_SATURATED   = 8;  // light sensor saturated at the lowest gain

// A clock reading before 2026-01-01T00:00:00Z means SNTP has not set it
// (an unset ESP32 clock starts at 1970).
const int64_t CLOCK_SET_AFTER_EPOCH_S = 1767225600LL;

inline bool clockLooksSet(int64_t epochSeconds) { return epochSeconds >= CLOCK_SET_AFTER_EPOCH_S; }

inline uint64_t makeBootId(uint32_t nvsBootCount, uint32_t random32) {
  return ((uint64_t)nvsBootCount << 32) | (uint64_t)random32;
}

// 16 lowercase hex characters plus the terminator.
inline void formatBootId(uint64_t bootId, char out[17]) {
  snprintf(out, 17, "%08lx%08lx", (unsigned long)(bootId >> 32), (unsigned long)(bootId & 0xFFFFFFFFUL));
}

// The epoch milliseconds of a sample taken at sampleMillis, given the clock
// now (nowEpochMs) and millis() now. Unsigned subtraction survives the
// millis() wrap.
inline int64_t sampleEpochMs(int64_t nowEpochMs, uint32_t nowMillis, uint32_t sampleMillis) {
  return nowEpochMs - (int64_t)(uint32_t)(nowMillis - sampleMillis);
}

// UTC ISO 8601 with milliseconds and Z, e.g. 2026-10-11T08:00:00.123Z.
// Needs 25 bytes. False (and an empty string) for a time before 1970.
inline bool formatUtcIso8601(int64_t epochMs, char* out, size_t n) {
  if (n > 0) out[0] = '\0';
  if (epochMs < 0 || n < 25) return false;
  int64_t secs = epochMs / 1000;
  int ms = (int)(epochMs % 1000);
  int64_t days = secs / 86400;
  int rem = (int)(secs % 86400);
  // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
  int64_t z = days + 719468;
  int64_t era = z / 146097;
  int64_t doe = z - era * 146097;
  int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  int64_t y = yoe + era * 400;
  int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  int64_t mp = (5 * doy + 2) / 153;
  int d = (int)(doy - (153 * mp + 2) / 5 + 1);
  int m = (int)(mp < 10 ? mp + 3 : mp - 9);
  if (m <= 2) y++;
  snprintf(out, n, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
           (int)y, m, d, rem / 3600, (rem / 60) % 60, rem % 60, ms);
  return true;
}

// ESP-IDF esp_reset_reason_t -> the name the database accepts (the same
// spelling as Backend/koi/models/device_health.py RESET_REASONS).
inline const char* resetReasonName(int code) {
  static const char* const NAMES[] = {
    "unknown", "power_on", "external_pin", "software", "panic", "interrupt_watchdog",
    "task_watchdog", "other_watchdog", "deep_sleep_wake", "brownout", "sdio", "usb",
    "jtag", "efuse", "power_glitch", "cpu_lockup"};
  if (code < 0 || code >= (int)(sizeof(NAMES) / sizeof(NAMES[0]))) return "unknown";
  return NAMES[code];
}

// After a failed upload: keep the queued cycles for the next wake only when
// a later try can succeed (no connection, timeout, rate limit, server
// error). A refused token, a refused batch or a missing function will not
// fix itself, so those cycles are dropped (the serial log says why).
inline bool ingestShouldRetry(int httpStatus) {
  if (httpStatus <= 0) return true;
  if (httpStatus == 408 || httpStatus == 429) return true;
  return httpStatus >= 500;
}

// ---------------------------------------------------------------------
// The cycles waiting to be uploaded. Plain data, so it can live in RTC
// memory (RTC_DATA_ATTR) and survive deep sleep; it is cleared on a cold
// boot. When full, pendingPush drops the oldest; the sensor node first moves
// it to NVS (store_forward.h, issue #44).
// ---------------------------------------------------------------------
enum class Channel : uint8_t { Temp = 0, Tds = 1, Ph = 2, Lux = 3 };

inline const char* channelSensorType(Channel c) {
  switch (c) {
    case Channel::Temp: return "temp";
    case Channel::Tds:  return "TDS";
    case Channel::Ph:   return "pH";
    case Channel::Lux:  return "LUX";
  }
  return "";
}

const int MAX_READINGS_PER_CYCLE = 4;
const int PENDING_CYCLE_CAPACITY = 24;  // issue #44: at least 24; 80 bytes each, under 2 KB of the 8 KB RTC slow memory

struct PendingReading {
  Channel channel;
  float   value;
  uint8_t flags;
};

struct PendingCycle {
  uint64_t bootId;
  uint32_t cycleSeq;
  int64_t  sampledEpochMs;   // < 0: sample time unknown
  int32_t  batteryMv;        // < 0: not measured
  uint8_t  resetReason;      // esp_reset_reason_t of the boot
  uint8_t  nReadings;
  PendingReading readings[MAX_READINGS_PER_CYCLE];
};

struct PendingCycles {
  PendingCycle items[PENDING_CYCLE_CAPACITY];
  uint8_t head;    // index of the oldest
  uint8_t count;
};
static_assert(sizeof(PendingCycles) <= 2048, "PendingCycles must stay small enough for RTC slow memory");

inline void pendingClear(PendingCycles& q) { q.head = 0; q.count = 0; }

// Adds a cycle; returns true when the oldest had to be dropped.
inline bool pendingPush(PendingCycles& q, const PendingCycle& c) {
  bool dropped = false;
  if (q.count >= PENDING_CYCLE_CAPACITY) {
    q.head = (uint8_t)((q.head + 1) % PENDING_CYCLE_CAPACITY);
    q.count--;
    dropped = true;
  }
  q.items[(q.head + q.count) % PENDING_CYCLE_CAPACITY] = c;
  q.count++;
  return dropped;
}

// i-th oldest, 0 <= i < count.
inline const PendingCycle& pendingAt(const PendingCycles& q, int i) {
  return q.items[(q.head + i) % PENDING_CYCLE_CAPACITY];
}

inline bool pendingAddReading(PendingCycle& c, Channel ch, float value, uint8_t flags) {
  if (c.nReadings >= MAX_READINGS_PER_CYCLE) return false;
  c.readings[c.nReadings].channel = ch;
  c.readings[c.nReadings].value = value;
  c.readings[c.nReadings].flags = flags;
  c.nReadings++;
  return true;
}
