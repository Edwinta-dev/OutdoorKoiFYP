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

void setup()
{
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

void loop()
{

  // Temperature Sensor

  sensors.requestTemperatures();
  float temperatureC = sensors.getTempCByIndex(0);
  Serial.print("Temperature in C: ");
  Serial.print(temperatureC);
  Serial.println("ºC");

  // PH Sensor Reading
  // TODO Work in PH Sensor with a voltage step down

  int pHanalogValue = analogRead(34);
  float voltage = pHanalogValue * (3.3 / 4095.0);
  voltage *= 2;
  // 3. Apply the Approximate Calibration Curve
  float approximate_slope = -1.49;
  float approximate_intercept = 12.95;
  float calculated_pH = (approximate_slope * voltage) + approximate_intercept;

  // 4. Print the results
  Serial.print("Voltage: ");
  Serial.print(voltage, 2);
  Serial.print("V | Approx pH: ");
  Serial.println(calculated_pH, 2);

  // TDS Sensor Reading

  int tdsanalogValue = analogRead(35);
  // 2. Convert to voltage (ESP32 uses 12-bit ADC: 0-4095 steps)
  float tdsvoltage = tdsanalogValue * (3.3 / 4095.0);
  // 3. Apply temperature compensation
  float compensationCoefficient = 1.0 + 0.02 * (temperatureC - 25.0);
  float compensationVoltage = tdsvoltage / compensationCoefficient;
  // 4. Convert the compensated voltage into a TDS ppm value
  // Using the standard polynomial formula for this specific sensor type
  float tdsValue = (133.42 * pow(compensationVoltage, 3) - 255.86 * pow(compensationVoltage, 2) + 857.39 * compensationVoltage) * 0.5;
  Serial.print("TDS voltage is: ");
  Serial.println(tdsvoltage);
  Serial.print("TDS Value: ");
  Serial.print(tdsValue, 0);
  Serial.println(" ppm");

  // TODO Work in Light Sensor after soldering

  // Uploading into database

  // Temperature Object
  doc.clear();
  JsonObject tempObj = doc.createNestedObject();
  tempObj["sensor_type"] = "temp";
  tempObj["data1"] = temperatureC;

  // TDS Object
  JsonObject tdsObj = doc.createNestedObject();
  tdsObj["sensor_type"] = "TDS";
  tdsObj["data1"] = tdsValue;

  // PH Object
  JsonObject phObj = doc.createNestedObject();
  phObj["sensor_type"] = "pH";
  phObj["data1"] = calculated_pH;

  // Testing value
  float luxValue = 35.0;
  // LUX Object
  JsonObject luxObj = doc.createNestedObject();
  luxObj["sensor_type"] = "LUX";
  luxObj["data1"] = luxValue;

  // Serializing
  String jsonOutput;
  serializeJson(doc, jsonOutput);
  Serial.println(jsonOutput);
  int code = db.insert(table, jsonOutput, upsert);
  Serial.println(code);
  db.urlQuery_reset();

  delay(5000);
}
