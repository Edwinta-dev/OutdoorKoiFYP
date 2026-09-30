/*
 * DS18B20 breakout — ESP32 DevKitC (GPIO-powered, time-multiplexed, raw OneWire)
 *
 * Wiring (breakout already has the 4.7k pull-up from DAT to VCC):
 *   GPIO27 -> VCC   sensor power, switched
 *   GND    -> GND
 *   GPIO23 -> DAT
 *
 * Library: OneWire by Paul Stoffregen (v2.3.8+ for ESP32 core 3.x).
 * Serial Monitor @ 115200.
 */

#include <Arduino.h>
#include <OneWire.h>
#include "driver/gpio.h"

// ---------- Pins ----------
const int DS_PWR_PIN  = 27;
const int DS_DATA_PIN = 23;

// ---------- Timing ----------
const uint32_t POWER_UP_MS   = 1000;  // wait after power pin goes HIGH before any bus activity
const uint32_t CONVERSION_MS = 800;   // fixed wait for 12-bit conversion (750 ms max per datasheet)
const uint32_t CYCLE_MS      = 5000;
const int      MAX_TRIES     = 3;

OneWire ow(DS_DATA_PIN);

// ---------- Power control ----------
void dsPowerOn() {
  pinMode(DS_DATA_PIN, INPUT);
  digitalWrite(DS_PWR_PIN, HIGH);
  delay(POWER_UP_MS);
}

void dsPowerOff() {
  pinMode(DS_DATA_PIN, INPUT);       // not driving DAT; pull-up drops with VCC
  digitalWrite(DS_PWR_PIN, LOW);
}

// ---------- Reading ----------
// Returns temperature in C, or NAN on failure. One sensor on its own bus, so Skip ROM is used.
float readWaterTempC() {
  for (int attempt = 1; attempt <= MAX_TRIES; attempt++) {
    const char *err = nullptr;
    float t = NAN;

    dsPowerOn();

    if (!ow.reset()) {
      err = "no presence pulse";
    } else {
      ow.skip();
      ow.write(0x44);                    // start conversion
      delay(CONVERSION_MS);              // fixed wait, same as the working diag sketch

      ow.reset();
      ow.skip();
      ow.write(0xBE);                    // read scratchpad
      byte d[9];
      for (int i = 0; i < 9; i++) d[i] = ow.read();

      int16_t raw = (d[1] << 8) | d[0];
      if (OneWire::crc8(d, 8) != d[8])   err = "scratchpad CRC fail";
      else if ((d[4] & 0x1F) != 0x1F)    err = "invalid config byte (line stuck low?)";
      else if (raw == 0x0550)            err = "85.0 C power-on value (conversion did not run)";
      else                               t = raw / 16.0;
    }

    dsPowerOff();

    if (!err) return t;
    Serial.printf("  try %d: %s\n", attempt, err);
    delay(100);
  }
  return NAN;
}

void printAddress() {
  dsPowerOn();
  byte addr[8];
  ow.reset_search();
  if (ow.search(addr) && OneWire::crc8(addr, 7) == addr[7]) {
    Serial.print("Found sensor, ROM: ");
    for (int i = 0; i < 8; i++) Serial.printf("%02X", addr[i]);
    Serial.println(addr[0] == 0x28 ? "  (DS18B20)" : "  (not a DS18B20 family code)");
  } else {
    Serial.println("No sensor found. Check wiring.");
  }
  dsPowerOff();
}

// ---------- Setup / loop ----------
void setup() {
  pinMode(DS_PWR_PIN, OUTPUT);
  digitalWrite(DS_PWR_PIN, LOW);
  gpio_set_drive_capability((gpio_num_t)DS_PWR_PIN, GPIO_DRIVE_CAP_3);
  pinMode(DS_DATA_PIN, INPUT);

  Serial.begin(115200);
  delay(500);
  Serial.println("\nDS18B20 bench test (raw OneWire)");
  printAddress();
}

void loop() {
  static uint32_t lastCycle = 0;
  if (millis() - lastCycle >= CYCLE_MS) {
    lastCycle = millis();
    float t = readWaterTempC();
    if (isnan(t)) Serial.println("Temperature read FAILED");
    else          Serial.printf("Water temperature: %.2f C\n", t);
  }
}