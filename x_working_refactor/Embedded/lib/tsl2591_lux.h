#pragma once
#include <cstdint>
// Extracted from Embedded/FullSketch/FullSketch.ino: readTsl()'s auto-gain
// stepping and lux formula. The I2C register read/write needs real hardware;
// the "what should we do with this reading" decision is pure logic.

enum class TslGainAction { Accept, LowerGain, RaiseGain };

// Mirrors readTsl()'s loop body exactly: saturated -> drop a gain step (if
// not already lowest), too dark -> raise a step (if not already highest),
// otherwise the reading is usable as-is.
inline TslGainAction tslGainDecision(uint16_t ch0, uint16_t ch1, uint16_t satCount,
                                      int gainIdx, int maxGainIdx) {
  bool sat = (ch0 >= satCount || ch1 >= satCount);
  if (sat && gainIdx > 0)              return TslGainAction::LowerGain;
  if (ch0 < 100 && gainIdx < maxGainIdx) return TslGainAction::RaiseGain;
  return TslGainAction::Accept;
}

// TSL2591 datasheet lux formula (CH0/CH1 counts -> lux), for a 100ms
// integration time (fixed divisor 408 baked in, matching FullSketch).
inline float tslComputeLux(uint16_t ch0, uint16_t ch1, float gainX) {
  if (ch0 == 0) return 0;
  float cpl = (100.0f * gainX) / 408.0f;
  float lux = ((float)ch0 - (float)ch1) * (1.0f - (float)ch1 / (float)ch0) / cpl;
  return lux < 0 ? 0 : lux;
}
