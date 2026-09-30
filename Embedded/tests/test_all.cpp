// Round-1 characterization tests: pin down the exact behavior of the logic
// extracted from the Embedded .ino sketches, before any refactor changes it.
// Build/run from the repo root:
//   g++ -std=c++17 -Wall -I Embedded/libraries/koi_sensing/src Embedded/tests/test_all.cpp -o build/fw_tests && ./build/fw_tests
#include <cstdio>
#include <cmath>
#include "koi_sensing.h"

static int g_pass = 0, g_fail = 0;
#define CHECK(cond) do { \
  if (cond) { g_pass++; } \
  else { g_fail++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } \
} while (0)
#define CHECK_NEAR(a, b, eps) CHECK(std::fabs((double)(a) - (double)(b)) <= (eps))

void test_median_filter() {
  int a[] = {5, 3, 1, 4, 2};
  CHECK(medianOfSamples(a, 5) == 3);

  int b[] = {7, 7, 7};
  CHECK(medianOfSamples(b, 3) == 7);

  int c[] = {1, 2, 3, 4};  // even n: matches FullSketch's buf[n/2] (upper-median), not textbook average
  CHECK(medianOfSamples(c, 4) == 3);

  int d[] = {9, 1, 8, 2, 7, 3, 6};
  CHECK(medianOfSamples(d, 7) == 6);
}

void test_tds_sensor() {
  CHECK_NEAR(tdsRawPpm(0, 25), 0.0, 1e-6);
  CHECK_NEAR(tdsRawPpm(1000, 25), 367.475, 0.01);        // hand-verified: vc=1 at 25C reference
  CHECK(tdsRawPpm(1000, 35) < tdsRawPpm(1000, 25));       // hotter -> compensation lowers vc -> lower ppm
  CHECK(tdsRawPpm(1000, 15) > tdsRawPpm(1000, 25));       // colder -> higher ppm
  CHECK_NEAR(tdsCalibratedPpm(1000, 25, 2.0), 2.0 * tdsRawPpm(1000, 25), 1e-6);
  CHECK_NEAR(tdsPpmToEcUs(100.0f), 200.0, 1e-6);
}

void test_ph_sensor() {
  PhBuffer buf7 = {'7', 7.00f, {7.060f, 7.020f, 7.000f, 6.990f, 6.980f, 6.970f}};
  CHECK_NEAR(bufferPhAt(buf7, 25.0f), 7.000, 1e-4);   // exactly on a table point
  CHECK_NEAR(bufferPhAt(buf7, 10.0f), 7.060, 1e-4);   // clamp low
  CHECK_NEAR(bufferPhAt(buf7, 40.0f), 6.970, 1e-4);   // clamp high
  CHECK_NEAR(bufferPhAt(buf7, 5.0f),  7.060, 1e-4);   // below table: still clamps low
  CHECK_NEAR(bufferPhAt(buf7, 22.5f), 7.010, 1e-4);   // midpoint of 20C/25C bracket

  // Default fit (FullSketch's factory constants) must reproduce pH7 at v7 and pH4 at 2032.44mV
  PhFit fit;
  CHECK_NEAR(mvToPh(fit, 1500.0f, 25.0f), 7.0, 1e-6);
  CHECK_NEAR(mvToPh(fit, 2032.44f, 25.0f), 4.0, 1e-4);

  // Fitting exactly those same two points should reproduce the factory-default fit
  float mv[2]  = {1500.0f, 2032.44f};
  float ref[2] = {7.0f, 4.0f};
  float tC[2]  = {25.0f, 25.0f};
  bool  ok[2]  = {true, true};
  PhFit refit = fitPhCalibration(mv, ref, tC, ok, 2);
  CHECK(refit.valid);
  CHECK_NEAR(refit.v7, 1500.0, 0.05);
  CHECK_NEAR(refit.slope25, fit.slope25, 1e-6);

  // Fewer than 2 valid points: rejected
  bool onlyOne[2] = {true, false};
  PhFit tooFew = fitPhCalibration(mv, ref, tC, onlyOne, 2);
  CHECK(!tooFew.valid);

  // Two near-identical mV points: rejected as "nearly identical voltages"
  float mvClose[2] = {1500.0f, 1500.5f};
  PhFit tooClose = fitPhCalibration(mvClose, ref, tC, ok, 2);
  CHECK(!tooClose.valid);
  CHECK(tooClose.rejectedNearIdentical);
}

void test_ds18b20_parse() {
  // Hand-traced CRC8({0x00},1) == 0x00 and CRC8({0x01},1) == 0x5E (Maxim poly 0x8C, reflected)
  uint8_t z = 0x00;
  CHECK(oneWireCrc8(&z, 1) == 0x00);
  uint8_t one = 0x01;
  CHECK(oneWireCrc8(&one, 1) == 0x5E);

  // Round-trip: build a scratchpad for 25.0625C (raw 0x0191), valid config, correct CRC
  uint8_t good[9] = {0x91, 0x01, 0x00, 0x00, 0x7F, 0xFF, 0xFF, 0x10, 0x00};
  good[8] = oneWireCrc8(good, 8);
  DsReading r = parseDs18b20Scratchpad(good);
  CHECK(r.error == DsError::None);
  CHECK_NEAR(r.tempC, 25.0625, 1e-4);

  // Corrupt the CRC byte: must be rejected, and MUST NOT hand back a plausible
  // temperature alongside the error - callers must not be able to accidentally
  // use tempC without checking `error` first (see sensor_bench.ino's
  // tempForCompensation() comment on why this matters: these are the sensor's
  // own "I am not working" signals, not real readings).
  uint8_t badCrc[9]; for (int i = 0; i < 9; i++) badCrc[i] = good[i];
  badCrc[8] ^= 0xFF;
  DsReading crcFail = parseDs18b20Scratchpad(badCrc);
  CHECK(crcFail.error == DsError::CrcFail);
  CHECK(std::isnan(crcFail.tempC));

  // Valid CRC but invalid config byte (low 5 bits must be 0x1F)
  uint8_t badCfg[9] = {0x91, 0x01, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0x10, 0x00};
  badCfg[8] = oneWireCrc8(badCfg, 8);
  DsReading cfgFail = parseDs18b20Scratchpad(badCfg);
  CHECK(cfgFail.error == DsError::InvalidConfig);
  CHECK(std::isnan(cfgFail.tempC));

  // Valid CRC and config, but the 85.0C power-on sentinel value - this is the
  // sensor's OWN error signal (conversion never actually ran). It must come
  // back as an error, never as a plausible-looking 85.0 reading.
  uint8_t powerOn[9] = {0x50, 0x05, 0x00, 0x00, 0x7F, 0xFF, 0xFF, 0x10, 0x00};
  powerOn[8] = oneWireCrc8(powerOn, 8);
  DsReading pwrOn = parseDs18b20Scratchpad(powerOn);
  CHECK(pwrOn.error == DsError::PowerOnValue);
  CHECK(std::isnan(pwrOn.tempC));
}

void test_tsl2591_lux() {
  CHECK(tslGainDecision(40000, 100, 36000, 1, 2) == TslGainAction::LowerGain);
  CHECK(tslGainDecision(40000, 100, 36000, 0, 2) == TslGainAction::Accept);   // can't lower further
  CHECK(tslGainDecision(50, 10, 36000, 0, 2) == TslGainAction::RaiseGain);
  CHECK(tslGainDecision(50, 10, 36000, 2, 2) == TslGainAction::Accept);      // can't raise further
  CHECK(tslGainDecision(5000, 1000, 36000, 1, 2) == TslGainAction::Accept);

  CHECK_NEAR(tslComputeLux(0, 0, 1.0f), 0.0, 1e-6);
  CHECK_NEAR(tslComputeLux(1000, 500, 1.0f), 1020.0, 0.5);   // hand-verified
  CHECK_NEAR(tslComputeLux(1000, 1000, 1.0f), 0.0, 1e-6);
}

void test_sleep_backoff() {
  CHECK(wifiFailSleepSeconds(0) == 300);
  CHECK(wifiFailSleepSeconds(1) == 300);
  CHECK(wifiFailSleepSeconds(2) == 900);
  CHECK(wifiFailSleepSeconds(3) == 1800);
  CHECK(wifiFailSleepSeconds(10) == 1800);
  CHECK(wifiFailSleepSeconds(5, 3600) == 3600);   // custom default

  CHECK(clampSleepSeconds(10, 30, 86400) == 30);
  CHECK(clampSleepSeconds(100000, 30, 86400) == 86400);
  CHECK(clampSleepSeconds(500, 30, 86400) == 500);
}

// ---- Round 2: new modules (not extractions - see adaptive_settle.h, wifi_retry.h) ----

void test_adaptive_settle() {
  SettleTracker t;
  settleTrackerAdd(t, 100.0f);
  settleTrackerAdd(t, 101.0f);
  settleTrackerAdd(t, 100.5f);
  CHECK_NEAR(settleTrackerSpread(t), 1.0, 1e-6);
  CHECK(settleTrackerIsSettled(t, 1.0f, 3));    // spread <= tolerance, enough samples
  CHECK(!settleTrackerIsSettled(t, 0.5f, 3));   // spread too wide for tighter tolerance
  CHECK(!settleTrackerIsSettled(t, 1.0f, 4));   // not enough samples yet

  SettleTracker t2;
  settleTrackerAdd(t2, 5.0f);
  settleTrackerAdd(t2, 5.0f);
  CHECK(!settleTrackerIsSettled(t2, 0.1f, 3));  // spread is 0 but count < minSamples

  settleTrackerReset(t2);
  CHECK(t2.count == 0);
  CHECK_NEAR(settleTrackerSpread(t2), 0.0, 1e-6);

  CHECK(!settleTrackerTimedOut(4999, 5000));
  CHECK(settleTrackerTimedOut(5000, 5000));
  CHECK(settleTrackerTimedOut(5001, 5000));
}

void test_wifi_retry() {
  CHECK(wifiShouldKeepWaitingThisAttempt(11999, 12000));
  CHECK(!wifiShouldKeepWaitingThisAttempt(12000, 12000));
  CHECK(!wifiShouldKeepWaitingThisAttempt(12001, 12000));

  CHECK(wifiShouldStartAnotherAttempt(0, 2));
  CHECK(wifiShouldStartAnotherAttempt(1, 2));
  CHECK(!wifiShouldStartAnotherAttempt(2, 2));
}

int main() {
  test_median_filter();
  test_tds_sensor();
  test_ph_sensor();
  test_ds18b20_parse();
  test_tsl2591_lux();
  test_sleep_backoff();
  test_adaptive_settle();
  test_wifi_retry();
  printf("\n%d passed, %d failed\n", g_pass, g_fail);
  return g_fail == 0 ? 0 : 1;
}
