// RS485 pH module bench test (ESP32 + auto-flow RS485-TTL converter)
// Serial Monitor 115200, "No line ending" or "Newline" both fine. Commands:
//   i = identify (broadcast 0xFF, reads address)   s = status (baud code, report period)
//   q = quiet (report period 0)                      p = read pH
//   4 / 7 / 0 = calibrate at pH 4.0 / 7.0 / 10.0 (probe in that buffer first)
//   w = print pH every second for 60 s (to measure warm-up / settling time)

#include <Arduino.h>

#define RS485_RX  16      // ESP32 RX2  <- converter TXD
#define RS485_TX  17      // ESP32 TX2  -> converter RXD
#define USE_DE    0       // 0 = auto-flow converter (yours). 1 = board with DE/RE pins on GPIO4
#define RS485_DE  4
#define BAUD      9600

HardwareSerial &rs485 = Serial2;
uint8_t slaveId = 0x01;

enum { ERR_NO_REPLY = -1, ERR_TOO_LONG = -2, ERR_CUT_OFF = -3, ERR_CRC = -4,
       ERR_WRONG_ID = -5, ERR_EXCEPTION = -6, ERR_UNEXPECTED = -7 };

uint16_t crc16(const uint8_t *buf, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= buf[i];
    for (int b = 0; b < 8; b++) crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : crc >> 1;
  }
  return crc;
}

void printHex(const char *tag, const uint8_t *b, size_t n) {
  Serial.print(tag);
  for (size_t i = 0; i < n; i++) Serial.printf("%02X ", b[i]);
  Serial.println();
}

void sendFrame(uint8_t *f, size_t lenNoCrc) {
  uint16_t crc = crc16(f, lenNoCrc);
  f[lenNoCrc] = crc & 0xFF;  f[lenNoCrc + 1] = crc >> 8;
  while (rs485.available()) rs485.read();
#if USE_DE
  digitalWrite(RS485_DE, HIGH);
#endif
  rs485.write(f, lenNoCrc + 2);
  rs485.flush();
#if USE_DE
  digitalWrite(RS485_DE, LOW);
#endif
  printHex("TX: ", f, lenNoCrc + 2);
}

int readResponse(uint8_t id, uint8_t *buf, size_t maxLen, uint32_t waitMs) {
  uint32_t t = millis();
  while (!rs485.available()) { if (millis() - t > waitMs) return ERR_NO_REPLY; delay(1); }
  size_t n = 0, expected = 5;
  t = millis();
  while (n < expected) {
    if (rs485.available()) {
      buf[n++] = rs485.read(); t = millis();
      if (n == 3) {
        expected = (buf[1] & 0x80) ? 5 : 3 + buf[2] + 2;
        if (expected > maxLen) return ERR_TOO_LONG;
      }
    } else if (millis() - t > 50) { printHex("RX (partial): ", buf, n); return ERR_CUT_OFF; }
  }
  printHex("RX: ", buf, n);
  uint16_t crc = crc16(buf, n - 2);
  if (buf[n - 2] != (crc & 0xFF) || buf[n - 1] != (crc >> 8)) return ERR_CRC;
  if (buf[0] != id)   return ERR_WRONG_ID;
  if (buf[1] & 0x80)  return ERR_EXCEPTION;
  return n;
}

// Every command on this module: 8-byte request -> 7-byte reply carrying one 16-bit value
int transact(uint8_t id, uint8_t fc, uint16_t reg, uint16_t val, uint16_t &out,
             uint32_t waitMs = 1000) {
  uint8_t req[8] = { id, fc, (uint8_t)(reg >> 8), (uint8_t)reg,
                     (uint8_t)(val >> 8), (uint8_t)val };
  uint8_t resp[16];
  sendFrame(req, 6);
  int r = readResponse(id, resp, sizeof resp, waitMs);
  if (r < 0) return r;
  if (resp[1] != fc || resp[2] != 2) return ERR_UNEXPECTED;
  out = ((uint16_t)resp[3] << 8) | resp[4];
  return 0;
}

int readReg (uint16_t reg, uint16_t &out) { return transact(slaveId, 0x03, reg, 1, out); }
int writeReg(uint16_t reg, uint16_t val)  { uint16_t e; return transact(slaveId, 0x06, reg, val, e); }

int calibrate(uint16_t reg, uint16_t &mV) {
  uint16_t ack;
  int r = transact(slaveId, 0x06, reg, 0, ack);          // stage 1: immediate ack
  if (r < 0) return r;
  uint8_t resp[16];
  r = readResponse(slaveId, resp, sizeof resp, 20000);    // stage 2: ~10 s later
  if (r < 0) return r;
  mV = ((uint16_t)resp[3] << 8) | resp[4];
  return 0;
}

const char *errName(int e) {
  switch (e) {
    case ERR_NO_REPLY:   return "no reply (wiring A/B, TX/RX swap, 12V power, common GND, baud)";
    case ERR_CUT_OFF:    return "reply cut off mid-frame";
    case ERR_CRC:        return "bad CRC (noise, or auto-report frames - try 'q')";
    case ERR_WRONG_ID:   return "reply from unexpected address";
    case ERR_EXCEPTION:  return "Modbus exception";
    case ERR_UNEXPECTED: return "unexpected reply format";
    default:             return "error";
  }
}
void report(const char *what, int e) { Serial.printf("%s FAILED: %s (%d)\n", what, errName(e), e); }

void setup() {
  Serial.begin(115200);
#if USE_DE
  pinMode(RS485_DE, OUTPUT); digitalWrite(RS485_DE, LOW);
#endif
  rs485.begin(BAUD, SERIAL_8N1, RS485_RX, RS485_TX);
  delay(300);
  Serial.println("\npH bench test. i=identify s=status q=quiet p=pH w=watch 60s | 4/7/0=calibrate pH4/7/10");
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  uint16_t v; int e;
  switch (c) {
    case 'i':
      if ((e = transact(0xFF, 0x03, 1, 1, v)) == 0) { slaveId = v; Serial.printf("Module address = %u\n", v); }
      else report("Identify", e);
      break;
    case 's':
      if ((e = readReg(2, v)) == 0) Serial.printf("Baud code = %u (3 = 9600)\n", v); else report("Baud read", e);
      if ((e = readReg(3, v)) == 0) Serial.printf("Report period = %u s (0 = off)\n", v); else report("Period read", e);
      break;
    case 'q':
      if ((e = writeReg(3, 0)) == 0) Serial.println("Auto-report off"); else report("Set period", e);
      break;
    case 'p':
      if ((e = readReg(0, v)) == 0) Serial.printf("pH = %.1f\n", v / 10.0); else report("pH read", e);
      break;
    case 'w':
      for (int s = 0; s < 60; s++) {
        if ((e = readReg(0, v)) == 0) Serial.printf("t=%2d s  pH = %.1f\n", s, v / 10.0);
        else report("pH read", e);
        delay(1000);
      }
      break;
    case '4': case '7': case '0': {
      uint16_t reg = (c == '4') ? 4 : (c == '7') ? 5 : 6;
      const char *name = (c == '4') ? "4.0" : (c == '7') ? "7.0" : "10.0";
      Serial.printf("Calibrating pH %s ... wait ~10 s\n", name);
      if ((e = calibrate(reg, v)) == 0) Serial.printf("pH %s done: %u mV. Rinse and dry the probe.\n", name, v);
      else report("Calibration", e);
      break;
    }
  }
}
