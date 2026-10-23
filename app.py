from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime
import time
import threading
import database
from fall_detection_engine import FallDetectionEngine, DecisionState, IllnessAlert, Orientation
from sms_service import sms_service

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# Initialize Fall Detection & Health Illness Engine
fall_engine = FallDetectionEngine()

# Packet watchdog, alert countdown, and hardware command queue
last_packet_timestamp = 0.0
alert_start_epoch = 0.0
hardware_command_queue = []
sms_timer_thread = None  # Track the delayed SMS background timer

def queue_hardware_command(cmd: str):
    """Queues a serial/gateway command to be dispatched to hardware."""
    clean_cmd = cmd.strip().upper()
    if clean_cmd and clean_cmd not in hardware_command_queue:
        hardware_command_queue.append(clean_cmd)


def _send_fall_sms_after_countdown(alert_type: str, patient_name: str, room: str, details: str):
    """
    Waits 10 seconds. If fall is still active (not cancelled), sends SMS to all
    registered caregivers and family members.
    Called in a background thread when a fall is first detected.
    """
    time.sleep(10)
    # Check if fall is still active (not cancelled by patient)
    if current_system_state.get("fall_detected") and \
       current_system_state.get("alert_state") == "FALL_DETECTED":
        print(f"[SMS] 10s elapsed, fall still active → sending SMS alerts")
        sms_service.send_emergency_alert(
            alert_type=alert_type,
            patient_name=patient_name,
            room=room,
            details=details
        )
    else:
        print("[SMS] Fall was cancelled within 10s. No SMS sent.")

# Global in-memory current system and sensor state
current_system_state = {
    "distance": -1.0,               # Ultrasonic distance in cm (-1 = clear / > 90cm)
    "distance_status": "CLEAR",     # CLEAR, SAFE, APPROACHING, URGENT
    "ldr": 450,                     # Ambient light ADC
    "is_dark": False,               # Tripping hazard flag
    "fsr": 0,                       # Footwear pressure
    "fsr_level": "Lifted / No Weight",
    "total_accel": 1.0,             # Accelerometer total magnitude in g
    "total_gyro": 0.0,              # Gyroscope total rate in deg/s
    "tilt_deg": 0.0,                # Tilt angle from upright vertical in degrees
    "orientation": "VERTICAL",      # VERTICAL, TILTED, HORIZONTAL, UNKNOWN
    "fsr_pressure_drop": False,     # FSR drop followed by non-recovery (Yes/No)
    "orientation_change": False,    # Orientation change from vertical to horizontal (Yes/No)
    "impact_inactivity": False,     # Supporting evidence: Impact peak + post-impact stillness (Yes/No)
    "decision": "NORMAL_MONITORING",# Current Decision Matrix state
    "decision_reason": "Normal monitoring",
    "illness_alert": "NONE",        # Illness warning: SUDDEN_BRADYCARDIA, SUDDEN_HYPOXIA, etc.
    "illness_notes": "",
    "heart_rate": 0,                # BPM (0 = Finger is not placed)
    "spo2": 0,                      # SpO2 percentage (0 = Finger is not placed)
    "finger_present": False,        # Finger presence
    "is_simulated": False,          # Whether vitals are simulated
    "vitals_badge": "NO FINGER",    # NO FINGER vs NORMAL vs REAL
    "fall_detected": False,         # Fall alert flag
    "system_armed": True,           # Hardware monitoring active flag
    "monitoring_status": "ACTIVE",  # ACTIVE or PAUSED (Wearable Not in Use)
    "alert_state": "NORMAL",        # NORMAL, FALL_DETECTED, CANCELLED_NO_WORRY, MONITORING_PAUSED
    "cancellation_message": "",     # "False Alert Cancelled – No Worry"
    "wifi_status": "DISCONNECTED",
    "wifi_rssi": -65,               # dBm
    "last_update": "Never",
    "device_id": "ELDERLY_BAND_01",
    "active_alert_id": None,
    "is_online": False,             # Watchdog: True only when fresh packet received within 6.0s
    "connection_status": "AWAITING_TELEMETRY", # LIVE, SENSOR_OFFLINE, AWAITING_TELEMETRY
    "countdown": 0,                 # Remaining seconds before automatic escalation
    "manual_sos": False,            # Independent manual emergency distress flag
    "manual_sos_time": None,
    "manual_sos_notes": ""
}

@app.route("/api/sensor-data", methods=["GET"])
def get_sensor_data():
    """Returns the latest sensor telemetry, watchdog status, countdown, and SOS flag."""
    global last_packet_timestamp, alert_start_epoch
    now_epoch = time.time()
    packet_age = (now_epoch - last_packet_timestamp) if last_packet_timestamp > 0 else 999999.0
    is_online = (packet_age <= 6.0)

    # Determine remaining countdown if fall alert is active
    countdown_val = current_system_state.get("countdown", 0)
    if current_system_state.get("fall_detected"):
        if alert_start_epoch > 0:
            elapsed = now_epoch - alert_start_epoch
            calc_countdown = max(0, 10 - int(elapsed))
            if countdown_val <= 0 or countdown_val > 10:
                countdown_val = calc_countdown
    else:
        countdown_val = 0

    response_data = dict(current_system_state)
    response_data["is_online"] = is_online
    response_data["packet_age_seconds"] = round(packet_age, 1) if last_packet_timestamp > 0 else None
    response_data["connection_status"] = "LIVE" if is_online else "SENSOR_OFFLINE"
    response_data["countdown"] = countdown_val
    response_data["manual_sos"] = current_system_state.get("manual_sos", False)

    return jsonify(response_data)

@app.route("/api/update", methods=["POST"])
def update_sensor_data():
    """Endpoint called periodically by ESP8266 or Python serial reader."""
    global last_packet_timestamp, alert_start_epoch
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Missing JSON payload"}), 400

    now_epoch = time.time()
    last_packet_timestamp = now_epoch
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n[RECEIVED TELEMETRY FROM NODEMCU]: {data}")
    current_system_state["is_online"] = True
    current_system_state["connection_status"] = "LIVE"
    current_system_state["wifi_status"] = "CONNECTED"

    # Update distance & status
    if "distance" in data:
        dist = float(data["distance"])
        current_system_state["distance"] = dist
        if dist > 90.0 or dist < 0:
            current_system_state["distance_status"] = "CLEAR"
        elif dist > 45.0:
            current_system_state["distance_status"] = "SAFE"
        elif dist > 15.0:
            current_system_state["distance_status"] = "APPROACHING"
        else:
            current_system_state["distance_status"] = "URGENT"

    # Update LDR
    if "ldr" in data:
        ldr_val = int(data["ldr"])
        current_system_state["ldr"] = ldr_val
        current_system_state["is_dark"] = (ldr_val < 200)

    # Update FSR
    fsr_val = int(data.get("fsr", current_system_state["fsr"]))
    current_system_state["fsr"] = fsr_val
    if fsr_val == 0:
        current_system_state["fsr_level"] = "Unweighted / Off Foot"
    elif fsr_val < 200:
        current_system_state["fsr_level"] = "Light Contact / Resting"
    elif fsr_val < 700:
        current_system_state["fsr_level"] = "Normal Walking"
    elif fsr_val < 950:
        current_system_state["fsr_level"] = "Firm Stance / Heavy"
    else:
        current_system_state["fsr_level"] = "Overload Pressure"

    # Update Vitals
    if "heart_rate" in data:
        hr = int(data["heart_rate"])
        current_system_state["heart_rate"] = hr
        current_system_state["finger_present"] = (hr > 0)
    if "spo2" in data:
        current_system_state["spo2"] = int(data["spo2"])
    if "finger_present" in data:
        current_system_state["finger_present"] = bool(data["finger_present"])
    if "is_simulated" in data:
        current_system_state["is_simulated"] = bool(data["is_simulated"])

    if not current_system_state["finger_present"] or current_system_state["heart_rate"] <= 0:
        current_system_state["vitals_badge"] = "NO FINGER"
    elif current_system_state.get("is_simulated", False):
        current_system_state["vitals_badge"] = "NORMAL"
    else:
        current_system_state["vitals_badge"] = "REAL"

    # Countdown from hardware firmware
    if "countdown" in data:
        current_system_state["countdown"] = int(data["countdown"])

    # Update Monitoring Status
    if "monitoring_status" in data:
        current_system_state["monitoring_status"] = data["monitoring_status"]
    if "system_armed" in data:
        current_system_state["system_armed"] = bool(data["system_armed"])
        if not current_system_state["system_armed"]:
            current_system_state["monitoring_status"] = "PAUSED"
            current_system_state["fall_detected"] = False

    # ----------------------------------------------------------------------
    # PROCESS THROUGH FALL DETECTION & ILLNESS ANOMALY ENGINE
    # ----------------------------------------------------------------------
    # Accept both full and abbreviated field names from NodeMCU / Arduino
    total_accel = float(data.get("total_accel", data.get("accel", current_system_state.get("total_accel", 1.0))))
    total_gyro = float(data.get("total_gyro", data.get("gyro", current_system_state.get("total_gyro", 0.0))))
    ax = float(data.get("ax", 0.0))
    ay = float(data.get("ay", 0.0))
    az = float(data.get("az", 1.0))
    tilt_deg_in = float(data["tilt_deg"]) if "tilt_deg" in data else (float(data["tilt"]) if "tilt" in data else None)
    orient_in = str(data["orientation"]) if "orientation" in data else None
    fsr_drop_in = bool(data["fsr_pressure_drop"]) if "fsr_pressure_drop" in data else (bool(data["fsr_drop"]) if "fsr_drop" in data else None)
    orient_chg_in = bool(data["orientation_change"]) if "orientation_change" in data else None
    impact_inact_in = bool(data["impact_inactivity"]) if "impact_inactivity" in data else None

    # Evaluate using the fall engine truth table
    engine_res = fall_engine.process_telemetry(
        fsr=fsr_val,
        ax=ax, ay=ay, az=az,
        total_accel=total_accel,
        total_gyro=total_gyro,
        heart_rate=current_system_state["heart_rate"],
        spo2=current_system_state["spo2"],
        finger_present=current_system_state["finger_present"],
        tilt_deg=tilt_deg_in,
        orientation=orient_in,
        fsr_pressure_drop_override=fsr_drop_in,
        orientation_change_override=orient_chg_in,
        impact_inactivity_override=impact_inact_in
    )

    # Sync engine states
    current_system_state["total_accel"] = engine_res["total_accel"]
    current_system_state["total_gyro"] = engine_res["total_gyro"]
    current_system_state["tilt_deg"] = engine_res["tilt_deg"]
    current_system_state["orientation"] = engine_res["orientation"]
    current_system_state["fsr_pressure_drop"] = engine_res["fsr_pressure_drop"]
    current_system_state["orientation_change"] = engine_res["orientation_change"]
    current_system_state["impact_inactivity"] = engine_res["impact_inactivity"]
    current_system_state["decision"] = engine_res["decision"]
    current_system_state["decision_reason"] = engine_res["decision_reason"]
    current_system_state["illness_alert"] = engine_res["illness_alert"]
    current_system_state["illness_notes"] = engine_res["illness_notes"]

    # Evaluate fall state according to truth table decision
    if engine_res["is_fall_candidate"] or engine_res["decision"] == "HIGH_CONFIDENCE_FALL" or data.get("fall_detected", False):
        if not current_system_state["fall_detected"]:
            current_system_state["fall_detected"] = True
            current_system_state["alert_state"] = "FALL_DETECTED"
            current_system_state["cancellation_message"] = ""
            alert_start_epoch = now_epoch
            if "countdown" not in data:
                current_system_state["countdown"] = 10
            alert_id = database.log_fall_alert("HIGH_CONFIDENCE_FALL", engine_res["decision_reason"])
            current_system_state["active_alert_id"] = alert_id
            profile = database.get_patient_profile()
            # Delayed SMS: send only after 10s if patient does NOT press cancel
            sms_thread = threading.Thread(
                target=_send_fall_sms_after_countdown,
                args=(
                    "HIGH_CONFIDENCE_FALL",
                    profile.get("patient_name", "Patient"),
                    profile.get("room_number", "Unknown Location"),
                    engine_res["decision_reason"]
                ),
                daemon=True
            )
            sms_thread.start()
    elif engine_res["decision"] == "POSSIBLE_FALL_EVAL_MOVEMENT":
        if not current_system_state["fall_detected"]:
            current_system_state["fall_detected"] = True
            current_system_state["alert_state"] = "FALL_DETECTED"
            alert_start_epoch = now_epoch
            if "countdown" not in data:
                current_system_state["countdown"] = 10
            alert_id = database.log_fall_alert("POSSIBLE_FALL_EVAL_MOVEMENT", engine_res["decision_reason"])
            current_system_state["active_alert_id"] = alert_id
            # Delayed SMS for possible fall too
            profile = database.get_patient_profile()
            threading.Thread(
                target=_send_fall_sms_after_countdown,
                args=(
                    "POSSIBLE_FALL_EVAL_MOVEMENT",
                    profile.get("patient_name", "Patient"),
                    profile.get("room_number", "Unknown Location"),
                    engine_res["decision_reason"]
                ),
                daemon=True
            ).start()
    elif current_system_state["alert_state"] == "NORMAL" and not data.get("fall_detected", False):
        current_system_state["fall_detected"] = False
        alert_start_epoch = 0.0
        current_system_state["countdown"] = 0

    # Evaluate illness alerts
    if engine_res["illness_alert"] != "NONE":
        if current_system_state.get("active_illness_type") != engine_res["illness_alert"]:
            current_system_state["active_illness_type"] = engine_res["illness_alert"]
            database.log_fall_alert(f"ILLNESS_{engine_res['illness_alert']}", engine_res["illness_notes"])
    else:
        current_system_state["active_illness_type"] = None

    if "alert_state" in data and not current_system_state["fall_detected"]:
        current_system_state["alert_state"] = data["alert_state"]

    if "wifi_rssi" in data:
        current_system_state["wifi_rssi"] = int(data["wifi_rssi"])

    current_system_state["last_update"] = now_str

    # Log telemetry record to database
    database.log_sensor_data(current_system_state)

    # Dequeue pending command for ESP8266 or reader
    pending_cmd = hardware_command_queue.pop(0) if hardware_command_queue else None

    return jsonify({
        "status": "success",
        "message": "Telemetry updated",
        "decision": current_system_state["decision"],
        "timestamp": now_str,
        "pending_command": pending_cmd
    })

@app.route("/api/alert", methods=["POST"])
def receive_alert():
    """Immediate high-priority alert endpoint from hardware (vitals, obstacle, fall, timeout)."""
    global alert_start_epoch
    data = request.get_json(silent=True) or {}
    event_type = data.get("event", "ABNORMAL_SENSOR_ALERT")
    notes = data.get("notes", "Urgent alert received from wearable hardware")

    if "FALL" in event_type:
        current_system_state["fall_detected"] = True
        current_system_state["alert_state"] = "FALL_DETECTED"
        alert_start_epoch = time.time()
        current_system_state["countdown"] = 10
    else:
        current_system_state["alert_state"] = f"ABNORMAL_{notes}"

    current_system_state["cancellation_message"] = ""
    current_system_state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    alert_id = database.log_fall_alert(event_type, notes)
    current_system_state["active_alert_id"] = alert_id

    print(f"[ALERT] Alert registered: {event_type} - {notes}! Alert ID: {alert_id}")
    return jsonify({
        "status": "success",
        "alert_id": alert_id,
        "message": f"Alert registered: {notes}"
    }), 201

@app.route("/api/cancel-alert", methods=["POST"])
def cancel_alert():
    """Triggered by patient press button: cancels active alert and silences buzzer."""
    global alert_start_epoch
    data = request.get_json(silent=True) or {}
    cancellation_note = data.get("message", "Wrong Alert Turned Off by Patient – Alarm Silenced")

    # Queue hardware silence command to silence physical buzzer on Arduino
    queue_hardware_command("CANCEL")
    alert_start_epoch = 0.0
    current_system_state["countdown"] = 0

    # Update system state & reset engine
    fall_engine.reset_state()
    current_system_state["fall_detected"] = False
    current_system_state["alert_state"] = "CANCELLED_NO_WORRY"
    current_system_state["cancellation_message"] = cancellation_note
    current_system_state["decision"] = "NORMAL_MONITORING"
    current_system_state["decision_reason"] = "Alert cancelled by patient"
    current_system_state["illness_alert"] = "NONE"
    current_system_state["illness_notes"] = ""
    current_system_state["active_illness_type"] = None
    current_system_state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Update database
    database.cancel_active_alert(notes=cancellation_note)
    print(f"[PRESS BUTTON] Alert cancelled by patient: '{cancellation_note}'. Buzzer silenced.")

    return jsonify({
        "status": "success",
        "message": cancellation_note,
        "fall_detected": False,
        "alert_state": "CANCELLED_NO_WORRY",
        "hardware_command": "CANCEL"
    })

@app.route("/api/sos/trigger", methods=["POST"])
def trigger_sos():
    """Triggers an independent manual SOS emergency distress alert."""
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "Manual Emergency SOS triggered by patient")
    current_system_state["manual_sos"] = True
    current_system_state["manual_sos_notes"] = notes
    current_system_state["manual_sos_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_id = database.log_fall_alert("MANUAL_SOS_URGENT", notes)
    queue_hardware_command("SOS_ALARM")
    profile = database.get_patient_profile()
    sms_service.send_emergency_alert(
        alert_type="MANUAL_SOS_URGENT",
        patient_name=profile.get("patient_name", "Eleanor Vance"),
        room=profile.get("room_number", "Room 304"),
        details=notes
    )
    print(f"[MANUAL SOS] Emergency SOS triggered! Alert ID: {alert_id}")
    return jsonify({
        "status": "success",
        "manual_sos": True,
        "alert_id": alert_id,
        "message": notes
    }), 201

@app.route("/api/sos/cancel", methods=["POST"])
def cancel_sos():
    """Cancels active manual SOS emergency distress alert."""
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "Manual SOS cancelled by patient/caregiver")
    current_system_state["manual_sos"] = False
    current_system_state["manual_sos_notes"] = ""
    database.cancel_active_alert(notes=notes)
    queue_hardware_command("CANCEL")
    print(f"[MANUAL SOS] Emergency SOS cancelled: '{notes}'. Buzzer silenced.")
    return jsonify({
        "status": "success",
        "manual_sos": False,
        "message": notes,
        "hardware_command": "CANCEL"
    })

@app.route("/api/hardware-command", methods=["GET", "POST"])
def handle_hardware_command():
    """API for serial bridge and WiFi gateway to poll or submit hardware commands."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        cmd = data.get("command", "").strip().upper()
        if cmd:
            queue_hardware_command(cmd)
            return jsonify({"status": "queued", "command": cmd})
        return jsonify({"status": "error", "message": "Missing command"}), 400
    else:
        if hardware_command_queue:
            cmd = hardware_command_queue.pop(0)
            return jsonify({"has_command": True, "command": cmd})
        return jsonify({"has_command": False})

@app.route("/api/monitoring-status", methods=["POST"])
def toggle_monitoring():
    """Triggered by Button 1 on wearable: toggles monitoring Active vs Paused."""
    data = request.get_json(silent=True) or {}
    status = data.get("monitoring_status")

    if not status:
        new_status = "PAUSED" if current_system_state["monitoring_status"] == "ACTIVE" else "ACTIVE"
    else:
        new_status = status.upper()

    if new_status == "PAUSED":
        current_system_state["monitoring_status"] = "PAUSED"
        current_system_state["system_armed"] = False
        current_system_state["fall_detected"] = False
        current_system_state["alert_state"] = "MONITORING_PAUSED"
        fall_engine.reset_state()
        msg = "Monitoring Paused – Wearable Not in Use"
        queue_hardware_command("STATUS_PAUSED")
    else:
        current_system_state["monitoring_status"] = "ACTIVE"
        current_system_state["system_armed"] = True
        current_system_state["alert_state"] = "NORMAL"
        msg = "Monitoring Active"
        queue_hardware_command("STATUS_ACTIVE")

    current_system_state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[BUTTON 1] Monitoring status changed to: {new_status}")

    return jsonify({
        "status": "success",
        "monitoring_status": current_system_state["monitoring_status"],
        "system_armed": current_system_state["system_armed"],
        "message": msg
    })

@app.route("/api/history", methods=["GET"])
def get_history():
    """Returns recent telemetry and alert history."""
    history = database.get_recent_history(limit=20)
    return jsonify(history)

@app.route("/api/acknowledge-alert", methods=["POST"])
def acknowledge_alert():
    """Triggered by caregiver to acknowledge active emergency alert, silencing buzzer."""
    global alert_start_epoch
    data = request.get_json(silent=True) or {}
    caregiver_name = data.get("caregiver_name", "Caregiver On-Duty")
    notes = data.get("notes", "Acknowledged by caregiver via CareShield Portal")

    # Queue hardware silence command to silence physical buzzer on Arduino
    queue_hardware_command("CANCEL")
    alert_start_epoch = 0.0
    current_system_state["countdown"] = 0

    fall_engine.reset_state()
    current_system_state["fall_detected"] = False
    current_system_state["manual_sos"] = False
    current_system_state["alert_state"] = "ACKNOWLEDGED_SAFE"
    current_system_state["cancellation_message"] = f"Acknowledged by {caregiver_name}"
    current_system_state["decision"] = "NORMAL_MONITORING"
    current_system_state["decision_reason"] = f"Alert acknowledged by {caregiver_name}: {notes}"
    current_system_state["illness_alert"] = "NONE"
    current_system_state["illness_notes"] = ""
    current_system_state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    database.acknowledge_active_alert(caregiver_name=caregiver_name, notes=notes)
    print(f"[CAREGIVER ACK] Alert acknowledged by {caregiver_name}: {notes}. Buzzer silenced.")

    return jsonify({
        "status": "success",
        "message": f"Alert acknowledged by {caregiver_name}",
        "fall_detected": False,
        "alert_state": "ACKNOWLEDGED_SAFE",
        "hardware_command": "CANCEL"
    })

@app.route("/api/patient-profile", methods=["GET", "POST"])
def handle_patient_profile():
    """Fetches or updates patient profile and emergency contacts."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        updated = database.update_patient_profile(data)
        return jsonify({"status": "success", "profile": updated})
    else:
        profile = database.get_patient_profile()
        return jsonify(profile)

@app.route("/api/sms-logs", methods=["GET"])
def get_sms_logs_endpoint():
    """Returns recent SMS notification logs."""
    logs = database.get_sms_logs(limit=25)
    return jsonify(logs)

@app.route("/api/sms/send-test", methods=["POST"])
def send_test_sms():
    """Dispatches a test SMS notification (Dry-run by default; live only if explicitly authorized)."""
    data = request.get_json(silent=True) or {}
    dry_run = data.get("dry_run", True)
    recipient = data.get("recipient")
    recipients = [recipient] if recipient else None
    profile = database.get_patient_profile()

    results = sms_service.send_emergency_alert(
        alert_type="HIGH_CONFIDENCE_FALL",
        patient_name=profile.get("patient_name", "Eleanor Vance"),
        room=profile.get("room_number", "Room 304"),
        details="Test notification trigger from CareShield Dashboard",
        recipients=recipients,
        dry_run=dry_run
    )
    return jsonify({"status": "success", "results": results})

@app.route("/api/sms/delivery-receipt", methods=["POST"])
def receive_delivery_receipt():
    """Updates SMS status to DELIVERED only upon verified delivery receipt."""
    data = request.get_json(silent=True) or {}
    sms_id = data.get("sms_id")
    receipt = data.get("receipt", "CARRIER_DELIVERY_CONFIRMED")
    if not sms_id:
        return jsonify({"status": "error", "message": "Missing sms_id"}), 400
    sms_service.confirm_delivery_receipt(int(sms_id), receipt)
    return jsonify({"status": "success", "sms_id": sms_id, "delivery_status": "DELIVERED"})

@app.route("/api/timeseries-data", methods=["GET"])
def get_timeseries_data():
    """Returns chronological time-series points for HR, SpO2, FSR, and Tilt for charts."""
    limit = int(request.args.get("limit", 30))
    records = database.get_timeseries_telemetry(limit=limit)
    return jsonify(records)

# ==============================================================================
# ALGORITHM SIMULATION & TESTING ENDPOINTS (Verifies all 6 rows & illness alerts)
# ==============================================================================
@app.route("/api/test/decision-row/<int:row_num>", methods=["POST"])
def test_decision_row(row_num):
    """Simulates each row of the user's truth table (Rows 1 to 6)."""
    global last_packet_timestamp, alert_start_epoch
    res = fall_engine.simulate_truth_table_row(row_num)
    now_epoch = time.time()
    last_packet_timestamp = now_epoch
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    current_system_state["is_online"] = True
    current_system_state["connection_status"] = "LIVE"
    current_system_state["wifi_status"] = "CONNECTED"
    current_system_state["fsr"] = res["fsr"]
    current_system_state["total_accel"] = res["total_accel"]
    current_system_state["total_gyro"] = res["total_gyro"]
    current_system_state["tilt_deg"] = res["tilt_deg"]
    current_system_state["orientation"] = res["orientation"]
    current_system_state["fsr_pressure_drop"] = res["fsr_pressure_drop"]
    current_system_state["orientation_change"] = res["orientation_change"]
    current_system_state["impact_inactivity"] = res["impact_inactivity"]
    current_system_state["decision"] = res["decision"]
    current_system_state["decision_reason"] = res["decision_reason"]
    current_system_state["illness_alert"] = res["illness_alert"]
    current_system_state["illness_notes"] = res["illness_notes"]
    current_system_state["heart_rate"] = res["heart_rate"]
    current_system_state["spo2"] = res["spo2"]
    current_system_state["finger_present"] = res["finger_present"]
    current_system_state["last_update"] = now_str

    if res["is_fall_candidate"] or res["decision"] in ["HIGH_CONFIDENCE_FALL", "POSSIBLE_FALL_EVAL_MOVEMENT"]:
        current_system_state["fall_detected"] = True
        current_system_state["alert_state"] = "FALL_DETECTED"
        current_system_state["cancellation_message"] = ""
        alert_start_epoch = now_epoch
        current_system_state["countdown"] = 10
        database.log_fall_alert(res["decision"], res["decision_reason"])
    else:
        current_system_state["fall_detected"] = False
        alert_start_epoch = 0.0
        current_system_state["countdown"] = 0
        if res["decision"] == "PRESSURE_DROP_WARNING":
            current_system_state["alert_state"] = "PRESSURE_DROP_WARNING"
        elif res["decision"] == "SENSOR_FAULT_WARNING":
            current_system_state["alert_state"] = "SENSOR_FAULT_WARNING"
        else:
            current_system_state["alert_state"] = "NORMAL"

    return jsonify({"status": "success", "row": row_num, "result": res})

@app.route("/api/test/illness/<string:illness_type>", methods=["POST"])
def test_illness(illness_type):
    """Simulates illness anomalies: 'bradycardia', 'hypoxia', 'syncopal_fall', 'normal'."""
    global last_packet_timestamp, alert_start_epoch
    res = fall_engine.simulate_illness_event(illness_type)
    now_epoch = time.time()
    last_packet_timestamp = now_epoch
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    current_system_state["is_online"] = True
    current_system_state["connection_status"] = "LIVE"
    current_system_state["wifi_status"] = "CONNECTED"
    current_system_state["heart_rate"] = res["heart_rate"]
    current_system_state["spo2"] = res["spo2"]
    current_system_state["finger_present"] = res["finger_present"]
    current_system_state["illness_alert"] = res["illness_alert"]
    current_system_state["illness_notes"] = res["illness_notes"]
    current_system_state["decision"] = res["decision"]
    current_system_state["decision_reason"] = res["decision_reason"]
    current_system_state["last_update"] = now_str

    if res["is_fall_candidate"]:
        current_system_state["fall_detected"] = True
        current_system_state["alert_state"] = "FALL_DETECTED"
        alert_start_epoch = now_epoch
        current_system_state["countdown"] = 10
        database.log_fall_alert("SYNCOPAL_FALL", res["decision_reason"])
    else:
        if res["illness_alert"] != "NONE":
            database.log_fall_alert(f"ILLNESS_{res['illness_alert']}", res["illness_notes"])

    return jsonify({"status": "success", "illness_type": illness_type, "result": res})

@app.route("/api/test/trigger-fall", methods=["POST"])
def test_trigger_fall():
    """Simulates Row 1 (High-Confidence Fall Candidate)."""
    return test_decision_row(1)

@app.route("/api/test/cancel-fall", methods=["POST"])
def test_cancel_fall():
    """Simulates pressing Button 2 to cancel wrong alert."""
    return cancel_alert()

@app.route("/api/test/toggle-monitoring", methods=["POST"])
def test_toggle_monitoring():
    """Simulates pressing Button 1."""
    return toggle_monitoring()

# ==============================================================================
# PATIENT REGISTRATION API ROUTES
# ==============================================================================

@app.route("/api/patients", methods=["GET"])
def get_patients():
    """Returns all registered patients."""
    patients = database.get_all_patients()
    return jsonify(patients)


@app.route("/api/patients", methods=["POST"])
def create_patient():
    """Registers a new patient with caregiver details."""
    data = request.get_json(silent=True) or {}
    if not data.get("patient_name"):
        return jsonify({"status": "error", "message": "patient_name is required"}), 400
    if not data.get("caregiver_phone"):
        return jsonify({"status": "error", "message": "caregiver_phone is required"}), 400
    patient_id = database.register_patient(data)
    return jsonify({"status": "success", "patient_id": patient_id,
                    "message": f"Patient '{data['patient_name']}' registered successfully"}), 201


@app.route("/api/patients/<int:patient_id>", methods=["GET"])
def get_patient(patient_id):
    """Returns a single patient with their family members."""
    patients = database.get_all_patients()
    patient = next((p for p in patients if p.get("id") == patient_id), None)
    if not patient:
        return jsonify({"status": "error", "message": "Patient not found"}), 404
    patient["family_members"] = database.get_family_members(patient_id)
    return jsonify(patient)


@app.route("/api/patients/<int:patient_id>", methods=["PUT"])
def update_patient(patient_id):
    """Updates a registered patient's details."""
    data = request.get_json(silent=True) or {}
    success = database.update_patient(patient_id, data)
    if success:
        return jsonify({"status": "success", "message": "Patient updated successfully"})
    return jsonify({"status": "error", "message": "Patient not found or update failed"}), 404


@app.route("/api/patients/<int:patient_id>", methods=["DELETE"])
def delete_patient_route(patient_id):
    """Deletes a registered patient."""
    success = database.delete_patient(patient_id)
    if success:
        return jsonify({"status": "success", "message": "Patient deleted"})
    return jsonify({"status": "error", "message": "Patient not found"}), 404


@app.route("/api/patients/<int:patient_id>/family", methods=["GET"])
def get_family_members_route(patient_id):
    """Returns family members for a patient."""
    members = database.get_family_members(patient_id)
    return jsonify(members)


@app.route("/api/patients/<int:patient_id>/family", methods=["POST"])
def add_family_member_route(patient_id):
    """Adds a family member to a patient."""
    data = request.get_json(silent=True) or {}
    if not data.get("member_name"):
        return jsonify({"status": "error", "message": "member_name is required"}), 400
    if not data.get("phone"):
        return jsonify({"status": "error", "message": "phone is required"}), 400
    member_id = database.add_family_member(patient_id, data)
    if member_id:
        return jsonify({"status": "success", "member_id": member_id,
                        "message": f"Family member '{data['member_name']}' added"}), 201
    return jsonify({"status": "error", "message": "Failed to add family member"}), 500


@app.route("/api/family/<int:member_id>", methods=["DELETE"])
def delete_family_member_route(member_id):
    """Deletes a family member contact."""
    success = database.delete_family_member(member_id)
    if success:
        return jsonify({"status": "success", "message": "Family member deleted"})
    return jsonify({"status": "error", "message": "Family member not found"}), 404


@app.route("/")
def dashboard():
    """Serves the main monitoring dashboard."""
    return render_template("dashboard.html")


@app.route("/registration")
def registration_page():
    """Serves the patient registration page."""
    return render_template("registration.html")


# Initialize DB on startup (runs whether invoked by gunicorn or __main__)
database.init_db()

if __name__ == "__main__":
    print("\n========================================================")
    print("  Elderly Fall Detection & Health Monitoring Dashboard  ")
    print("  Server running on http://0.0.0.0:5000                 ")
    print("========================================================\n")
    app.run(debug=True, host="0.0.0.0", port=5000)
