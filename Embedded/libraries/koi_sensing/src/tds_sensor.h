#pragma once
// Extracted from archive/pre-refactor/Embedded/FullSketch/FullSketch.ino (tdsRawPpm) and
// archive/pre-refactor/Embedded/TempSensor/TempSensor.ino (the inline duplicate of the same formula).
// Both sketches carried an identical DFRobot/Keyestudio-style TDS curve with
// 2%/C temperature compensation - this is the single shared home for it.

// mv: raw probe reading in millivolts. tC: water temperature in Celsius.
// K: per-device calibration factor (FullSketch's "tdscal" command), 1.0 if uncalibrated.
inline float tdsRawPpm(float mv, float tC) {
  float v  = mv / 1000.0f;
  float vc = v / (1.0f + 0.02f * (tC - 25.0f));
  float ppm = (133.42f * vc * vc * vc - 255.86f * vc * vc + 857.39f * vc) * 0.5f;
  return ppm < 0 ? 0 : ppm;
}

inline float tdsCalibratedPpm(float mv, float tC, float k) {
  return tdsRawPpm(mv, tC) * k;
}

// FullSketch's "TDS factor 0.5" EC conversion, pulled out so it isn't a
// magic literal buried in runCycle().
inline float tdsPpmToEcUs(float ppm) {
  return ppm / 0.5f;
}
