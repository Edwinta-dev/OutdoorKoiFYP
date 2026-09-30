/*
 * OutdoorKoi sensor node — ESP32 DevKitC (WROOM-32) — REFACTORED
 * Round-3 result of the efficiency pass over archive/pre-refactor/Embedded/FullSketch/FullSketch.ino.
 * Behavior, wiring, pin map, and the service-mode command set are UNCHANGED
 * from the original — see that file's header comment for hardware/timing
 * rationale. What changed:
 *
 *   1. TDS curve, pH buffer interpolation/fit, DS18B20 scratchpad validation,
 *      and TSL2591 auto-gain/lux math now live in Embedded/libraries/koi_sensing/src/,
 *      as plain functions with their own compiled+passing unit tests (see
 *      Embedded/tests/test_all.cpp). The .ino used to carry this logic
 *      inline, duplicated in places (the TDS formula also lived, copy-pasted,
 *      in TempSensor.ino).
 *   2. measurePhMv() no longer always waits the fixed PH_SETTLE_MS (5000 ms).
 *      It now polls every 200 ms and accepts the reading once 3 consecutive
 *      samples agree within PH_SETTLE_TOLERANCE_MV, falling back to the same
 *      5000 ms ceiling if it never settles - so the worst case is identical
 *      to before, but the typical case should be faster, which matters
 *      because pH settling is the single largest time/power cost in the
 *      15 s wake cycle (see the Round-3 summary for the full duty-cycle math).
 *      NEEDS HARDWARE VALIDATION: PH_SETTLE_TOLERANCE_MV is a starting guess,
 *      not measured against the real probe - tune it with "settle ph" data.
 *
 * This file has not been compiled against the ESP32 Arduino core (no board
 * toolchain in this environment) - only the extracted koi_sensing logic has been
 * compiled and unit-tested on the host. Flash and re-run "sample" / "show" /
 * "settle ph" against the real sensors before trusting this over the
 * original FullSketch.ino.
 */

#include <Arduino.h>
#include <Wire.h>
#include <OneWire.h>
#include <Preferences.h>
#include "driver/gpio.h"
#include "esp_sleep.h"

#include <koi_sensing.h>

// =====================================================================
// Pins (unchanged)
// =====================================================================
const int TDS_PWR_PIN = 25;  const int TDS_ADC_PIN = 34;
const int PH_PWR_PIN  = 4;   const int PH_ADC_PIN  = 35;
const int TSL_PWR_PIN = 26;  const int TSL_SDA_PIN = 21;  const int TSL_SCL_PIN = 22;
const int DS_PWR_PIN  = 27;  const int DS_DATA_PIN = 23;
const int GY_PWR_PIN  = 32;  const int GY_SDA_PIN  = 18;  const int GY_SCL_PIN  = 19;

const int POWER_PINS[]  = {TDS_PWR_PIN, PH_PWR_PIN, TSL_PWR_PIN, DS_PWR_PIN, GY_PWR_PIN};
const int N_POWER_PINS  = sizeof(POWER_PINS) / sizeof(POWER_PINS[0]);

// =====================================================================
// Timing
// =====================================================================
const uint64_t SLEEP_S            = 15;
const uint32_t SERVICE_WINDOW_MS  = 3000;

const uint32_t TDS_SETTLE_MS      = 2000;
const uint32_t PH_SETTLE_MS       = 5000;   // now a ceiling, not a fixed wait - see measurePhMv()
const uint32_t PH_SETTLE_POLL_MS  = 200;
const float    PH_SETTLE_TOLERANCE_MV = 3.0f;  // NEEDS HARDWARE VALIDATION (see file header)
const int      PH_SETTLE_MIN_SAMPLES  = 3;
const uint32_t TSL_POWER_UP_MS    = 50;
const uint32_t GY_POWER_UP_MS     = 1500;
const uint32_t DS_POWER_UP_MS     = 1000;
const uint32_t DS_CONVERSION_MS   = 800;
const int      DS_MAX_TRIES       = 3;

const int N_SAMPLES     = 31;
const int SAMPLE_GAP_MS = 20;

RTC_DATA_ATTR float    lastGoodTempC = NAN;
RTC_DATA_ATTR uint32_t cycleCount    = 0;

float    r_tdsMv = NAN, r_tdsPpm = NAN, r_ecUs = NAN;
bool     r_tslOk = false, r_tslSat = false;
float    r_lux = NAN;
uint16_t r_tslCh0 = 0, r_tslCh1 = 0;
int      r_tslGainIdx = 0;
bool     r_gyOk = false;
uint16_t r_gyR = 0, r_gyG = 0, r_gyB = 0, r_gyC = 0, r_gyLux = 0, r_gyCT = 0;
float    r_tempC = NAN;
bool     r_tempFallback = false;
float    r_phMv = NAN, r_ph = NAN;

Preferences prefs;
bool serviceMode = false;

// =====================================================================
// Shared helpers (unchanged)
// =====================================================================
void allPowerOff() {
  for (int i = 0; i < N_POWER_PINS; i++) digitalWrite(POWER_PINS[i], LOW);
}

float readMedianMv(int adcPin) {
  int buf[N_SAMPLES];
  for (int i = 0; i < N_SAMPLES; i++) {
    buf[i] = analogReadMilliVolts(adcPin);
    delay(SAMPLE_GAP_MS);
  }
  return medianOfSamples(buf, N_SAMPLES);   // was: inline insertion sort, now median_filter.h
}

void settleLog(int pwrPin, int adcPin) {
  allPowerOff();
  Serial.println("ms,mV");
  digitalWrite(pwrPin, HIGH);
  uint32_t t0 = millis();
  while (millis() - t0 < 30000) {
    long sum = 0;
    for (int i = 0; i < 8; i++) sum += analogReadMilliVolts(adcPin);
    Serial.printf("%lu,%.1f\n", millis() - t0, sum / 8.0);
    delay(100);
  }
  digitalWrite(pwrPin, LOW);
  Serial.println("Done. Pick the settle time where the curve goes flat, plus some margin.");
}

// =====================================================================
// 1. TDS  (formula now in tds_sensor.h - was duplicated with TempSensor.ino)
// =====================================================================
float tdsK = 1.0;

float measureTdsMv() {
  digitalWrite(TDS_PWR_PIN, HIGH);
  delay(TDS_SETTLE_MS);
  float mv = readMedianMv(TDS_ADC_PIN);
  digitalWrite(TDS_PWR_PIN, LOW);
  return mv;
}

void loadTdsK() {
  prefs.begin("tds", true);
  tdsK = prefs.getFloat("k", 1.0);
  prefs.end();
}

void saveTdsK() {
  prefs.begin("tds", false);
  prefs.putFloat("k", tdsK);
  prefs.end();
}

// =====================================================================
// 2. TSL2591 (auto-gain decision + lux formula now in tsl2591_lux.h)
// =====================================================================
const uint8_t  TSL_ADDR = 0x29;
const uint8_t  TSL_CMD  = 0xA0;
const uint8_t  TSL_GAIN_BITS[] = {0x00, 0x10, 0x20};
const float    TSL_GAIN_X[]    = {1.0, 25.0, 428.0};
const uint16_t TSL_SAT_COUNT   = 36000;

bool tslWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(TSL_ADDR);
  Wire.write(TSL_CMD | reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool tslRead(uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire.beginTransmission(TSL_ADDR);
  Wire.write(TSL_CMD | reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)TSL_ADDR, n) != n) return false;
  for (int i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

void readTsl() {
  r_tslOk = false; r_tslSat = false; r_lux = NAN;

  digitalWrite(TSL_PWR_PIN, HIGH);
  delay(TSL_POWER_UP_MS);
  Wire.begin(TSL_SDA_PIN, TSL_SCL_PIN, 100000);

  uint8_t id = 0;
  bool ok = tslRead(0x12, &id, 1) && id == 0x50;
  if (!ok) Serial.printf("  TSL2591: not found (ID read 0x%02X)\n", id);

  int g = 1;
  for (int attempt = 0; ok && attempt < 4; attempt++) {
    ok = tslWrite(0x00, 0x00) && tslWrite(0x01, TSL_GAIN_BITS[g]) && tslWrite(0x00, 0x03);
    if (!ok) break;
    delay(250);
    uint8_t d[4];
    ok = tslRead(0x14, d, 4);
    if (!ok) break;
    uint16_t ch0 = d[0] | (d[1] << 8);
    uint16_t ch1 = d[2] | (d[3] << 8);

    TslGainAction action = tslGainDecision(ch0, ch1, TSL_SAT_COUNT, g, 2);
    if (action == TslGainAction::LowerGain) { g--; continue; }
    if (action == TslGainAction::RaiseGain) { g++; continue; }

    r_lux = tslComputeLux(ch0, ch1, TSL_GAIN_X[g]);
    r_tslCh0 = ch0; r_tslCh1 = ch1; r_tslGainIdx = g;
    r_tslSat = (ch0 >= TSL_SAT_COUNT || ch1 >= TSL_SAT_COUNT);
    r_tslOk = true;
    break;
  }
  if (ok) tslWrite(0x00, 0x00);

  Wire.end();
  pinMode(TSL_SDA_PIN, INPUT);
  pinMode(TSL_SCL_PIN, INPUT);
  digitalWrite(TSL_PWR_PIN, LOW);
}

// =====================================================================
// 3. GY-33 (unchanged - no pure-logic extraction needed here)
// =====================================================================
const uint8_t GY_ADDR = 0x5A;

bool gyRead(uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire1.beginTransmission(GY_ADDR);
  Wire1.write(reg);
  if (Wire1.endTransmission(true) != 0) return false;
  if (Wire1.requestFrom((uint8_t)GY_ADDR, n) != n) return false;
  for (int i = 0; i < n; i++) buf[i] = Wire1.read();
  return true;
}

void readGy33() {
  r_gyOk = false;

  digitalWrite(GY_PWR_PIN, HIGH);
  delay(GY_POWER_UP_MS);
  Wire1.begin(GY_SDA_PIN, GY_SCL_PIN, 100000);

  uint8_t d[12];
  bool ok = gyRead(0x00, d, 12);
  if (ok) { delay(200); ok = gyRead(0x00, d, 12); }
  if (ok) {
    r_gyR   = (d[0]  << 8) | d[1];
    r_gyG   = (d[2]  << 8) | d[3];
    r_gyB   = (d[4]  << 8) | d[5];
    r_gyC   = (d[6]  << 8) | d[7];
    r_gyLux = (d[8]  << 8) | d[9];
    r_gyCT  = (d[10] << 8) | d[11];
    r_gyOk  = true;
  } else {
    Serial.println("  GY-33: no response at 0x5A (check S0 -> GND and wiring, then try 'scan')");
  }

  Wire1.end();
  pinMode(GY_SDA_PIN, INPUT);
  pinMode(GY_SCL_PIN, INPUT);
  digitalWrite(GY_PWR_PIN, LOW);
}

// =====================================================================
// 4. DS18B20 (scratchpad validation now in ds18b20_parse.h)
// =====================================================================
OneWire ow(DS_DATA_PIN);

float readWaterTempC() {
  for (int attempt = 1; attempt <= DS_MAX_TRIES; attempt++) {
    pinMode(DS_DATA_PIN, INPUT);
    digitalWrite(DS_PWR_PIN, HIGH);
    delay(DS_POWER_UP_MS);

    DsReading reading;
    if (!ow.reset()) {
      reading.error = DsError::NoPresencePulse;
    } else {
      ow.skip();
      ow.write(0x44);
      delay(DS_CONVERSION_MS);
      ow.reset();
      ow.skip();
      ow.write(0xBE);
      uint8_t d[9];
      for (int i = 0; i < 9; i++) d[i] = ow.read();
      reading = parseDs18b20Scratchpad(d);
    }

    pinMode(DS_DATA_PIN, INPUT);
    digitalWrite(DS_PWR_PIN, LOW);

    if (reading.error == DsError::None) { lastGoodTempC = reading.tempC; return reading.tempC; }
    Serial.printf("  DS18B20 try %d: %s\n", attempt, dsErrorMessage(reading.error));
    delay(100);
  }
  return NAN;
}

// readWaterTempC() already refuses to return a value for any of DsError's
// cases (CRC fail, bad config byte, the 85.0C power-on sentinel, no presence
// pulse) - those are the sensor's own way of signalling "not working right
// now", not real temperatures, and must never be reported as one. This
// function's *only* job is picking a number for the pH/TDS compensation math
// below, which needs some temperature even when the real sensor read failed.
// That is why every caller of tempForCompensation() (runCycle(), showCal())
// always prints/logs the `fallback` flag alongside the value - so "fallback"
// data is never indistinguishable from a genuine reading downstream.
float tempForCompensation(bool *fallback) {
  float t = readWaterTempC();
  if (!isnan(t)) { *fallback = false; return t; }
  *fallback = true;
  return isnan(lastGoodTempC) ? 25.0 : lastGoodTempC;
}

// =====================================================================
// 5. pH (buffer interpolation + fit now in ph_sensor.h; adaptive settle
//    from adaptive_settle.h replaces the old fixed 5000 ms wait)
// =====================================================================
PhBuffer BUFFERS[] = {
  {'1', 10.01f, {10.177f, 10.060f, 10.010f, 9.964f, 9.923f, 9.887f}},
  {'9',  9.18f, { 9.332f,  9.225f,  9.180f, 9.139f, 9.102f, 9.068f}},
  {'7',  7.00f, { 7.060f,  7.020f,  7.000f, 6.990f, 6.980f, 6.970f}},
  {'6',  6.86f, { 6.918f,  6.876f,  6.860f, 6.848f, 6.839f, 6.833f}},
};
const int NBUF = sizeof(BUFFERS) / sizeof(BUFFERS[0]);

float bufMv[NBUF], bufRef[NBUF], bufT[NBUF];
bool  bufOk[NBUF];
PhFit phFit;

// Was: digitalWrite(HIGH); delay(PH_SETTLE_MS); read once. Now: power on, then
// poll every PH_SETTLE_POLL_MS and accept as soon as 3 consecutive readings
// agree within PH_SETTLE_TOLERANCE_MV - falling back to the original fixed
// wait if it never settles, so the worst case is unchanged.
float measurePhMv() {
  digitalWrite(PH_PWR_PIN, HIGH);

  SettleTracker t;
  uint32_t t0 = millis();
  float mv = 0;
  while (true) {
    mv = analogReadMilliVolts(PH_ADC_PIN);
    settleTrackerAdd(t, mv);
    uint32_t elapsed = millis() - t0;
    if (settleTrackerIsSettled(t, PH_SETTLE_TOLERANCE_MV, PH_SETTLE_MIN_SAMPLES)) break;
    if (settleTrackerTimedOut(elapsed, PH_SETTLE_MS)) break;
    delay(PH_SETTLE_POLL_MS);
  }
  // Final reading: the same 31-sample median filter as before, taken now that
  // the electrode has (probably) settled, for noise rejection.
  float medianMv = readMedianMv(PH_ADC_PIN);

  digitalWrite(PH_PWR_PIN, LOW);
  return medianMv;
}

// Same namespace as before, so a calibration done on the original firmware carries over.
String nvsKey(const char *p, int i) { return String(p) + String(i); }

void loadPhCal() {
  prefs.begin("ph3", true);
  for (int i = 0; i < NBUF; i++) {
    bufOk[i]  = prefs.getBool (nvsKey("ok", i).c_str(), false);
    bufMv[i]  = prefs.getFloat(nvsKey("mv", i).c_str(), 0);
    bufRef[i] = prefs.getFloat(nvsKey("ph", i).c_str(), BUFFERS[i].nominal);
    bufT[i]   = prefs.getFloat(nvsKey("tc", i).c_str(), 25.0);
  }
  prefs.end();
  phFit = fitPhCalibration(bufMv, bufRef, bufT, bufOk, NBUF);
}

void savePhCal() {
  prefs.begin("ph3", false);
  for (int i = 0; i < NBUF; i++) {
    prefs.putBool (nvsKey("ok", i).c_str(), bufOk[i]);
    prefs.putFloat(nvsKey("mv", i).c_str(), bufMv[i]);
    prefs.putFloat(nvsKey("ph", i).c_str(), bufRef[i]);
    prefs.putFloat(nvsKey("tc", i).c_str(), bufT[i]);
  }
  prefs.end();
}

void printPhPoints() {
  for (int i = 0; i < NBUF; i++) {
    if (bufOk[i]) {
      float pred = mvToPh(phFit, bufMv[i], bufT[i]);
      Serial.printf("  %5.2f buffer: ref %.3f @%.2fC, %7.1f mV, fit %.3f (error %+.3f)\n",
                    BUFFERS[i].nominal, bufRef[i], bufT[i], bufMv[i], pred, pred - bufRef[i]);
    } else {
      Serial.printf("  %5.2f buffer: not calibrated\n", BUFFERS[i].nominal);
    }
  }
  if (phFit.valid) {
    Serial.printf("  Fit: V7 = %.1f mV, span = %.1f mV/pH at 25 C (cal temp %.2f C)\n",
                  phFit.v7, -1.0 / phFit.slope25, phFit.tcal);
    if (phFit.slope25 >= 0) Serial.println("  WARNING: voltage should FALL as pH rises. Check buffers.");
  } else {
    Serial.println("  Fit: need at least 2 points (pH is UNCALIBRATED)");
    if (phFit.rejectedNearIdentical)
      Serial.println("  WARNING: pH calibration points have nearly identical voltages. Fit rejected.");
  }
}

void showCal() {
  bool fb;
  float tNow = tempForCompensation(&fb);
  Serial.printf("Water temperature: %.2f C%s  (ideal electrode slope %.2f mV/pH)\n",
                tNow, fb ? " [FALLBACK]" : "", 59.16 * (tNow + 273.15) / 298.15);
  Serial.println("Buffer pH at this temperature:");
  for (int i = 0; i < NBUF; i++) {
    Serial.printf("  cal%c  %5.2f @25C  ->  %.3f\n",
                  BUFFERS[i].key, BUFFERS[i].nominal, bufferPhAt(BUFFERS[i], tNow));
  }
  Serial.println("pH calibration:");
  printPhPoints();
  Serial.printf("TDS K factor: %.3f\n", tdsK);
}

// =====================================================================
// Full cycle (unchanged control flow; internals now call into koi_sensing)
// =====================================================================
void runCycle() {
  allPowerOff();
  Serial.printf("---- Cycle %lu ----\n", (unsigned long)cycleCount);

  r_tdsMv = measureTdsMv();
  readTsl();
  readGy33();
  r_tempC = tempForCompensation(&r_tempFallback);
  r_phMv  = measurePhMv();

  r_ph     = mvToPh(phFit, r_phMv, r_tempC);
  r_tdsPpm = tdsCalibratedPpm(r_tdsMv, r_tempC, tdsK);
  r_ecUs   = tdsPpmToEcUs(r_tdsPpm);

  Serial.printf("  Temp   : %.2f C%s\n", r_tempC, r_tempFallback ? " [FALLBACK]" : "");
  Serial.printf("  pH     : %.2f  (%.1f mV)%s\n", r_ph, r_phMv, phFit.valid ? "" : " [UNCALIBRATED]");
  Serial.printf("  TDS    : %.0f ppm  (EC %.0f uS/cm, %.1f mV, K %.3f)\n",
                r_tdsPpm, r_ecUs, r_tdsMv, tdsK);
  if (r_tslOk) {
    Serial.printf("  TSL2591: %.1f lux  (ch0 %u, ch1 %u, gain %.0fx)%s\n",
                  r_lux, r_tslCh0, r_tslCh1, TSL_GAIN_X[r_tslGainIdx],
                  r_tslSat ? " [SATURATED]" : "");
  } else {
    Serial.println("  TSL2591: FAILED");
  }
  if (r_gyOk) {
    Serial.printf("  GY-33  : R %u  G %u  B %u  C %u  |  lux %u  CT %u K\n",
                  r_gyR, r_gyG, r_gyB, r_gyC, r_gyLux, r_gyCT);
  } else {
    Serial.println("  GY-33  : FAILED");
  }

  Serial.printf("CSV,%lu,%.2f,%d,%.3f,%.1f,%.1f,%.1f,%.1f,%u,%u,%u,%u,%u,%u\n",
                (unsigned long)cycleCount, r_tempC, r_tempFallback ? 1 : 0,
                r_ph, r_phMv, r_tdsPpm, r_tdsMv, r_tslOk ? r_lux : -1.0,
                r_gyR, r_gyG, r_gyB, r_gyC, r_gyLux, r_gyCT);
}

// =====================================================================
// Deep sleep (unchanged)
// =====================================================================
void goToSleep() {
  allPowerOff();
  for (int i = 0; i < N_POWER_PINS; i++) gpio_hold_en((gpio_num_t)POWER_PINS[i]);
  gpio_deep_sleep_hold_en();
  esp_sleep_enable_timer_wakeup(SLEEP_S * 1000000ULL);
  Serial.printf("Sleeping %llu s\n\n", SLEEP_S);
  Serial.flush();
  esp_deep_sleep_start();
}

// =====================================================================
// Service mode (unchanged, adapted only for the PhBuffer/PhFit types)
// =====================================================================
void i2cScan(TwoWire &bus, const char *name) {
  Serial.printf("  %s:", name);
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    bus.beginTransmission(a);
    if (bus.endTransmission() == 0) { Serial.printf(" 0x%02X", a); found++; }
  }
  Serial.println(found ? "" : " nothing found");
}

void scanBuses() {
  allPowerOff();
  digitalWrite(TSL_PWR_PIN, HIGH);
  delay(TSL_POWER_UP_MS);
  Wire.begin(TSL_SDA_PIN, TSL_SCL_PIN, 100000);
  i2cScan(Wire, "Wire  (TSL2591, expect 0x29)");
  Wire.end();
  pinMode(TSL_SDA_PIN, INPUT); pinMode(TSL_SCL_PIN, INPUT);
  digitalWrite(TSL_PWR_PIN, LOW);

  digitalWrite(GY_PWR_PIN, HIGH);
  delay(GY_POWER_UP_MS);
  Wire1.begin(GY_SDA_PIN, GY_SCL_PIN, 100000);
  i2cScan(Wire1, "Wire1 (GY-33, expect 0x5A)");
  Wire1.end();
  pinMode(GY_SDA_PIN, INPUT); pinMode(GY_SCL_PIN, INPUT);
  digitalWrite(GY_PWR_PIN, LOW);
}

void printHelp() {
  Serial.println("Commands: cal1 cal9 cal7 cal6, show, clear, tdscal X, temp,");
  Serial.println("          settle ph, settle tds, scan, sample, run");
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toLowerCase();
  allPowerOff();

  if (cmd.length() == 4 && cmd.startsWith("cal")) {
    char k = cmd.charAt(3);
    for (int i = 0; i < NBUF; i++) {
      if (BUFFERS[i].key != k) continue;
      float tC = readWaterTempC();
      if (isnan(tC)) { Serial.println("Calibration aborted: water temperature read failed."); return; }
      float ref = bufferPhAt(BUFFERS[i], tC);
      Serial.printf("Measuring %.2f buffer at %.2f C (true pH %.3f)...\n", BUFFERS[i].nominal, tC, ref);
      bufMv[i] = measurePhMv(); bufRef[i] = ref; bufT[i] = tC; bufOk[i] = true;
      phFit = fitPhCalibration(bufMv, bufRef, bufT, bufOk, NBUF);
      savePhCal();
      Serial.printf("Saved: %.1f mV -> pH %.3f\n", bufMv[i], ref);
      printPhPoints();
      return;
    }
    Serial.println("Unknown buffer. Use cal1, cal9, cal7 or cal6.");
  } else if (cmd == "show") {
    showCal();
  } else if (cmd == "clear") {
    for (int i = 0; i < NBUF; i++) bufOk[i] = false;
    phFit = fitPhCalibration(bufMv, bufRef, bufT, bufOk, NBUF);
    savePhCal();
    Serial.println("All pH calibration points cleared.");
  } else if (cmd.startsWith("tdscal ")) {
    float target = cmd.substring(7).toFloat();
    if (target <= 0) { Serial.println("Usage: tdscal 707  (ppm of your standard solution)"); return; }
    float tC = readWaterTempC();
    if (isnan(tC)) { Serial.println("TDS calibration aborted: water temperature read failed."); return; }
    float mv  = measureTdsMv();
    float raw = tdsRawPpm(mv, tC);
    if (raw < 1) { Serial.printf("TDS reading too low (%.1f mV). Is the probe in the solution?\n", mv); return; }
    tdsK = target / raw;
    saveTdsK();
    Serial.printf("TDS: %.1f mV at %.2f C, raw %.0f ppm -> K = %.3f saved\n", mv, tC, raw, tdsK);
  } else if (cmd == "temp") {
    float t = readWaterTempC();
    if (isnan(t)) Serial.println("Water temperature read FAILED");
    else          Serial.printf("Water temperature: %.2f C\n", t);
  } else if (cmd == "settle ph") {
    settleLog(PH_PWR_PIN, PH_ADC_PIN);
  } else if (cmd == "settle tds") {
    settleLog(TDS_PWR_PIN, TDS_ADC_PIN);
  } else if (cmd == "scan") {
    scanBuses();
  } else if (cmd == "sample") {
    runCycle();
  } else if (cmd == "run") {
    Serial.println("Leaving service mode.");
    goToSleep();
  } else {
    printHelp();
  }
}

// =====================================================================
// Setup / loop (unchanged)
// =====================================================================
void setup() {
  for (int i = 0; i < N_POWER_PINS; i++) {
    pinMode(POWER_PINS[i], OUTPUT);
    digitalWrite(POWER_PINS[i], LOW);
    gpio_hold_dis((gpio_num_t)POWER_PINS[i]);
    gpio_set_drive_capability((gpio_num_t)POWER_PINS[i], GPIO_DRIVE_CAP_3);
  }
  gpio_deep_sleep_hold_dis();

  pinMode(DS_DATA_PIN, INPUT);
  pinMode(TSL_SDA_PIN, INPUT); pinMode(TSL_SCL_PIN, INPUT);
  pinMode(GY_SDA_PIN,  INPUT); pinMode(GY_SCL_PIN,  INPUT);

  Serial.begin(115200);
  delay(100);

  analogReadResolution(12);
  analogSetPinAttenuation(PH_ADC_PIN,  ADC_11db);
  analogSetPinAttenuation(TDS_ADC_PIN, ADC_11db);

  loadPhCal();
  loadTdsK();
  cycleCount++;

  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER) {
    Serial.println("\nOutdoorKoi node — cold boot (refactored build)");
    Serial.println("pH calibration:");
    printPhPoints();
    Serial.printf("TDS K factor: %.3f\n", tdsK);
  }

  runCycle();

  Serial.printf("Type any command within %lu s for service mode...\n", SERVICE_WINDOW_MS / 1000);
  uint32_t t0 = millis();
  while (millis() - t0 < SERVICE_WINDOW_MS) {
    if (Serial.available()) {
      serviceMode = true;
      Serial.println("SERVICE MODE (type 'run' to resume the sleep cycle)");
      handleCommand(Serial.readStringUntil('\n'));
      return;
    }
    delay(10);
  }
  goToSleep();
}

void loop() {
  if (Serial.available()) handleCommand(Serial.readStringUntil('\n'));
}
