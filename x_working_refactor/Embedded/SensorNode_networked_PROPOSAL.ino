/*
 * OutdoorKoi networked sensor node — PROPOSAL, Round 3 "new planned approach"
 *
 * Why this file exists: today the project has the sensor-reading sophistication
 * (time-multiplexed power, median filtering, multi-point calibration) in
 * FullSketch.ino, which uploads NOTHING - it only prints to Serial. Meanwhile
 * Embedded/TempSensor/TempSensor.ino is the only sketch that talks to Supabase,
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
 * time-multiplexed, deep-sleep sensor cycle (Round-3 refactored version, this
 * directory's FullSketch_refactored.ino) plus esp32cam_pythonanywhere.ino's
 * bounded-retry Wi-Fi pattern (lib/wifi_retry.h, lib/sleep_backoff.h), driving
 * the same batched single-request Supabase upload TempSensor.ino already did
 * correctly (all 4 readings in one JSON array, one insert() call - that part
 * of the original was fine and is kept as-is).
 *
 * NOT compiled or flashed - no ESP32 toolchain in this environment, and it
 * depends on the ESPSupabase library already used by TempSensor.ino. Treat
 * this as a reviewed design to build and bench-test, not a drop-in.
 * OPEN QUESTION for whoever wires this up: TempSensor.ino's SensorData rows
 * carry no pond/device id. Check how Backend/DigitalTwin keys incoming
 * readings before deciding whether this needs a DEVICE_ID field added to the
 * payload - that's a Backend-side question, out of scope for this pass.
 */

#include <Arduino.h>
#include <Wire.h>
#include <OneWire.h>
#include <WiFi.h>
#include <Preferences.h>
#include <ESPSupabase.h>
#include "driver/gpio.h"
#include "esp_sleep.h"

#include "lib/median_filter.h"
#include "lib/tds_sensor.h"
#include "lib/ph_sensor.h"
#include "lib/ds18b20_parse.h"
#include "lib/tsl2591_lux.h"
#include "lib/sleep_backoff.h"
#include "lib/wifi_retry.h"

// ---- Pins: same as FullSketch_refactored.ino ----
const int TDS_PWR_PIN = 25;  const int TDS_ADC_PIN = 34;
const int PH_PWR_PIN  = 4;   const int PH_ADC_PIN  = 35;
const int TSL_PWR_PIN = 26;  const int TSL_SDA_PIN = 21;  const int TSL_SCL_PIN = 22;
const int DS_PWR_PIN  = 27;  const int DS_DATA_PIN = 23;
const int POWER_PINS[] = {TDS_PWR_PIN, PH_PWR_PIN, TSL_PWR_PIN, DS_PWR_PIN};
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

// ---- Network (fill in real values before flashing) ----
const char *WIFI_SSID   = "REPLACE_ME";
const char *WIFI_PASS   = "REPLACE_ME";
const char *SUPABASE_URL = "https://mkzfdxhzmrnapvrhshte.supabase.co";
const char *SUPABASE_ANON_KEY = "REPLACE_ME";   // do not commit a real key here - load from NVS/build flag
const char *TABLE = "SensorData";
const int   WIFI_MAX_ATTEMPTS         = 2;
const unsigned long WIFI_ATTEMPT_TIMEOUT_MS = 12000;
const unsigned long DEFAULT_SLEEP_ON_FAIL_S  = 1800;

RTC_DATA_ATTR uint32_t wifiFailStreak = 0;

Preferences prefs;
OneWire ow(DS_DATA_PIN);
Supabase db;

void allPowerOff() { for (int i = 0; i < N_POWER_PINS; i++) digitalWrite(POWER_PINS[i], LOW); }

float readMedianMv(int adcPin) {
  int buf[N_SAMPLES];
  for (int i = 0; i < N_SAMPLES; i++) { buf[i] = analogReadMilliVolts(adcPin); delay(SAMPLE_GAP_MS); }
  return medianOfSamples(buf, N_SAMPLES);
}

// ---- Sensor reads (same approach as FullSketch_refactored.ino) ----
float tdsK = 1.0f;
PhFit phFit;

float measureTdsMv() {
  digitalWrite(TDS_PWR_PIN, HIGH); delay(TDS_SETTLE_MS);
  float mv = readMedianMv(TDS_ADC_PIN);
  digitalWrite(TDS_PWR_PIN, LOW);
  return mv;
}

float measurePhMv() {
  digitalWrite(PH_PWR_PIN, HIGH); delay(PH_SETTLE_MS);   // adaptive settle: see FullSketch_refactored.ino
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

bool readLux(float &luxOut) {
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
    got = true;
    break;
  }
  if (ok) tslWrite(0x00, 0x00);
  Wire.end();
  pinMode(TSL_SDA_PIN, INPUT); pinMode(TSL_SCL_PIN, INPUT);
  digitalWrite(TSL_PWR_PIN, LOW);
  return got;
}

// ---- Wi-Fi: bounded attempts with timeout, using lib/wifi_retry.h's policy ----
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

void goToSleep(unsigned long seconds) {
  allPowerOff();
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  Serial.printf("Sleeping %lu s\n", seconds);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
  esp_deep_sleep_start();
}

void setup() {
  for (int i = 0; i < N_POWER_PINS; i++) { pinMode(POWER_PINS[i], OUTPUT); digitalWrite(POWER_PINS[i], LOW); }
  Serial.begin(115200);
  delay(100);
  analogReadResolution(12);
  analogSetPinAttenuation(PH_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(TDS_ADC_PIN, ADC_11db);

  prefs.begin("tds", true);  tdsK = prefs.getFloat("k", 1.0f); prefs.end();
  float bufMv[4], bufRef[4], bufT[4]; bool bufOk[4];
  prefs.begin("ph3", true);
  for (int i = 0; i < 4; i++) {
    String p = String(i);
    bufOk[i]  = prefs.getBool (("ok" + p).c_str(), false);
    bufMv[i]  = prefs.getFloat(("mv" + p).c_str(), 0);
    bufRef[i] = prefs.getFloat(("ph" + p).c_str(), 7.0f);
    bufT[i]   = prefs.getFloat(("tc" + p).c_str(), 25.0f);
  }
  prefs.end();
  phFit = fitPhCalibration(bufMv, bufRef, bufT, bufOk, 4);

  // ---- Read cycle (same power-multiplexing order as FullSketch, and why) ----
  allPowerOff();
  float tdsMv = measureTdsMv();
  float lux; bool luxOk = readLux(lux);
  float measuredTempC = readWaterTempC();
  bool  tempOk = !isnan(measuredTempC);
  // 25C is a plausible REAL pond temperature - unlike the DS18B20's own 85.0C
  // power-on sentinel, it can't double as an error signal. So this fallback is
  // ONLY for the pH/TDS compensation math below (which needs *some* number,
  // same as FullSketch's tempForCompensation()) - it must never be reported
  // or uploaded as if it were an actual reading. See lib/ds18b20_parse.h's
  // DsError enum for what a real sensor failure looks like; a failed read
  // here means the sensor is off/broken, not that the pond is at 25C.
  float compensationTempC = tempOk ? measuredTempC : 25.0f;
  float phMv = measurePhMv();

  float ph  = mvToPh(phFit, phMv, compensationTempC);
  float tds = tdsCalibratedPpm(tdsMv, compensationTempC, tdsK);

  Serial.printf("temp=%s ph=%.2f (comp @%.1fC) tds=%.0fppm lux=%s\n",
                tempOk ? (String(measuredTempC, 2) + "C").c_str() : "SENSOR FAILURE",
                ph, compensationTempC, tds,
                luxOk ? String(lux, 1).c_str() : "FAILED");

  // ---- Upload: bounded Wi-Fi, single batched insert (as TempSensor.ino already did) ----
  unsigned long sleepSec = wifiFailSleepSeconds(wifiFailStreak, DEFAULT_SLEEP_ON_FAIL_S);
  if (connectWifi()) {
    wifiFailStreak = 0;
    db.begin(SUPABASE_URL, SUPABASE_ANON_KEY);

    // Only report a temp reading when the sensor actually produced one - never
    // upload the compensation fallback as if it were real data (that's the
    // exact bug in the original TempSensor.ino: it uploads DallasTemperature's
    // -127C DEVICE_DISCONNECTED_C sentinel as a normal reading, with nothing
    // downstream able to tell a disconnected sensor from a very cold pond).
    JsonDocument doc;
    if (tempOk) {
      JsonObject tempObj = doc.createNestedObject();
      tempObj["sensor_type"] = "temp"; tempObj["data1"] = measuredTempC;
    }
    JsonObject tdsObj  = doc.createNestedObject(); tdsObj["sensor_type"]  = "TDS";  tdsObj["data1"]  = tds;
    JsonObject phObj   = doc.createNestedObject(); phObj["sensor_type"]   = "pH";   phObj["data1"]   = ph;
    JsonObject luxObj  = doc.createNestedObject(); luxObj["sensor_type"]  = "LUX";  luxObj["data1"]  = luxOk ? lux : -1.0f;
    if (!tempOk) Serial.println("NOTE: temp sensor failed this cycle - omitted from upload, not faked as 25C");

    String json; serializeJson(doc, json);
    int code = db.insert(TABLE, json, false);
    Serial.printf("Supabase insert -> %d\n", code);
    db.urlQuery_reset();
    sleepSec = SLEEP_S;   // normal cadence resumes once the network is healthy
  } else {
    wifiFailStreak++;
    Serial.printf("Wi-Fi failed %u time(s) in a row -> sleeping %lu s\n", wifiFailStreak, sleepSec);
  }

  goToSleep(sleepSec);
}

void loop() {}
