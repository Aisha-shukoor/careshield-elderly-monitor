import serial
import serial.tools.list_ports
import requests
import time
import sys

# Default configuration
BAUD_RATE = 115200  # Matches Serial.begin(115200) in elderly_sensor_test.ino
FLASK_API_BASE = "http://127.0.0.1:5000/api"

def find_arduino_port():
    """Auto-detects Arduino COM port on Windows."""
    ports = serial.tools.list_ports.comports()
    for port in ports:
        desc = (port.description or "").lower()
        hwid = (port.hwid or "").lower()
        if "arduino" in desc or "ch340" in desc or "usb-serial" in desc or "cp210" in desc or "ftdi" in desc:
            return port.device
    if ports:
        return ports[0].device
    return "COM5"

def send_to_flask(endpoint, payload):
    try:
        url = f"{FLASK_API_BASE}/{endpoint}"
        resp = requests.post(url, json=payload, timeout=2)
        return resp.status_code
    except Exception as e:
        print(f"[HTTP Error] Could not forward to Flask ({endpoint}): {e}")
        return None

last_cmd_poll_time = 0.0

def check_hardware_commands(ser):
    """Polls Flask command queue and dispatches pending commands to physical Arduino."""
    global last_cmd_poll_time
    now = time.time()
    if now - last_cmd_poll_time < 0.35:
        return
    last_cmd_poll_time = now

    if not ser or not ser.is_open:
        return

    try:
        url = f"{FLASK_API_BASE}/hardware-command"
        resp = requests.get(url, timeout=0.5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("has_command"):
                cmd = data.get("command", "")
                if cmd:
                    try:
                        ser.write(f"{cmd}\n".encode("utf-8"))
                        ser.flush()
                        print(f"\n[Hardware Bridge] >>> Forwarded command to Arduino: {cmd} <<<")
                    except Exception as err:
                        print(f"[Hardware Bridge Error] Could not write to serial: {err}")
    except Exception:
        pass

def run_reader(port=None):
    if not port:
        port = find_arduino_port()

    print(f"==================================================")
    print(f"  Elderly Monitor - USB Serial Bridge Reader      ")
    print(f"  Connecting to: {port} at {BAUD_RATE} baud...    ")
    print(f"==================================================")

    while True:
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=0.5)
            time.sleep(2)
            print(f"[OK] Connected to {port}! Streaming data to Flask...\n")

            while True:
                # Check and forward pending hardware commands (e.g. CANCEL to silence buzzer)
                check_hardware_commands(ser)

                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                print(f"[Arduino]: {line}")

                # 1. Structured DATA packet
                # Format: DATA:HR=78,SPO2=98,SIM=1,DIST=35.2,LDR=450,FSR=180,COUNTDOWN=8,ARMED=1,FALL=0,ALERT=NORMAL
                if line.startswith("DATA:"):
                    try:
                        payload_str = line[5:]
                        items = dict(item.split("=") for item in payload_str.split(",") if "=" in item)

                        telemetry = {
                            "heart_rate": int(items.get("HR", 0)),
                            "spo2": int(items.get("SPO2", 0)),
                            "finger_present": items.get("FINGER", "1" if int(items.get("HR", 0)) > 0 else "0") == "1",
                            "is_simulated": items.get("SIM", "0") == "1",
                            "distance": float(items.get("DIST", -1.0)),
                            "ldr": int(items.get("LDR", 500)),
                            "fsr": int(items.get("FSR", 0)),
                            "total_accel": float(items.get("ACCEL", 1.0)),
                            "total_gyro": float(items.get("GYRO", 0.0)),
                            "tilt_deg": float(items.get("TILT", 0.0)),
                            "orientation": items.get("ORIENT", "VERTICAL"),
                            "fsr_pressure_drop": items.get("FSR_DROP", "0") == "1",
                            "orientation_change": items.get("ORIENT_CHG", "0") == "1",
                            "impact_inactivity": items.get("IMPACT_INACT", "0") == "1",
                            "decision": items.get("DECISION", "NORMAL_MONITORING"),
                            "illness_alert": items.get("ILLNESS", "NONE"),
                            "countdown": int(items.get("COUNTDOWN", 0)),
                            "system_armed": items.get("ARMED", "1") == "1",
                            "monitoring_status": "ACTIVE" if items.get("ARMED", "1") == "1" else "PAUSED",
                            "fall_detected": items.get("FALL", "0") == "1",
                            "alert_state": items.get("ALERT", "NORMAL"),
                            "device_id": "ELDERLY_BAND_01_USB"
                        }
                        send_to_flask("update", telemetry)
                    except Exception as err:
                        print(f"[Parse Error]: {err}")

                # 2. Immediate Events
                elif line.startswith("EVENT:EMERGENCY_TIMEOUT_UNRESPONSIVE"):
                    reason = line.replace("EVENT:EMERGENCY_TIMEOUT_UNRESPONSIVE:", "").strip()
                    print(f"\n[URGENT] 10s Unresponsive Timeout Escalation: {reason}")
                    send_to_flask("alert", {"event": "PATIENT_UNRESPONSIVE_10S_TIMEOUT", "notes": f"10s Unresponsive: {reason}"})
                elif line.startswith("EVENT:FALL_HIGH_CONFIDENCE"):
                    print(f"\n[CRITICAL] High-Confidence Fall Detected (Row 1: FSR Drop + Horizontal + Impact/Inactivity)!")
                    send_to_flask("alert", {"event": "FALL_HIGH_CONFIDENCE", "notes": "High-Confidence Fall Candidate (FSR drop, orientation horizontal, impact/inactivity confirmed)"})
                elif line.startswith("EVENT:FALL_EVAL_MOVEMENT"):
                    print(f"\n[ALERT] Possible Fall Detected (Row 4: Orientation Horizontal + Impact/Inactivity)!")
                    send_to_flask("alert", {"event": "FALL_EVAL_MOVEMENT", "notes": "Possible Fall Candidate (Horizontal tilt + impact/inactivity)"})
                elif line.startswith("EVENT:ABNORMAL_ALERT"):
                    reason = line.replace("EVENT:ABNORMAL_ALERT:", "").strip()
                    print(f"\n[ALERT] Abnormal Sensor Activity: {reason}")
                    send_to_flask("alert", {"event": "ABNORMAL_SENSOR_ALERT", "notes": reason})
                elif line.startswith("EVENT:FALL") or "FALL DETECTED" in line:
                    send_to_flask("alert", {"event": "FALL_DETECTED", "notes": "Received over USB Serial"})
                elif "ALERT_CANCELLED" in line or "FALSE ALERT CANCELLED" in line or "WRONG_ALERT_TURNED_OFF" in line:
                    print("\n[OK] Wrong alert turned off by patient: Buzzer silenced & alert cancelled.")
                    send_to_flask("cancel-alert", {"message": "Wrong alert turned off"})
                elif "STATUS_PAUSED" in line:
                    send_to_flask("monitoring-status", {"monitoring_status": "PAUSED"})
                elif "STATUS_ACTIVE" in line:
                    send_to_flask("monitoring-status", {"monitoring_status": "ACTIVE"})

                # 3. Legacy line fallback (e.g. Distance: 40.12,LDR:250)
                elif "Distance:" in line and "LDR:" in line:
                    try:
                        parts = line.split(",")
                        dist = float(parts[0].split(":")[1].strip())
                        ldr_val = int(parts[1].split(":")[1].strip())
                        send_to_flask("update", {"distance": dist, "ldr": ldr_val})
                    except Exception:
                        pass

        except serial.SerialException as e:
            print(f"[Serial Warning] Lost connection to {port}: {e}. Retrying in 3s...")
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nReader stopped by user.")
            break

if __name__ == "__main__":
    com_port = sys.argv[1] if len(sys.argv) > 1 else None
    run_reader(com_port)
