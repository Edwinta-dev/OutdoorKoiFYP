#pragma once
// Issue #42: the sensor node's supply voltage from a GPIO-switched resistor
// divider, and the low-battery flag. Kept free of the Arduino core so the
// host tests can build it.
//
// The divider: battery + -> r_top -> ADC pin -> r_bottom -> ground, switched
// on only for the reading so it does not drain the battery between cycles.
// The ADC reads the voltage across r_bottom; the battery is that voltage
// times (r_top + r_bottom) / r_bottom.
#include <math.h>
#include <stdint.h>
#include "sample_cycle.h"

// ingest_readings refuses a battery_mv outside 0..20000 (0027), and refuses
// the whole row with it, so a value outside that range is not sent at all.
const int32_t BATTERY_MV_MAX = 20000;

// SensorData.flags bit for a cycle read on a low supply (0028).
const uint8_t SAMPLE_FLAG_BATTERY_LOW = 16;

// Battery millivolts from the millivolts measured at the divider's midpoint,
// rounded to the nearest millivolt. -1 (not measured) for a negative, NaN or
// infinite reading, a negative r_top, an r_bottom that is not positive, or a
// result above BATTERY_MV_MAX.
inline int32_t batteryMvFromAdc(float mv, float r_top, float r_bottom) {
  if (!(mv >= 0.0f) || !(r_top >= 0.0f) || !(r_bottom > 0.0f)) return -1;
  float battery = mv * (r_top + r_bottom) / r_bottom;
  if (!(battery < (float)BATTERY_MV_MAX + 0.5f)) return -1;
  return (int32_t)lroundf(battery);
}

// True when a measured battery voltage is below lowMv. Not measured (< 0)
// and 0 (no divider connected: a running node cannot be at 0 V) are not low.
inline bool batteryIsLow(int32_t batteryMv, int32_t lowMv) {
  return batteryMv > 0 && batteryMv < lowMv;
}

// The flags of one uploaded row: the reading's own flags, time_unsynced when
// the cycle has no sample time, and battery_low when the cycle's battery
// reading is below lowMv.
inline uint8_t uploadRowFlags(uint8_t readingFlags, bool timed, int32_t batteryMv, int32_t lowMv) {
  uint8_t flags = readingFlags;
  if (!timed) flags |= SAMPLE_FLAG_TIME_UNSYNCED;
  if (batteryIsLow(batteryMv, lowMv)) flags |= SAMPLE_FLAG_BATTERY_LOW;
  return flags;
}
