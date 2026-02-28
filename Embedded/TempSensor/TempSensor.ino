#include <OneWire.h>
#include <DallasTemperature.h>
#include <WiFi.h>
#include <Arduino.h>
#include <ESPSupabase.h>


Supabase db;
String supabase_url = "https://mkzfdxhzmrnapvrhshte.supabase.co";
String anon_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1remZkeGh6bXJuYXB2cmhzaHRlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEzNTExMTgsImV4cCI6MjA4NjkyNzExOH0.x-f-jAGaps1fjzXx4FyKATHxtFOPb-s7_1_tqqpuQUs";

const char *ssid = "107";
const char *psswd = "6Casting.";

String table = "SensorData";
JsonDocument doc;
bool upsert = false;
String JSON = "";
const int oneWireBus = 18;     
OneWire oneWire(oneWireBus);
DallasTemperature sensors(&oneWire);

void setup() {
  Serial.begin(115200);
  sensors.begin();
  Serial.print("Connecting to WiFi");
  WiFi.begin(ssid, psswd);
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(100);
    Serial.print(".");
  }
  Serial.println("\nConnected!");
  db.begin(supabase_url, anon_key);
}

void loop() {
  sensors.requestTemperatures(); 
  float temperatureC = sensors.getTempCByIndex(0);
  Serial.print("Temperature in C: ");
  Serial.print(temperatureC);
  Serial.println("ºC");
  doc["sensor_type"] = "temp";
  doc["data1"] = temperatureC;
  serializeJson(doc, JSON);
  Serial.println(JSON);
  Serial.println("This is something else");
  int code = db.insert(table, JSON, upsert);
  Serial.println(code);
  db.urlQuery_reset();
  delay(60000);
}
