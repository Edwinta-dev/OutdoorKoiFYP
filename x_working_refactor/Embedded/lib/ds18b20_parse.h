#pragma once
#include <cstdint>
#include <cmath>
// Extracted from Embedded/FullSketch/FullSketch.ino and
// Embedded/DS18B20Sketch/DS18B20Sketch.ino, which both carried an identical
// copy of this scratchpad validation. The 1-Wire bus transaction (ow.reset/
// write/read) needs real hardware; only the "is this scratchpad trustworthy"
// decision is pure logic.

// Maxim/Dallas 1-Wire CRC8 (poly 0x8C, reflected) - same algorithm as
// OneWire::crc8(), reimplemented here so it's testable without the OneWire
// library (which requires Arduino.h).
inline uint8_t oneWireCrc8(const uint8_t *data, uint8_t len) {
  uint8_t crc = 0;
  while (len--) {
    uint8_t inbyte = *data++;
    for (uint8_t i = 8; i; i--) {
      uint8_t mix = (crc ^ inbyte) & 0x01;
      crc >>= 1;
      if (mix) crc ^= 0x8C;
      inbyte >>= 1;
    }
  }
  return crc;
}

enum class DsError { None, NoPresencePulse, CrcFail, InvalidConfig, PowerOnValue };

inline const char *dsErrorMessage(DsError e) {
  switch (e) {
    case DsError::NoPresencePulse: return "no presence pulse";
    case DsError::CrcFail:         return "scratchpad CRC fail";
    case DsError::InvalidConfig:   return "invalid config byte";
    case DsError::PowerOnValue:    return "85.0 C power-on value";
    default:                       return "";
  }
}

struct DsReading {
  DsError error = DsError::None;
  float   tempC = NAN;
};

// d must be the 9-byte scratchpad (bytes 0-1 = temp LSB/MSB, byte 4 = config,
// byte 8 = CRC), exactly as read from the DS18B20 after a 0xBE command.
inline DsReading parseDs18b20Scratchpad(const uint8_t d[9]) {
  DsReading r;
  int16_t raw = (int16_t)((d[1] << 8) | d[0]);
  if (oneWireCrc8(d, 8) != d[8]) {
    r.error = DsError::CrcFail;
  } else if ((d[4] & 0x1F) != 0x1F) {
    r.error = DsError::InvalidConfig;
  } else if (raw == 0x0550) {
    r.error = DsError::PowerOnValue;
  } else {
    r.tempC = raw / 16.0f;
  }
  return r;
}
