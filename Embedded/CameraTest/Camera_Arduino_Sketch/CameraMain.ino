#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h> // Fixed header include

// --- AI-Thinker Physical Board Pin Routing ---
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

// --- NETWORK PROFILE CONFIGURATION ---
const char *ssid = "107";                   // Wi-Fi Router Name
const char *password = "6Casting.";         // Wi-Fi Router Password
const char *flask_server = "192.168.68.61"; // Flask server URL
// Default fallback sleep duration (2 hours in seconds)
const unsigned long DEFAULT_SLEEP_SEC = 7200;

// --- Function Declarations ---
void initCamera();
bool ConnectWifi();
unsigned long UploadPhoto(camera_fb_t *fb);
unsigned long takePhoto();
void enterDeepSleep(unsigned long secondsToSleep);

void setup()
{
  Serial.begin(115200);
  delay(500); // Short delay to let Serial Monitor attach

  Serial.println("\n--- ESP32-CAM Wake Cycle Started ---");

  initCamera();

  unsigned long sleepSec = DEFAULT_SLEEP_SEC;

  // Attempt Wi-Fi Connection
  if (ConnectWifi())
  {
    // Capture photo and attempt upload
    sleepSec = takePhoto();
  }
  else
  {
    Serial.println("Wi-Fi failed. Entering fallback sleep mode.");
  }

  // Always enter deep sleep at the end of setup
  enterDeepSleep(sleepSec);
}

void loop()
{
  // Unreachable code due to Deep Sleep
}

void initCamera()
{
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;

  config.xclk_freq_hz = 10000000; // 10MHz stable clock
  config.pixel_format = PIXFORMAT_JPEG;
  config.fb_count = 1;
  config.jpeg_quality = 12;
  config.frame_size = FRAMESIZE_VGA; // 640x480

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK)
  {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    // If camera fails, sleep 5 mins and reboot
    enterDeepSleep(300);
  }
}

bool ConnectWifi()
{
  Serial.print("Connecting to Wi-Fi: ");
  WiFi.begin(ssid, password);

  int attempts = 0;
  // Non-blocking timeout guardrail (10 seconds max)
  while (WiFi.status() != WL_CONNECTED && attempts < 20)
  {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED)
  {
    Serial.println("\nWi-Fi Connected! IP: " + WiFi.localIP().toString());
    return true;
  }
  else
  {
    Serial.println("\nWi-Fi Connection Timed Out!");
    return false;
  }
}

unsigned long takePhoto()
{
  camera_fb_t *fb = esp_camera_fb_get();

  if (!fb)
  {
    Serial.println("Camera capture failed!");
    return DEFAULT_SLEEP_SEC; // Return fallback sleep if capture fails
  }

  // Pass frame buffer pointer into upload function
  unsigned long sleepSec = UploadPhoto(fb);

  // Clean up buffer memory
  esp_camera_fb_return(fb);
  return sleepSec;
}

unsigned long UploadPhoto(camera_fb_t *fb)
{
  unsigned long calculatedSleepSec = DEFAULT_SLEEP_SEC;

  String serverUrl = "http://" + String(flask_server) + ":5000/upload";
  HTTPClient http;
  http.begin(serverUrl);
  http.addHeader("Content-Type", "application/octet-stream");

  Serial.println("Posting photo payload to Flask...");
  int httpResponseCode = http.POST(fb->buf, fb->len);

  if (httpResponseCode == 200)
  {
    Serial.printf("Network Push Complete. Server Status: %d\n", httpResponseCode);

    String responseString = http.getString();
    Serial.println("Raw HTTP Response: " + responseString);

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, responseString);

    if (error)
    {
      Serial.print(F("deserializeJson() failed: "));
      Serial.println(error.f_str());
    }
    else if (doc.containsKey("sleep_sec"))
    {
      calculatedSleepSec = doc["sleep_sec"].as<unsigned long>();
      Serial.printf("Server scheduled next wake in %lu seconds.\n", calculatedSleepSec);
    }
  }
  else
  {
    Serial.printf("Network Error Occurred (Code %d): %s\n",
                  httpResponseCode,
                  http.errorToString(httpResponseCode).c_str());
  }

  http.end(); // Safely close HTTP socket
  return calculatedSleepSec;
}

void enterDeepSleep(unsigned long secondsToSleep)
{
  Serial.printf("Shutting down network & entering deep sleep for %lu seconds...\n", secondsToSleep);
  Serial.flush();

  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);

  esp_sleep_enable_timer_wakeup((uint64_t)secondsToSleep * 1000000ULL);
  esp_deep_sleep_start();
}