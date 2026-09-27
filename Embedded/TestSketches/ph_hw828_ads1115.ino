// HW-828 pH board -> ADS1115 (A0) -> ESP32. Calibration + reading sketch.
// Library: "Adafruit ADS1X15" (Library Manager). Serial Monitor 115200.
//
// Wiring: Po --10k--+-- ADS1115 A0      ADS1115 VDD -> 3V3, GND -> rail
//                  100nF -> GND         SDA -> GPIO21, SCL -> GPIO22, ADDR -> GND (0x48)
//         HW-828 G -> GND rail, V+ <- 6V pack via 1N400x (stripe toward board)
//
// Commands:
//   p  read Po voltage and pH
//   w  watch: print once a second for 60 s (warm-up / settling)
//   4 / 7 / 0  store calibration point for pH 4.00 / 7.00 / 10.00 (probe settled in that buffer)
//   c  show calibration
//   x  clear calibration

#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <Preferences.h>

#define SDA_PIN 21
#define SCL_PIN 22
#define ADS_ADDR 0x48
#define SETTLE_MV 3.0f        // a point is stored only if 5 s of readings stay within this spread

Adafruit_ADS1115 ads;
Preferences prefs;

const float BUF_PH[3] = {4.00f, 7.00f, 10.00f};
float calV[3] = {NAN, NAN, NAN};           // Po voltage measured in each buffer

// ---------- measurement ----------
float readPoVolts(int n = 16) {             // 16 samples at 16 SPS ~ 1 s
  float sum = 0;
  for (int i = 0; i < n; i++) sum += ads.computeVolts(ads.readADC_SingleEnded(0));
  return sum / n;
}

// 5 one-second means; returns the average, reports the spread
float readSettled(float &spreadMv) {
  float lo = 1e9, hi = -1e9, sum = 0;
  for (int i = 0; i < 5; i++) {
    float v = readPoVolts();
    lo = min(lo, v); hi = max(hi, v); sum += v;
  }
  spreadMv = (hi - lo) * 1000.0f;
  return sum / 5;
}

// ---------- calibration ----------
// Least-squares line pH = a + b*V through whichever points exist (needs 2+)
bool fitLine(float &a, float &b, int &n) {
  float sx = 0, sy = 0, sxx = 0, sxy = 0; n = 0;
  for (int i = 0; i < 3; i++) if (!isnan(calV[i])) {
    sx += calV[i]; sy += BUF_PH[i]; sxx += calV[i] * calV[i]; sxy += calV[i] * BUF_PH[i]; n++;
  }
  if (n < 2) return false;
  float d = n * sxx - sx * sx;
  if (fabs(d) < 1e-9) return false;
  b = (n * sxy - sx * sy) / d;
  a = (sy - b * sx) / n;
  return true;
}

bool voltsToPh(float v, float &ph) {
  float a, b; int n;
  if (!fitLine(a, b, n)) return false;      // not calibrated: no pH, only volts
  ph = a + b * v;
  return true;
}

void loadCal() {
  prefs.begin("ph", true);
  calV[0] = prefs.getFloat("v4", NAN);
  calV[1] = prefs.getFloat("v7", NAN);
  calV[2] = prefs.getFloat("v10", NAN);
  prefs.end();
}

void saveCal() {
  prefs.begin("ph", false);
  const char* keys[3] = {"v4", "v7", "v10"};
  for (int i = 0; i < 3; i++) {
    if (isnan(calV[i])) prefs.remove(keys[i]); else prefs.putFloat(keys[i], calV[i]);
  }
  prefs.end();
}

void showCal() {
  for (int i = 0; i < 3; i++) {
    if (isnan(calV[i])) Serial.printf("  pH %5.2f : not set\n", BUF_PH[i]);
    else                Serial.printf("  pH %5.2f : %.4f V\n", BUF_PH[i], calV[i]);
  }
  float a, b; int n;
  if (!fitLine(a, b, n)) { Serial.println("  Not calibrated (need at least 2 points)"); return; }
  float mvPerPh = -1000.0f / b;             // Po change per pH unit
  Serial.printf("  %d points, slope %.1f mV per pH, pH 7 at %.4f V\n", n, mvPerPh, (7.0f - a) / b);
  if (mvPerPh <= 0) Serial.println("  WARNING: Po rises with pH - buffers mixed up or wiring wrong");
  if (n == 3) {
    float worst = 0;
    for (int i = 0; i < 3; i++) worst = max(worst, fabs(a + b * calV[i] - BUF_PH[i]));
    Serial.printf("  worst fit error %.2f pH%s\n", worst, worst > 0.1f ? "  (high: redo the points)" : "");
  }
}

void storePoint(int idx) {
  Serial.printf("Measuring pH %.2f buffer for 5 s, keep the probe still...\n", BUF_PH[idx]);
  float spread;
  float v = readSettled(spread);
  if (spread > SETTLE_MV) {
    Serial.printf("Not settled (%.1f mV drift). Wait a little and press again.\n", spread);
    return;
  }
  calV[idx] = v;
  saveCal();
  Serial.printf("Stored pH %.2f = %.4f V (drift %.1f mV). Rinse and dry the probe.\n", BUF_PH[idx], v, spread);
  showCal();
}

void printReading(float v) {
  float ph;
  if (voltsToPh(v, ph)) Serial.printf("Po %.4f V  ->  pH %.2f\n", v, ph);
  else                  Serial.printf("Po %.4f V  (not calibrated)\n", v);
}

// ---------- setup / loop ----------
void setup() {
  Serial.begin(115200);
  delay(300);
  Wire.begin(SDA_PIN, SCL_PIN);
  if (!ads.begin(ADS_ADDR)) {
    Serial.println("ADS1115 not found at 0x48. Check VDD=3V3, GND, SDA=21, SCL=22, ADDR->GND.");
    while (true) delay(1000);
  }
  ads.setGain(GAIN_ONE);                    // +/-4.096 V range, 125 uV per step
  ads.setDataRate(RATE_ADS1115_16SPS);      // slow = low noise
  loadCal();
  Serial.println("\nHW-828 + ADS1115 pH. p=read w=watch 4/7/0=calibrate c=show x=clear");
  showCal();
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  switch (c) {
    case 'p': printReading(readPoVolts()); break;
    case 'w':
      for (int s = 0; s < 60; s++) { Serial.printf("t=%2d s  ", s); printReading(readPoVolts()); }
      break;
    case '4': storePoint(0); break;
    case '7': storePoint(1); break;
    case '0': storePoint(2); break;
    case 'c': showCal(); break;
    case 'x':
      for (int i = 0; i < 3; i++) calV[i] = NAN;
      saveCal();
      Serial.println("Calibration cleared.");
      break;
  }
}
