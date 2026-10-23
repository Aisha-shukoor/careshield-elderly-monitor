"""
CareShield - Emergency SMS Notification Service (SMS Gate API)
---------------------------------------------------------------
Uses sms-gate.app API to send real SMS via your Android phone.
- Indian numbers formatted as +91XXXXXXXXXX automatically.
- Dry-run mode available for safe testing (no real SMS sent).
- 60-second cooldown per recipient to prevent SMS floods.
- All SMS events logged to DB or in-memory fallback.
"""

import time
import threading
import requests
import os
from datetime import datetime
from typing import Dict, List, Optional
import database

# ---------------------------------------------------------------
# SMS Gate API Credentials (from env vars or fallback defaults)
# ---------------------------------------------------------------
SMSGATE_USERNAME   = os.getenv("SMSGATE_USERNAME",  "1YENZ2")
SMSGATE_PASSWORD   = os.getenv("SMSGATE_PASSWORD",  "fic7tup7rnadag")
SMSGATE_DEVICE_ID  = os.getenv("SMSGATE_DEVICE_ID", "w2X8I1a_2s71np87xPrcJ")
SMSGATE_API_URL    = "https://api.sms-gate.app/3rdparty/v1/messages"
SMSGATE_SIM_NUMBER = 2  # Active SIM slot


def _format_indian_number(phone: str) -> str:
    """Ensures the phone number is in +91XXXXXXXXXX format for India."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        return phone  # Already formatted
    if phone.startswith("91") and len(phone) == 12:
        return "+" + phone
    if len(phone) == 10:
        return "+91" + phone
    return "+" + phone  # Fallback


def _send_via_smsgate(phone: str, message: str) -> dict:
    """
    Sends a real SMS via sms-gate.app API.
    Returns dict with 'success', 'status_code', and 'response' keys.
    """
    formatted = _format_indian_number(phone)
    payload = {
        "textMessage": {"text": message},
        "phoneNumbers": [formatted],
        "deviceId": SMSGATE_DEVICE_ID,
        "simNumber": SMSGATE_SIM_NUMBER
    }
    try:
        resp = requests.post(
            SMSGATE_API_URL,
            json=payload,
            auth=(SMSGATE_USERNAME, SMSGATE_PASSWORD),
            timeout=15
        )
        success = resp.status_code in (200, 201, 202)
        return {
            "success": success,
            "status_code": resp.status_code,
            "response": resp.text[:300]
        }
    except requests.exceptions.Timeout:
        return {"success": False, "status_code": 0, "response": "SMS Gateway timeout"}
    except Exception as e:
        return {"success": False, "status_code": 0, "response": str(e)}


class SMSService:
    def __init__(self, cooldown_seconds: float = 60.0):
        self.cooldown_seconds = cooldown_seconds
        self._recent_dispatches: Dict[str, float] = {}
        self._lock = threading.Lock()

    def send_emergency_alert(
        self,
        alert_type: str,
        patient_name: str,
        room: str,
        details: str,
        recipients: Optional[List[str]] = None,
        dry_run: Optional[bool] = None
    ) -> List[Dict]:
        """
        Sends emergency SMS to caregiver/family contacts when fall or SOS detected.
        Reads phone numbers from registered patient profile.
        """
        profile = database.get_patient_profile()
        if not profile.get("sms_enabled", True):
            print("[SMS] Notifications disabled in patient profile.")
            return []

        # Determine dry-run mode from profile
        if dry_run is None:
            dry_run = bool(profile.get("sms_dry_run", True))

        # Build recipient list:
        # Priority 1: explicitly passed recipients
        # Priority 2: all registered patients' caregiver + family member phones
        # Priority 3: profile sms_recipients field
        if not recipients:
            # Pull from all registered patients with sms enabled
            registered_phones = database.get_all_caregiver_phones()
            # Also include family members from all patients
            all_patients = database.get_all_patients()
            for p in all_patients:
                if p.get("sms_alerts_enabled", True):
                    pid = p.get("id")
                    if pid:
                        for member in database.get_family_members(pid):
                            if member.get("sms_enabled") and member.get("phone"):
                                registered_phones.append(member["phone"].strip())
            if registered_phones:
                recipients = list(dict.fromkeys(registered_phones))  # deduplicate
            else:
                # Fallback to profile sms_recipients
                raw = profile.get("sms_recipients", "")
                recipients = [r.strip() for r in raw.replace(";", ",").split(",") if r.strip()]
                if not recipients and profile.get("caregiver_phone"):
                    recipients = [profile["caregiver_phone"].strip()]
        if not recipients:
            print("[SMS] No recipient phone numbers configured.")
            return []

        now_epoch = time.time()
        time_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

        # Compose message based on alert type
        if alert_type == "HIGH_CONFIDENCE_FALL":
            body = (
                f"[CARESHIELD FALL ALERT] {patient_name} has been detected in a fall "
                f"at {room} on {time_str}. "
                f"Impact and horizontal posture confirmed by sensors. "
                f"Please check immediately! Details: {details}"
            )
        elif alert_type == "MANUAL_SOS_URGENT":
            body = (
                f"[CARESHIELD SOS EMERGENCY] {patient_name} pressed the emergency SOS button "
                f"at {room} on {time_str}! "
                f"Immediate assistance is required. Notes: {details}"
            )
        elif "ILLNESS" in alert_type or "BRADYCARDIA" in alert_type or "HYPOXIA" in alert_type:
            body = (
                f"[CARESHIELD VITAL ALERT] Abnormal vital signs detected for {patient_name} "
                f"at {room} on {time_str}. "
                f"{details}. Please check patient immediately."
            )
        else:
            body = (
                f"[CARESHIELD ALERT] Event '{alert_type}' for {patient_name} "
                f"at {room} on {time_str}. {details}"
            )

        results = []
        for recipient in recipients:
            throttle_key = f"{recipient}:{alert_type}"

            with self._lock:
                last_sent = self._recent_dispatches.get(throttle_key, 0.0)

            # Cooldown check - prevent SMS flood
            if (now_epoch - last_sent) < self.cooldown_seconds:
                elapsed = int(now_epoch - last_sent)
                msg = f"Duplicate suppressed ({elapsed}s < {self.cooldown_seconds}s cooldown)"
                log_id = database.log_sms_notification(
                    recipient=recipient, message=body,
                    status="SUPPRESSED_DUPLICATE", provider_response=msg, dry_run=dry_run
                )
                results.append({"id": log_id, "recipient": recipient, "status": "SUPPRESSED_DUPLICATE"})
                print(f"[SMS Throttled] {recipient}: {msg}")
                continue

            # Log as QUEUED first
            log_id = database.log_sms_notification(
                recipient=recipient, message=body,
                status="QUEUED", provider_response="Queued for dispatch", dry_run=dry_run
            )

            if dry_run:
                # Safe dry-run: simulate without real SMS
                response_msg = f"DRY-RUN MODE: SMS simulated (not sent). Ref #{log_id}."
                database.update_sms_status(log_id, "SENT_TO_GATEWAY", response_msg)
                with self._lock:
                    self._recent_dispatches[throttle_key] = now_epoch
                results.append({
                    "id": log_id, "recipient": recipient, "status": "DRY_RUN_SIMULATED",
                    "dry_run": True, "message": body
                })
                print(f"[SMS DRY-RUN] #{log_id} -> {recipient}")
            else:
                # Real SMS via SMS Gate API (runs in background thread)
                threading.Thread(
                    target=self._dispatch_background,
                    args=(log_id, recipient, body, throttle_key, now_epoch),
                    daemon=True
                ).start()
                results.append({
                    "id": log_id, "recipient": recipient,
                    "status": "DISPATCHING", "dry_run": False, "message": body
                })
                print(f"[SMS LIVE] Dispatching to {recipient} via SMS Gate API...")

        return results

    def _dispatch_background(self, log_id: int, phone: str, message: str,
                              throttle_key: str, sent_epoch: float):
        """Background thread: sends real SMS and updates DB status."""
        result = _send_via_smsgate(phone, message)
        if result["success"]:
            database.update_sms_status(log_id, "SENT_TO_GATEWAY",
                                       f"HTTP {result['status_code']}: {result['response']}")
            with self._lock:
                self._recent_dispatches[throttle_key] = sent_epoch
            print(f"[SMS Sent] #{log_id} -> {phone}: HTTP {result['status_code']}")
        else:
            database.update_sms_status(log_id, "FAILED",
                                       f"Gateway error: {result['response']}")
            print(f"[SMS Failed] #{log_id} -> {phone}: {result['response']}")

    def confirm_delivery_receipt(self, log_id: int, report: str = "DELIVERED_HANDSET_ACK") -> bool:
        """Updates SMS log status to DELIVERED after real carrier confirmation."""
        database.update_sms_status(log_id, "DELIVERED", report)
        print(f"[SMS Delivered] #{log_id}: {report}")
        return True

    def reset_cooldown(self):
        """Clears cooldown cache (used in tests)."""
        with self._lock:
            self._recent_dispatches.clear()


# Global singleton
sms_service = SMSService(cooldown_seconds=60.0)
