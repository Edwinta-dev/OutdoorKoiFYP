#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>

// AI-Thinker Physical Board Routings
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
const char *ssid = "107";
const char *password = "6Casting.";
// --- Replace with Server URL instead of Localhost IP ---
const char *server_ip = "192.168.68.61";
unsigned long lastTriggerTime = 0;
const unsigned long loopInterval = 10000; // Fires every 10 seconds

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

  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.fb_count = 1;
  config.jpeg_quality = 12;
  config.frame_size = FRAMESIZE_VGA;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK)
  {
    while (true)
      ;
  }
}

void setup()
{
  Serial.begin(115200);
  initCamera();

  // Establish Wireless Connection Link
  Serial.print("Connecting to local network: ");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi Network Bridge Active!");
}

void loop()
{
  unsigned long currentMillis = millis();

  if (currentMillis - lastTriggerTime >= loopInterval)
  {
    lastTriggerTime = currentMillis;

    if (WiFi.status() != WL_CONNECTED)
    {
      Serial.println("Network dropped! Skipping cycle...");
      return;
    }

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb)
    {
      Serial.println("Camera capture failed!");
      return;
    }

    // Build the structural destination string URL pointing to Flask
    String serverUrl = "http://" + String(server_ip) + ":5000/upload";

    HTTPClient http;
    http.begin(serverUrl);

    // Explicitly set the transmission context type header to handle binary
    http.addHeader("Content-Type", "application/octet-stream");

    // Execute the POST operation directly pushing the memory bytes array across Wi-Fi
    int httpResponseCode = http.POST(fb->buf, fb->len);

    if (httpResponseCode > 0)
    {
      Serial.printf("Network Push Complete. Server HTTP Status: %d\n", httpResponseCode);
    }
    else
    {
      Serial.printf("Network Error Occurred: %s\n", http.errorToString(httpResponseCode).c_str());
    }

    http.end();
    esp_camera_fb_return(fb); // Clear volatile buffer indices instantly
  }
}