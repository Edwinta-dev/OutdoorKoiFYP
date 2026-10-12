/*
 * OutdoorKoi networked sensor node — PROPOSAL, Round 3 "new planned approach"
 *
 * Why this file exists: today the project has the sensor-reading sophistication
 * (time-multiplexed power, median filtering, multi-point calibration) in
 * FullSketch.ino, which uploads NOTHING - it only prints to Serial. Meanwhile
 * archive/pre-refactor/Embedded/TempSensor/TempSensor.ino is the only sketch that talks to Supabase,
 * but it:
 *   - never sleeps: WiFi + CPU stay on 100% of the time, polling every 5 s
 *     (FullSketch's deep-sleep design uses roughly 15s active / 15s asleep -
 *     TempSensor's continuous loop is the single biggest power inefficiency
 *     found anywhere in Embedded/)
 *   - has no timeout on its Wi-Fi connect loop (`while (WiFi.status() !=
 *     WL_CONNECTED)`) - a dead router hangs the node forever
 *   - hardcodes LUX to 35.0 (the exact issue flagged in the project's own
 *     backend-validation notes: no real light data ever reaches the digital
 *     twin through this path)
 *   - reads TDS/pH as single raw analogRead() samples (no median filtering)
 *     and pH via a fixed linear calibration (no per-device calibration)
 *
 * This sketch is what "merge the two" looks like: FullSketch's calibrated,
 * time-multiplexed, deep-sleep sensor cycle (the Round-3 refactored bench
 * build, since retired into this sketch's service mode) plus
 * esp32cam_pythonanywhere.ino's bounded-retry Wi-Fi pattern (wifi_retry.h,
 * sleep_backoff.h), driving the same batched single-request upload
 * TempSensor.ino already did correctly (all readings in one JSON array, one
 * request).
 *
 * Upload (issues #12, #17): one POST to Supabase's /rest/v1/rpc/ingest_readings
 * with {"device_token": SENSOR_DEVICE_TOKEN, "rows": [...]}, payload v2: each
 * row is {sensor_type, data1, boot_id, cycle_seq, flags, reset_reason,
 * sampled_at?, battery_mv?}. The database finds the pond and device from the
 * token, so the payload carries no pond id; a direct insert into SensorData is
 * refused since migration 0026.
 *
 * Battery (issue #42, koi_sensing/battery_monitor.h): when secrets.h defines
 * the divider (KOI_BATTERY_*), the divider is switched on, read and switched
 * off right after the Wi-Fi attempt, so a connected node reads its supply
 * under radio load. Every row of the cycle carries battery_mv, and the
 * battery_low flag (16) when it is below KOI_BATTERY_LOW_MV. Without the
 * defines no battery_mv is sent, as before.
 *
 * Cycle identity, sample clock and retries (koi_sensing/sample_cycle.h):
 *   - boot_id: NVS boot counter (high 32 bits) + hardware random (low 32),
 *     new on every cold boot; cycle_seq counts wakes within the boot in RTC
 *     memory. The database stores a re-sent cycle once.
 *   - The clock is set by SNTP after Wi-Fi connects and runs through deep
 *     sleep. A cycle gets sampled_at only when the clock was set (at the start
 *     of the cycle, or by SNTP later in the same wake, worked back with
 *     millis()). Otherwise it is sent without sampled_at, flagged
 *     time_unsynced; no absolute time is made up.
 *   - A cycle whose upload fails for a reason that can pass (no connection,
 *     rate limit, server error) stays in RTC memory and is re-sent with the
 *     next cycle, up to 4 cycles; a power loss or reset loses them.
 *   - A failed numeric read (temperature, light) is left out of the upload,
 *     never sent as a sentinel.
 *
 * Service mode (calibration, scans, settle logs) is compiled in only with
 * KOI_SERVICE_MODE defined: uncomment the #define below, or build with
 *   arduino-cli compile --build-property compiler.cpp.extra_flags=-DKOI_SERVICE_MODE ...
 * The normal build never waits for serial input.
 *
 * Compiled by tools/check.py (both builds); not yet flashed. Treat this as a
 * reviewed design to build and bench-test, not a drop-in.
 */

// #define KOI_SERVICE_MODE

#include <Arduino.h>
#include <Wire.h>
#include <OneWire.h>
#include <WiFi.h>
#include <Preferences.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <sys/time.h>
#include <time.h>
#include "driver/gpio.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "esp_random.h"
#include "bootloader_random.h"

#include <koi_sensing.h>

// ---- Pins: same as the original FullSketch ----
const int TDS_PWR_PIN = 25;  const int TDS_ADC_PIN = 34;
const int PH_PWR_PIN  = 4;   const int PH_ADC_PIN  = 35;
const int TSL_PWR_PIN = 26;  const int TSL_SDA_PIN = 21;  const int TSL_SCL_PIN = 22;
const int DS_PWR_PIN  = 27;  const int DS_DATA_PIN = 23;
#ifdef KOI_SERVICE_MODE
const int GY_PWR_PIN  = 32;  const int GY_SDA_PIN  = 18;  const int GY_SCL_PIN  = 19;
const int POWER_PINS[] = {TDS_PWR_PIN, PH_PWR_PIN, TSL_PWR_PIN, DS_PWR_PIN, GY_PWR_PIN};
#else
const int POWER_PINS[] = {TDS_PWR_PIN, PH_PWR_PIN, TSL_PWR_PIN, DS_PWR_PIN};
#endif
const int N_POWER_PINS = sizeof(POWER_PINS) / sizeof(POWER_PINS[0]);

// ---- Timing ----
const uint64_t SLEEP_S          = 15;
const uint32_t TDS_SETTLE_MS    = 2000;
const uint32_t PH_SETTLE_MS     = 5000;
const uint32_t TSL_POWER_UP_MS  = 50;
const uint32_t DS_POWER_UP_MS   = 1000;
const uint32_t DS_CONVERSION_MS = 800;
const int      N_SAMPLES        = 31;
const int      SAMPLE_GAP_MS    = 20;
const int      DS_MAX_TRIES     = 3;
#ifdef KOI_SERVICE_MODE
const uint32_t SERVICE_WINDOW_MS  = 3000;
const uint32_t GY_POWER_UP_MS     = 1500;
#endif

// ---- Clock ----
const char    *NTP_SERVER_1          = "pool.ntp.org";
const char    *NTP_SERVER_2          = "time.google.com";
const uint32_t NTP_WAIT_MS           = 5000;
const uint32_t NTP_RESYNC_EVERY_CYCLES = 240;   // about once an hour at the 15 s cadence

// ---- Network: WIFI_SSID, WIFI_PASS, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SENSOR_DEVICE_TOKEN ----
// Kept out of git. Copy secrets.h.example to secrets.h in this folder and fill it in.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "sensor_node: secrets.h not found. Copy Embedded/sensor_node/secrets.h.example to secrets.h and fill in the values."
#endif
const char *INGEST_PATH = "/rest/v1/rpc/ingest_readings";

// ---- Battery divider (issue #42): pins, resistors and threshold in secrets.h ----
// Without KOI_BATTERY_ADC_PIN the node sends no battery_mv. The ADC pin must
// be on ADC1 (GPIO 32-39): ADC2 cannot be read while Wi-Fi is on, and the
// reading is taken with Wi-Fi up.
#ifdef KOI_BATTERY_ADC_PIN
#if !defined(KOI_BATTERY_EN_PIN) || !defined(KOI_BATTERY_R_TOP_OHM) || !defined(KOI_BATTERY_R_BOTTOM_OHM)
#error "sensor_node: KOI_BATTERY_ADC_PIN needs KOI_BATTERY_EN_PIN, KOI_BATTERY_R_TOP_OHM and KOI_BATTERY_R_BOTTOM_OHM in secrets.h"
#endif
#endif
#ifndef KOI_BATTERY_LOW_MV
#define KOI_BATTERY_LOW_MV 4400   // linear regulator on 4 alkaline cells (report Section 3.2.8)
#endif
const int32_t  BATTERY_LOW_MV    = KOI_BATTERY_LOW_MV;
const uint32_t BATTERY_SETTLE_MS = 20;   // divider and ADC input settle after switch-on
const int   WIFI_MAX_ATTEMPTS         = 2;
const unsigned long WIFI_ATTEMPT_TIMEOUT_MS = 12000;
const unsigned long DEFAULT_SLEEP_ON_FAIL_S  = 1800;

// ---- State kept through deep sleep (lost on power loss or reset) ----
RTC_DATA_ATTR uint32_t      wifiFailStreak = 0;
RTC_DATA_ATTR uint64_t      bootId = 0;
RTC_DATA_ATTR uint32_t      cycleSeq = 0;
RTC_DATA_ATTR uint8_t       bootResetReason = 0;
RTC_DATA_ATTR bool          clockSynced = false;
RTC_DATA_ATTR uint32_t      lastSyncCycle = 0;
RTC_DATA_ATTR PendingCycles pending;

Preferences prefs;
OneWire ow(DS_DATA_PIN);

void allPowerOff() { for (int i = 0; i < N_POWER_PINS; i++) digitalWrite(POWER_PINS[i], LOW); }

float readMedianMv(int adcPin) {
  int buf[N_SAMPLES];
  for (int i = 0; i < N_SAMPLES; i++) { buf[i] = analogReadMilliVolts(adcPin); delay(SAMPLE_GAP_MS); }
  return medianOfSamples(buf, N_SAMPLES);
}

int64_t clockEpochMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

// ---- Cold boot: new boot id, fresh cycle count and queue ----
void startBoot() {
  bootResetReason = (uint8_t)esp_reset_reason();
  prefs.begin("koi", false);
  uint32_t boots = prefs.getUInt("boots", 0) + 1;
  prefs.putUInt("boots", boots);
  prefs.end();
  // True random without the radio on: the bootloader's entropy source,
  // enabled only here (it shares the SAR ADC, which is not in use yet).
  bootloader_random_enable();
  uint32_t r = esp_random();
  bootloader_random_disable();
  bootId = makeBootId(boots, r);
  cycleSeq = 0;
  clockSynced = false;
  lastSyncCycle = 0;
  pendingClear(pending);
  char id[17]; formatBootId(bootId, id);
  Serial.printf("\nCold boot (%s): boot %lu, boot_id %s\n",
                resetReasonName(bootResetReason), (unsigned long)boots, id);
}

// ---- Sensor reads ----
float tdsK = 1.0f;
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

// Same NVS namespaces and keys as the original firmware, so a calibration
// done there carries over.
String nvsKey(const char *p, int i) { return String(p) + String(i); }

void loadCalibration() {
  prefs.begin("tds", true);  tdsK = prefs.getFloat("k", 1.0f); prefs.end();
  prefs.begin("ph3", true);
  for (int i = 0; i < NBUF; i++) {
    bufOk[i]  = prefs.getBool (nvsKey("ok", i).c_str(), false);
    bufMv[i]  = prefs.getFloat(nvsKey("mv", i).c_str(), 0);
    bufRef[i] = prefs.getFloat(nvsKey("ph", i).c_str(), BUFFERS[i].nominal);
    bufT[i]   = prefs.getFloat(nvsKey("tc", i).c_str(), 25.0f);
  }
  prefs.end();
  phFit = fitPhCalibration(bufMv, bufRef, bufT, bufOk, NBUF);
}

float measureTdsMv() {
  digitalWrite(TDS_PWR_PIN, HIGH); delay(TDS_SETTLE_MS);
  float mv = readMedianMv(TDS_ADC_PIN);
  digitalWrite(TDS_PWR_PIN, LOW);
  return mv;
}

float measurePhMv() {
  digitalWrite(PH_PWR_PIN, HIGH); delay(PH_SETTLE_MS);   // fixed settle; adaptive_settle.h is not used here
  float mv = readMedianMv(PH_ADC_PIN);
  digitalWrite(PH_PWR_PIN, LOW);
  return mv;
}

float readWaterTempC() {
  for (int attempt = 1; attempt <= DS_MAX_TRIES; attempt++) {
    pinMode(DS_DATA_PIN, INPUT);
    digitalWrite(DS_PWR_PIN, HIGH);
    delay(DS_POWER_UP_MS);

    DsReading reading;
    if (!ow.reset()) {
      reading.error = DsError::NoPresencePulse;
    } else {
      ow.skip(); ow.write(0x44); delay(DS_CONVERSION_MS);
      ow.reset(); ow.skip(); ow.write(0xBE);
      uint8_t d[9];
      for (int i = 0; i < 9; i++) d[i] = ow.read();
      reading = parseDs18b20Scratchpad(d);
    }
    digitalWrite(DS_PWR_PIN, LOW);
    if (reading.error == DsError::None) return reading.tempC;
    Serial.printf("  DS18B20 try %d: %s\n", attempt, dsErrorMessage(reading.error));
    delay(100);
  }
  return NAN;
}

const uint8_t  TSL_ADDR = 0x29, TSL_CMD = 0xA0;
const uint8_t  TSL_GAIN_BITS[] = {0x00, 0x10, 0x20};
const float    TSL_GAIN_X[]    = {1.0f, 25.0f, 428.0f};
const uint16_t TSL_SAT_COUNT   = 36000;

bool tslWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(TSL_ADDR); Wire.write(TSL_CMD | reg); Wire.write(val);
  return Wire.endTransmission() == 0;
}
bool tslRead(uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire.beginTransmission(TSL_ADDR); Wire.write(TSL_CMD | reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)TSL_ADDR, n) != n) return false;
  for (int i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

// True with luxOut set when the sensor answered; saturated is set when a
// channel hit the saturation count at the lowest gain.
bool readLux(float &luxOut, bool &saturated) {
  saturated = false;
  digitalWrite(TSL_PWR_PIN, HIGH); delay(TSL_POWER_UP_MS);
  Wire.begin(TSL_SDA_PIN, TSL_SCL_PIN, 100000);

  uint8_t id = 0;
  bool ok = tslRead(0x12, &id, 1) && id == 0x50;
  bool got = false;
  int g = 1;
  for (int attempt = 0; ok && attempt < 4; attempt++) {
    ok = tslWrite(0x00, 0x00) && tslWrite(0x01, TSL_GAIN_BITS[g]) && tslWrite(0x00, 0x03);
    if (!ok) break;
    delay(250);
    uint8_t d[4];
    ok = tslRead(0x14, d, 4);
    if (!ok) break;
    uint16_t ch0 = d[0] | (d[1] << 8), ch1 = d[2] | (d[3] << 8);
    TslGainAction action = tslGainDecision(ch0, ch1, TSL_SAT_COUNT, g, 2);
    if (action == TslGainAction::LowerGain) { g--; continue; }
    if (action == TslGainAction::RaiseGain) { g++; continue; }
    luxOut = tslComputeLux(ch0, ch1, TSL_GAIN_X[g]);
    saturated = (ch0 >= TSL_SAT_COUNT || ch1 >= TSL_SAT_COUNT);
    got = true;
    break;
  }
  if (ok) tslWrite(0x00, 0x00);
  Wire.end();
  pinMode(TSL_SDA_PIN, INPUT); pinMode(TSL_SCL_PIN, INPUT);
  digitalWrite(TSL_PWR_PIN, LOW);
  return got;
}

// Battery millivolts through the switched divider, or -1 without one.
int32_t readBatteryMv() {
#ifdef KOI_BATTERY_ADC_PIN
  digitalWrite(KOI_BATTERY_EN_PIN, HIGH); delay(BATTERY_SETTLE_MS);
  float mv = readMedianMv(KOI_BATTERY_ADC_PIN);
  digitalWrite(KOI_BATTERY_EN_PIN, LOW);
  return batteryMvFromAdc(mv, KOI_BATTERY_R_TOP_OHM, KOI_BATTERY_R_BOTTOM_OHM);
#else
  return -1;
#endif
}

void reportBattery(int32_t mv) {
  if (mv < 0) { Serial.println("battery: not measured"); return; }
  Serial.printf("battery: %ld mV%s\n", (long)mv, batteryIsLow(mv, BATTERY_LOW_MV) ? " (LOW)" : "");
}

// One read cycle into a queued cycle. Failed numeric reads are left out.
PendingCycle readCycle(int64_t sampledEpochMs) {
  PendingCycle cycle = {};
  cycle.bootId = bootId;
  cycle.cycleSeq = cycleSeq;
  cycle.sampledEpochMs = sampledEpochMs;
  cycle.resetReason = bootResetReason;
  cycle.batteryMv = -1;   // read later, under Wi-Fi load (setup)

  // ---- Read cycle (same power-multiplexing order as FullSketch, and why) ----
  allPowerOff();
  float tdsMv = measureTdsMv();
  float lux; bool luxSaturated = false;
  bool luxOk = readLux(lux, luxSaturated);
  float measuredTempC = readWaterTempC();
  bool  tempOk = !isnan(measuredTempC);
  // 25C is a plausible REAL pond temperature - unlike the DS18B20's own 85.0C
  // power-on sentinel, it can't double as an error signal. So this fallback is
  // ONLY for the pH/TDS compensation math below (which needs *some* number,
  // same as FullSketch's tempForCompensation()) - it must never be reported
  // or uploaded as if it were an actual reading. The pH and TDS rows carry
  // the temp_fallback flag instead.
  float compensationTempC = tempOk ? measuredTempC : 25.0f;
  float phMv = measurePhMv();

  float ph  = mvToPh(phFit, phMv, compensationTempC);
  float tds = tdsCalibratedPpm(tdsMv, compensationTempC, tdsK);
  uint8_t compFlag = tempOk ? 0 : SAMPLE_FLAG_TEMP_FALLBACK;

  // Only report a temp reading when the sensor actually produced one - never
  // upload the compensation fallback as if it were real data (that's the
  // exact bug in the original TempSensor.ino: it uploads DallasTemperature's
  // -127C DEVICE_DISCONNECTED_C sentinel as a normal reading). The same rule
  // covers lux: a failed read is left out, never sent as -1.
  if (tempOk) pendingAddReading(cycle, Channel::Temp, measuredTempC, 0);
  pendingAddReading(cycle, Channel::Tds, tds, compFlag);
  pendingAddReading(cycle, Channel::Ph, ph, (uint8_t)(compFlag | (phFit.valid ? 0 : SAMPLE_FLAG_PH_UNCALIBRATED)));
  if (luxOk) pendingAddReading(cycle, Channel::Lux, lux, luxSaturated ? SAMPLE_FLAG_LUX_SATURATED : 0);

  Serial.printf("cycle %lu: temp=%s ph=%.2f (comp @%.1fC%s) tds=%.0fppm lux=%s\n",
                (unsigned long)cycleSeq,
                tempOk ? (String(measuredTempC, 2) + "C").c_str() : "SENSOR FAILURE (omitted)",
                ph, compensationTempC, tempOk ? "" : ", fallback", tds,
                luxOk ? String(lux, 1).c_str() : "FAILED (omitted)");
  return cycle;
}

// ---- Clock: SNTP after Wi-Fi ----
bool syncClock() {
  configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);
  uint32_t t0 = millis();
  while (millis() - t0 < NTP_WAIT_MS) {
    if (clockLooksSet(clockEpochMs() / 1000)) return true;
    delay(100);
  }
  return false;
}

// ---- Wi-Fi: bounded attempts with timeout, using wifi_retry.h's policy ----
// POSTs one batch to ingest_readings with the publishable key; code is the
// HTTP status (negative when there was no connection). TLS is not
// certificate-checked, as with the ESPSupabase upload this replaces.
String postIngest(const String &json, int &code) {
  WiFiClientSecure tls;
  tls.setInsecure();
  HTTPClient http;
  String reply;
  code = -1;
  if (!http.begin(tls, String(SUPABASE_URL) + INGEST_PATH)) return reply;
  http.addHeader("apikey", SUPABASE_PUBLISHABLE_KEY);
  http.addHeader("Content-Type", "application/json");
  code = http.POST(json);
  if (code > 0) reply = http.getString();
  http.end();
  return reply;
}

bool connectWifi() {
  WiFi.mode(WIFI_STA);
  for (int attempt = 0; wifiShouldStartAnotherAttempt(attempt, WIFI_MAX_ATTEMPTS); attempt++) {
    Serial.printf("Wi-Fi attempt %d/%d", attempt + 1, WIFI_MAX_ATTEMPTS);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED &&
           wifiShouldKeepWaitingThisAttempt(millis() - t0, WIFI_ATTEMPT_TIMEOUT_MS)) {
      delay(250); Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED) { Serial.printf(" OK (%lu ms)\n", millis() - t0); return true; }
    Serial.println(" failed");
    WiFi.disconnect(true); delay(500);
  }
  return false;
}

// Every queued cycle in one ingest_readings call. Clears the queue when the
// batch was stored or cannot be stored by retrying.
void uploadPending() {
  JsonDocument doc;
  doc["device_token"] = SENSOR_DEVICE_TOKEN;
  JsonArray rows = doc["rows"].to<JsonArray>();
  int nRows = 0;
  for (int i = 0; i < pending.count; i++) {
    const PendingCycle &c = pendingAt(pending, i);
    char id[17]; formatBootId(c.bootId, id);
    char iso[25];
    bool timed = c.sampledEpochMs >= 0 && formatUtcIso8601(c.sampledEpochMs, iso, sizeof iso);
    Serial.printf("  boot_id %s cycle %lu sampled_at %s\n", id, (unsigned long)c.cycleSeq,
                  timed ? iso : "unknown (clock not set)");
    for (int k = 0; k < c.nReadings; k++) {
      const PendingReading &r = c.readings[k];
      JsonObject row = rows.add<JsonObject>();
      row["sensor_type"] = channelSensorType(r.channel);
      row["data1"] = r.value;
      row["boot_id"] = id;
      row["cycle_seq"] = c.cycleSeq;
      row["flags"] = uploadRowFlags(r.flags, timed, c.batteryMv, BATTERY_LOW_MV);
      row["reset_reason"] = resetReasonName(c.resetReason);
      if (timed) row["sampled_at"] = iso;
      if (c.batteryMv >= 0) row["battery_mv"] = c.batteryMv;
      nRows++;
    }
  }
  if (nRows == 0) { pendingClear(pending); return; }

  String json; serializeJson(doc, json);
  int code = -1;
  String reply = postIngest(json, code);
  int accepted = 0;
  if (ingestAccepted(code, reply.c_str(), accepted)) {
    Serial.printf("ingest_readings -> %d accepted of %d %s\n", accepted, nRows, reply.c_str());
    pendingClear(pending);
  } else if (ingestShouldRetry(code)) {
    Serial.printf("ingest_readings -> HTTP %d: %s; keeping %u cycle(s) to re-send\n",
                  code, ingestStatusText(code), pending.count);
  } else {
    Serial.printf("ingest_readings -> HTTP %d: %s %s; dropping %u cycle(s)\n",
                  code, ingestStatusText(code), reply.c_str(), pending.count);
    pendingClear(pending);
  }
}

void goToSleep(unsigned long seconds) {
  allPowerOff();
#ifdef KOI_BATTERY_ADC_PIN
  digitalWrite(KOI_BATTERY_EN_PIN, LOW);
#endif
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  Serial.printf("Sleeping %lu s\n", seconds);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
  esp_deep_sleep_start();
}

#ifdef KOI_SERVICE_MODE
// =====================================================================
// Service mode (merged from the retired sensor_bench build). Commands:
//   cal1 cal9 cal7 cal6 : store a pH buffer point at the measured water temp
//   show                : water temp, buffer pH at that temp, pH and TDS calibration
//   clear               : forget every pH buffer point
//   tdscal X            : set the TDS K factor from an X ppm standard
//   temp                : read the water temperature
//   settle ph | tds     : 30 s millivolt log after power-on, to choose settle times
//   scan                : I2C scan of the TSL2591 and GY-33 buses
//   sample              : one read cycle printed, not uploaded
//   battery             : read the battery divider now, to compare with a multimeter
//   run                 : leave service mode and resume the sleep cycle
// =====================================================================
bool serviceMode = false;
unsigned long serviceSleepSec = SLEEP_S;

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

void saveTdsK() {
  prefs.begin("tds", false);
  prefs.putFloat("k", tdsK);
  prefs.end();
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
  float t = readWaterTempC();
  bool fallback = isnan(t);
  float tNow = fallback ? 25.0f : t;
  Serial.printf("Water temperature: %.2f C%s  (ideal electrode slope %.2f mV/pH)\n",
                tNow, fallback ? " [FALLBACK]" : "", 59.16 * (tNow + 273.15) / 298.15);
  Serial.println("Buffer pH at this temperature:");
  for (int i = 0; i < NBUF; i++) {
    Serial.printf("  cal%c  %5.2f @25C  ->  %.3f\n",
                  BUFFERS[i].key, BUFFERS[i].nominal, bufferPhAt(BUFFERS[i], tNow));
  }
  Serial.println("pH calibration:");
  printPhPoints();
  Serial.printf("TDS K factor: %.3f\n", tdsK);
  char id[17]; formatBootId(bootId, id);
  Serial.printf("boot_id %s, cycle %lu, clock %s\n", id, (unsigned long)cycleSeq,
                clockSynced ? "set by SNTP" : "not set");
}

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
  Serial.println("          settle ph, settle tds, scan, sample, battery, run");
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
    readCycle(-1);   // printed only: not queued, not uploaded
  } else if (cmd == "battery") {
    reportBattery(readBatteryMv());
  } else if (cmd == "run") {
    Serial.println("Leaving service mode.");
    goToSleep(serviceSleepSec);
  } else {
    printHelp();
  }
}
#endif

void setup() {
  for (int i = 0; i < N_POWER_PINS; i++) { pinMode(POWER_PINS[i], OUTPUT); digitalWrite(POWER_PINS[i], LOW); }
#ifdef KOI_BATTERY_ADC_PIN
  pinMode(KOI_BATTERY_EN_PIN, OUTPUT); digitalWrite(KOI_BATTERY_EN_PIN, LOW);
#endif
  Serial.begin(115200);
  delay(100);

  if (esp_reset_reason() != ESP_RST_DEEPSLEEP) startBoot();

  analogReadResolution(12);
  analogSetPinAttenuation(PH_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(TDS_ADC_PIN, ADC_11db);
#ifdef KOI_BATTERY_ADC_PIN
  analogSetPinAttenuation(KOI_BATTERY_ADC_PIN, ADC_11db);
#endif
  loadCalibration();

  // ---- This cycle's identity and sample time ----
  cycleSeq++;
  uint32_t sampleMillis = millis();
  int64_t nowMs = clockEpochMs();
  int64_t sampledEpochMs = (clockSynced && clockLooksSet(nowMs / 1000)) ? nowMs : -1;
  PendingCycle cycle = readCycle(sampledEpochMs);

  // ---- Upload: bounded Wi-Fi, SNTP when needed, one ingest_readings call ----
  unsigned long sleepSec = wifiFailSleepSeconds(wifiFailStreak, DEFAULT_SLEEP_ON_FAIL_S);
  bool online = connectWifi();
  // Battery under load: with Wi-Fi connected the radio is on. After a failed
  // attempt the radio is already off, so that reading is at rest (higher).
  cycle.batteryMv = readBatteryMv();
  reportBattery(cycle.batteryMv);
  if (online) {
    wifiFailStreak = 0;
    if (!clockSynced || cycleSeq - lastSyncCycle >= NTP_RESYNC_EVERY_CYCLES) {
      if (syncClock()) {
        clockSynced = true;
        lastSyncCycle = cycleSeq;
        Serial.println("Clock set by SNTP");
      } else {
        Serial.println("SNTP failed: clock not set");
      }
    }
    // The clock was set later in this wake: work this cycle's sample time
    // back from millis(), which has not stopped since the sample.
    if (cycle.sampledEpochMs < 0 && clockSynced && clockLooksSet(clockEpochMs() / 1000)) {
      cycle.sampledEpochMs = sampleEpochMs(clockEpochMs(), millis(), sampleMillis);
    }
    if (pendingPush(pending, cycle)) Serial.println("NOTE: retry queue full - oldest unsent cycle dropped");
    uploadPending();
    sleepSec = SLEEP_S;   // normal cadence resumes once the network is healthy
  } else {
    if (pendingPush(pending, cycle)) Serial.println("NOTE: retry queue full - oldest unsent cycle dropped");
    wifiFailStreak++;
    Serial.printf("Wi-Fi failed %u time(s) in a row -> %u cycle(s) kept, sleeping %lu s\n",
                  wifiFailStreak, pending.count, sleepSec);
  }

#ifdef KOI_SERVICE_MODE
  serviceSleepSec = sleepSec;
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
#endif
  goToSleep(sleepSec);
}

void loop() {
#ifdef KOI_SERVICE_MODE
  if (Serial.available()) handleCommand(Serial.readStringUntil('\n'));
#endif
}
