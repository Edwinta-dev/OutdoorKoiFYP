// ESP32-CAM (AI-Thinker) -> PythonAnywhere /upload -> (server) HSV analysis + Supabase
// Wake -> capture -> camera off -> Wi-Fi -> POST JPEG -> read sleep_sec, reason, next_at -> deep sleep

#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "driver/rtc_io.h"

// --- AI-Thinker pin map ---
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM    5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22
#define FLASH_LED_PIN  4

// --- Network: WIFI_SSID, WIFI_PASS, UPLOAD_URL, DEVICE_TOKEN, USER_ID ---
// Kept out of git. Copy secrets.h.example to secrets.h in this folder and fill it in.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "camera_node: secrets.h not found. Copy Embedded/camera_node/secrets.h.example to secrets.h and fill in the values."
#endif

// Next-wake parsing and clamps (camera_wake.h), shared with the host tests.
#include <koi_sensing.h>
#include <sys/time.h>

// Lower clamp on the server's sleep, from secrets.h (issue #38). An older
// secrets.h without it gets 15 min; under 15 min needs CAMERA_TEST_BUILD.
#ifndef CAMERA_MIN_SLEEP_SEC
#define CAMERA_MIN_SLEEP_SEC CAMERA_MIN_SLEEP_DEFAULT_SEC
#endif
#ifdef CAMERA_TEST_BUILD
const bool TEST_BUILD = true;
#else
const bool TEST_BUILD = false;
#endif

// Used when Wi-Fi or the server fails. Was 60 s: a dead router would then wake the
// camera every minute and drain the AAA pack in hours. The server's own error
// replies carry sleep_sec, so this only applies when no reply arrives at all.
const unsigned long DEFAULT_SLEEP_SEC = 1800;
const unsigned long MIN_SLEEP_SEC     = cameraMinSleepSeconds(CAMERA_MIN_SLEEP_SEC, TEST_BUILD);
const unsigned long MAX_SLEEP_SEC     = 86400;

RTC_DATA_ATTR uint32_t bootCount     = 0;
RTC_DATA_ATTR uint32_t brownoutCount = 0;
RTC_DATA_ATTR uint32_t wifiFailStreak = 0;   // consecutive wakes with no Wi-Fi

// Retry sooner after a Wi-Fi failure, backing off if the network stays down
unsigned long wifiFailSleep(uint32_t streak) {
  if (streak <= 1) return 300;     // 5 min: probably a one-off blip
  if (streak == 2) return 900;     // 15 min
  return DEFAULT_SLEEP_SEC;        // 30 min: router likely down, save battery
}

WiFiClientSecure tls;
uint8_t* jpgBuf = nullptr;
size_t   jpgLen = 0;

bool initCamera();
bool capturePhoto();
void cameraOff();
bool connectWifi();
unsigned long uploadPhoto();
void enterDeepSleep(unsigned long seconds);

void setup() {
  Serial.begin(115200);
  delay(200);
  bootCount++;
  if (esp_reset_reason() == ESP_RST_BROWNOUT) brownoutCount++;
  Serial.printf("\n--- Wake #%u (brownout resets so far: %u) ---\n", bootCount, brownoutCount);
  Serial.printf("Shortest accepted sleep: %lu s%s\n", MIN_SLEEP_SEC, TEST_BUILD ? " (test build)" : "");

  rtc_gpio_hold_dis(GPIO_NUM_4);
  rtc_gpio_hold_dis(GPIO_NUM_32);

  // Capture first and power the camera down, so camera and Wi-Fi peaks don't overlap
  if (!initCamera()) enterDeepSleep(300);
  bool gotPhoto = capturePhoto();
  cameraOff();

  unsigned long sleepSec = DEFAULT_SLEEP_SEC;
  if (gotPhoto) {
    if (connectWifi()) {
      wifiFailStreak = 0;
      tls.setInsecure();   // encrypted, but server certificate not verified (prototype)
      sleepSec = uploadPhoto();
    } else {
      wifiFailStreak++;
      sleepSec = wifiFailSleep(wifiFailStreak);
      Serial.printf("Wi-Fi failed %u time(s) in a row\n", wifiFailStreak);
    }
  }
  enterDeepSleep(sleepSec);
}

void loop() {}

bool initCamera() {
  camera_config_t c;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;  c.pin_d1 = Y3_GPIO_NUM;  c.pin_d2 = Y4_GPIO_NUM;  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;  c.pin_d5 = Y7_GPIO_NUM;  c.pin_d6 = Y8_GPIO_NUM;  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 10000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size   = FRAMESIZE_VGA;
  c.jpeg_quality = 12;
  c.fb_count     = 1;
  c.fb_location  = CAMERA_FB_IN_PSRAM;
  c.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) { Serial.printf("Camera init failed: 0x%x\n", err); return false; }
  return true;
}

bool capturePhoto() {
  for (int i = 0; i < 2; i++) {            // let auto-exposure / white balance settle
    camera_fb_t* d = esp_camera_fb_get();
    if (d) esp_camera_fb_return(d);
    delay(150);
  }
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { Serial.println("Capture failed"); return false; }

  jpgBuf = (uint8_t*)ps_malloc(fb->len);
  if (!jpgBuf) jpgBuf = (uint8_t*)malloc(fb->len);
  if (!jpgBuf) { esp_camera_fb_return(fb); Serial.println("No memory for JPEG"); return false; }
  memcpy(jpgBuf, fb->buf, fb->len);
  jpgLen = fb->len;
  esp_camera_fb_return(fb);
  Serial.printf("Captured %u bytes\n", (unsigned)jpgLen);
  return true;
}

void cameraOff() {
  esp_camera_deinit();
  pinMode(PWDN_GPIO_NUM, OUTPUT);
  digitalWrite(PWDN_GPIO_NUM, HIGH);
}

const char* wifiStatusName(wl_status_t st) {
  switch (st) {
    case WL_NO_SSID_AVAIL: return "network not found (out of range or 5 GHz only?)";
    case WL_CONNECT_FAILED: return "connect failed (wrong password?)";
    case WL_CONNECTION_LOST: return "connection lost";
    case WL_DISCONNECTED: return "disconnected (weak signal / router busy)";
    case WL_IDLE_STATUS: return "idle";
    default: return "other";
  }
}

// Two attempts of up to 12 s each. The first attempt at -73 dBm can simply be too slow.
bool connectWifi() {
  WiFi.mode(WIFI_STA);
  for (int attempt = 1; attempt <= 2; attempt++) {
    Serial.printf("Wi-Fi attempt %d", attempt);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 12000) { delay(250); Serial.print("."); }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf(" OK in %lu ms, RSSI %d dBm\n", millis() - t0, WiFi.RSSI());
      return true;
    }
    wl_status_t st = WiFi.status();
    Serial.printf(" failed: %s (status %d)\n", wifiStatusName(st), (int)st);
    WiFi.disconnect(true);
    delay(500);
  }
  return false;
}

// Returns the seconds to sleep. Reads sleep_sec, reason and next_at from ANY
// reply that contains them, including the server's 4xx/5xx error replies.
// When the reply has a Date header the clock is set from it and the node
// sleeps until next_at; otherwise it sleeps for sleep_sec.
unsigned long uploadPhoto() {
  unsigned long s = DEFAULT_SLEEP_SEC;
  HTTPClient http;
  if (!http.begin(tls, UPLOAD_URL)) { Serial.println("HTTP begin failed"); return s; }
  http.setTimeout(20000);                  // analysis + Supabase round trips take a few seconds
  http.addHeader("Content-Type", "application/octet-stream");
  http.addHeader("X-User-ID", String(USER_ID));
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  const char* replyHeaders[] = {"Date"};
  http.collectHeaders(replyHeaders, 1);

  Serial.println("Posting photo...");
  bool haveServerNow = false, haveNextAt = false;
  int64_t serverNow = 0, nextAt = 0;
  int code = http.POST(jpgBuf, jpgLen);
  if (code > 0) {
    String body = http.getString();
    Serial.printf("Server %d: %s\n", code, body.c_str());

    String date = http.header("Date");
    haveServerNow = parseHttpDate(date.c_str(), serverNow);
    if (haveServerNow) {
      struct timeval tv;
      tv.tv_sec = (time_t)serverNow;
      tv.tv_usec = 0;
      settimeofday(&tv, nullptr);
      Serial.printf("Clock set from Date: %s\n", date.c_str());
    }

    JsonDocument doc;
    if (!deserializeJson(doc, body)) {
      if (doc["sleep_sec"].is<unsigned long>()) s = doc["sleep_sec"].as<unsigned long>();
      const char* reason = doc["reason"] | "none";
      const char* nextAtText = doc["next_at"] | "";
      haveNextAt = parseIsoTime(nextAtText, nextAt);
      Serial.printf("Reason: %s, next_at: %s\n", reason, haveNextAt ? nextAtText : "(missing)");
    }
  } else {
    Serial.printf("HTTP error %d: %s\n", code, http.errorToString(code).c_str());
  }
  http.end();

  // The clock was just set to serverNow, so next_at - serverNow is next_at - now.
  s = cameraWakeSleepSeconds(haveServerNow, serverNow, haveNextAt, nextAt, s, MIN_SLEEP_SEC, MAX_SLEEP_SEC);
  Serial.printf("Next wake in %lu s (%s)\n", s,
                haveServerNow && haveNextAt ? "to next_at" : "from sleep_sec");
  return s;
}

void enterDeepSleep(unsigned long seconds) {
  Serial.printf("Deep sleep for %lu s\n", seconds);
  Serial.flush();
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);

  pinMode(FLASH_LED_PIN, OUTPUT);          // flash LED held off during sleep
  digitalWrite(FLASH_LED_PIN, LOW);
  rtc_gpio_hold_en(GPIO_NUM_4);

  pinMode(PWDN_GPIO_NUM, OUTPUT);          // camera held in power-down
  digitalWrite(PWDN_GPIO_NUM, HIGH);
  rtc_gpio_hold_en(GPIO_NUM_32);

  esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
  esp_deep_sleep_start();
}
