/*
 * =================================================================================
 * ESP8266 ESP-12E / NodeMCU WiFi Telemetry Gateway
 * =================================================================================
 * Hardware Connections:
 * - ESP8266 RX (GPIO3 / Pin RX) <- Arduino TX (Pin 10) via Voltage Divider (1k/2k)
 *   (Crucial: Arduino 5V TX -> 1k resistor -> ESP RX -> 2k resistor -> GND)
 * - ESP8266 TX (GPIO1 / Pin TX) -> Arduino RX (Pin 9) directly (3.3V logic is safe)
 * - ESP8266 VCC (or VIN)        -> Clean 3.3V or 5V Power Bank (Never share weak 3.3V Uno rail)
 * - ESP8266 GND                 -> Common Ground with Arduino and Power Bank
 * =================================================================================
 */

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>

// ---------------------------------------------------------------------------------
// 1. WIFI & FLASK BACKEND CONFIGURATION
// ---------------------------------------------------------------------------------
// Set your WiFi SSID and Password here:
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Flask Server URL (Replace with your Laptop's LAN IP, e.g., 192.168.1.50, or Render Cloud URL)
// Note: Do NOT use "localhost" or "127.0.0.1" here because ESP8266 is a separate device!
const char* FLASK_BASE_URL = "http://192.168.1.100:5000";

// Onboard LED indicator
#define LED_PIN 2 // Built-in LED on most ESP8266 / NodeMCU modules (Active LOW)

// ---------------------------------------------------------------------------------
// 2. STATE & BUFFERS
// ---------------------------------------------------------------------------------
String incomingSerialLine = "";
unsigned long lastWifiCheck = 0;
const unsigned long WIFI_CHECK_INTERVAL = 5000; // Check WiFi connection every 5s

// Telemetry state cached from Arduino
int heartRate = 78;
int spo2 = 98;
bool fingerPresent = false;
bool isSimulated = true;
float distance = 100.0;
int ldr = 500;
int fsr = 0;
float totalAccel = 1.0;
float totalGyro = 0.0;
float tiltDeg = 0.0;
String orientation = "VERTICAL";
bool fsrPressureDrop = false;
bool orientationChange = false;
bool impactInactivity = false;
String decision = "NORMAL_MONITORING";
String illnessAlert = "NONE";
bool systemArmed = true;
bool fallDetected = false;
String alertState = "NORMAL";

// =================================================================================
// HELPER: Send HTTP POST to Flask API
// =================================================================================
bool sendHttpPost(const String& endpoint, const String& jsonPayload) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("[ESP8266] WiFi not connected. Cannot send HTTP POST."));
    return false;
  }

  WiFiClient client;
  HTTPClient http;
  String fullUrl = String(FLASK_BASE_URL) + endpoint;

  if (http.begin(client, fullUrl)) {
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(3000); // 3 second timeout

    int httpCode = http.POST(jsonPayload);
    if (httpCode > 0) {
      Serial.print(F("[ESP8266] POST "));
      Serial.print(endpoint);
      Serial.print(F(" -> Response: "));
      Serial.println(httpCode);
      http.end();
      return (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED);
    } else {
      Serial.print(F("[ESP8266] POST failed, error: "));
      Serial.println(http.errorToString(httpCode));
      http.end();
      return false;
    }
  }
  return false;
}

// =================================================================================
// HELPER: Connect to WiFi with Auto-Reconnect
// =================================================================================
void connectToWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.println(F("\n[ESP8266] Connecting to WiFi..."));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - startAttempt < 15000)) {
    delay(400);
    digitalWrite(LED_PIN, !digitalRead(LED_PIN)); // Blink LED while connecting
    Serial.print(F("."));
  }

  if (WiFi.status() == WL_CONNECTED) {
    digitalWrite(LED_PIN, LOW); // LED Solid ON when connected (Active LOW)
    Serial.println(F("\n[ESP8266] WiFi Connected successfully!"));
    Serial.print(F("[ESP8266] IP Address: "));
    Serial.println(WiFi.localIP());
    Serial.print(F("[ESP8266] Signal Strength (RSSI): "));
    Serial.print(WiFi.RSSI());
    Serial.println(F(" dBm"));
  } else {
    digitalWrite(LED_PIN, HIGH); // LED OFF if connection failed
    Serial.println(F("\n[ESP8266] WiFi Connection failed! Will auto-retry in background."));
  }
}

// =================================================================================
// PARSER: Parse Telemetry & Immediate Events from Arduino
// =================================================================================
void processArduinoLine(const String& line) {
  // 1. Process Periodic DATA Packet
  // Format: DATA:HR=76,SPO2=98,FINGER=1,SIM=0,DIST=35.2,LDR=450,FSR=180,ACCEL=1.00,GYRO=0.0,TILT=12.0,ORIENT=VERTICAL,FSR_DROP=0,ORIENT_CHG=0,IMPACT_INACT=0,DECISION=NORMAL_MONITORING,ILLNESS=NONE,ARMED=1,FALL=0,ALERT=NORMAL
  if (line.startsWith("DATA:")) {
    String payload = line.substring(5);

    // Extract values using key-value scanning
    int idx;
    if ((idx = payload.indexOf("HR=")) != -1) {
      heartRate = payload.substring(idx + 3, payload.indexOf(",", idx)).toInt();
    }
    if ((idx = payload.indexOf("SPO2=")) != -1) {
      spo2 = payload.substring(idx + 5, payload.indexOf(",", idx)).toInt();
    }
    if ((idx = payload.indexOf("FINGER=")) != -1) {
      fingerPresent = (payload.substring(idx + 7, payload.indexOf(",", idx)).toInt() == 1);
    }
    if ((idx = payload.indexOf("SIM=")) != -1) {
      isSimulated = (payload.substring(idx + 4, payload.indexOf(",", idx)).toInt() == 1);
    }
    if ((idx = payload.indexOf("DIST=")) != -1) {
      distance = payload.substring(idx + 5, payload.indexOf(",", idx)).toFloat();
    }
    if ((idx = payload.indexOf("LDR=")) != -1) {
      ldr = payload.substring(idx + 4, payload.indexOf(",", idx)).toInt();
    }
    if ((idx = payload.indexOf("FSR=")) != -1) {
      fsr = payload.substring(idx + 4, payload.indexOf(",", idx)).toInt();
    }
    if ((idx = payload.indexOf("ACCEL=")) != -1) {
      totalAccel = payload.substring(idx + 6, payload.indexOf(",", idx)).toFloat();
    }
    if ((idx = payload.indexOf("GYRO=")) != -1) {
      totalGyro = payload.substring(idx + 5, payload.indexOf(",", idx)).toFloat();
    }
    if ((idx = payload.indexOf("TILT=")) != -1) {
      tiltDeg = payload.substring(idx + 5, payload.indexOf(",", idx)).toFloat();
    }
    if ((idx = payload.indexOf("ORIENT=")) != -1) {
      orientation = payload.substring(idx + 7, payload.indexOf(",", idx));
    }
    if ((idx = payload.indexOf("FSR_DROP=")) != -1) {
      fsrPressureDrop = (payload.substring(idx + 9, payload.indexOf(",", idx)).toInt() == 1);
    }
    if ((idx = payload.indexOf("ORIENT_CHG=")) != -1) {
      orientationChange = (payload.substring(idx + 11, payload.indexOf(",", idx)).toInt() == 1);
    }
    if ((idx = payload.indexOf("IMPACT_INACT=")) != -1) {
      impactInactivity = (payload.substring(idx + 13, payload.indexOf(",", idx)).toInt() == 1);
    }
    if ((idx = payload.indexOf("DECISION=")) != -1) {
      decision = payload.substring(idx + 9, payload.indexOf(",", idx));
    }
    if ((idx = payload.indexOf("ILLNESS=")) != -1) {
      illnessAlert = payload.substring(idx + 8, payload.indexOf(",", idx));
    }
    if ((idx = payload.indexOf("ARMED=")) != -1) {
      systemArmed = (payload.substring(idx + 6, payload.indexOf(",", idx)).toInt() == 1);
    }
    if ((idx = payload.indexOf("FALL=")) != -1) {
      fallDetected = (payload.substring(idx + 5, payload.indexOf(",", idx)).toInt() == 1);
    }
    if ((idx = payload.indexOf("ALERT=")) != -1) {
      alertState = payload.substring(idx + 6);
      alertState.trim();
    }

    // Build JSON string
    String json = "{";
    json += "\"distance\":" + String(distance, 1) + ",";
    json += "\"ldr\":" + String(ldr) + ",";
    json += "\"fsr\":" + String(fsr) + ",";
    json += "\"heart_rate\":" + String(heartRate) + ",";
    json += "\"spo2\":" + String(spo2) + ",";
    json += "\"finger_present\":" + String(fingerPresent ? "true" : "false") + ",";
    json += "\"is_simulated\":" + String(isSimulated ? "true" : "false") + ",";
    json += "\"total_accel\":" + String(totalAccel, 2) + ",";
    json += "\"total_gyro\":" + String(totalGyro, 1) + ",";
    json += "\"tilt_deg\":" + String(tiltDeg, 1) + ",";
    json += "\"orientation\":\"" + orientation + "\",";
    json += "\"fsr_pressure_drop\":" + String(fsrPressureDrop ? "true" : "false") + ",";
    json += "\"orientation_change\":" + String(orientationChange ? "true" : "false") + ",";
    json += "\"impact_inactivity\":" + String(impactInactivity ? "true" : "false") + ",";
    json += "\"decision\":\"" + decision + "\",";
    json += "\"illness_alert\":\"" + illnessAlert + "\",";
    json += "\"fall_detected\":" + String(fallDetected ? "true" : "false") + ",";
    json += "\"system_armed\":" + String(systemArmed ? "true" : "false") + ",";
    json += "\"monitoring_status\":\"" + String(systemArmed ? "ACTIVE" : "PAUSED") + "\",";
    json += "\"alert_state\":\"" + alertState + "\",";
    json += "\"wifi_rssi\":" + String(WiFi.RSSI()) + ",";
    json += "\"device_id\":\"ELDERLY_BAND_01\"";
    json += "}";

    // Transmit to Flask /api/update
    sendHttpPost("/api/update", json);

    // Flash LED briefly to indicate packet transmission
    digitalWrite(LED_PIN, HIGH);
    delay(20);
    digitalWrite(LED_PIN, LOW);
  }

  // 2. Process Immediate Emergency Event: Fall Detected or Abnormal Alert
  else if (line.startsWith("EVENT:FALL_HIGH_CONFIDENCE")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: High-Confidence Fall Detected (Row 1)!"));
    String json = "{\"event\":\"FALL_HIGH_CONFIDENCE\",\"notes\":\"High-Confidence Fall Candidate (FSR drop + Horizontal posture + Impact/Stillness)\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/alert", json);
  }
  else if (line.startsWith("EVENT:FALL_EVAL_MOVEMENT")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Possible Fall (Row 4: Horizontal + Impact/Stillness)!"));
    String json = "{\"event\":\"FALL_EVAL_MOVEMENT\",\"notes\":\"Possible Fall Candidate (Horizontal tilt + impact/stillness)\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/alert", json);
  }
  else if (line.startsWith("EVENT:POSSIBLE_FALL")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Possible Fall - Collecting Evidence..."));
    String json = "{\"event\":\"POSSIBLE_FALL\",\"notes\":\"Possible Fall (Collecting more evidence)\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/alert", json);
  }
  else if (line.startsWith("EVENT:PRESSURE_DROP_WARNING")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Pressure-drop Warning (Sitting Down)"));
  }
  else if (line.startsWith("EVENT:EMERGENCY_TIMEOUT_UNRESPONSIVE")) {
    String reason = line.substring(34);
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: 10s Unresponsive Timeout! Escalating alert..."));
    String json = "{\"event\":\"PATIENT_UNRESPONSIVE_10S_TIMEOUT\",\"notes\":\"" + reason + "\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/alert", json);
  }
  else if (line.startsWith("EVENT:ABNORMAL_ALERT")) {
    String reason = line.substring(21);
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Abnormal Sensor Alert! Sending to server..."));
    String json = "{\"event\":\"ABNORMAL_SENSOR_ALERT\",\"notes\":\"" + reason + "\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/alert", json);
  }
  else if (line.startsWith("EVENT:FALL")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Fall Detected! Sending urgent alert..."));
    String json = "{\"event\":\"FALL_DETECTED\",\"device_id\":\"ELDERLY_BAND_01\",\"timestamp\":" + String(millis()) + "}";
    sendHttpPost("/api/alert", json);
  }

  // 3. Process Immediate Event: Alert Cancelled by Patient Button (Wrong Alert Turned Off)
  else if (line.startsWith("EVENT:ALERT_CANCELLED") || line.startsWith("EVENT:WRONG_ALERT_TURNED_OFF")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Wrong Alert Turned Off by Patient!"));
    String json = "{\"event\":\"ALERT_CANCELLED\",\"message\":\"Wrong alert turned off\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/cancel-alert", json);
  }

  // 4. Process Immediate Event: Monitoring Paused / Resumed
  else if (line.startsWith("EVENT:STATUS_PAUSED")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Monitoring Paused (Footwear Off)"));
    String json = "{\"monitoring_status\":\"PAUSED\",\"message\":\"Monitoring Paused – Wearable Not in Use\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/monitoring-status", json);
  } else if (line.startsWith("EVENT:STATUS_ACTIVE")) {
    Serial.println(F("[ESP8266] IMMEDIATE EVENT: Monitoring Resumed (Footwear On)"));
    String json = "{\"monitoring_status\":\"ACTIVE\",\"message\":\"Monitoring Active\",\"device_id\":\"ELDERLY_BAND_01\"}";
    sendHttpPost("/api/monitoring-status", json);
  }
}

// =================================================================================
// SETUP
// =================================================================================
void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH); // Off initially

  // Arduino SoftwareSerial runs at 9600 baud
  Serial.begin(9600);
  delay(500);

  Serial.println(F("\n=================================================="));
  Serial.println(F("   ESP8266 WiFi Gateway for Elderly Monitor       "));
  Serial.println(F("=================================================="));

  connectToWiFi();
}

// =================================================================================
// MAIN LOOP
// =================================================================================
void loop() {
  // Periodically check and auto-recover WiFi connection
  unsigned long now = millis();
  if (now - lastWifiCheck >= WIFI_CHECK_INTERVAL) {
    lastWifiCheck = now;
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println(F("[ESP8266] WiFi disconnected. Attempting auto-reconnect..."));
      WiFi.reconnect();
    }
  }

  // Read serial commands from Arduino Uno
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (incomingSerialLine.length() > 0) {
        processArduinoLine(incomingSerialLine);
        incomingSerialLine = "";
      }
    } else {
      incomingSerialLine += c;
      // Safety limit on buffer length
      if (incomingSerialLine.length() > 256) {
        incomingSerialLine = "";
      }
    }
  }
}
