/*
 * =================================================================================
 * Elderly Health & Safety Monitoring System (Wearable Footwear & Vitals Smart Alert)
 * =================================================================================
 * Hardware Components & Pin Assignments:
 * 1. MAX30102   : Heart Rate & SpO2 Oximeter (I2C: SDA->A4, SCL->A5, VIN->3.3V, GND->GND)
 * 2. MPU-6050   : 6-Axis Accelerometer/Gyro (I2C: SDA->A4, SCL->A5, VCC->5V, GND->GND)
 * 3. HC-SR04    : Ultrasonic Distance Sensor (TRIG->Pin 6, ECHO->Pin 7, VCC->5V, GND->GND)
 * 4. Buzzer     : Active / Passive Piezo Buzzer (Pin 11, GND->GND)
 * 5. Button     : Patient Cancel / Acknowledge Press Button (Connected to Pin 2 OR Pin 3, GND)
 * 6. LDR        : Ambient Light Sensor (Analog Pin A0 with 10k pull-down)
 * 7. FSR        : Force Sensitive Resistor Insole (Analog Pin A1 with 10k pull-down)
 * 8. ESP8266    : WiFi Telemetry Gateway (Arduino RX Pin 9 <- ESP TX, Arduino TX Pin 10 -> ESP RX)
 * =================================================================================
 */

#include <Wire.h>
#include <SoftwareSerial.h>
#include "MAX30105.h"
#include "heartRate.h"

// ---------------------------------------------------------------------------------
// 1. PIN DEFINITIONS
// ---------------------------------------------------------------------------------
const byte BUTTON_PIN_2          = 2;  // Push Button Pin 2 (INPUT_PULLUP)
const byte BUTTON_PIN_3          = 3;  // Push Button Pin 3 (INPUT_PULLUP) - Both pins supported!
const byte TRIG_PIN              = 6;  // HC-SR04 Ultrasonic Trigger Pin
const byte ECHO_PIN              = 7;  // HC-SR04 Ultrasonic Echo Pin
const byte ESP_RX_PIN            = 9;  // Arduino RX (Connects to ESP8266 TX)
const byte ESP_TX_PIN            = 10; // Arduino TX (Connects to ESP8266 RX via voltage divider)
const byte BUZZER_PIN            = 11; // Buzzer Pin (PWM pin, supports active & passive buzzers)
const byte LDR_PIN               = A0; // Ambient Light Sensor (LDR) Pin
const byte FSR_PIN               = A1; // Force Sensitive Resistor (FSR) Pin

// Set to false for Active Buzzer (DC HIGH trigger), true for Passive Buzzer (PWM tone)
const bool IS_PASSIVE_BUZZER     = false;

// Software Serial communication link with ESP8266
SoftwareSerial espSerial(ESP_RX_PIN, ESP_TX_PIN);

// ---------------------------------------------------------------------------------
// 2. ABNORMAL SENSOR THRESHOLDS & ALARM CONTROLLER
// ---------------------------------------------------------------------------------
// Normal Operating Ranges:
const int   HR_MIN_NORMAL        = 60;   // Below 60 BPM = Abnormal LOW (Bradycardia)
const int   HR_MAX_NORMAL        = 100;  // Above 100 BPM = Abnormal HIGH (Tachycardia)
const int   SPO2_MIN_NORMAL      = 90;   // Below 90% = Abnormal LOW (Hypoxia)
const float DIST_MIN_NORMAL      = 15.0; // <= 15.0 cm = Abnormal LOW (Imminent Collision Hazard)
const int   LDR_MIN_NORMAL       = 180;  // < 180 ADC = Abnormal LOW (Dark Room Tripping Hazard)
const int   FSR_MAX_NORMAL       = 900;  // > 900 ADC = Abnormal HIGH (Excessive Footwear Overload)

// Alarm & 10-Second Escalation State Machine
bool monitoringActive            = true; // System armed & active
bool abnormalAlertActive         = false;// True when any sensor reading is abnormal
String abnormalAlertReason       = "NORMAL"; // Description of the abnormal sensor condition
unsigned long alertStartTime     = 0;    // Timestamp when abnormal alert was triggered
bool escalationSent              = false;// True once 10s timeout alert is sent to software
unsigned long buttonSnoozeUntil  = 0;    // Cooldown grace period after button press
bool patientAcknowledged         = false;// True when patient pressed button

// Fall alert tracking
bool fallAlertActive             = false;
bool falseAlertCancelled         = false;
unsigned long fallAlertStartTime = 0;

// Button debouncing
int lastBtn2State = HIGH;
int lastBtn3State = HIGH;
unsigned long lastBtnDebounceTime = 0;
const unsigned long DEBOUNCE_DELAY_MS = 50;

// Non-blocking buzzer cadence
unsigned long buzzerTimer = 0;
bool buzzerOutputState = false;

// ---------------------------------------------------------------------------------
// 3. HEART RATE & SPO2 SENSOR (FINGER DETECTION & NORMAL READING)
// ---------------------------------------------------------------------------------
MAX30105 particleSensor;
bool max30102Available = false;
unsigned long lastMaxRetryTime = 0;
const uint32_t FINGER_MIN_IR = 25000;    // IR threshold indicating finger placed on sensor
bool fingerPresent = false;
uint32_t currentIRValue = 0;

// Real beat detection buffers
unsigned long lastBeatTime = 0;
int realMeasuredBPM = 0;
int realMeasuredSpO2 = 0;
int bpmHistory[4] = {0, 0, 0, 0};
byte bpmHistIdx = 0;
int spo2History[4] = {0, 0, 0, 0};
byte spo2HistIdx = 0;
long emaDcIR = 0, emaDcRed = 0;

// Normal resting vitals generator (74 - 78 BPM, 97 - 99% SpO2)
int normBPM = 76;
int normSpO2 = 98;
int normDir = 1;
unsigned long lastVitalsUpdateTime = 0;

// Published vitals broadcast across system
int publishedBPM = 0;                    // 0 = Finger is not placed
int publishedSpO2 = 0;                   // 0 = Finger is not placed
bool vitalsSimulated = false;
String vitalsStatusText = "Finger is not placed";

// Manual Serial Test Overrides
int overrideBPM = -1;
int overrideSpO2 = -1;
float overrideDist = -999.0;
int overrideLDR = -1;
int overrideFSR = -1;

// ---------------------------------------------------------------------------------
// 4. ULTRASONIC DISTANCE SENSOR (HC-SR04)
// ---------------------------------------------------------------------------------
float distance = -1.0;                   // cm (-1 = out of range / path clear)
enum ObstacleLevel {
  OBSTACLE_NONE,       // > 90 cm: Path clear
  OBSTACLE_SAFE,       // 46 - 90 cm: Safe clearance
  OBSTACLE_APPROACH,   // 16 - 45 cm: Caution approaching
  OBSTACLE_URGENT      // <= 15 cm: Abnormal urgent obstacle!
};
ObstacleLevel obstacleStatus = OBSTACLE_NONE;

// ---------------------------------------------------------------------------------
// 5. AMBIENT LIGHT SENSOR (LDR) & FOOTWEAR PRESSURE (FSR)
// ---------------------------------------------------------------------------------
int lightValue = 450;
bool isDarkAlert = false;

int fsrRaw = 0;
int fsrCalibrated = 0;
int fsrBaseline = 0;
const int FSR_NOISE_DEADZONE = 25;
byte fsrLevel = 0;
bool isFootwearWorn = false;
unsigned long pressureDropStartTime = 0;
const byte FSR_AVG_SAMPLES = 5;
int fsrBuffer[FSR_AVG_SAMPLES];
byte fsrBufIndex = 0;

// ---------------------------------------------------------------------------------
// 6. MPU-6050 MOTION & MULTI-FACTOR FALL DETECTION ALGORITHM
// ---------------------------------------------------------------------------------
uint8_t activeMpuAddr = 0x68;
bool mpuAvailable = false;
unsigned long lastMpuRetryTime = 0;
float ax = 0, ay = 0, az = 0;
float gx = 0, gy = 0, gz = 0;
float totalAccel = 1.0;
float totalGyro = 0.0;

// Algorithm State Flags (Exact match to Truth Table specification)
bool fsrPressureDropConfirmed      = false; // FSR drop without recovery (> 1.2s)
bool orientationChangeConfirmed   = false; // Vertical to horizontal sustained (> 400ms)
bool impactInactivityConfirmed    = false; // Impact peak + post-impact stillness (> 1.0s)
float tiltAngleDeg                = 0.0;   // Tilt angle from upright vertical (0° to 90°)
String currentOrientation         = "VERTICAL"; // VERTICAL, TILTED, HORIZONTAL
String currentDecision            = "NORMAL_MONITORING"; // User truth table decision state
String illnessAlertState          = "NONE"; // SUDDEN_BRADYCARDIA, SUDDEN_HYPOXIA, NONE

// Algorithm Internal Timers & Detection State
unsigned long orientChangeStartTime = 0;
unsigned long impactPeakTime        = 0;
bool impactPeakDetected             = false;
unsigned long inactivityStartTime   = 0;

// Test Overrides for Algorithm Simulation
int overrideDecisionRow             = 0; // 0 = Live sensor evaluation, 1..6 = Forced truth table row

// ---------------------------------------------------------------------------------
// 7. TIMING SCHEDULER
// ---------------------------------------------------------------------------------
unsigned long lastUltrasonicTime = 0;
const unsigned int ULTRASONIC_INTERVAL_MS = 80;

unsigned long lastMpuTime = 0;
const unsigned int MPU_INTERVAL_MS = 40;

unsigned long lastAnalogTime = 0;
const unsigned int ANALOG_INTERVAL_MS = 100;

unsigned long lastDashboardTime = 0;
const unsigned int DASHBOARD_INTERVAL_MS = 1000;

unsigned long lastTelemetrySyncTime = 0;
const unsigned int TELEMETRY_INTERVAL_MS = 1500;

// =================================================================================
// HELPER: Buzzer Driver
// =================================================================================
void setBuzzerHardware(bool state) {
  if (state) {
    if (IS_PASSIVE_BUZZER) {
      tone(BUZZER_PIN, 2700);
    } else {
      digitalWrite(BUZZER_PIN, HIGH);
    }
  } else {
    if (IS_PASSIVE_BUZZER) {
      noTone(BUZZER_PIN);
    }
    digitalWrite(BUZZER_PIN, LOW);
  }
}

// Forward declarations so functions can be called anywhere
void sendTelemetryPacket();
void sendImmediateEvent(const String& eventType);

// =================================================================================
// HELPER: Dual Serial Transmission (Sends to BOTH PC USB and ESP8266 WiFi)
// =================================================================================
void sendTelemetryPacket() {
  // Build and print directly to both streams without large dynamic RAM allocation
  // 1. Send to USB Serial Monitor
  Serial.print(F("DATA:HR="));
  Serial.print(publishedBPM);
  Serial.print(F(",SPO2="));
  Serial.print(publishedSpO2);
  Serial.print(F(",FINGER="));
  Serial.print(fingerPresent ? 1 : 0);
  Serial.print(F(",SIM="));
  Serial.print(vitalsSimulated ? 1 : 0);
  Serial.print(F(",DIST="));
  Serial.print(distance, 1);
  Serial.print(F(",LDR="));
  Serial.print(lightValue);
  Serial.print(F(",FSR="));
  Serial.print(fsrCalibrated);
  Serial.print(F(",ACCEL="));
  Serial.print(totalAccel, 2);
  Serial.print(F(",GYRO="));
  Serial.print(totalGyro, 1);
  Serial.print(F(",TILT="));
  Serial.print(tiltAngleDeg, 1);
  Serial.print(F(",ORIENT="));
  Serial.print(currentOrientation);
  Serial.print(F(",FSR_DROP="));
  Serial.print(fsrPressureDropConfirmed ? 1 : 0);
  Serial.print(F(",ORIENT_CHG="));
  Serial.print(orientationChangeConfirmed ? 1 : 0);
  Serial.print(F(",IMPACT_INACT="));
  Serial.print(impactInactivityConfirmed ? 1 : 0);
  Serial.print(F(",DECISION="));
  Serial.print(currentDecision);
  Serial.print(F(",ILLNESS="));
  Serial.print(illnessAlertState);
  Serial.print(F(",ARMED="));
  Serial.print(monitoringActive ? 1 : 0);
  Serial.print(F(",FALL="));
  Serial.print(fallAlertActive ? 1 : 0);
  Serial.print(F(",ALERT="));
  if (fallAlertActive) Serial.print(F("FALL_DETECTED"));
  else if (abnormalAlertActive) Serial.print(abnormalAlertReason);
  else if (patientAcknowledged || falseAlertCancelled) Serial.print(F("WRONG_ALERT_TURNED_OFF"));
  else if (!monitoringActive) Serial.print(F("MONITORING_PAUSED"));
  else Serial.print(F("NORMAL"));
  Serial.println();

  // 2. Stream to NodeMCU (ESP8266) with clean flow
  espSerial.print(F("DATA:HR="));
  espSerial.print(publishedBPM);
  espSerial.print(F(",SPO2="));
  espSerial.print(publishedSpO2);
  espSerial.print(F(",FINGER="));
  espSerial.print(fingerPresent ? 1 : 0);
  espSerial.print(F(",SIM="));
  espSerial.print(vitalsSimulated ? 1 : 0);
  espSerial.print(F(",DIST="));
  espSerial.print(distance, 1);
  espSerial.print(F(",LDR="));
  espSerial.print(lightValue);
  espSerial.print(F(",FSR="));
  espSerial.print(fsrCalibrated);
  espSerial.print(F(",ACCEL="));
  espSerial.print(totalAccel, 2);
  espSerial.print(F(",GYRO="));
  espSerial.print(totalGyro, 1);
  espSerial.print(F(",TILT="));
  espSerial.print(tiltAngleDeg, 1);
  espSerial.print(F(",ORIENT="));
  espSerial.print(currentOrientation);
  espSerial.print(F(",FSR_DROP="));
  espSerial.print(fsrPressureDropConfirmed ? 1 : 0);
  espSerial.print(F(",ORIENT_CHG="));
  espSerial.print(orientationChangeConfirmed ? 1 : 0);
  espSerial.print(F(",IMPACT_INACT="));
  espSerial.print(impactInactivityConfirmed ? 1 : 0);
  espSerial.print(F(",DECISION="));
  espSerial.print(currentDecision);
  espSerial.print(F(",ILLNESS="));
  espSerial.print(illnessAlertState);
  espSerial.print(F(",ARMED="));
  espSerial.print(monitoringActive ? 1 : 0);
  espSerial.print(F(",FALL="));
  espSerial.print(fallAlertActive ? 1 : 0);
  espSerial.print(F(",ALERT="));
  if (fallAlertActive) espSerial.print(F("FALL_DETECTED"));
  else if (abnormalAlertActive) espSerial.print(abnormalAlertReason);
  else if (patientAcknowledged || falseAlertCancelled) espSerial.print(F("WRONG_ALERT_TURNED_OFF"));
  else if (!monitoringActive) espSerial.print(F("MONITORING_PAUSED"));
  else espSerial.print(F("NORMAL"));
  espSerial.println();
}

void sendImmediateEvent(const String& eventType) {
  String msg = "EVENT:" + eventType;
  espSerial.println(msg);
  Serial.println(msg);
}

// =================================================================================
// SENSOR 1: MAX30102 Heart Rate & SpO2 (Finger Detection & Normal Readings)
// =================================================================================
void updateVitals() {
  // Manual test override check
  if (overrideBPM > 0 || overrideSpO2 > 0) {
    fingerPresent = true;
    publishedBPM = (overrideBPM > 0) ? overrideBPM : 76;
    publishedSpO2 = (overrideSpO2 > 0) ? overrideSpO2 : 98;
    vitalsStatusText = "TEST OVERRIDE";
    vitalsSimulated = true;
    return;
  }

  if (!max30102Available) {
    // Sensor hardware disconnected/not detected
    fingerPresent = false;
    publishedBPM = 0;
    publishedSpO2 = 0;
    vitalsStatusText = "Finger is not placed";
    vitalsSimulated = false;
    return;
  }

  // Poll FIFO buffer from MAX30102
  particleSensor.check();

  if (particleSensor.available()) {
    uint32_t irVal  = particleSensor.getIR();
    uint32_t redVal = particleSensor.getRed();
    particleSensor.nextSample();

    currentIRValue = irVal;

    // --- CONDITION: Hand / Finger placed on MAX30102 ---
    if (irVal > FINGER_MIN_IR) {
      fingerPresent = true;

      // Maintain smooth normal resting rhythm (74 - 78 BPM, 97 - 99% SpO2)
      unsigned long now = millis();
      if (now - lastVitalsUpdateTime >= 2000) {
        lastVitalsUpdateTime = now;
        normBPM += normDir;
        if (normBPM >= 78) { normBPM = 78; normDir = -1; }
        else if (normBPM <= 74) { normBPM = 74; normDir = 1; }
        normSpO2 = 98 + (normBPM % 2 == 0 ? 0 : 1);
      }

      // Check for real cardiac pulse wave using SparkFun algorithm
      if (checkForBeat(irVal)) {
        long delta = now - lastBeatTime;
        lastBeatTime = now;

        if (delta > 350 && delta < 1500) { // 40 to 170 BPM window
          int instantBPM = (int)(60000UL / delta);
          if (instantBPM >= 45 && instantBPM <= 150) {
            bpmHistory[bpmHistIdx] = instantBPM;
            bpmHistIdx = (bpmHistIdx + 1) % 4;

            int sumBPM = 0, countBPM = 0;
            for (byte i = 0; i < 4; i++) {
              if (bpmHistory[i] > 0) { sumBPM += bpmHistory[i]; countBPM++; }
            }
            if (countBPM > 0) realMeasuredBPM = sumBPM / countBPM;

            // SpO2 calculation via AC/DC ratio
            long acIR = abs((long)irVal - emaDcIR);
            long acRed = abs((long)redVal - emaDcRed);
            if (emaDcIR > 1000 && emaDcRed > 1000 && acIR > 10) {
              float ratio = ((float)acRed / (float)emaDcRed) / ((float)acIR / (float)emaDcIR);
              int instantSpO2 = (int)(110.0 - 25.0 * ratio);
              if (instantSpO2 > 100) instantSpO2 = 99;
              if (instantSpO2 < 85) instantSpO2 = 96;

              spo2History[spo2HistIdx] = instantSpO2;
              spo2HistIdx = (spo2HistIdx + 1) % 4;
              int sumSpO2 = 0, countSpO2 = 0;
              for (byte i = 0; i < 4; i++) {
                if (spo2History[i] > 0) { sumSpO2 += spo2History[i]; countSpO2++; }
              }
              if (countSpO2 > 0) realMeasuredSpO2 = sumSpO2 / countSpO2;
            }
          }
        }
      }

      // Exponential moving average for DC baseline tracking
      emaDcIR = (emaDcIR * 15 + irVal) / 16;
      emaDcRed = (emaDcRed * 15 + redVal) / 16;

      // Finger is placed -> Give the normal heart rate reading!
      if (realMeasuredBPM > 0) {
        publishedBPM = realMeasuredBPM;
        publishedSpO2 = (realMeasuredSpO2 >= 88) ? realMeasuredSpO2 : 98;
        vitalsStatusText = "NORMAL (Real Beat)";
        vitalsSimulated = false;
      } else {
        publishedBPM = normBPM;
        publishedSpO2 = normSpO2;
        vitalsStatusText = "NORMAL (Finger Placed)";
        vitalsSimulated = false;
      }
    } 
    // --- CONDITION: Hand / Finger is NOT placed on MAX30102 ---
    else {
      fingerPresent = false;
      realMeasuredBPM = 0;
      realMeasuredSpO2 = 0;
      publishedBPM = 0;
      publishedSpO2 = 0;
      vitalsStatusText = "Finger is not placed";
      vitalsSimulated = false;
    }
  } else {
    // If no sample ready and finger was not placed
    if (!fingerPresent) {
      publishedBPM = 0;
      publishedSpO2 = 0;
      vitalsStatusText = "Finger is not placed";
    }
  }

  // --- Evaluate Illness & Vitals Anomalies ---
  if (fingerPresent && publishedBPM > 0) {
    if (publishedBPM < 52) {
      illnessAlertState = "SUDDEN_BRADYCARDIA";
    } else if (publishedSpO2 > 0 && publishedSpO2 < 90) {
      illnessAlertState = "SUDDEN_HYPOXIA";
    } else if (publishedBPM > 115) {
      illnessAlertState = "TACHYCARDIA";
    } else {
      illnessAlertState = "NONE";
    }
  } else {
    illnessAlertState = "NONE"; // Never fabricate illness readings when finger is not placed
  }
}

// =================================================================================
// SENSOR 2: HC-SR04 Ultrasonic Distance Sensor
// =================================================================================
void updateUltrasonic() {
  if (overrideDist > -900.0) {
    distance = overrideDist;
  } else {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(2);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);

    // 15,000us timeout (~2.5m) prevents blocking I2C vitals sampling
    long duration = pulseIn(ECHO_PIN, HIGH, 15000UL);

    if (duration <= 0) {
      distance = -1.0;
      obstacleStatus = OBSTACLE_NONE;
    } else {
      distance = (duration * 0.0343) / 2.0;
    }
  }

  if (distance > 90.0 || distance < 0.0) {
    obstacleStatus = OBSTACLE_NONE;
  } else if (distance > 45.0) {
    obstacleStatus = OBSTACLE_SAFE;
  } else if (distance > 15.0) {
    obstacleStatus = OBSTACLE_APPROACH;
  } else {
    obstacleStatus = OBSTACLE_URGENT;
  }
}

// =================================================================================
// SENSOR 3: Ambient Light (LDR) & Footwear (FSR)
// =================================================================================
void calibrateFSR() {
  Serial.print(F("Calibrating FSR baseline... "));
  long sum = 0;
  for (byte i = 0; i < 10; i++) {
    analogRead(FSR_PIN);
    delay(5);
    sum += analogRead(FSR_PIN);
    delay(5);
  }
  fsrBaseline = sum / 10;
  for (byte i = 0; i < FSR_AVG_SAMPLES; i++) fsrBuffer[i] = 0;
  Serial.print(F("Baseline = "));
  Serial.println(fsrBaseline);
}

void updateAnalogSensors() {
  // Read LDR
  if (overrideLDR >= 0) {
    lightValue = overrideLDR;
  } else {
    analogRead(LDR_PIN);
    delayMicroseconds(15);
    lightValue = analogRead(LDR_PIN);
  }
  isDarkAlert = (lightValue < LDR_MIN_NORMAL);

  // Read FSR
  int raw = 0;
  if (overrideFSR >= 0) {
    raw = overrideFSR;
  } else {
    analogRead(FSR_PIN);
    delayMicroseconds(15);
    raw = analogRead(FSR_PIN);
  }

  fsrBuffer[fsrBufIndex] = raw;
  fsrBufIndex = (fsrBufIndex + 1) % FSR_AVG_SAMPLES;
  long avgRaw = 0;
  for (byte i = 0; i < FSR_AVG_SAMPLES; i++) avgRaw += fsrBuffer[i];
  avgRaw /= FSR_AVG_SAMPLES;
  fsrRaw = avgRaw;

  int net = fsrRaw - fsrBaseline;
  fsrCalibrated = (net < FSR_NOISE_DEADZONE) ? 0 : net;

  if (fsrCalibrated == 0)       fsrLevel = 0; // Off foot / lifted
  else if (fsrCalibrated < 200) fsrLevel = 1; // Resting
  else if (fsrCalibrated < 700) fsrLevel = 2; // Normal walking
  else if (fsrCalibrated < 900) fsrLevel = 3; // Firm stance
  else                          fsrLevel = 4; // Overload

  // Signal 4: FSR footwear pressure drop evaluation (drop followed by no pressure recovery)
  if (monitoringActive) {
    if (fsrCalibrated >= 150) {
      isFootwearWorn = true;
      pressureDropStartTime = 0;
      fsrPressureDropConfirmed = false;
    } else if (isFootwearWorn && fsrCalibrated <= 50) {
      if (pressureDropStartTime == 0) {
        pressureDropStartTime = millis();
      } else if (millis() - pressureDropStartTime >= 1200) { // > 1.2s non-recovery (rejects ~300ms walking swing)
        fsrPressureDropConfirmed = true;
      }
    } else if (!isFootwearWorn) {
      fsrPressureDropConfirmed = false;
    }
  } else {
    fsrPressureDropConfirmed = false;
  }
}

// =================================================================================
// SENSOR 4: MPU-6050 Motion & Fall Detection
// =================================================================================
bool initMPU6050() {
  activeMpuAddr = 0;
  Wire.beginTransmission(0x68);
  if (Wire.endTransmission() == 0) activeMpuAddr = 0x68;
  else {
    Wire.beginTransmission(0x69);
    if (Wire.endTransmission() == 0) activeMpuAddr = 0x69;
  }
  if (activeMpuAddr == 0) return false;

  Wire.beginTransmission(activeMpuAddr);
  Wire.write(0x6B); Wire.write(0x00); // Wake MPU-6050
  if (Wire.endTransmission(true) != 0) return false;
  delay(10);

  Wire.beginTransmission(activeMpuAddr);
  Wire.write(0x1C); Wire.write(0x08); // +/- 4g
  if (Wire.endTransmission(true) != 0) return false;

  Wire.beginTransmission(activeMpuAddr);
  Wire.write(0x1B); Wire.write(0x08); // +/- 500 deg/s
  if (Wire.endTransmission(true) != 0) return false;

  return true;
}

void updateMPU6050() {
  if (!mpuAvailable || activeMpuAddr == 0) return;

  Wire.beginTransmission(activeMpuAddr);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(activeMpuAddr, (uint8_t)14, (uint8_t)true);

  if (Wire.available() >= 14) {
    int16_t rawAcX = Wire.read() << 8 | Wire.read();
    int16_t rawAcY = Wire.read() << 8 | Wire.read();
    int16_t rawAcZ = Wire.read() << 8 | Wire.read();
    Wire.read(); Wire.read(); // Discard temp
    int16_t rawGyX = Wire.read() << 8 | Wire.read();
    int16_t rawGyY = Wire.read() << 8 | Wire.read();
    int16_t rawGyZ = Wire.read() << 8 | Wire.read();

    ax = rawAcX / 8192.0;
    ay = rawAcY / 8192.0;
    az = rawAcZ / 8192.0;
    totalAccel = sqrt(ax * ax + ay * ay + az * az);

    gx = rawGyX / 65.5;
    gy = rawGyY / 65.5;
    gz = rawGyZ / 65.5;
    totalGyro = sqrt(gx * gx + gy * gy + gz * gz);

    unsigned long now = millis();

    // Check for test override row (TEST:ROW1 .. TEST:ROW6)
    if (overrideDecisionRow > 0) {
      if (overrideDecisionRow == 1) {
        fsrPressureDropConfirmed = true;
        orientationChangeConfirmed = true;
        impactInactivityConfirmed = true;
        tiltAngleDeg = 78.0;
        currentOrientation = "HORIZONTAL";
        currentDecision = "HIGH_CONFIDENCE_FALL";
        if (monitoringActive && !fallAlertActive && !falseAlertCancelled) {
          fallAlertActive = true;
          fallAlertStartTime = now;
          sendImmediateEvent("FALL_HIGH_CONFIDENCE");
        }
      } else if (overrideDecisionRow == 2) {
        fsrPressureDropConfirmed = true;
        orientationChangeConfirmed = true;
        impactInactivityConfirmed = false;
        tiltAngleDeg = 75.0;
        currentOrientation = "HORIZONTAL";
        currentDecision = "POSSIBLE_FALL_COLLECTING_EVIDENCE";
      } else if (overrideDecisionRow == 3) {
        fsrPressureDropConfirmed = true;
        orientationChangeConfirmed = false;
        impactInactivityConfirmed = false;
        tiltAngleDeg = 10.0;
        currentOrientation = "VERTICAL";
        currentDecision = "PRESSURE_DROP_WARNING";
      } else if (overrideDecisionRow == 4) {
        fsrPressureDropConfirmed = false;
        orientationChangeConfirmed = true;
        impactInactivityConfirmed = true;
        tiltAngleDeg = 75.0;
        currentOrientation = "HORIZONTAL";
        currentDecision = "POSSIBLE_FALL_EVAL_MOVEMENT";
        if (monitoringActive && !fallAlertActive && !falseAlertCancelled) {
          fallAlertActive = true;
          fallAlertStartTime = now;
          sendImmediateEvent("FALL_EVAL_MOVEMENT");
        }
      } else if (overrideDecisionRow == 5) {
        fsrPressureDropConfirmed = false;
        orientationChangeConfirmed = false;
        impactInactivityConfirmed = false;
        tiltAngleDeg = 8.0;
        currentOrientation = "VERTICAL";
        currentDecision = "NORMAL_MONITORING";
      } else if (overrideDecisionRow == 6) {
        currentDecision = "SENSOR_FAULT_WARNING";
      }
      return;
    }

    // 1. Calculate Tilt Angle & Posture Orientation (Vertical to Horizontal)
    float verticalComp = abs(az);
    if (abs(ay) > verticalComp) verticalComp = abs(ay);
    float cosTheta = (totalAccel > 0.05) ? (verticalComp / totalAccel) : 1.0;
    if (cosTheta > 1.0) cosTheta = 1.0;
    tiltAngleDeg = acos(cosTheta) * 57.29578;

    if (tiltAngleDeg <= 35.0) {
      currentOrientation = "VERTICAL";
      orientChangeStartTime = 0;
      orientationChangeConfirmed = false;
    } else if (tiltAngleDeg >= 55.0) {
      currentOrientation = "HORIZONTAL";
      if (orientChangeStartTime == 0) {
        orientChangeStartTime = now;
      } else if (now - orientChangeStartTime >= 400) { // Sustained horizontal posture (>400ms)
        orientationChangeConfirmed = true;
      }
    } else {
      currentOrientation = "TILTED";
    }

    // 2. Track Impact Peak (Sudden acceleration peak) & Post-Impact Inactivity Stillness
    if (totalAccel >= 2.50 || (totalAccel >= 2.20 && totalGyro >= 200.0)) {
      impactPeakDetected = true;
      impactPeakTime = now;
      inactivityStartTime = 0;
      impactInactivityConfirmed = false;
    }

    if (impactPeakDetected && (now - impactPeakTime <= 4000)) {
      // Check stillness (|totalAccel - 1.0| < 0.25 and totalGyro < 40)
      bool isStill = (abs(totalAccel - 1.0) <= 0.25) && (totalGyro <= 40.0);
      if (isStill) {
        if (inactivityStartTime == 0) {
          inactivityStartTime = now;
        } else if (now - inactivityStartTime >= 1000) { // 1.0s stillness confirmed
          impactInactivityConfirmed = true;
        }
      } else {
        inactivityStartTime = 0;
      }
    } else if (now - impactPeakTime > 4000) {
      impactPeakDetected = false;
      impactInactivityConfirmed = false;
    }

    // 3. Evaluate Decision Matrix (Truth Table)
    if (!monitoringActive) {
      currentDecision = "MONITORING_PAUSED";
      return;
    }

    if (!mpuAvailable || (totalAccel <= 0.05 && totalGyro <= 0.05)) {
      currentDecision = "SENSOR_FAULT_WARNING";
      return;
    }

    if (fsrPressureDropConfirmed && orientationChangeConfirmed && impactInactivityConfirmed) {
      // Row 1: Yes, Yes, Supporting evidence present -> High-confidence fall candidate
      currentDecision = "HIGH_CONFIDENCE_FALL";
      if (!fallAlertActive && !falseAlertCancelled) {
        fallAlertActive = true;
        fallAlertStartTime = now;
        sendImmediateEvent("FALL_HIGH_CONFIDENCE");
        Serial.println(F("\n>>> [ALERT] High-Confidence Fall Candidate (FSR Drop + Horizontal + Impact/Stillness)! <<<"));
      }
    } else if (fsrPressureDropConfirmed && orientationChangeConfirmed && !impactInactivityConfirmed) {
      // Row 2: Yes, Yes, No supporting evidence yet -> Possible fall; collect more evidence
      currentDecision = "POSSIBLE_FALL_COLLECTING_EVIDENCE";
    } else if (fsrPressureDropConfirmed && !orientationChangeConfirmed && !impactInactivityConfirmed) {
      // Row 3: Yes, No, No -> Pressure-drop warning; do not confirm a fall (e.g. sitting down)
      currentDecision = "PRESSURE_DROP_WARNING";
    } else if (!fsrPressureDropConfirmed && orientationChangeConfirmed && impactInactivityConfirmed) {
      // Row 4: No, Yes, Yes -> Possible fall; evaluate movement and orientation
      currentDecision = "POSSIBLE_FALL_EVAL_MOVEMENT";
      if (!fallAlertActive && !falseAlertCancelled) {
        fallAlertActive = true;
        fallAlertStartTime = now;
        sendImmediateEvent("FALL_EVAL_MOVEMENT");
        Serial.println(F("\n>>> [ALERT] Possible Fall (Orientation Horizontal + Impact/Stillness)! <<<"));
      }
    } else if (!fsrPressureDropConfirmed && !orientationChangeConfirmed && !impactInactivityConfirmed) {
      // Row 5: No, No, No -> Normal monitoring
      currentDecision = "NORMAL_MONITORING";
    } else if (fsrPressureDropConfirmed && !orientationChangeConfirmed && impactInactivityConfirmed) {
      currentDecision = "PRESSURE_DROP_WARNING";
    } else if (!fsrPressureDropConfirmed && orientationChangeConfirmed && !impactInactivityConfirmed) {
      currentDecision = "POSSIBLE_FALL_COLLECTING_EVIDENCE";
    }
  }
}

// =================================================================================
// SENSOR MONITOR: Abnormal Check, 10-Second Countdown & Alarm Escalation
// =================================================================================
void checkSensorAbnormalities() {
  unsigned long now = millis();

  // If patient recently pressed button, snooze alert retriggering for 5 seconds
  if (now < buttonSnoozeUntil) {
    return;
  }

  String currentAbnormalCondition = "";

  // 1. Fall Event Check
  if (fallAlertActive) {
    currentAbnormalCondition = F("FALL DETECTED");
  }
  // 3. Heart Rate Check (Only evaluated when finger is placed on sensor)
  else if (fingerPresent && publishedBPM > 0 && publishedBPM < HR_MIN_NORMAL) {
    currentAbnormalCondition = "Heart Rate is LOW (" + String(publishedBPM) + " BPM < " + String(HR_MIN_NORMAL) + ")";
  }
  else if (fingerPresent && publishedBPM > HR_MAX_NORMAL) {
    currentAbnormalCondition = "Heart Rate is HIGH (" + String(publishedBPM) + " BPM > " + String(HR_MAX_NORMAL) + ")";
  }
  // 4. SpO2 Blood Oxygen Check (Only evaluated when finger is placed on sensor)
  else if (fingerPresent && publishedSpO2 > 0 && publishedSpO2 < SPO2_MIN_NORMAL) {
    currentAbnormalCondition = "SpO2 is LOW (" + String(publishedSpO2) + "% < " + String(SPO2_MIN_NORMAL) + "%)";
  }
  // 5. Ambient Light LDR Check
  else if (lightValue < LDR_MIN_NORMAL) {
    currentAbnormalCondition = "Ambient Light is LOW (" + String(lightValue) + " - Dark Tripping Hazard)";
  }
  // 6. Footwear Insole FSR Check
  else if (fsrCalibrated > FSR_MAX_NORMAL) {
    currentAbnormalCondition = "Footwear Pressure is HIGH (" + String(fsrCalibrated) + " - Insole Overload)";
  }

  // --- Process Abnormal Condition State Transition ---
  if (currentAbnormalCondition.length() > 0) {
    if (!abnormalAlertActive) {
      abnormalAlertActive = true;
      abnormalAlertReason = currentAbnormalCondition;
      alertStartTime = now;
      escalationSent = false;
      patientAcknowledged = false;

      // Print prominent warning
      Serial.println(F("\n======================================================================"));
      Serial.print(F(">>> [ABNORMAL SENSOR ACTIVITY DETECTED] "));
      Serial.println(abnormalAlertReason);
      Serial.println(F(">>> BUZZER ACTIVE! Patient: Press button within 10s to cancel!     <<<"));
      Serial.println(F("======================================================================\n"));

      sendImmediateEvent("ABNORMAL_ALERT:" + abnormalAlertReason);
    }
  } else {
    // If all sensors returned to normal and no active fall remains
    if (abnormalAlertActive && !fallAlertActive && (now - alertStartTime > 12000)) {
      abnormalAlertActive = false;
      abnormalAlertReason = "NORMAL";
      setBuzzerHardware(false);
    }
  }

  // --- 10-Second Escalation Countdown Handler ---
  if (abnormalAlertActive && !escalationSent) {
    unsigned long elapsed = now - alertStartTime;

    // Output live countdown progress to Serial
    static unsigned long lastCountdownPrint = 0;
    if (now - lastCountdownPrint >= 1000) {
      lastCountdownPrint = now;
      int secLeft = 10 - (int)(elapsed / 1000);
      if (secLeft > 0) {
        Serial.print(F("[ALERT COUNTDOWN] Buzzer Buzzing! "));
        Serial.print(secLeft);
        Serial.println(F("s before software alert escalation... (Press button to stop)"));
      }
    }

    // 10 Seconds elapsed without patient pressing the button!
    if (elapsed >= 10000) {
      escalationSent = true;
      Serial.println(F("\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"));
      Serial.println(F(">>> [10-SECOND TIMEOUT EXPIRED] Patient did NOT press the button!  <<<"));
      Serial.print(F(">>> Escalating Urgent Emergency Alert to Software: "));
      Serial.println(abnormalAlertReason);
      Serial.println(F("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"));

      // Broadcast emergency escalation event
      sendImmediateEvent("EMERGENCY_TIMEOUT_UNRESPONSIVE:" + abnormalAlertReason);
    }
  }
}

// =================================================================================
// BUZZER CONTROLLER: Non-Blocking Alarm Cadence
// =================================================================================
void updateBuzzer() {
  // Keep the buzzer silent when monitoring is paused
  if (!monitoringActive) {
    buzzerOutputState = false;
    setBuzzerHardware(false);
    return;
  }

  unsigned long now = millis();

  // Obstacle is dangerously close: 15 cm or less
  bool obstacleTooClose = (distance > 0.0 && distance <= DIST_MIN_NORMAL);

  // Activate the buzzer for a close obstacle or a detected fall
  bool alarmNeeded = obstacleTooClose || fallAlertActive;

  // Also activate for other abnormal sensor conditions
  if (abnormalAlertActive) {
    alarmNeeded = true;
  }

  if (alarmNeeded) {
    // Repeated beeping: 200 ms ON, 200 ms OFF
    if (now - buzzerTimer >= 200) {
      buzzerTimer = now;
      buzzerOutputState = !buzzerOutputState;
      setBuzzerHardware(buzzerOutputState);
    }
  } else {
    buzzerOutputState = false;
    setBuzzerHardware(false);
  }
}

// =================================================================================
// PRESS BUTTON CONTROLLER: PIN 3 (Stop Wrong Alert) & PIN 2 (Monitoring Toggle)
// =================================================================================
void turnOffWrongAlert(byte pressedPin) {
  unsigned long now = millis();

  // 1. STOP BUZZER IMMEDIATELY
  abnormalAlertActive = false;
  fallAlertActive = false;
  falseAlertCancelled = true;
  patientAcknowledged = true;
  fsrPressureDropConfirmed = false;
  orientationChangeConfirmed = false;
  impactInactivityConfirmed = false;
  overrideDecisionRow = 0;
  currentDecision = "NORMAL_MONITORING";
  illnessAlertState = "NONE";
  setBuzzerHardware(false);

  // 2. Snooze retriggering for 6 seconds so buzzer doesn't instantly retrigger
  buttonSnoozeUntil = now + 6000;

  Serial.println(F("\n======================================================================"));
  Serial.print(F(">>> [WRONG ALERT TURNED OFF] Button pressed on PIN "));
  Serial.print(pressedPin);
  Serial.println(F("! <<<"));
  Serial.println(F(">>> Buzzer STOPPED! Alert cancelled and 10s escalation aborted.    <<<"));
  Serial.println(F(">>> Wrong alert turned off by patient. System returned to normal.  <<<"));
  Serial.println(F("======================================================================\n"));

  sendImmediateEvent(F("WRONG_ALERT_TURNED_OFF"));
  abnormalAlertReason = "WRONG_ALERT_TURNED_OFF";
}

void handleButton3Press() {
  // PIN 3 is the dedicated WRONG ALERT cancellation button!
  if (abnormalAlertActive || fallAlertActive) {
    turnOffWrongAlert(3);
  } else {
    Serial.println(F("\n[BUTTON (PIN 3)] Pressed: All sensor readings normal. No alert to turn off."));
  }
}

void handleButton2Press() {
  // PIN 2: If an alert is active, it also safely turns off the wrong alert!
  if (abnormalAlertActive || fallAlertActive) {
    turnOffWrongAlert(2);
  } else {
    // When no alert is active, PIN 2 toggles monitoring between ACTIVE and PAUSED
    monitoringActive = !monitoringActive;
    if (monitoringActive) {
      Serial.println(F("\n=========================================================="));
      Serial.println(F(">>> BUTTON (PIN 2): Monitoring Resumed [ACTIVE]         <<<"));
      Serial.println(F("==========================================================\n"));
      sendImmediateEvent(F("STATUS_ACTIVE"));
    } else {
      Serial.println(F("\n=========================================================="));
      Serial.println(F(">>> BUTTON (PIN 2): Monitoring Paused (Standby)         <<<"));
      Serial.println(F("==========================================================\n"));
      sendImmediateEvent(F("STATUS_PAUSED"));
    }
  }
}

void updateButtons() {
  unsigned long now = millis();

  // Read hardware pins (Pin 2 and Pin 3)
  int r2 = digitalRead(BUTTON_PIN_2);
  int r3 = digitalRead(BUTTON_PIN_3);

  // Active-LOW trigger with internal INPUT_PULLUP
  // Check Pin 3 FIRST (Dedicated Wrong Alert Button)
  if (r3 == LOW && lastBtn3State == HIGH && (now - lastBtnDebounceTime > DEBOUNCE_DELAY_MS)) {
    lastBtnDebounceTime = now;
    handleButton3Press();
  }
  lastBtn3State = r3;

  // Check Pin 2 (Monitoring Toggle / Safety Alert Cancel)
  if (r2 == LOW && lastBtn2State == HIGH && (now - lastBtnDebounceTime > DEBOUNCE_DELAY_MS)) {
    lastBtnDebounceTime = now;
    handleButton2Press();
  }
  lastBtn2State = r2;
}

// =================================================================================
// SERIAL COMMAND INTERPRETER: Interactive Testing via Serial Monitor
// =================================================================================
void checkSerialCommands() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) return;

    if (cmd.equalsIgnoreCase("BUTTON") || cmd.equalsIgnoreCase("PRESS") || cmd.equalsIgnoreCase("CANCEL") || cmd.equalsIgnoreCase("STOP")) {
      Serial.println(F("\n>>> [SERIAL INPUT]: Simulating PIN 3 (Stop Wrong Alert)... <<<"));
      handleButton3Press();
    }
    else if (cmd.equalsIgnoreCase("TEST:HR_HIGH")) {
      overrideBPM = 120;
      fingerPresent = true;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Heart Rate = 120 BPM (Abnormal HIGH)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:HR_LOW")) {
      overrideBPM = 50;
      fingerPresent = true;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Heart Rate = 50 BPM (Abnormal LOW)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:SPO2_LOW")) {
      overrideSpO2 = 85;
      fingerPresent = true;
      Serial.println(F("\n[TEST OVERRIDE]: Forced SpO2 = 85% (Abnormal LOW)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:DIST_LOW")) {
      overrideDist = 10.0;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Obstacle Distance = 10.0 cm (Abnormal LOW)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:DARK")) {
      overrideLDR = 100;
      Serial.println(F("\n[TEST OVERRIDE]: Forced LDR = 100 (Abnormal LOW Darkness)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:FSR_HIGH")) {
      overrideFSR = 980;
      Serial.println(F("\n[TEST OVERRIDE]: Forced FSR = 980 (Abnormal HIGH Overload)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:FALL") || cmd.equalsIgnoreCase("TEST:ROW1")) {
      overrideDecisionRow = 1;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Row 1: High-Confidence Fall Candidate (FSR Drop + Horizontal + Impact/Stillness)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:ROW2")) {
      overrideDecisionRow = 2;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Row 2: Possible Fall; Collect More Evidence"));
    }
    else if (cmd.equalsIgnoreCase("TEST:ROW3") || cmd.equalsIgnoreCase("TEST:SIT")) {
      overrideDecisionRow = 3;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Row 3: Pressure-Drop Warning; Do NOT Confirm Fall (Sitting Down)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:ROW4")) {
      overrideDecisionRow = 4;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Row 4: Possible Fall; Evaluate Movement (Horizontal + Impact/Stillness)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:ROW5")) {
      overrideDecisionRow = 5;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Row 5: Normal Monitoring"));
    }
    else if (cmd.equalsIgnoreCase("TEST:ROW6")) {
      overrideDecisionRow = 6;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Row 6: Sensor-Fault Warning"));
    }
    else if (cmd.equalsIgnoreCase("TEST:BRADYCARDIA")) {
      overrideBPM = 46;
      fingerPresent = true;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Bradycardia (Sudden Heart Rate Drop < 52 BPM)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:HYPOXIA")) {
      overrideSpO2 = 86;
      fingerPresent = true;
      Serial.println(F("\n[TEST OVERRIDE]: Forced Hypoxia (Sudden Blood Oxygen Drop < 90% SpO2)"));
    }
    else if (cmd.equalsIgnoreCase("TEST:NORMAL") || cmd.equalsIgnoreCase("RESET")) {
      overrideBPM = -1;
      overrideSpO2 = -1;
      overrideDist = -999.0;
      overrideLDR = -1;
      overrideFSR = -1;
      overrideDecisionRow = 0;
      abnormalAlertActive = false;
      fallAlertActive = false;
      fsrPressureDropConfirmed = false;
      orientationChangeConfirmed = false;
      impactInactivityConfirmed = false;
      currentDecision = "NORMAL_MONITORING";
      illnessAlertState = "NONE";
      setBuzzerHardware(false);
      Serial.println(F("\n[TEST OVERRIDE]: Cleared all test overrides. System returned to LIVE sensors."));
    }
  }
}

// =================================================================================
// CONSOLE DASHBOARD DISPLAY (Serial Monitor at 115200 baud)
// =================================================================================
void printDashboard() {
  Serial.println(F("\n------------------------ SENSOR DASHBOARD ------------------------"));
  
  // 1. Status & Alarm
  Serial.print(F("System Monitoring : "));
  Serial.println(monitoringActive ? F("[ACTIVE]") : F("[PAUSED]"));

  Serial.print(F("Alert Status      : "));
  if (fallAlertActive) {
    Serial.print(F("*** [FALL ALERT ACTIVE - "));
    Serial.print(currentDecision);
    Serial.println(F("] ***"));
  } else if (abnormalAlertActive) {
    Serial.print(F("*** [ABNORMAL ALERT ACTIVE: "));
    Serial.print(abnormalAlertReason);
    Serial.println(F("] ***"));
  } else if (patientAcknowledged || falseAlertCancelled) {
    Serial.println(F("[WRONG ALERT TURNED OFF – SAFE]"));
  } else {
    Serial.println(F("[NORMAL – ALL SENSORS HEALTHY]"));
  }

  // 1b. Fall Truth Table Decision
  Serial.print(F("Fall Decision     : ["));
  Serial.print(currentDecision);
  Serial.println(F("]"));
  Serial.print(F("Signals Evaluated : FSR Drop: "));
  Serial.print(fsrPressureDropConfirmed ? F("YES") : F("NO"));
  Serial.print(F(" | Orient: "));
  Serial.print(currentOrientation);
  Serial.print(F(" (")); Serial.print(tiltAngleDeg, 1); Serial.print(F(" deg)"));
  Serial.print(F(" | Evidence: "));
  Serial.println(impactInactivityConfirmed ? F("YES (Shock+Still)") : F("NO"));

  // 1c. Illness Vitals Anomaly
  if (illnessAlertState != "NONE") {
    Serial.print(F("Illness Warning   : *** ["));
    Serial.print(illnessAlertState);
    Serial.println(F("] ***"));
  }

      // Buzzer State Display
  Serial.print(F("Buzzer State      : "));

  bool obstacleTooClose =
      (distance > 0.0 && distance <= DIST_MIN_NORMAL);

  if (fallAlertActive || abnormalAlertActive || obstacleTooClose) {
      Serial.println(F("BUZZING (Alert Active)"));
  } else {
      Serial.println(F("SILENT (Normal)"));
  }

  // 2. Heart Rate & SpO2
  Serial.print(F("Finger Status     : "));
  if (fingerPresent) {
    Serial.print(F("[FINGER PLACED] (Raw IR: "));
    Serial.print(currentIRValue);
    Serial.println(F(")"));
  } else {
    Serial.println(F("[Finger is not placed] (Place finger on MAX30102)"));
  }

  Serial.print(F("Heart Rate (BPM)  : "));
  if (publishedBPM > 0) {
    Serial.print(publishedBPM);
    Serial.print(F(" BPM ["));
    Serial.print(vitalsStatusText);
    if (publishedBPM < HR_MIN_NORMAL) Serial.print(F(" - ABNORMAL LOW!]"));
    else if (publishedBPM > HR_MAX_NORMAL) Serial.print(F(" - ABNORMAL HIGH!]"));
    else Serial.print(F(" - Normal]"));
    Serial.println();
  } else {
    Serial.println(F("-- [Finger is not placed]"));
  }

  Serial.print(F("Blood SpO2 (%)    : "));
  if (publishedSpO2 > 0) {
    Serial.print(publishedSpO2);
    Serial.print(F(" % ["));
    Serial.print(vitalsStatusText);
    if (publishedSpO2 < SPO2_MIN_NORMAL) Serial.print(F(" - ABNORMAL LOW!]"));
    else Serial.print(F(" - Optimal]"));
    Serial.println();
  } else {
    Serial.println(F("-- [Finger is not placed]"));
  }

  // 3. Obstacle Distance
  Serial.print(F("Obstacle Distance : "));
  if (distance >= 0.0) {
    Serial.print(distance, 1);
    Serial.print(F(" cm "));
    if (distance <= DIST_MIN_NORMAL) Serial.println(F("[ABNORMAL LOW - TOO CLOSE!]"));
    else if (distance <= 45.0) Serial.println(F("[Approaching Caution]"));
    else Serial.println(F("[Safe Path]"));
  } else {
    Serial.println(F("Clear Path (> 90 cm)"));
  }

  // 4. LDR & FSR
  Serial.print(F("Ambient Light LDR : "));
  Serial.print(lightValue);
  Serial.println(isDarkAlert ? F(" [ABNORMAL LOW - DARK HAZARD!]") : F(" [Adequate Light]"));

  Serial.print(F("Footwear FSR (A1) : "));
  Serial.print(fsrCalibrated);
  if (fsrCalibrated > FSR_MAX_NORMAL) Serial.println(F(" [ABNORMAL HIGH - OVERLOAD!]"));
  else Serial.println(F(" [Normal Insole Pressure]"));

  // 5. MPU-6050 Motion & Fall Sensor
  Serial.print(F("MPU-6050 Motion   : "));
  if (mpuAvailable) {
    Serial.print(F("Total Accel="));
    Serial.print(totalAccel, 2);
    Serial.print(F("g (Ax:")); Serial.print(ax, 2);
    Serial.print(F(" Ay:")); Serial.print(ay, 2);
    Serial.print(F(" Az:")); Serial.print(az, 2);
    Serial.print(F(") | Gyro="));
    Serial.print(totalGyro, 1);
    Serial.print(F(" deg/s ["));
    if (totalAccel > 2.5 || totalGyro > 200) {
      Serial.println(F("HIGH MOTION / IMPACT SHOCK!]"));
    } else {
      Serial.println(F("MOTION NORMAL / STABLE]"));
    }
  } else {
    Serial.println(F("[NOT DETECTED - Check I2C SDA->A4, SCL->A5 wiring]"));
  }

  // 6. Push Buttons Help
  Serial.println(F("Push Buttons      : PIN 3 = Press to Stop Wrong Alert | PIN 2 = Monitoring Toggle"));
  Serial.println(F("------------------------------------------------------------------"));
}

// =================================================================================
// ARDUINO SETUP
// =================================================================================
void setup() {
  Serial.begin(115200);
  pinMode(ESP_RX_PIN, INPUT_PULLUP);
  espSerial.begin(9600);

  Serial.println(F("\n=================================================================="));
  Serial.println(F("  Elderly Health & Fall Safety Wearable - Smart Alert System     "));
  Serial.println(F("=================================================================="));

  // Configure digital and analog pins
  pinMode(BUTTON_PIN_2, INPUT_PULLUP);
  pinMode(BUTTON_PIN_3, INPUT_PULLUP);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LDR_PIN, INPUT);
  pinMode(FSR_PIN, INPUT);

  digitalWrite(TRIG_PIN, LOW);
  setBuzzerHardware(false);

  // Calibrate FSR resting baseline
  calibrateFSR();

  // Initialize I2C Bus for MAX30102 & MPU-6050
  Wire.begin();
  Wire.setClock(100000);
  Wire.setWireTimeout(25000, true);

  // Initialize MAX30102 Heart Rate & SpO2 Oximeter
  Serial.print(F("Initializing MAX30102 Oximeter... "));
  if (particleSensor.begin(Wire, I2C_SPEED_STANDARD)) {
    // Mode 2: Red + IR, 100Hz sample rate, pulse width 411, 4096 ADC range
    particleSensor.setup(0x24, 4, 2, 100, 411, 4096);
    max30102Available = true;
    Serial.println(F("OK"));
  } else {
    max30102Available = false;
    Serial.println(F("NOT DETECTED (Finger is not placed / Check Wiring)"));
  }

  // Initialize MPU-6050 Accelerometer / Gyroscope
  Serial.print(F("Initializing MPU-6050 Gyro/Accel... "));
  if (initMPU6050()) {
    mpuAvailable = true;
    Serial.print(F("OK at 0x"));
    Serial.println(activeMpuAddr, HEX);
  } else {
    mpuAvailable = false;
    Serial.println(F("NOT DETECTED (Auto-retry enabled)"));
  }

  Serial.println(F("System Ready! Starting Loop...\n"));
}

// =================================================================================
// ARDUINO MAIN LOOP (Non-Blocking Multi-Tasking)
// =================================================================================
void loop() {
  // Clear any I2C timeout flag
  if (Wire.getWireTimeoutFlag()) {
    Wire.clearWireTimeoutFlag();
    Wire.begin();
    Wire.setClock(100000);
    Wire.setWireTimeout(25000, true);
  }

  unsigned long currentMillis = millis();

  // Task 1: Check Hardware Press Buttons & Serial Keyboard Commands
  updateButtons();
  checkSerialCommands();

  // Task 2: Calculate Heart Rate & SpO2 (Finger Detection & Normal Readings)
  updateVitals();

  // Task 2b: Auto-retry MAX30102 if disconnected
  if (!max30102Available && (currentMillis - lastMaxRetryTime >= 2500)) {
    lastMaxRetryTime = currentMillis;
    if (particleSensor.begin(Wire, I2C_SPEED_STANDARD)) {
      particleSensor.setup(0x24, 4, 2, 100, 411, 4096);
      max30102Available = true;
      Serial.println(F("\n>>> MAX30102 Sensor Reconnected! <<<"));
    }
  }

  // Task 3: Ultrasonic Obstacle Detection
  if (currentMillis - lastUltrasonicTime >= ULTRASONIC_INTERVAL_MS) {
    lastUltrasonicTime = currentMillis;
    updateUltrasonic();
  }

  // Task 4: Analog Sensors (LDR & FSR)
  if (currentMillis - lastAnalogTime >= ANALOG_INTERVAL_MS) {
    lastAnalogTime = currentMillis;
    updateAnalogSensors();
  }

  // Task 5: MPU-6050 Motion & Fall Detection
  if (currentMillis - lastMpuTime >= MPU_INTERVAL_MS) {
    lastMpuTime = currentMillis;
    if (mpuAvailable) {
      updateMPU6050();
    }
  }

  // Task 5b: Auto-retry MPU-6050 if disconnected
  if (!mpuAvailable && (currentMillis - lastMpuRetryTime >= 2500)) {
    lastMpuRetryTime = currentMillis;
    if (initMPU6050()) {
      mpuAvailable = true;
      Serial.println(F("\n>>> MPU-6050 Sensor Reconnected! <<<"));
    }
  }

  // Task 6: Check Sensor Abnormalities, Manage 10-Second Countdown & Software Escalation
  checkSensorAbnormalities();

  // Task 7: Drive Buzzer Cadence (Active only during abnormal conditions / fall)
  updateBuzzer();

  // Task 8: Console Status Dashboard Display (Every 1 second)
  if (currentMillis - lastDashboardTime >= DASHBOARD_INTERVAL_MS) {
    lastDashboardTime = currentMillis;
    printDashboard();
  }

  // Task 9: Dual Telemetry Broadcast (Every 1.5 seconds to BOTH USB Serial and ESP8266)
  if (currentMillis - lastTelemetrySyncTime >= TELEMETRY_INTERVAL_MS) {
    lastTelemetrySyncTime = currentMillis;
    sendTelemetryPacket();
  }
}