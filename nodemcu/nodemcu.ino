/*
 * =================================================================================
 * NodeMCU ESP8266 WiFi Gateway for CareShield Elderly Monitoring
 * =================================================================================
 * Hardware Wiring:
 * - NodeMCU Pin D2 (GPIO4) = RX <- Connects to Arduino Uno Pin 10 (TX)
 * - NodeMCU Pin D1 (GPIO5) = TX -> Connects to Arduino Uno Pin 9 (RX)
 * - Common GND between Arduino Uno, NodeMCU, and Power Supply
 * =================================================================================
 */

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>
#include <SoftwareSerial.h>

// 1. PIN DEFINITION & SOFTWARE SERIAL TO ARDUINO
// D2 (GPIO4) = RX, receives from Arduino Pin 10 TX
// D1 (GPIO5) = TX, sends to Arduino Pin 9 RX
SoftwareSerial arduinoSerial(D2, D1);

// 2. HOTSPOT CREDENTIALS
const char* WIFI_SSID     = "OnePlus Nord CE 3";
const char* WIFI_PASSWORD = "AISHA123";

// 3. FLASK BACKEND URL (Your Laptop's Current Hotspot IP)
const char* FLASK_BASE_URL = "http://10.96.229.234:5000";

// LED indicator (Builtin LED on ESP-12E / NodeMCU, active LOW)
#define LED_PIN 2

// Caching and buffer
String incomingLine = "";
unsigned long lastWifiCheck = 0;
const unsigned long WIFI_CHECK_INTERVAL = 6000;

// =================================================================================
// HELPER: Robust WiFi Connection Routine
// =================================================================================
void connectToWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.println();
  Serial.print(F("[WiFi] Connecting to hotspot '"));
  Serial.print(WIFI_SSID);
  Serial.println(F("' (Ensure 2.4 GHz band is enabled on phone)..."));

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - startAttempt < 15000)) {
    delay(300);
    digitalWrite(LED_PIN, !digitalRead(LED_PIN)); // Blink LED
    Serial.print(F("."));
    yield();
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    digitalWrite(LED_PIN, LOW); // Solid ON (active low)
    Serial.println(F("[WiFi] >>> CONNECTED SUCCESSFULLY! <<<"));
    Serial.print(F("[WiFi] NodeMCU IP: "));
    Serial.println(WiFi.localIP());
    Serial.print(F("[WiFi] RSSI Signal: "));
    Serial.print(WiFi.RSSI());
    Serial.println(F(" dBm"));
    Serial.print(F("[WiFi] Target Server: "));
    Serial.println(FLASK_BASE_URL);
  } else {
    digitalWrite(LED_PIN, HIGH); // OFF
    Serial.println(F("[WiFi] FAILED to connect!"));
    Serial.println(F("IMPORTANT HOTSPOT CHECKS:"));
    Serial.println(F(" 1. In Phone Hotspot settings: Change Band from 5 GHz to 2.4 GHz Band!"));
    Serial.println(F(" 2. Turn OFF 'Wi-Fi 6 hotspot' if enabled on OnePlus."));
    Serial.println(F(" 3. Set Hotspot Security to WPA2-Personal."));
  }
}

// =================================================================================
// HELPER: Send HTTP POST to Flask API
// =================================================================================
bool postToFlask(const String& endpoint, const String& jsonPayload) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  WiFiClient client;
  HTTPClient http;
  String fullUrl = String(FLASK_BASE_URL) + endpoint;

  if (http.begin(client, fullUrl)) {
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(2500);

    int httpCode = http.POST(jsonPayload);
    if (httpCode > 0) {
      Serial.print(F("[HTTP] POST "));
      Serial.print(endpoint);
      Serial.print(F(" -> Status "));
      Serial.println(httpCode);
      http.end();
      return (httpCode == 200 || httpCode == 201);
    } else {
      Serial.print(F("[HTTP Error] POST failed: "));
      Serial.println(http.errorToString(httpCode));
      http.end();
      return false;
    }
  }
  return false;
}

// =================================================================================
// PARSER: Convert Arduino DATA & EVENT lines to JSON and forward to Flask
// =================================================================================
void handleArduinoMessage(const String& line) {
  // 1. Telemetry DATA packet
  if (line.startsWith("DATA:")) {
    String payload = line.substring(5);

    // Default values
    int hr = 0, spo2 = 0, finger = 0, sim = 0, ldr = 450, fsr = 0, armed = 1, fall = 0;
    int fsrDrop = 0, orientChg = 0, impactInact = 0;
    float dist = -1.0, accel = 1.0, gyro = 0.0, tilt = 0.0;
    String orient = "VERTICAL", decision = "NORMAL_MONITORING", illness = "NONE", alert = "NORMAL";

    // Parse key-value tokens
    int start = 0;
    while (start < payload.length()) {
      int eqIdx = payload.indexOf('=', start);
      if (eqIdx == -1) break;
      int commaIdx = payload.indexOf(',', eqIdx);
      if (commaIdx == -1) commaIdx = payload.length();

      String key = payload.substring(start, eqIdx);
      String val = payload.substring(eqIdx + 1, commaIdx);

      if (key == "HR") hr = val.toInt();
      else if (key == "SPO2") spo2 = val.toInt();
      else if (key == "FINGER") finger = val.toInt();
      else if (key == "SIM") sim = val.toInt();
      else if (key == "DIST") dist = val.toFloat();
      else if (key == "LDR") ldr = val.toInt();
      else if (key == "FSR") fsr = val.toInt();
      else if (key == "ACCEL") accel = val.toFloat();
      else if (key == "GYRO") gyro = val.toFloat();
      else if (key == "TILT") tilt = val.toFloat();
      else if (key == "ORIENT") orient = val;
      else if (key == "FSR_DROP") fsrDrop = val.toInt();
      else if (key == "ORIENT_CHG") orientChg = val.toInt();
      else if (key == "IMPACT_INACT") impactInact = val.toInt();
      else if (key == "DECISION") decision = val;
      else if (key == "ILLNESS") illness = val;
      else if (key == "ARMED") armed = val.toInt();
      else if (key == "FALL") fall = val.toInt();
      else if (key == "ALERT") alert = val;

      start = commaIdx + 1;
    }

    // Build JSON
    String json = "{";
    json += "\"heart_rate\":" + String(hr) + ",";
    json += "\"spo2\":" + String(spo2) + ",";
    json += "\"finger_present\":" + String(finger == 1 ? "true" : "false") + ",";
    json += "\"is_simulated\":" + String(sim == 1 ? "true" : "false") + ",";
    json += "\"distance\":" + String(dist, 1) + ",";
    json += "\"ldr\":" + String(ldr) + ",";
    json += "\"fsr\":" + String(fsr) + ",";
    json += "\"total_accel\":" + String(accel, 2) + ",";
    json += "\"total_gyro\":" + String(gyro, 1) + ",";
    json += "\"tilt_deg\":" + String(tilt, 1) + ",";
    json += "\"orientation\":\"" + orient + "\",";
    json += "\"fsr_pressure_drop\":" + String(fsrDrop == 1 ? "true" : "false") + ",";
    json += "\"orientation_change\":" + String(orientChg == 1 ? "true" : "false") + ",";
    json += "\"impact_inactivity\":" + String(impactInact == 1 ? "true" : "false") + ",";
    json += "\"decision\":\"" + decision + "\",";
    json += "\"illness_alert\":\"" + illness + "\",";
    json += "\"system_armed\":" + String(armed == 1 ? "true" : "false") + ",";
    json += "\"fall_detected\":" + String(fall == 1 ? "true" : "false") + ",";
    json += "\"alert_state\":\"" + alert + "\",";
    json += "\"wifi_rssi\":" + String(WiFi.RSSI()) + ",";
    json += "\"device_id\":\"ELDERLY_BAND_01\"";
    json += "}";

    postToFlask("/api/update", json);
  }
  // 2. Immediate Events
  else if (line.startsWith("EVENT:")) {
    String ev = line.substring(6);
    Serial.print(F("[EVENT] Forwarding immediate event: "));
    Serial.println(ev);

    String json = "{\"event\":\"" + ev + "\",\"device_id\":\"ELDERLY_BAND_01\"}";
    postToFlask("/api/alert", json);
  }
}

// =================================================================================
// SETUP
// =================================================================================
void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  // USB serial to PC Serial Monitor (115200 baud)
  Serial.begin(115200);
  delay(500);

  // Hardware serial from Arduino Uno (D2 RX, D1 TX at 9600 baud)
  arduinoSerial.begin(9600);
  arduinoSerial.setTimeout(120);

  Serial.println(F("\n========================================================"));
  Serial.println(F("   CareShield NodeMCU WiFi Telemetry Gateway Starting   "));
  Serial.println(F("========================================================"));

  connectToWiFi();
  lastWifiCheck = millis();

  Serial.println(F("[Ready] Listening for Arduino telemetry on Pin D2..."));
}

// =================================================================================
// MAIN LOOP
// =================================================================================
void loop() {
  unsigned long now = millis();

  // Periodic WiFi health check (every 15s if disconnected)
  if (now - lastWifiCheck >= 15000) {
    lastWifiCheck = now;
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println(F("[WiFi] Not connected, attempting reconnect..."));
      connectToWiFi();
      lastWifiCheck = millis();
    }
  }

  // Read data coming from Arduino Uno via SoftwareSerial on Pin D2
  while (arduinoSerial.available() > 0) {
    char c = (char)arduinoSerial.read();
    if (c == '\n' || c == '\r') {
      if (incomingLine.length() > 0) {
        Serial.print(F("[FROM ARDUINO]: "));
        Serial.println(incomingLine);
        handleArduinoMessage(incomingLine);
        incomingLine = "";
      }
    } else {
      incomingLine += c;
      if (incomingLine.length() > 350) {
        incomingLine = "";
      }
    }
    yield();
  }
}
