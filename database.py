import os
import mysql.connector
from mysql.connector import Error
from datetime import datetime

# Database Configuration with Environment Variable support
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "1234")
DB_NAME = os.getenv("MYSQL_DB", "elderly_fall_detection")
DB_PORT = int(os.getenv("MYSQL_PORT", 3306))

# SMS Gateway Credentials (also used by sms_service.py via env)
SMSGATE_USERNAME  = os.getenv("SMSGATE_USERNAME",  "1YENZ2")
SMSGATE_PASSWORD  = os.getenv("SMSGATE_PASSWORD",  "fic7tup7rnadag")
SMSGATE_DEVICE_ID = os.getenv("SMSGATE_DEVICE_ID", "w2X8I1a_2s71np87xPrcJ")

# In-memory fallback storage if MySQL connection is unavailable
in_memory_telemetry = []
in_memory_alerts = []
in_memory_sms_logs = []
in_memory_profile = {
    "patient_name": "Ravi Kumar",
    "age": 72,
    "blood_type": "B+",
    "room_number": "Ward 3 - Bed 12",
    "medical_conditions": "Hypertension, Mild Bradycardia, High Fall Risk",
    "notes": "Uses wearable footwear with sensor insole. Check vitals if tilt > 55 deg.",
    "primary_caregiver": "Anita Kumar (Daughter)",
    "caregiver_phone": "+917338416795",
    "caregiver_email": "anita.kumar@gmail.com",
    "emergency_doctor": "Dr. Suresh Mehta, MD (Cardiology)",
    "doctor_phone": "+918867529190",
    "emergency_service": "Apollo Hospital Emergency",
    "service_phone": "108",
    "sms_enabled": True,
    "sms_dry_run": False,  # LIVE SMS enabled by default
    "sms_recipients": "+917338416795, +918867529190"
}

# In-memory registered patients list
in_memory_patients = []
db_available = False

def get_db_connection():
    """Returns a connection to MySQL, or None if unavailable."""
    global db_available
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT,
            database=DB_NAME
        )
        db_available = True
        return connection
    except Error as e:
        # If database does not exist, try creating it
        if e.errno == 1049: # Unknown database
            try:
                root_conn = mysql.connector.connect(
                    host=DB_HOST, user=DB_USER, password=DB_PASSWORD, port=DB_PORT
                )
                cursor = root_conn.cursor()
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4;")
                root_conn.commit()
                root_conn.close()
                return get_db_connection()
            except Exception:
                pass
        db_available = False
        return None

def init_db():
    """Initializes tables in MySQL or logs fallback status."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            # Telemetry Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sensor_telemetry (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    distance FLOAT,
                    ldr INT,
                    fsr INT,
                    heart_rate INT,
                    spo2 INT,
                    is_simulated BOOLEAN DEFAULT FALSE,
                    fall_detected BOOLEAN DEFAULT FALSE,
                    monitoring_status VARCHAR(20) DEFAULT 'ACTIVE',
                    device_id VARCHAR(50) DEFAULT 'ELDERLY_BAND_01'
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Fall Alerts Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fall_alerts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    alert_type VARCHAR(50) NOT NULL,
                    status VARCHAR(50) DEFAULT 'ACTIVE',
                    cancellation_timestamp DATETIME NULL,
                    notes TEXT,
                    device_id VARCHAR(50) DEFAULT 'ELDERLY_BAND_01'
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Patient Profile Table (wearable device owner)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS patient_profile (
                    id INT PRIMARY KEY DEFAULT 1,
                    patient_name VARCHAR(100),
                    age INT,
                    blood_type VARCHAR(10),
                    room_number VARCHAR(100),
                    medical_conditions TEXT,
                    notes TEXT,
                    primary_caregiver VARCHAR(100),
                    caregiver_phone VARCHAR(50),
                    caregiver_email VARCHAR(100),
                    emergency_doctor VARCHAR(100),
                    doctor_phone VARCHAR(50),
                    emergency_service VARCHAR(100),
                    service_phone VARCHAR(50),
                    sms_enabled BOOLEAN DEFAULT TRUE,
                    sms_dry_run BOOLEAN DEFAULT TRUE,
                    sms_recipients TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Registered Patients Table (full registration)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS registered_patients (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    patient_name VARCHAR(100) NOT NULL,
                    age INT,
                    blood_type VARCHAR(10),
                    gender VARCHAR(20),
                    address TEXT,
                    medical_conditions TEXT,
                    notes TEXT,
                    room_number VARCHAR(100),
                    device_id VARCHAR(50) DEFAULT 'ELDERLY_BAND_01',
                    caregiver_name VARCHAR(100),
                    caregiver_phone VARCHAR(20),
                    caregiver_relation VARCHAR(50),
                    caregiver_email VARCHAR(100),
                    doctor_name VARCHAR(100),
                    doctor_phone VARCHAR(20),
                    emergency_service VARCHAR(100),
                    emergency_service_phone VARCHAR(20),
                    sms_alerts_enabled BOOLEAN DEFAULT TRUE,
                    is_active BOOLEAN DEFAULT TRUE,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Family Members Table (multiple contacts per patient)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS family_members (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    patient_id INT NOT NULL,
                    member_name VARCHAR(100) NOT NULL,
                    relation VARCHAR(50),
                    phone VARCHAR(20) NOT NULL,
                    email VARCHAR(100),
                    sms_enabled BOOLEAN DEFAULT TRUE,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (patient_id) REFERENCES registered_patients(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # SMS Notification Log Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sms_notifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    recipient VARCHAR(100),
                    message TEXT,
                    status VARCHAR(50) DEFAULT 'QUEUED',
                    provider_response TEXT,
                    dry_run BOOLEAN DEFAULT TRUE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            conn.commit()
            cursor.close()
            conn.close()
            print(f"[MySQL] Successfully connected to database '{DB_NAME}' and verified tables.")
            return True
        except Error as err:
            print(f"[MySQL Warning] Could not create tables: {err}")
            return False
    else:
        print("[MySQL Info] MySQL not accessible with current credentials. Operating with in-memory persistence fallback.")
        return False

def log_sensor_data(data):
    """Inserts a new telemetry record into MySQL or fallback buffer."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                INSERT INTO sensor_telemetry 
                (distance, ldr, fsr, heart_rate, spo2, is_simulated, fall_detected, monitoring_status, device_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            values = (
                data.get("distance", 0.0),
                data.get("ldr", 0),
                data.get("fsr", 0),
                data.get("heart_rate", 0),
                data.get("spo2", 0),
                data.get("is_simulated", False),
                data.get("fall_detected", False),
                data.get("monitoring_status", "ACTIVE"),
                data.get("device_id", "ELDERLY_BAND_01")
            )
            cursor.execute(query, values)
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Error as e:
            print(f"[MySQL Insert Error]: {e}")
    
    # In-memory buffer fallback (retains last 100 records)
    record = dict(data)
    record["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    in_memory_telemetry.append(record)
    if len(in_memory_telemetry) > 100:
        in_memory_telemetry.pop(0)
    return False

def log_fall_alert(alert_type="FALL_DETECTED", notes=""):
    """Logs an active fall event into MySQL and returns record ID."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                INSERT INTO fall_alerts (alert_type, status, notes)
                VALUES (%s, 'ACTIVE', %s)
            """
            cursor.execute(query, (alert_type, notes))
            conn.commit()
            alert_id = cursor.lastrowid
            cursor.close()
            conn.close()
            return alert_id
        except Error as e:
            print(f"[MySQL Alert Error]: {e}")

    # Fallback
    alert_id = len(in_memory_alerts) + 1
    in_memory_alerts.append({
        "id": alert_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "alert_type": alert_type,
        "status": "ACTIVE",
        "notes": notes,
        "cancellation_timestamp": None
    })
    return alert_id

def cancel_active_alert(notes="False Alert Cancelled – No Worry"):
    """Updates latest active fall alert to CANCELLED_FALSE_ALARM with timestamp."""
    conn = get_db_connection()
    updated = False
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                UPDATE fall_alerts
                SET status = 'CANCELLED_FALSE_ALARM',
                    cancellation_timestamp = NOW(),
                    notes = %s
                WHERE status = 'ACTIVE'
                ORDER BY id DESC LIMIT 1
            """
            cursor.execute(query, (notes,))
            conn.commit()
            updated = (cursor.rowcount > 0)
            cursor.close()
            conn.close()
        except Error as e:
            print(f"[MySQL Cancellation Error]: {e}")

    # Update in-memory storage as well
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for alert in reversed(in_memory_alerts):
        if alert["status"] == "ACTIVE":
            alert["status"] = "CANCELLED_FALSE_ALARM"
            alert["cancellation_timestamp"] = now_str
            alert["notes"] = notes
            updated = True
            break
    return updated

def get_recent_history(limit=15):
    """Retrieves recent telemetry and alerts for display."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM sensor_telemetry ORDER BY id DESC LIMIT %s", (limit,))
            telemetry = cursor.fetchall()
            cursor.execute("SELECT * FROM fall_alerts ORDER BY id DESC LIMIT %s", (limit,))
            alerts = cursor.fetchall()
            cursor.close()
            conn.close()
            return {"telemetry": telemetry, "alerts": alerts, "db_connected": True}
        except Error:
            pass

    return {
        "telemetry": list(reversed(in_memory_telemetry[-limit:])),
        "alerts": list(reversed(in_memory_alerts[-limit:])),
        "db_connected": False
    }

def acknowledge_active_alert(caregiver_name="Caregiver", notes="Acknowledged by caregiver"):
    """Acknowledges an active fall or SOS alert by caregiver, updating status to ACKNOWLEDGED."""
    conn = get_db_connection()
    updated = False
    ack_note = f"Acknowledged by {caregiver_name}: {notes}".strip()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                UPDATE fall_alerts
                SET status = 'ACKNOWLEDGED',
                    cancellation_timestamp = NOW(),
                    notes = %s
                WHERE status = 'ACTIVE'
                ORDER BY id DESC LIMIT 1
            """
            cursor.execute(query, (ack_note,))
            conn.commit()
            updated = (cursor.rowcount > 0)
            cursor.close()
            conn.close()
        except Error as e:
            print(f"[MySQL Acknowledge Error]: {e}")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for alert in reversed(in_memory_alerts):
        if alert["status"] == "ACTIVE":
            alert["status"] = "ACKNOWLEDGED"
            alert["cancellation_timestamp"] = now_str
            alert["notes"] = ack_note
            updated = True
            break
    return updated

def get_patient_profile():
    """Retrieves patient profile and emergency contacts."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM patient_profile WHERE id = 1")
            row = cursor.fetchone()
            if not row:
                # Seed default profile row
                insert_query = """
                    INSERT INTO patient_profile 
                    (id, patient_name, age, blood_type, room_number, medical_conditions, notes,
                     primary_caregiver, caregiver_phone, caregiver_email, emergency_doctor,
                     doctor_phone, emergency_service, service_phone, sms_enabled, sms_dry_run, sms_recipients)
                    VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                p = in_memory_profile
                cursor.execute(insert_query, (
                    p["patient_name"], p["age"], p["blood_type"], p["room_number"],
                    p["medical_conditions"], p["notes"], p["primary_caregiver"],
                    p["caregiver_phone"], p["caregiver_email"], p["emergency_doctor"],
                    p["doctor_phone"], p["emergency_service"], p["service_phone"],
                    p["sms_enabled"], p["sms_dry_run"], p["sms_recipients"]
                ))
                conn.commit()
                row = dict(in_memory_profile)
                row["id"] = 1
            cursor.close()
            conn.close()
            if row:
                row["sms_enabled"] = bool(row.get("sms_enabled", True))
                row["sms_dry_run"] = bool(row.get("sms_dry_run", True))
                if "updated_at" in row and row["updated_at"]:
                    row["updated_at"] = str(row["updated_at"])
                return row
        except Error as e:
            print(f"[MySQL Profile Fetch Error]: {e}")

    return dict(in_memory_profile)

def update_patient_profile(profile_data):
    """Updates patient profile and emergency contacts in DB or memory."""
    for key in in_memory_profile:
        if key in profile_data:
            if key in ["sms_enabled", "sms_dry_run"]:
                in_memory_profile[key] = bool(profile_data[key])
            elif key == "age":
                try:
                    in_memory_profile[key] = int(profile_data[key])
                except (ValueError, TypeError):
                    pass
            else:
                in_memory_profile[key] = str(profile_data[key])

    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                INSERT INTO patient_profile 
                (id, patient_name, age, blood_type, room_number, medical_conditions, notes,
                 primary_caregiver, caregiver_phone, caregiver_email, emergency_doctor,
                 doctor_phone, emergency_service, service_phone, sms_enabled, sms_dry_run, sms_recipients)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    patient_name = VALUES(patient_name),
                    age = VALUES(age),
                    blood_type = VALUES(blood_type),
                    room_number = VALUES(room_number),
                    medical_conditions = VALUES(medical_conditions),
                    notes = VALUES(notes),
                    primary_caregiver = VALUES(primary_caregiver),
                    caregiver_phone = VALUES(caregiver_phone),
                    caregiver_email = VALUES(caregiver_email),
                    emergency_doctor = VALUES(emergency_doctor),
                    doctor_phone = VALUES(doctor_phone),
                    emergency_service = VALUES(emergency_service),
                    service_phone = VALUES(service_phone),
                    sms_enabled = VALUES(sms_enabled),
                    sms_dry_run = VALUES(sms_dry_run),
                    sms_recipients = VALUES(sms_recipients)
            """
            p = in_memory_profile
            cursor.execute(query, (
                p["patient_name"], p["age"], p["blood_type"], p["room_number"],
                p["medical_conditions"], p["notes"], p["primary_caregiver"],
                p["caregiver_phone"], p["caregiver_email"], p["emergency_doctor"],
                p["doctor_phone"], p["emergency_service"], p["service_phone"],
                p["sms_enabled"], p["sms_dry_run"], p["sms_recipients"]
            ))
            conn.commit()
            cursor.close()
            conn.close()
        except Error as e:
            print(f"[MySQL Profile Update Error]: {e}")

    return dict(in_memory_profile)

def log_sms_notification(recipient, message, status="QUEUED", provider_response="", dry_run=True):
    """Logs an outgoing SMS notification record."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                INSERT INTO sms_notifications (recipient, message, status, provider_response, dry_run)
                VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(query, (recipient, message, status, provider_response, dry_run))
            conn.commit()
            log_id = cursor.lastrowid
            cursor.close()
            conn.close()
            return log_id
        except Error as e:
            print(f"[MySQL SMS Log Error]: {e}")

    log_id = len(in_memory_sms_logs) + 1
    in_memory_sms_logs.append({
        "id": log_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "recipient": recipient,
        "message": message,
        "status": status,
        "provider_response": provider_response,
        "dry_run": dry_run
    })
    return log_id

def update_sms_status(log_id, status, provider_response=""):
    """Updates status of a queued/sent SMS log."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = "UPDATE sms_notifications SET status = %s, provider_response = %s WHERE id = %s"
            cursor.execute(query, (status, provider_response, log_id))
            conn.commit()
            cursor.close()
            conn.close()
        except Error as e:
            print(f"[MySQL SMS Status Update Error]: {e}")

    for log in in_memory_sms_logs:
        if log["id"] == log_id:
            log["status"] = status
            if provider_response:
                log["provider_response"] = provider_response
            break

def get_sms_logs(limit=25):
    """Retrieves recent SMS notification logs."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM sms_notifications ORDER BY id DESC LIMIT %s", (limit,))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            for r in rows:
                if "dry_run" in r:
                    r["dry_run"] = bool(r["dry_run"])
                if "timestamp" in r and r["timestamp"]:
                    r["timestamp"] = str(r["timestamp"])
            return rows
        except Error:
            pass

    return list(reversed(in_memory_sms_logs[-limit:]))

def get_timeseries_telemetry(limit=30):
    """Retrieves time-series telemetry records in chronological order for chart visualization."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM sensor_telemetry ORDER BY id DESC LIMIT %s", (limit,))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            # Reverse to chronological order (oldest to newest)
            for r in rows:
                if "timestamp" in r and r["timestamp"]:
                    r["timestamp"] = str(r["timestamp"])
            return list(reversed(rows))
        except Error:
            pass

    # From in-memory fallback
    return list(in_memory_telemetry[-limit:])


# =============================================================================
# PATIENT REGISTRATION CRUD
# =============================================================================

def register_patient(data: dict) -> int:
    """Registers a new patient with full details and caregiver info."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                INSERT INTO registered_patients
                (patient_name, age, blood_type, gender, address, medical_conditions, notes,
                 room_number, device_id,
                 caregiver_name, caregiver_phone, caregiver_relation, caregiver_email,
                 doctor_name, doctor_phone,
                 emergency_service, emergency_service_phone, sms_alerts_enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (
                data.get("patient_name", ""),
                data.get("age", 0),
                data.get("blood_type", ""),
                data.get("gender", ""),
                data.get("address", ""),
                data.get("medical_conditions", ""),
                data.get("notes", ""),
                data.get("room_number", ""),
                data.get("device_id", "ELDERLY_BAND_01"),
                data.get("caregiver_name", ""),
                data.get("caregiver_phone", ""),
                data.get("caregiver_relation", ""),
                data.get("caregiver_email", ""),
                data.get("doctor_name", ""),
                data.get("doctor_phone", ""),
                data.get("emergency_service", "Apollo Hospital Emergency"),
                data.get("emergency_service_phone", "108"),
                data.get("sms_alerts_enabled", True)
            ))
            conn.commit()
            new_id = cursor.lastrowid
            cursor.close()
            conn.close()
            return new_id
        except Error as e:
            print(f"[MySQL Register Patient Error]: {e}")

    # In-memory fallback
    new_id = len(in_memory_patients) + 1
    record = dict(data)
    record["id"] = new_id
    record["registered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    in_memory_patients.append(record)
    return new_id


def get_all_patients() -> list:
    """Returns all registered patients."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM registered_patients ORDER BY id DESC")
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            for r in rows:
                if "registered_at" in r and r["registered_at"]:
                    r["registered_at"] = str(r["registered_at"])
                if "sms_alerts_enabled" in r:
                    r["sms_alerts_enabled"] = bool(r["sms_alerts_enabled"])
            return rows
        except Error as e:
            print(f"[MySQL Get Patients Error]: {e}")
    return list(reversed(in_memory_patients))


def delete_patient(patient_id: int) -> bool:
    """Deletes a registered patient by ID."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM registered_patients WHERE id = %s", (patient_id,))
            conn.commit()
            affected = cursor.rowcount
            cursor.close()
            conn.close()
            return affected > 0
        except Error as e:
            print(f"[MySQL Delete Patient Error]: {e}")

    global in_memory_patients
    before = len(in_memory_patients)
    in_memory_patients = [p for p in in_memory_patients if p.get("id") != patient_id]
    return len(in_memory_patients) < before


def get_all_caregiver_phones() -> list:
    """Returns all caregiver phone numbers from registered patients (for SMS broadcast)."""
    patients = get_all_patients()
    phones = []
    for p in patients:
        phone = p.get("caregiver_phone", "").strip()
        if phone and p.get("sms_alerts_enabled", True):
            phones.append(phone)
    return phones


# =============================================================================
# FAMILY MEMBERS CRUD
# =============================================================================

def add_family_member(patient_id: int, data: dict) -> int:
    """Adds a family member contact for a patient."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                INSERT INTO family_members
                (patient_id, member_name, relation, phone, email, sms_enabled)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (
                patient_id,
                data.get("member_name", ""),
                data.get("relation", ""),
                data.get("phone", ""),
                data.get("email", ""),
                data.get("sms_enabled", True)
            ))
            conn.commit()
            new_id = cursor.lastrowid
            cursor.close()
            conn.close()
            return new_id
        except Error as e:
            print(f"[MySQL Add Family Member Error]: {e}")
    return 0


def get_family_members(patient_id: int) -> list:
    """Returns all family members for a patient."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM family_members WHERE patient_id = %s ORDER BY id",
                (patient_id,)
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            for r in rows:
                r["sms_enabled"] = bool(r.get("sms_enabled", True))
                if "added_at" in r and r["added_at"]:
                    r["added_at"] = str(r["added_at"])
            return rows
        except Error as e:
            print(f"[MySQL Get Family Members Error]: {e}")
    return []


def delete_family_member(member_id: int) -> bool:
    """Deletes a family member contact by ID."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM family_members WHERE id = %s", (member_id,))
            conn.commit()
            affected = cursor.rowcount
            cursor.close()
            conn.close()
            return affected > 0
        except Error as e:
            print(f"[MySQL Delete Family Member Error]: {e}")
    return False


def get_all_sms_recipients_for_patient(patient_id: int) -> list:
    """
    Returns all SMS-enabled phone numbers for a patient:
    caregiver phone + all family members with sms_enabled=True.
    Numbers formatted as +91XXXXXXXXXX for Indian phones.
    """
    phones = []
    # Get patient's caregiver phone
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT caregiver_phone, sms_alerts_enabled FROM registered_patients WHERE id = %s",
                (patient_id,)
            )
            patient = cursor.fetchone()
            if patient and patient.get("sms_alerts_enabled") and patient.get("caregiver_phone"):
                phones.append(patient["caregiver_phone"].strip())
            cursor.close()
            conn.close()
        except Error:
            pass
    # Get all family members
    for member in get_family_members(patient_id):
        if member.get("sms_enabled") and member.get("phone"):
            phones.append(member["phone"].strip())
    return phones


def update_patient(patient_id: int, data: dict) -> bool:
    """Updates a registered patient's details."""
    conn = get_db_connection()
    if conn and conn.is_connected():
        try:
            cursor = conn.cursor()
            query = """
                UPDATE registered_patients SET
                    patient_name = %s, age = %s, blood_type = %s, gender = %s,
                    address = %s, medical_conditions = %s, notes = %s,
                    room_number = %s, device_id = %s,
                    caregiver_name = %s, caregiver_phone = %s,
                    caregiver_relation = %s, caregiver_email = %s,
                    doctor_name = %s, doctor_phone = %s,
                    emergency_service = %s, emergency_service_phone = %s,
                    sms_alerts_enabled = %s
                WHERE id = %s
            """
            cursor.execute(query, (
                data.get("patient_name", ""),
                data.get("age", 0),
                data.get("blood_type", ""),
                data.get("gender", ""),
                data.get("address", ""),
                data.get("medical_conditions", ""),
                data.get("notes", ""),
                data.get("room_number", ""),
                data.get("device_id", "ELDERLY_BAND_01"),
                data.get("caregiver_name", ""),
                data.get("caregiver_phone", ""),
                data.get("caregiver_relation", ""),
                data.get("caregiver_email", ""),
                data.get("doctor_name", ""),
                data.get("doctor_phone", ""),
                data.get("emergency_service", ""),
                data.get("emergency_service_phone", ""),
                data.get("sms_alerts_enabled", True),
                patient_id
            ))
            conn.commit()
            updated = cursor.rowcount > 0
            cursor.close()
            conn.close()
            return updated
        except Error as e:
            print(f"[MySQL Update Patient Error]: {e}")
    return False
