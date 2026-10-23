"""
=================================================================================
Elderly Wearable Fall Detection & Health Illness Anomaly Algorithm Engine
=================================================================================
Signals evaluated:
1. Sudden acceleration peak (impact shock)
2. Orientation change (vertical/upright to horizontal/lying down)
3. Continued post-impact inactivity (stillness)
4. FSR footwear pressure drop followed by non-recovery (standing to lifted)
5. Repeated abnormal movement or tilt (sustained vs brief)
6. Physiological vitals anomalies (sudden HR reduction / sudden SpO2 collapse)
7. Sensor integrity validation (sensor fault warnings; no fabricated readings)

Decision Matrix:
+-------------------+--------------------+----------------------+----------------------------------------------------+
| FSR pressure drop | Orientation change | Impact / inactivity  | Decision                                           |
+-------------------+--------------------+----------------------+----------------------------------------------------+
| Yes               | Yes                | Supporting evidence  | High-confidence fall candidate; start confirmation |
| Yes               | Yes                | No evidence yet      | Possible fall; collect more evidence               |
| Yes               | No                 | No                   | Pressure-drop warning; do not confirm a fall       |
| No                | Yes                | Supporting evidence  | Possible fall; evaluate movement and orientation   |
| No                | No                 | No                   | Normal monitoring                                  |
| Any               | Any                | Sensor invalid       | Sensor-fault warning; do not fabricate readings   |
+-------------------+--------------------+----------------------+----------------------------------------------------+
"""

import time
import math
from enum import Enum
from typing import Dict, Any, Optional, Tuple

class Orientation(str, Enum):
    VERTICAL = "VERTICAL"       # Upright posture (0° to 35° from vertical)
    TILTED = "TILTED"           # Intermediate lean / bend (35° to 60°)
    HORIZONTAL = "HORIZONTAL"   # Lying flat / horizontal (> 60° from vertical)
    UNKNOWN = "UNKNOWN"

class DecisionState(str, Enum):
    HIGH_CONFIDENCE_FALL = "HIGH_CONFIDENCE_FALL"
    POSSIBLE_FALL_COLLECTING_EVIDENCE = "POSSIBLE_FALL_COLLECTING_EVIDENCE"
    PRESSURE_DROP_WARNING = "PRESSURE_DROP_WARNING"
    POSSIBLE_FALL_EVAL_MOVEMENT = "POSSIBLE_FALL_EVAL_MOVEMENT"
    NORMAL_MONITORING = "NORMAL_MONITORING"
    SENSOR_FAULT_WARNING = "SENSOR_FAULT_WARNING"

class IllnessAlert(str, Enum):
    NONE = "NONE"
    SUDDEN_BRADYCARDIA = "SUDDEN_BRADYCARDIA"         # Sudden heart rate reduction (e.g. syncope / cardiac block)
    SUDDEN_HYPOXIA = "SUDDEN_HYPOXIA"                 # Sudden SpO2 oxygen level collapse
    SYNCOPAL_FALL_RISK = "SYNCOPAL_FALL_RISK"         # Vitals collapse directly preceding or accompanying a fall
    TACHYCARDIA = "TACHYCARDIA"                       # Excessively elevated heart rate (> 110 BPM)

class FallDetectionEngine:
    """
    Real-time multi-factor decision engine for fall detection.
    Processes continuous streams of MPU-6050, FSR, and vitals telemetry.
    """

    def __init__(self):
        # Configuration Thresholds
        self.FSR_WEIGHTED_MIN = 150       # Minimum ADC for weighted footwear stance
        self.FSR_DROP_MAX = 50            # ADC considered off-foot / unweighted
        self.FSR_NON_RECOVERY_SEC = 1.2   # Time foot must stay unweighted to reject walking swing phase (~300ms)
        self.IMPACT_ACCEL_PEAK = 2.5      # Total acceleration g-force peak
        self.IMPACT_GYRO_PEAK = 200.0     # Angular rate deg/s
        self.INACTIVITY_ACCEL_TOL = 0.25  # Inactivity: |totalAccel - 1.0g| < 0.25g
        self.INACTIVITY_GYRO_MAX = 40.0   # Inactivity: gyro < 40 deg/s
        self.INACTIVITY_CONFIRM_SEC = 1.0 # Duration of post-impact stillness
        self.TILT_HORIZONTAL_DEG = 55.0   # Tilt angle threshold from vertical

        # Baseline & Calibration
        self.vertical_axis = "Z"          # Upright gravity axis ('Z', 'Y', or 'X')
        self.vertical_direction = 1.0     # +1.0 or -1.0
        self.calibrated = True

        # State tracking
        self.is_footwear_worn = False
        self.fsr_drop_start_time = 0.0
        self.fsr_pressure_drop_confirmed = False

        self.last_orientation = Orientation.VERTICAL
        self.orientation_change_start_time = 0.0
        self.orientation_changed_confirmed = False
        self.current_tilt_deg = 0.0

        self.last_impact_time = 0.0
        self.impact_peak_detected = False
        self.inactivity_start_time = 0.0
        self.impact_inactivity_confirmed = False

        # Vitals tracking
        self.vitals_history = []          # [(timestamp, hr, spo2)]
        self.last_normal_hr = 76
        self.last_normal_spo2 = 98
        self.recent_illness_event = IllnessAlert.NONE
        self.illness_event_time = 0.0

        # System output
        self.current_decision = DecisionState.NORMAL_MONITORING
        self.decision_reason = "Normal monitoring"
        self.current_illness_alert = IllnessAlert.NONE
        self.illness_notes = ""

    def calibrate_orientation(self, ax: float, ay: float, az: float):
        """Calibrates upright vertical axis from current resting standing posture."""
        axes = [('X', abs(ax), ax), ('Y', abs(ay), ay), ('Z', abs(az), az)]
        axes.sort(key=lambda x: x[1], reverse=True)
        dominant_axis, _, raw_val = axes[0]
        self.vertical_axis = dominant_axis
        self.vertical_direction = 1.0 if raw_val >= 0 else -1.0
        self.calibrated = True

    def calculate_tilt_angle(self, ax: float, ay: float, az: float, total_accel: float) -> Tuple[float, Orientation]:
        """Calculates tilt angle (degrees from upright vertical) and classifies orientation."""
        if total_accel <= 0.01:
            return 0.0, Orientation.UNKNOWN

        # Vertical component along calibrated axis
        if self.vertical_axis == 'Z':
            v_comp = az * self.vertical_direction
        elif self.vertical_axis == 'Y':
            v_comp = ay * self.vertical_direction
        else:
            v_comp = ax * self.vertical_direction

        # Clamp cosine to [-1.0, 1.0]
        cos_theta = max(-1.0, min(1.0, v_comp / total_accel))
        tilt_deg = math.degrees(math.acos(abs(cos_theta)))

        if tilt_deg <= 35.0:
            orient = Orientation.VERTICAL
        elif tilt_deg >= self.TILT_HORIZONTAL_DEG:
            orient = Orientation.HORIZONTAL
        else:
            orient = Orientation.TILTED

        return tilt_deg, orient

    def reset_state(self):
        """Resets all transient fall detection states to normal."""
        self.is_footwear_worn = True
        self.fsr_drop_start_time = 0.0
        self.fsr_pressure_drop_confirmed = False
        self.last_orientation = Orientation.VERTICAL
        self.orientation_change_start_time = 0.0
        self.orientation_changed_confirmed = False
        self.current_tilt_deg = 0.0
        self.last_impact_time = 0.0
        self.impact_peak_detected = False
        self.inactivity_start_time = 0.0
        self.impact_inactivity_confirmed = False
        self.current_decision = DecisionState.NORMAL_MONITORING
        self.decision_reason = "Normal monitoring"
        self.current_illness_alert = IllnessAlert.NONE
        self.illness_notes = ""
        self.recent_illness_event = IllnessAlert.NONE

    def process_telemetry(self,
                          fsr: int,
                          ax: float = 0.0,
                          ay: float = 0.0,
                          az: float = 1.0,
                          total_accel: float = 1.0,
                          total_gyro: float = 0.0,
                          heart_rate: int = 0,
                          spo2: int = 0,
                          finger_present: bool = False,
                          sensor_valid: bool = True,
                          tilt_deg: Optional[float] = None,
                          orientation: Optional[str] = None,
                          fsr_pressure_drop_override: Optional[bool] = None,
                          orientation_change_override: Optional[bool] = None,
                          impact_inactivity_override: Optional[bool] = None,
                          now: Optional[float] = None) -> Dict[str, Any]:
        """
        Main decision algorithm matching the truth table specification.
        """
        if now is None:
            now = time.time()

        # ----------------------------------------------------------------------
        # 1. SENSOR VALIDITY CHECK ("Sensor invalid -> Sensor-fault warning")
        # ----------------------------------------------------------------------
        if not sensor_valid or (fsr < 0 or fsr > 1023) or (total_accel <= 0.05 and total_gyro <= 0.05):
            self.current_decision = DecisionState.SENSOR_FAULT_WARNING
            self.decision_reason = "Sensor-fault warning; do not fabricate readings"
            return self._build_result(fsr, total_accel, total_gyro, 0.0, Orientation.UNKNOWN,
                                      False, False, False, finger_present, heart_rate, spo2)

        # ----------------------------------------------------------------------
        # 2. FSR PRESSURE DROP & NON-RECOVERY EVALUATION
        # ----------------------------------------------------------------------
        # Tracks standing vs lifted. Walking swing phase is typically ~300ms.
        # FSR drop requires sustained absence of pressure (> 1.2s).
        if fsr_pressure_drop_override is not None:
            self.fsr_pressure_drop_confirmed = fsr_pressure_drop_override
        else:
            if fsr >= self.FSR_WEIGHTED_MIN:
                self.is_footwear_worn = True
                self.fsr_drop_start_time = 0.0
                self.fsr_pressure_drop_confirmed = False
            elif self.is_footwear_worn and fsr <= self.FSR_DROP_MAX:
                if self.fsr_drop_start_time == 0.0:
                    self.fsr_drop_start_time = now
                elif (now - self.fsr_drop_start_time) >= self.FSR_NON_RECOVERY_SEC:
                    # Footwear pressure dropped and has NOT recovered
                    self.fsr_pressure_drop_confirmed = True
            else:
                # Intermediate or unweighted from boot
                if not self.is_footwear_worn:
                    self.fsr_pressure_drop_confirmed = False

        # ----------------------------------------------------------------------
        # 3. MPU-6050 ORIENTATION TRACKING (VERTICAL TO HORIZONTAL)
        # ----------------------------------------------------------------------
        if tilt_deg is not None and orientation is not None:
            calc_tilt = float(tilt_deg)
            try:
                calc_orient = Orientation(orientation)
            except Exception:
                calc_orient = Orientation.HORIZONTAL if calc_tilt >= self.TILT_HORIZONTAL_DEG else Orientation.VERTICAL
        else:
            calc_tilt, calc_orient = self.calculate_tilt_angle(ax, ay, az, total_accel)

        self.current_tilt_deg = calc_tilt

        if orientation_change_override is not None:
            self.orientation_changed_confirmed = orientation_change_override
        else:
            if calc_orient == Orientation.HORIZONTAL:
                if self.orientation_change_start_time == 0.0:
                    self.orientation_change_start_time = now
                elif (now - self.orientation_change_start_time) >= 0.4:
                    # Sustained horizontal posture
                    self.orientation_changed_confirmed = True
            elif calc_orient == Orientation.VERTICAL:
                self.orientation_change_start_time = 0.0
                self.orientation_changed_confirmed = False
        self.last_orientation = calc_orient

        # ----------------------------------------------------------------------
        # 4. IMPACT PEAK & POST-IMPACT INACTIVITY TRACKING
        # ----------------------------------------------------------------------
        if impact_inactivity_override is not None:
            self.impact_inactivity_confirmed = impact_inactivity_override
        else:
            # Check for sudden acceleration peak (impact shock)
            if total_accel >= self.IMPACT_ACCEL_PEAK or (total_accel >= 2.2 and total_gyro >= self.IMPACT_GYRO_PEAK):
                self.impact_peak_detected = True
                self.last_impact_time = now
                self.inactivity_start_time = 0.0
                self.impact_inactivity_confirmed = False

            # Check for post-impact inactivity within 4.0s of impact
            if self.impact_peak_detected and (now - self.last_impact_time) <= 4.0:
                is_still = (abs(total_accel - 1.0) <= self.INACTIVITY_ACCEL_TOL) and (total_gyro <= self.INACTIVITY_GYRO_MAX)
                if is_still:
                    if self.inactivity_start_time == 0.0:
                        self.inactivity_start_time = now
                    elif (now - self.inactivity_start_time) >= self.INACTIVITY_CONFIRM_SEC:
                        # Sustained post-impact stillness confirmed!
                        self.impact_inactivity_confirmed = True
                else:
                    self.inactivity_start_time = 0.0
            elif (now - self.last_impact_time) > 4.0:
                # Impact window expired
                self.impact_peak_detected = False
                self.impact_inactivity_confirmed = False

        # ----------------------------------------------------------------------
        # 5. ILLNESS & VITALS ANOMALY DETECTION
        # ----------------------------------------------------------------------
        self._evaluate_illness_anomalies(heart_rate, spo2, finger_present, now)

        # ----------------------------------------------------------------------
        # 6. DECISION MATRIX EVALUATION (Exact Match to User Table)
        # ----------------------------------------------------------------------
        fsr_drop = self.fsr_pressure_drop_confirmed
        orient_change = self.orientation_changed_confirmed
        impact_inact = self.impact_inactivity_confirmed

        # Case 1: Yes, Yes, Supporting evidence present
        if fsr_drop and orient_change and impact_inact:
            self.current_decision = DecisionState.HIGH_CONFIDENCE_FALL
            if self.recent_illness_event != IllnessAlert.NONE and (now - self.illness_event_time) <= 20.0:
                self.decision_reason = f"High-confidence syncopal fall candidate ({self.recent_illness_event.value}); start confirmation"
            else:
                self.decision_reason = "High-confidence fall candidate; start confirmation"

        # Case 2: Yes, Yes, No supporting evidence yet
        elif fsr_drop and orient_change and not impact_inact:
            self.current_decision = DecisionState.POSSIBLE_FALL_COLLECTING_EVIDENCE
            self.decision_reason = "Possible fall; collect more evidence"

        # Case 3: Yes, No, No
        elif fsr_drop and not orient_change and not impact_inact:
            self.current_decision = DecisionState.PRESSURE_DROP_WARNING
            self.decision_reason = "Pressure-drop warning; do not confirm a fall"

        # Case 4: No, Yes, Yes
        elif not fsr_drop and orient_change and impact_inact:
            self.current_decision = DecisionState.POSSIBLE_FALL_EVAL_MOVEMENT
            self.decision_reason = "Possible fall; evaluate movement and orientation"

        # Case 5: No, No, No
        elif not fsr_drop and not orient_change and not impact_inact:
            self.current_decision = DecisionState.NORMAL_MONITORING
            self.decision_reason = "Normal monitoring"

        # Additional edge cases (e.g. impact while upright):
        elif fsr_drop and not orient_change and impact_inact:
            self.current_decision = DecisionState.PRESSURE_DROP_WARNING
            self.decision_reason = "Pressure-drop with impact, but orientation is upright; do not confirm a fall"
        elif not fsr_drop and orient_change and not impact_inact:
            self.current_decision = DecisionState.POSSIBLE_FALL_COLLECTING_EVIDENCE
            self.decision_reason = "Orientation changed without FSR drop; evaluating posture"
        else:
            self.current_decision = DecisionState.NORMAL_MONITORING
            self.decision_reason = "Normal monitoring"

        return self._build_result(fsr, total_accel, total_gyro, calc_tilt, calc_orient,
                                  fsr_drop, orient_change, impact_inact, finger_present, heart_rate, spo2)

    def _evaluate_illness_anomalies(self, hr: int, spo2: int, finger_present: bool, now: float):
        """
        Detects sudden heart rate reductions and oxygen drops when finger is placed.
        Never fabricates readings when finger is not placed.
        """
        self.current_illness_alert = IllnessAlert.NONE
        self.illness_notes = ""

        if not finger_present or hr <= 0 or spo2 <= 0:
            # Sensor idle / finger not placed. Strictly avoid fabricating illness alerts.
            return

        # Maintain resting baseline buffer
        self.vitals_history.append((now, hr, spo2))
        # Keep last 30 seconds
        self.vitals_history = [v for v in self.vitals_history if now - v[0] <= 30.0]

        if len(self.vitals_history) >= 3:
            # Baseline is median/avg of window excluding newest
            avg_prev_hr = sum(v[1] for v in self.vitals_history[:-1]) / (len(self.vitals_history) - 1)
            avg_prev_spo2 = sum(v[2] for v in self.vitals_history[:-1]) / (len(self.vitals_history) - 1)

            # 1. Sudden Heart Rate Reduction (Bradycardia / Vasovagal syncope / Sick sinus)
            # Either HR drops below 52 BPM or drops rapidly by > 20 BPM from baseline
            if hr < 52 or (avg_prev_hr - hr >= 20):
                self.current_illness_alert = IllnessAlert.SUDDEN_BRADYCARDIA
                self.illness_notes = f"Sudden Heart Rate Drop detected ({hr} BPM vs baseline {avg_prev_hr:.0f} BPM). Risk of syncope / cardiovascular distress."
                self.recent_illness_event = IllnessAlert.SUDDEN_BRADYCARDIA
                self.illness_event_time = now

            # 2. Sudden Oxygen Level Falls (Hypoxemia / Respiratory distress / Pulmonary embolism)
            elif spo2 < 90 or (avg_prev_spo2 - spo2 >= 5):
                self.current_illness_alert = IllnessAlert.SUDDEN_HYPOXIA
                self.illness_notes = f"Sudden Blood Oxygen Drop detected ({spo2}% vs baseline {avg_prev_spo2:.0f}%). Risk of acute hypoxia / respiratory crisis."
                self.recent_illness_event = IllnessAlert.SUDDEN_HYPOXIA
                self.illness_event_time = now

            # 3. Severe Tachycardia (> 115 BPM resting)
            elif hr > 115:
                self.current_illness_alert = IllnessAlert.TACHYCARDIA
                self.illness_notes = f"Elevated Heart Rate ({hr} BPM). Risk of arrhythmia or acute panic/stress."

    def _build_result(self, fsr: int, total_accel: float, total_gyro: float, tilt_deg: float,
                      orient: Orientation, fsr_drop: bool, orient_change: bool,
                      impact_inact: bool, finger_present: bool, hr: int, spo2: int) -> Dict[str, Any]:
        return {
            "fsr": fsr,
            "total_accel": round(total_accel, 2),
            "total_gyro": round(total_gyro, 1),
            "tilt_deg": round(tilt_deg, 1),
            "orientation": orient.value,
            "fsr_pressure_drop": fsr_drop,
            "orientation_change": orient_change,
            "impact_inactivity": impact_inact,
            "decision": self.current_decision.value,
            "decision_reason": self.decision_reason,
            "illness_alert": self.current_illness_alert.value,
            "illness_notes": self.illness_notes,
            "heart_rate": hr,
            "spo2": spo2,
            "finger_present": finger_present,
            "is_fall_candidate": self.current_decision == DecisionState.HIGH_CONFIDENCE_FALL
        }

    def simulate_truth_table_row(self, row_num: int) -> Dict[str, Any]:
        """
        Simulates one of the 6 truth table decisions specified by the user:
        Row 1: Yes, Yes, Yes -> High-confidence fall candidate; start confirmation
        Row 2: Yes, Yes, No  -> Possible fall; collect more evidence
        Row 3: Yes, No,  No  -> Pressure-drop warning; do not confirm a fall
        Row 4: No,  Yes, Yes -> Possible fall; evaluate movement and orientation
        Row 5: No,  No,  No  -> Normal monitoring
        Row 6: Any, Any, Invalid -> Sensor-fault warning; do not fabricate readings
        """
        now = time.time()
        if row_num == 1:
            self.reset_state()
            return self.process_telemetry(
                fsr=0, ax=0.96, ay=0.1, az=0.05, total_accel=1.0, total_gyro=2.0,
                tilt_deg=78.0, orientation="HORIZONTAL",
                fsr_pressure_drop_override=True,
                orientation_change_override=True,
                impact_inactivity_override=True,
                heart_rate=76, spo2=98, finger_present=True, now=now
            )
        elif row_num == 2:
            self.reset_state()
            return self.process_telemetry(
                fsr=0, ax=0.95, ay=0.1, az=0.05, total_accel=1.0, total_gyro=3.0,
                tilt_deg=75.0, orientation="HORIZONTAL",
                fsr_pressure_drop_override=True,
                orientation_change_override=True,
                impact_inactivity_override=False,
                heart_rate=76, spo2=98, finger_present=True, now=now
            )
        elif row_num == 3:
            self.reset_state()
            return self.process_telemetry(
                fsr=0, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, total_gyro=0.0,
                tilt_deg=10.0, orientation="VERTICAL",
                fsr_pressure_drop_override=True,
                orientation_change_override=False,
                impact_inactivity_override=False,
                heart_rate=76, spo2=98, finger_present=True, now=now
            )
        elif row_num == 4:
            self.reset_state()
            return self.process_telemetry(
                fsr=450, ax=0.95, ay=0.1, az=0.05, total_accel=1.0, total_gyro=2.0,
                tilt_deg=75.0, orientation="HORIZONTAL",
                fsr_pressure_drop_override=False,
                orientation_change_override=True,
                impact_inactivity_override=True,
                heart_rate=76, spo2=98, finger_present=True, now=now
            )
        elif row_num == 5:
            self.reset_state()
            return self.process_telemetry(
                fsr=450, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, total_gyro=0.0,
                tilt_deg=8.0, orientation="VERTICAL",
                fsr_pressure_drop_override=False,
                orientation_change_override=False,
                impact_inactivity_override=False,
                heart_rate=76, spo2=98, finger_present=True, now=now
            )
        elif row_num == 6:
            self.reset_state()
            return self.process_telemetry(
                fsr=1023, total_accel=0.0, total_gyro=0.0,
                sensor_valid=False, heart_rate=0, spo2=0, finger_present=False, now=now
            )
        else:
            return self.process_telemetry(fsr=450, total_accel=1.0, now=now)

    def simulate_illness_event(self, illness_type: str) -> Dict[str, Any]:
        """
        Simulates health illness anomalies:
        - 'bradycardia': sudden heart rate drop (< 52 BPM or > 20 BPM reduction)
        - 'hypoxia': sudden SpO2 oxygen level collapse (< 90% or > 5% drop)
        - 'syncopal_fall': vitals drop directly preceding high-confidence fall
        - 'normal': normal healthy vitals
        """
        now = time.time()
        self.vitals_history = [(now - 10.0, 76, 98), (now - 5.0, 78, 98)]
        if illness_type == "bradycardia":
            return self.process_telemetry(fsr=400, total_accel=1.0, heart_rate=45, spo2=98, finger_present=True, now=now)
        elif illness_type == "hypoxia":
            return self.process_telemetry(fsr=400, total_accel=1.0, heart_rate=74, spo2=84, finger_present=True, now=now)
        elif illness_type == "syncopal_fall":
            self.recent_illness_event = IllnessAlert.SUDDEN_BRADYCARDIA
            self.illness_event_time = now
            return self.process_telemetry(
                fsr=0, ax=0.96, ay=0.1, az=0.05, total_accel=1.0, total_gyro=2.0,
                tilt_deg=80.0, orientation="HORIZONTAL",
                fsr_pressure_drop_override=True, orientation_change_override=True, impact_inactivity_override=True,
                heart_rate=44, spo2=97, finger_present=True, now=now
            )
        else:
            return self.process_telemetry(fsr=400, total_accel=1.0, heart_rate=76, spo2=98, finger_present=True, now=now)


# =================================================================================
# SELF-TEST & VALIDATION SUITE (Verifies all 6 rows of the user's specification)
# =================================================================================
def run_validation_suite():
    print("======================================================================")
    print("  RUNNING FALL DETECTION & ILLNESS ALGORITHM VERIFICATION SUITE")
    print("======================================================================")
    engine = FallDetectionEngine()

    test_passed = 0
    test_total = 0

    def assert_test(name, condition, details=""):
        nonlocal test_passed, test_total
        test_total += 1
        if condition:
            print(f" [PASS] Test {test_total}: {name}")
            test_passed += 1
        else:
            print(f"![FAIL] Test {test_total}: {name} -> {details}")

    # Initial state: Normal standing
    t0 = 1000.0
    res = engine.process_telemetry(fsr=500, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, total_gyro=0.0,
                                   heart_rate=75, spo2=98, finger_present=True, now=t0)
    assert_test("Row 5: Normal monitoring (No, No, No)",
                res["decision"] == DecisionState.NORMAL_MONITORING.value and not res["fsr_pressure_drop"])

    # Test rejection of walking swing phase (~300ms FSR dip)
    t1 = t0 + 0.3
    res_swing = engine.process_telemetry(fsr=0, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, total_gyro=15.0, now=t1)
    # Swing phase should NOT confirm FSR drop yet (requires > 1.2s non-recovery)
    assert_test("Walking Swing Phase Rejection (300ms FSR dip is ignored)",
                not res_swing["fsr_pressure_drop"] and res_swing["decision"] == DecisionState.NORMAL_MONITORING.value)

    # Foot re-strikes ground during walking (recovery to 550 ADC)
    t2 = t1 + 0.2
    res_recover = engine.process_telemetry(fsr=550, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, total_gyro=5.0, now=t2)
    assert_test("Normal Walking Gait Recovery Verified",
                not res_recover["fsr_pressure_drop"] and res_recover["decision"] == DecisionState.NORMAL_MONITORING.value)

    # Test Row 3: Yes, No, No -> Pressure-drop warning; do not confirm a fall (e.g. Sitting down on a chair)
    t3 = t2 + 1.0
    engine.process_telemetry(fsr=0, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, total_gyro=0.0, now=t3) # Drop starts
    t4 = t3 + 1.5 # After 1.5s non-recovery
    res_sit = engine.process_telemetry(fsr=0, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, total_gyro=0.0, now=t4)
    assert_test("Row 3: Pressure-drop warning (Yes, No, No - Sitting Down)",
                res_sit["decision"] == DecisionState.PRESSURE_DROP_WARNING.value and res_sit["fsr_pressure_drop"])

    # Test Row 2: Yes, Yes, No -> Possible fall; collect more evidence (Orientation changed to horizontal, but no impact peak)
    t5 = t4 + 1.0
    res_tilt = engine.process_telemetry(fsr=0, ax=0.98, ay=0.1, az=0.05, total_accel=1.0, total_gyro=5.0, now=t5)
    t6 = t5 + 0.5
    res_orient = engine.process_telemetry(fsr=0, ax=0.98, ay=0.1, az=0.05, total_accel=1.0, total_gyro=5.0, now=t6)
    assert_test("Row 2: Possible fall; collect more evidence (Yes, Yes, No)",
                res_orient["decision"] == DecisionState.POSSIBLE_FALL_COLLECTING_EVIDENCE.value and res_orient["orientation_change"])

    # Test Row 1: Yes, Yes, Supporting evidence present -> High-confidence fall candidate
    # Impact shock peak occurs
    t7 = t6 + 0.2
    engine.process_telemetry(fsr=0, ax=2.1, ay=1.2, az=0.8, total_accel=2.8, total_gyro=240.0, now=t7)
    # Stillness begins right after impact
    engine.process_telemetry(fsr=0, ax=0.95, ay=0.1, az=0.0, total_accel=1.0, total_gyro=2.0, now=t7 + 0.1)
    # Stillness sustained for > 1.0s
    t8 = t7 + 1.2
    res_fall = engine.process_telemetry(fsr=0, ax=0.95, ay=0.1, az=0.0, total_accel=1.0, total_gyro=2.0, now=t8)
    assert_test("Row 1: High-confidence fall candidate (Yes, Yes, Yes)",
                res_fall["decision"] == DecisionState.HIGH_CONFIDENCE_FALL.value and res_fall["impact_inactivity"])

    # Test Row 4: No, Yes, Yes -> Possible fall; evaluate movement and orientation
    engine2 = FallDetectionEngine()
    engine2.process_telemetry(fsr=400, ax=0.0, ay=0.0, az=1.0, total_accel=1.0, now=100.0) # Establish standing
    # Foot stays pressed (e.g. pinned), but torso tilts to horizontal and impact occurs
    engine2.process_telemetry(fsr=400, ax=0.95, ay=0.1, az=0.0, total_accel=2.9, total_gyro=220.0, now=101.0) # Impact
    # Stillness begins
    engine2.process_telemetry(fsr=400, ax=0.95, ay=0.1, az=0.0, total_accel=1.0, total_gyro=2.0, now=101.1)
    # Stillness sustained for > 1.0s
    t_inact = 101.0 + 1.3
    res_pinned = engine2.process_telemetry(fsr=400, ax=0.95, ay=0.1, az=0.0, total_accel=1.0, total_gyro=2.0, now=t_inact)
    assert_test("Row 4: Possible fall; evaluate movement (No, Yes, Yes)",
                res_pinned["decision"] == DecisionState.POSSIBLE_FALL_EVAL_MOVEMENT.value and not res_pinned["fsr_pressure_drop"])

    # Test Row 6: Any, Any, Sensor invalid -> Sensor-fault warning; do not fabricate readings
    res_fault = engine.process_telemetry(fsr=1023, total_accel=0.0, total_gyro=0.0, sensor_valid=False, now=200.0)
    assert_test("Row 6: Sensor-fault warning (Sensor invalid -> no fabricated readings)",
                res_fault["decision"] == DecisionState.SENSOR_FAULT_WARNING.value)

    # Test Health Illness 1: Sudden Heart Rate Reduction (Bradycardia / Syncope)
    engine_health = FallDetectionEngine()
    # Feed resting history
    engine_health.process_telemetry(fsr=400, total_accel=1.0, heart_rate=78, spo2=98, finger_present=True, now=300.0)
    engine_health.process_telemetry(fsr=400, total_accel=1.0, heart_rate=76, spo2=98, finger_present=True, now=302.0)
    engine_health.process_telemetry(fsr=400, total_accel=1.0, heart_rate=77, spo2=98, finger_present=True, now=304.0)
    # Sudden drop to 46 BPM
    res_brady = engine_health.process_telemetry(fsr=400, total_accel=1.0, heart_rate=46, spo2=98, finger_present=True, now=306.0)
    assert_test("Health Illness 1: Sudden Heart Rate Reduction Detected",
                res_brady["illness_alert"] == IllnessAlert.SUDDEN_BRADYCARDIA.value)

    # Test Health Illness 2: Sudden Oxygen Level Fall (Hypoxia)
    res_hypo = engine_health.process_telemetry(fsr=400, total_accel=1.0, heart_rate=75, spo2=87, finger_present=True, now=308.0)
    assert_test("Health Illness 2: Sudden Oxygen Level Fall (< 90% SpO2)",
                res_hypo["illness_alert"] == IllnessAlert.SUDDEN_HYPOXIA.value)

    # Test Health Illness 3: Finger Off does NOT fabricate illness alert
    res_finger_off = engine_health.process_telemetry(fsr=400, total_accel=1.0, heart_rate=0, spo2=0, finger_present=False, now=310.0)
    assert_test("Finger Off does NOT fabricate illness alert",
                res_finger_off["illness_alert"] == IllnessAlert.NONE.value)

    print("----------------------------------------------------------------------")
    print(f"  VERIFICATION RESULT: {test_passed}/{test_total} TESTS PASSED!")
    print("======================================================================\n")

if __name__ == "__main__":
    run_validation_suite()
