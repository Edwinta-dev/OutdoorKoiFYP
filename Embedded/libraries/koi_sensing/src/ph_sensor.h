#pragma once
// Extracted from archive/pre-refactor/Embedded/FullSketch/FullSketch.ino: bufferPhAt(), mvToPh(),
// fitCalibration(). Behavior is ported unchanged; only the Serial.print calls
// and NVS (Preferences) persistence were left behind in the .ino, since those
// aren't pure logic.

struct PhBuffer {
  char  key;
  float nominal;
  float ph[6];  // pH of this buffer at each PhTableT[] temperature
};

inline const float *phTableT() {
  static const float t[6] = {10, 20, 25, 30, 35, 40};
  return t;
}

// Temperature-compensated pH of a calibration buffer at water temperature tC,
// linearly interpolated across the datasheet table (clamped at the ends).
inline float bufferPhAt(const PhBuffer &b, float tC) {
  const float *t = phTableT();
  const int NT = 6;
  if (tC <= t[0])      return b.ph[0];
  if (tC >= t[NT - 1]) return b.ph[NT - 1];
  for (int i = 0; i < NT - 1; i++) {
    if (tC <= t[i + 1]) {
      float f = (tC - t[i]) / (t[i + 1] - t[i]);
      return b.ph[i] + f * (b.ph[i + 1] - b.ph[i]);
    }
  }
  return b.nominal;
}

struct PhFit {
  double slope25 = -3.0 / (2032.44 - 1500.0);  // FullSketch's factory-default fit
  double v7      = 1500.0;
  double tcal    = 25.0;
  bool   valid   = false;
  bool   rejectedNearIdentical = false;  // sxx < 1.0: FullSketch's "nearly identical voltages" warning
};

inline float mvToPh(const PhFit &fit, float mv, float tC) {
  double slopeT = fit.slope25 * 298.15 / (tC + 273.15);
  return 7.0 + slopeT * (mv - fit.v7);
}

// mv/ref/tC/ok are parallel arrays of length n (one slot per calibration buffer).
inline PhFit fitPhCalibration(const float *mv, const float *ref, const float *tC,
                               const bool *ok, int n) {
  PhFit out;
  int cnt = 0;
  double xm = 0, ym = 0, tm = 0;
  for (int i = 0; i < n; i++) {
    if (ok[i]) { xm += mv[i]; ym += ref[i]; tm += tC[i]; cnt++; }
  }
  if (cnt < 2) return out;  // valid stays false, defaults kept (matches FullSketch)
  xm /= cnt; ym /= cnt; tm /= cnt;

  double sxy = 0, sxx = 0;
  for (int i = 0; i < n; i++) {
    if (!ok[i]) continue;
    double dx = mv[i] - xm;
    sxy += dx * (ref[i] - ym);
    sxx += dx * dx;
  }
  if (sxx < 1.0) {
    out.rejectedNearIdentical = true;
    return out;  // valid stays false
  }
  double slopeCal = sxy / sxx;
  out.tcal    = tm;
  out.v7      = xm + (7.0 - ym) / slopeCal;
  out.slope25 = slopeCal * (tm + 273.15) / 298.15;
  out.valid   = true;
  return out;
}
