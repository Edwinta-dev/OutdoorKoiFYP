/*
 * OutdoorKoi sensor node — ESP32 DevKitC (WROOM-32)
 * Time-multiplexed: only ONE sensor is powered at any moment.
 *
 * Cycle order:  TDS -> TSL2591 -> GY-33 -> DS18B20 (temp) -> pH  -> upload -> deep sleep 5 min
 *   - TDS is first and pH is last, so the TDS AC excitation is off (and has been for
 *     several seconds) before the pH board is powered.
 *   - Temperature is read before pH. TDS is sampled first as a raw voltage and its
 *     temperature compensation is applied AFTER the temperature read, so both
 *     analog channels get this cycle's water temperature.
 *   - TSL2591 runs before GY-33, so the GY-33's illumination LED can never light the TSL2591.
 *
 * Wiring:
 *   TDS board   : VCC -> GPIO25   GND -> GND   AOUT -> GPIO34 (ADC1_CH6)
 *   pH V2 board : "+" -> GPIO4    "-" -> GND   signal -> GPIO35 (ADC1_CH7)
 *   TSL2591     : VIN -> GPIO26   GND -> GND   SDA -> GPIO21   SCL -> GPIO22   (bus: Wire)
 *   DS18B20     : VCC -> GPIO27   GND -> GND   DAT -> GPIO23   (breakout has the 4.7k pull-up)
 *   GY-33       : VCC -> GPIO32   GND -> GND   DR (SDA) -> GPIO18   CT (SCL) -> GPIO19
 *                 S0 -> GND  (selects the module's I2C mode, address 0x5A)          (bus: Wire1)
 *
 * Library: OneWire by Paul Stoffregen (v2.3.8+ for ESP32 core 3.x). Everything else is built in.
 *
 * Upload: after all five sensors are powered off, the node connects to WiFi, inserts the
 * readings into the SensorData table (same long format as the original firmware: one row
 * per sensor, {"sensor_type": ..., "data1": ...}), switches WiFi off, then sleeps.
 * No sensor is ever powered while WiFi transmits.
 *
 * Libraries: ESPSupabase (jhagas) and ArduinoJson v7, as in the original upload sketch.
 *
 * Serial Monitor @ 115200, line ending "Newline".
 * After each cycle there is a 3 s window: type any command to enter SERVICE MODE
 * (stays awake). Service mode commands:
 *   cal1 / cal9 / cal7 / cal6 : pH calibration in 10.01 / 9.18 / 7.00 / 6.86 buffer
 *   show        : water temperature, buffer pH at that temperature, pH points and fit
 *   clear       : delete all pH calibration points
 *   tdscal X    : TDS calibration in a known solution of X ppm (e.g. "tdscal 707")
 *   temp        : read water temperature only
 *   settle ph   : log pH mV every 100 ms for 30 s
 *   settle tds  : log TDS mV every 100 ms for 30 s
 *   scan        : power both I2C sensors and list the addresses found
 *   sample      : run one full cycle now and upload it
 *   upload      : re-send the last cycle's readings
 *   run         : leave service mode and resume the sleep cycle
 */

#include <Arduino.h>
#include <Wire.h>
#include <OneWire.h>
#include <Preferences.h>
#include "driver/gpio.h"
#include "esp_sleep.h"
#include <WiFi.h>
#include <ArduinoJson.h>
#include <ESPSupabase.h>

// =====================================================================
// WiFi / Supabase (from the original upload sketch)
// =====================================================================
// Network: WIFI_SSID, WIFI_PASS, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY.
// Kept out of git. Copy secrets.h.example to secrets.h in this folder and fill it in.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "full_sketch: secrets.h not found. Copy Embedded/full_sketch/secrets.h.example to secrets.h and fill in the values."
#endif

String table        = "SensorData";
const bool UPSERT   = false;

// Every row carries the user id. Column name must match SensorData exactly (case-sensitive).
const char *COL_USER_ID = "userID";
const int   USER_ID     = 455;

// Extra rows. GY-33 goes up as four new sensor_type values (GY33_R/G/B/C) in data1,
// so no new columns are needed. Turn on once the backend expects them.
const bool UPLOAD_GY33 = false;

const uint32_t WIFI_TIMEOUT_MS = 15000;

Supabase db;

// =====================================================================
// Pins
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
const uint64_t SLEEP_S            = 300;    // 5 minutes between cycles
const uint32_t SERVICE_WINDOW_MS  = 3000;

const uint32_t TDS_SETTLE_MS      = 2000;   // check with "settle tds"
uint32_t       PH_SETTLE_MS       = 5000;   // check with "settle ph"
const uint32_t TSL_POWER_UP_MS    = 50;
const uint32_t GY_POWER_UP_MS     = 1500;   // module MCU boot + first colour frames
const uint32_t DS_POWER_UP_MS     = 1000;
const uint32_t DS_CONVERSION_MS   = 800;
const int      DS_MAX_TRIES       = 3;

const int N_SAMPLES     = 31;
const int SAMPLE_GAP_MS = 20;

// Survives deep sleep
RTC_DATA_ATTR float    lastGoodTempC = NAN;
RTC_DATA_ATTR uint32_t cycleCount    = 0;

// =====================================================================
// Latest results (globals, so the Arduino IDE prototype generator stays happy)
// =====================================================================
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
// Shared helpers
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
  for (int i = 1; i < N_SAMPLES; i++) {
    int k = buf[i], j = i - 1;
    while (j >= 0 && buf[j] > k) { buf[j + 1] = buf[j]; j--; }
    buf[j + 1] = k;
  }
  return buf[N_SAMPLES / 2];
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
// 1. TDS
// =====================================================================
float tdsK = 1.0;   // calibration factor, set by "tdscal"

float measureTdsMv() {
  digitalWrite(TDS_PWR_PIN, HIGH);
  delay(TDS_SETTLE_MS);
  float mv = readMedianMv(TDS_ADC_PIN);
  digitalWrite(TDS_PWR_PIN, LOW);
  return mv;
}

// DFRobot/Keyestudio-style TDS curve with 2 %/C temperature compensation (K = 1)
float tdsRawPpm(float mv, float tC) {
  float v  = mv / 1000.0;
  float vc = v / (1.0 + 0.02 * (tC - 25.0));
  float ppm = (133.42 * vc * vc * vc - 255.86 * vc * vc + 857.39 * vc) * 0.5;
  return ppm < 0 ? 0 : ppm;
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
// 2. TSL2591 (raw registers, auto gain, 100 ms integration)
// =====================================================================
const uint8_t  TSL_ADDR = 0x29;
const uint8_t  TSL_CMD  = 0xA0;
const uint8_t  TSL_GAIN_BITS[] = {0x00, 0x10, 0x20};      // low, medium, high
const float    TSL_GAIN_X[]    = {1.0, 25.0, 428.0};
const uint16_t TSL_SAT_COUNT   = 36000;                  // 100 ms full scale is 36863

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

  int g = 1;  // start at medium gain
  for (int attempt = 0; ok && attempt < 4; attempt++) {
    ok = tslWrite(0x00, 0x00) && tslWrite(0x01, TSL_GAIN_BITS[g]) && tslWrite(0x00, 0x03);
    if (!ok) break;
    delay(250);                                        // > two integration periods
    uint8_t d[4];
    ok = tslRead(0x14, d, 4);
    if (!ok) break;
    uint16_t ch0 = d[0] | (d[1] << 8);
    uint16_t ch1 = d[2] | (d[3] << 8);
    bool sat = (ch0 >= TSL_SAT_COUNT || ch1 >= TSL_SAT_COUNT);

    if (sat && g > 0)       { g--; continue; }         // too bright: lower gain
    if (ch0 < 100 && g < 2) { g++; continue; }         // too dark: raise gain

    float cpl = (100.0 * TSL_GAIN_X[g]) / 408.0;
    float lux = (ch0 == 0) ? 0 : ((float)ch0 - ch1) * (1.0 - (float)ch1 / ch0) / cpl;
    r_lux = lux < 0 ? 0 : lux;
    r_tslCh0 = ch0; r_tslCh1 = ch1; r_tslGainIdx = g; r_tslSat = sat;
    r_tslOk = true;
    break;
  }
  if (ok) tslWrite(0x00, 0x00);                        // power the chip down

  Wire.end();
  pinMode(TSL_SDA_PIN, INPUT);
  pinMode(TSL_SCL_PIN, INPUT);
  digitalWrite(TSL_PWR_PIN, LOW);
}

// =====================================================================
// 3. GY-33 (module MCU in I2C mode, S0 tied to GND, address 0x5A)
// Register map: 0x00-07 raw R,G,B,C (16-bit, high byte first), 0x08-09 lux, 0x0A-0B colour temp
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
  if (ok) { delay(200); ok = gyRead(0x00, d, 12); }    // second read = a fresh frame
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
// 4. DS18B20 (raw OneWire, fixed waits)
// =====================================================================
OneWire ow(DS_DATA_PIN);

float readWaterTempC() {
  for (int attempt = 1; attempt <= DS_MAX_TRIES; attempt++) {
    const char *err = nullptr;
    float t = NAN;

    pinMode(DS_DATA_PIN, INPUT);
    digitalWrite(DS_PWR_PIN, HIGH);
    delay(DS_POWER_UP_MS);

    if (!ow.reset()) {
      err = "no presence pulse";
    } else {
      ow.skip();
      ow.write(0x44);
      delay(DS_CONVERSION_MS);
      ow.reset();
      ow.skip();
      ow.write(0xBE);
      byte d[9];
      for (int i = 0; i < 9; i++) d[i] = ow.read();
      int16_t raw = (d[1] << 8) | d[0];
      if (OneWire::crc8(d, 8) != d[8]) err = "scratchpad CRC fail";
      else if ((d[4] & 0x1F) != 0x1F)  err = "invalid config byte";
      else if (raw == 0x0550)          err = "85.0 C power-on value";
      else                             t = raw / 16.0;
    }

    pinMode(DS_DATA_PIN, INPUT);
    digitalWrite(DS_PWR_PIN, LOW);

    if (!err) { lastGoodTempC = t; return t; }
    Serial.printf("  DS18B20 try %d: %s\n", attempt, err);
    delay(100);
  }
  return NAN;
}

// Live reading, else last good reading (kept through deep sleep), else 25 C.
float tempForCompensation(bool *fallback) {
  float t = readWaterTempC();
  if (!isnan(t)) { *fallback = false; return t; }
  *fallback = true;
  return isnan(lastGoodTempC) ? 25.0 : lastGoodTempC;
}

// =====================================================================
// 5. pH (multi-point calibration, temperature-corrected buffers)
// =====================================================================
const int NT = 6;
const float TABLE_T[NT] = {10, 20, 25, 30, 35, 40};

struct Buffer {
  char  key;
  float nominal;
  float ph[NT];
};

Buffer BUFFERS[] = {
  {'1', 10.01, {10.177, 10.060, 10.010, 9.964, 9.923, 9.887}},
  {'9',  9.18, { 9.332,  9.225,  9.180, 9.139, 9.102, 9.068}},
  {'7',  7.00, { 7.060,  7.020,  7.000, 6.990, 6.980, 6.970}},
  {'6',  6.86, { 6.918,  6.876,  6.860, 6.848, 6.839, 6.833}},
};
const int NBUF = sizeof(BUFFERS) / sizeof(BUFFERS[0]);

float bufferPhAt(int idx, float tC) {
  const Buffer &b = BUFFERS[idx];
  if (tC <= TABLE_T[0])      return b.ph[0];
  if (tC >= TABLE_T[NT - 1]) return b.ph[NT - 1];
  for (int i = 0; i < NT - 1; i++) {
    if (tC <= TABLE_T[i + 1]) {
      float f = (tC - TABLE_T[i]) / (TABLE_T[i + 1] - TABLE_T[i]);
      return b.ph[i] + f * (b.ph[i + 1] - b.ph[i]);
    }
  }
  return b.nominal;
}

float bufMv[NBUF], bufRef[NBUF], bufT[NBUF];
bool  bufOk[NBUF];

double fitSlope25 = -3.0 / (2032.44 - 1500.0);
double fitV7      = 1500.0;
double fitTcal    = 25.0;
bool   fitValid   = false;

float measurePhMv() {
  digitalWrite(PH_PWR_PIN, HIGH);
  delay(PH_SETTLE_MS);
  float mv = readMedianMv(PH_ADC_PIN);
  digitalWrite(PH_PWR_PIN, LOW);
  return mv;
}

float mvToPh(float mv, float tC) {
  double slopeT = fitSlope25 * 298.15 / (tC + 273.15);
  return 7.0 + slopeT * (mv - fitV7);
}

void fitCalibration() {
  int n = 0;
  double xm = 0, ym = 0, tm = 0;
  for (int i = 0; i < NBUF; i++) {
    if (bufOk[i]) { xm += bufMv[i]; ym += bufRef[i]; tm += bufT[i]; n++; }
  }
  if (n < 2) { fitValid = false; return; }
  xm /= n; ym /= n; tm /= n;

  double sxy = 0, sxx = 0;
  for (int i = 0; i < NBUF; i++) {
    if (!bufOk[i]) continue;
    double dx = bufMv[i] - xm;
    sxy += dx * (bufRef[i] - ym);
    sxx += dx * dx;
  }
  if (sxx < 1.0) {
    fitValid = false;
    Serial.println("WARNING: pH calibration points have nearly identical voltages. Fit rejected.");
    return;
  }
  double slopeCal = sxy / sxx;
  fitTcal    = tm;
  fitV7      = xm + (7.0 - ym) / slopeCal;
  fitSlope25 = slopeCal * (tm + 273.15) / 298.15;
  fitValid   = true;
}

// Same namespace as ph_temp.cpp, so a calibration done there carries over.
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
  fitCalibration();
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
      float pred = mvToPh(bufMv[i], bufT[i]);
      Serial.printf("  %5.2f buffer: ref %.3f @%.2fC, %7.1f mV, fit %.3f (error %+.3f)\n",
                    BUFFERS[i].nominal, bufRef[i], bufT[i], bufMv[i], pred, pred - bufRef[i]);
    } else {
      Serial.printf("  %5.2f buffer: not calibrated\n", BUFFERS[i].nominal);
    }
  }
  if (fitValid) {
    Serial.printf("  Fit: V7 = %.1f mV, span = %.1f mV/pH at 25 C (cal temp %.2f C)\n",
                  fitV7, -1.0 / fitSlope25, fitTcal);
    if (fitSlope25 >= 0) Serial.println("  WARNING: voltage should FALL as pH rises. Check buffers.");
  } else {
    Serial.println("  Fit: need at least 2 points (pH is UNCALIBRATED)");
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
                  BUFFERS[i].key, BUFFERS[i].nominal, bufferPhAt(i, tNow));
  }
  Serial.println("pH calibration:");
  printPhPoints();
  Serial.printf("TDS K factor: %.3f\n", tdsK);
}

// =====================================================================
// Full cycle
// =====================================================================
void runCycle() {
  allPowerOff();
  Serial.printf("---- Cycle %lu ----\n", (unsigned long)cycleCount);

  r_tdsMv = measureTdsMv();                           // 1. TDS raw (compensated later)
  readTsl();                                          // 2. light
  readGy33();                                         // 3. colour
  r_tempC = tempForCompensation(&r_tempFallback);     // 4. water temperature
  r_phMv  = measurePhMv();                            // 5. pH (TDS off for several seconds by now)

  r_ph     = mvToPh(r_phMv, r_tempC);
  r_tdsPpm = tdsRawPpm(r_tdsMv, r_tempC) * tdsK;
  r_ecUs   = r_tdsPpm / 0.5;                          // TDS factor 0.5

  Serial.printf("  Temp   : %.2f C%s\n", r_tempC, r_tempFallback ? " [FALLBACK]" : "");
  Serial.printf("  pH     : %.2f  (%.1f mV)%s\n", r_ph, r_phMv, fitValid ? "" : " [UNCALIBRATED]");
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

  // One machine-readable line for logging
  Serial.printf("CSV,%lu,%.2f,%d,%.3f,%.1f,%.1f,%.1f,%.1f,%u,%u,%u,%u,%u,%u\n",
                (unsigned long)cycleCount, r_tempC, r_tempFallback ? 1 : 0,
                r_ph, r_phMv, r_tdsPpm, r_tdsMv, r_tslOk ? r_lux : -1.0,
                r_gyR, r_gyG, r_gyB, r_gyC, r_gyLux, r_gyCT);
}

// =====================================================================
// Supabase upload (runs after every sensor is powered off)
// =====================================================================
bool ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  Serial.printf("  WiFi: connecting to \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("  WiFi: connect FAILED, nothing uploaded");
    return false;
  }
  Serial.printf("  WiFi: connected, RSSI %d dBm\n", WiFi.RSSI());
  return true;
}

void wifiOff() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
}

// One row per sensor. Readings that failed or can't be trusted are left out
// instead of being uploaded as a wrong number.
void addRow(JsonArray &rows, const char *type, float value) {
  JsonObject o = rows.add<JsonObject>();
  o[COL_USER_ID]   = USER_ID;
  o["sensor_type"] = type;
  o["data1"]       = value;
}

bool uploadReadings() {
  allPowerOff();                                     // guarantee: no sensor on during TX

  JsonDocument doc;
  JsonArray rows = doc.to<JsonArray>();

  if (!r_tempFallback)        addRow(rows, "temp", r_tempC);
  else                        Serial.println("  skip temp: sensor read failed");

  addRow(rows, "TDS", r_tdsPpm);

  if (fitValid)               addRow(rows, "pH", r_ph);
  else                        Serial.println("  skip pH: not calibrated");

  if (r_tslOk && !r_tslSat)   addRow(rows, "LUX", r_lux);
  else                        Serial.println("  skip LUX: sensor failed or saturated");

  if (UPLOAD_GY33 && r_gyOk) {
    addRow(rows, "GY33_R", r_gyR);
    addRow(rows, "GY33_G", r_gyG);
    addRow(rows, "GY33_B", r_gyB);
    addRow(rows, "GY33_C", r_gyC);
  }

  if (rows.size() == 0) { Serial.println("  Supabase: nothing valid to upload"); return false; }

  String json;
  serializeJson(doc, json);

  if (!ensureWifi()) return false;
  db.begin(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

  int code = db.insert(table, json, UPSERT);
  db.urlQuery_reset();

  bool ok = (code >= 200 && code < 300);
  Serial.printf("  Supabase: %s (HTTP %d) %s\n", ok ? "uploaded" : "upload FAILED", code, json.c_str());
  return ok;
}

// =====================================================================
// Deep sleep
// =====================================================================
void goToSleep() {
  wifiOff();
  allPowerOff();
  for (int i = 0; i < N_POWER_PINS; i++) gpio_hold_en((gpio_num_t)POWER_PINS[i]);
  gpio_deep_sleep_hold_en();
  esp_sleep_enable_timer_wakeup(SLEEP_S * 1000000ULL);
  Serial.printf("Sleeping %llu s\n\n", SLEEP_S);
  Serial.flush();
  esp_deep_sleep_start();
}

// =====================================================================
// Service mode
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
  Serial.println("          settle ph, settle tds, scan, sample, upload, run");
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toLowerCase();
  allPowerOff();
  wifiOff();                                         // WiFi never on while a sensor samples

  if (cmd.length() == 4 && cmd.startsWith("cal")) {
    char k = cmd.charAt(3);
    for (int i = 0; i < NBUF; i++) {
      if (BUFFERS[i].key != k) continue;
      float tC = readWaterTempC();                     // temperature before pH
      if (isnan(tC)) { Serial.println("Calibration aborted: water temperature read failed."); return; }
      float ref = bufferPhAt(i, tC);
      Serial.printf("Measuring %.2f buffer at %.2f C (true pH %.3f)...\n", BUFFERS[i].nominal, tC, ref);
      bufMv[i] = measurePhMv(); bufRef[i] = ref; bufT[i] = tC; bufOk[i] = true;
      fitCalibration();
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
    fitCalibration();
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
    uploadReadings();
    wifiOff();
  } else if (cmd == "upload") {
    uploadReadings();
    wifiOff();
  } else if (cmd == "run") {
    Serial.println("Leaving service mode.");
    goToSleep();
  } else {
    printHelp();
  }
}

// =====================================================================
// Setup / loop
// =====================================================================
void setup() {
  // Power pins LOW first, then release the deep-sleep hold
  for (int i = 0; i < N_POWER_PINS; i++) {
    pinMode(POWER_PINS[i], OUTPUT);
    digitalWrite(POWER_PINS[i], LOW);
    gpio_hold_dis((gpio_num_t)POWER_PINS[i]);
    gpio_set_drive_capability((gpio_num_t)POWER_PINS[i], GPIO_DRIVE_CAP_3);
  }
  gpio_deep_sleep_hold_dis();

  // Signal pins released so nothing back-powers an unpowered sensor
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
    Serial.println("\nOutdoorKoi node — cold boot");
    Serial.println("pH calibration:");
    printPhPoints();
    Serial.printf("TDS K factor: %.3f\n", tdsK);
  }

  runCycle();          // all sensors: on -> sample -> off
  uploadReadings();    // then WiFi on -> POST
  wifiOff();

  Serial.printf("Type any command within %lu s for service mode...\n", SERVICE_WINDOW_MS / 1000);
  uint32_t t0 = millis();
  while (millis() - t0 < SERVICE_WINDOW_MS) {
    if (Serial.available()) {
      serviceMode = true;
      Serial.println("SERVICE MODE (type 'run' to resume the sleep cycle)");
      handleCommand(Serial.readStringUntil('\n'));
      return;                                          // loop() takes over
    }
    delay(10);
  }
  goToSleep();
}

void loop() {
  if (Serial.available()) handleCommand(Serial.readStringUntil('\n'));
}