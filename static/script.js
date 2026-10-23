// ==========================================================================
// CareShield – Multi-Factor Fall Detection & Physiological Anomaly Engine
// Enhanced Dashboard Logic with Canvas Charts, Watchdog & SMS Service
// ==========================================================================

const API_BASE = "";

// Time-series telemetry cache for Canvas charts (up to 30 data points)
let timeseriesCache = [];
let activeTabName = "live";

// Lifecycle hooks
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  fetchSensorData();
  fetchHistory();
  fetchTimeseriesData();
  fetchPatientProfile();

  // Fast 1.0-second real-time telemetry cycle
  setInterval(fetchSensorData, 1000);

  // 3.5-second audit database and historical timeseries cycle
  setInterval(() => {
    if (activeTabName === "charts" || activeTabName === "live") {
      fetchTimeseriesData();
    }
    if (activeTabName === "charts") {
      fetchHistory();
    }
    if (activeTabName === "sms") {
      fetchSMSLogs();
    }
  }, 3500);

  // Resize listener for responsive Canvas charts
  window.addEventListener("resize", () => {
    if (activeTabName === "charts") {
      renderAllCharts(timeseriesCache);
    }
  });
});

// --------------------------------------------------------------------------
// Navigation & Tab Switching (Phase 2 Requirement)
// --------------------------------------------------------------------------
function switchTab(tabName) {
  activeTabName = tabName;
  const tabs = ["live", "charts", "patient", "sms"];

  tabs.forEach(t => {
    const btn = document.getElementById(`nav-btn-${t}`);
    const pane = document.getElementById(`tab-content-${t}`);
    if (btn) btn.classList.toggle("active", t === tabName);
    if (pane) pane.classList.toggle("active", t === tabName);
  });

  if (tabName === "charts") {
    fetchTimeseriesData();
    fetchHistory();
  } else if (tabName === "patient") {
    fetchPatientProfile();
  } else if (tabName === "sms") {
    fetchSMSLogs();
    fetchPatientProfile();
  }
}

// --------------------------------------------------------------------------
// Dark / Light Theme Toggle (Phase 2 Requirement)
// --------------------------------------------------------------------------
function initTheme() {
  const saved = localStorage.getItem("careshield_theme") || "dark";
  const icon = document.getElementById("theme-icon");
  if (saved === "light") {
    document.body.classList.add("light-theme");
    if (icon) icon.innerText = "☀️";
  } else {
    document.body.classList.remove("light-theme");
    if (icon) icon.innerText = "🌙";
  }
}

function toggleTheme() {
  const isLight = document.body.classList.toggle("light-theme");
  const icon = document.getElementById("theme-icon");
  if (isLight) {
    localStorage.setItem("careshield_theme", "light");
    if (icon) icon.innerText = "☀️";
  } else {
    localStorage.setItem("careshield_theme", "dark");
    if (icon) icon.innerText = "🌙";
  }
  // Re-render charts with updated theme colors if on charts tab
  if (activeTabName === "charts") {
    renderAllCharts(timeseriesCache);
  }
}

// --------------------------------------------------------------------------
// 1. Fetch Latest Telemetry & Render Dashboard
// --------------------------------------------------------------------------
async function fetchSensorData() {
  try {
    const response = await fetch(`${API_BASE}/api/sensor-data`);
    if (!response.ok) throw new Error("HTTP network error fetching telemetry");
    const data = await response.json();
    renderDashboard(data);
  } catch (error) {
    console.warn("[Dashboard Sync Reconnecting]:", error);
    const wifiState = document.getElementById("wifi-state");
    if (wifiState) {
      wifiState.innerText = "Reconnecting...";
      wifiState.style.color = "var(--accent-amber)";
    }
  }
}

function renderDashboard(data) {
  const isOnline = Boolean(data.is_online);

  // 1. Sensor Packet Watchdog Connection Status (Phase 1 Requirement)
  const watchdogPill = document.getElementById("sensor-watchdog-pill");
  const watchdogText = document.getElementById("watchdog-text");
  if (watchdogPill && watchdogText) {
    if (isOnline) {
      watchdogPill.className = "status-pill status-active";
      watchdogText.innerText = "LIVE STREAM";
    } else {
      watchdogPill.className = "status-pill status-offline";
      const ageStr = (typeof data.packet_age_seconds === "number" && data.packet_age_seconds < 3600)
        ? ` (${Math.round(data.packet_age_seconds)}s)`
        : "";
      watchdogText.innerText = `SENSOR OFFLINE${ageStr}`;
    }
  }

  // 2. Hardware Monitoring State (Button 1)
  const monitoringPill = document.getElementById("monitoring-pill");
  const monitoringText = document.getElementById("monitoring-text");
  if (data.monitoring_status === "ACTIVE") {
    monitoringPill.className = "status-pill status-active";
    monitoringText.innerText = "Monitoring Active";
  } else {
    monitoringPill.className = "status-pill status-paused";
    monitoringText.innerText = "Monitoring Paused (Standby)";
  }

  // 3. Standalone Hotspot / ESP8266 Link State
  const wifiState = document.getElementById("wifi-state");
  if (wifiState) {
    if (isOnline) {
      wifiState.innerText = "Connected (Hotspot)";
      wifiState.style.color = "var(--accent-green)";
    } else {
      wifiState.innerText = "No Sensor Packets";
      wifiState.style.color = "var(--accent-rose)";
    }
  }

  // 4. Individual Sensor Status Indicators (Phase 2 Requirement)
  updateSensorHealthBadges(data, isOnline);

  // 5. Independent Manual SOS Emergency Banner (Phase 1 Requirement)
  const sosBanner = document.getElementById("sos-banner");
  const sosDesc = document.getElementById("sos-desc");
  if (sosBanner) {
    if (data.manual_sos) {
      sosBanner.className = "alert-banner banner-sos";
      if (sosDesc) sosDesc.innerText = data.manual_sos_notes || "Patient triggered manual emergency distress call! Immediate help required.";
    } else {
      sosBanner.className = "alert-banner banner-sos banner-hidden";
    }
  }

  // 6. Emergency Fall & Cancellation Banner (Separate Patient Cancel vs Caregiver Ack)
  const alertBanner = document.getElementById("alert-banner");
  const alertTitle  = document.getElementById("alert-title");
  const alertDesc   = document.getElementById("alert-desc");
  const btnCancel   = document.getElementById("btn-cancel-alert");
  const btnAck      = document.getElementById("btn-ack-alert");

  if (data.fall_detected) {
    alertBanner.className = "alert-banner";
    alertTitle.innerText = "Emergency Fall Candidate Detected!";
    alertDesc.innerText = data.decision_reason || "Wearable detected multi-factor fall event. Buzzer active on hardware.";
    if (btnCancel) btnCancel.style.display = "block";
    if (btnAck) btnAck.style.display = "block";
  } else if (data.alert_state === "CANCELLED_NO_WORRY" || data.alert_state === "WRONG_ALERT_TURNED_OFF") {
    alertBanner.className = "alert-banner banner-cancelled";
    alertTitle.innerText = "Wrong Alert Turned Off";
    alertDesc.innerText = data.cancellation_message || "Wrong alert turned off by patient. Alarm silenced and system safe.";
    if (btnCancel) btnCancel.style.display = "none";
    if (btnAck) btnAck.style.display = "none";
  } else if (data.alert_state === "ACKNOWLEDGED_SAFE") {
    alertBanner.className = "alert-banner banner-cancelled";
    alertTitle.innerText = "Caregiver Acknowledged Alert";
    alertDesc.innerText = data.cancellation_message || "Alert acknowledged by caregiver. Hardware alarm silenced.";
    if (btnCancel) btnCancel.style.display = "none";
    if (btnAck) btnAck.style.display = "none";
  } else if (data.alert_state && data.alert_state.startsWith("ABNORMAL")) {
    alertBanner.className = "alert-banner";
    alertTitle.innerText = "Abnormal Sensor Alert!";
    alertDesc.innerText = data.alert_state.replace("ABNORMAL_", "") + " - Press Button 2 or acknowledge.";
    if (btnCancel) btnCancel.style.display = "block";
    if (btnAck) btnAck.style.display = "block";
  } else {
    alertBanner.className = "alert-banner banner-hidden";
  }

  // 7. Automatic Fall Escalation Countdown Progress
  const countdownWrap = document.getElementById("alert-countdown-wrap");
  const countdownTimer = document.getElementById("alert-countdown-timer");
  const countdownFill = document.getElementById("alert-countdown-fill");
  if (countdownWrap && countdownTimer && countdownFill) {
    if (data.fall_detected) {
      countdownWrap.style.display = "block";
      const secLeft = typeof data.countdown === "number" ? data.countdown : 0;
      if (secLeft > 0) {
        countdownTimer.innerText = `${secLeft}s`;
        countdownTimer.className = "font-mono text-rose";
        countdownFill.style.width = `${(secLeft / 10) * 100}%`;
      } else {
        countdownTimer.innerText = "ESCALATED / UNRESPONSIVE";
        countdownTimer.className = "font-mono text-amber";
        countdownFill.style.width = "0%";
      }
    } else {
      countdownWrap.style.display = "none";
    }
  }

  // 8. Physiological Illness Anomaly Warning Banner
  const illnessBanner = document.getElementById("illness-banner");
  const illnessTitle  = document.getElementById("illness-title");
  const illnessDesc   = document.getElementById("illness-desc");

  if (data.illness_alert && data.illness_alert !== "NONE") {
    illnessBanner.className = "illness-banner";
    if (data.illness_alert === "SUDDEN_BRADYCARDIA") {
      illnessTitle.innerText = "Sudden Heart Rate Reduction Alert (Bradycardia)";
      illnessDesc.innerText = data.illness_notes || `Heart Rate collapsed to ${data.heart_rate} BPM. High risk of syncope or cardiovascular distress.`;
    } else if (data.illness_alert === "SUDDEN_HYPOXIA") {
      illnessTitle.innerText = "Sudden Blood Oxygen Drop Alert (Hypoxemia)";
      illnessDesc.innerText = data.illness_notes || `Blood oxygen collapsed to ${data.spo2}%. Risk of acute respiratory crisis.`;
    } else if (data.illness_alert === "TACHYCARDIA") {
      illnessTitle.innerText = "Elevated Heart Rate Alert (Tachycardia)";
      illnessDesc.innerText = data.illness_notes || `Heart rate elevated at ${data.heart_rate} BPM.`;
    } else {
      illnessTitle.innerText = "Physiological Illness Anomaly Warning";
      illnessDesc.innerText = data.illness_notes;
    }
  } else {
    illnessBanner.className = "illness-banner banner-hidden";
  }

  // 9. HERO DECISION MATRIX ENGINE CARD
  const heroBadge = document.getElementById("hero-decision-badge");
  const heroReason = document.getElementById("hero-decision-reason");
  const decision = data.decision || "NORMAL_MONITORING";

  if (heroBadge) {
    heroBadge.innerText = decision.replace(/_/g, " ");
    if (decision === "HIGH_CONFIDENCE_FALL") {
      heroBadge.className = "badge-decision badge-decision-fall";
    } else if (decision === "NORMAL_MONITORING") {
      heroBadge.className = "badge-decision badge-decision-normal";
    } else {
      heroBadge.className = "badge-decision badge-decision-warning";
    }
  }

  if (heroReason) {
    heroReason.innerText = data.decision_reason || "Normal monitoring";
  }

  // Factor 1: FSR Footwear Drop
  const sigCardFsr = document.getElementById("sig-card-fsr");
  const sigFsrState = document.getElementById("sig-fsr-state");
  const sigFsrVal = document.getElementById("sig-fsr-val");
  const sigFsrSub = document.getElementById("sig-fsr-sub");

  if (sigFsrState) {
    if (data.fsr_pressure_drop && isOnline) {
      sigFsrState.className = "signal-pill pill-yes";
      sigFsrState.innerText = "DROP CONFIRMED";
      sigCardFsr?.classList.add("signal-active");
    } else {
      sigFsrState.className = "signal-pill pill-no";
      sigFsrState.innerText = isOnline ? "NO DROP" : "OFFLINE";
      sigCardFsr?.classList.remove("signal-active");
    }
    if (sigFsrVal) sigFsrVal.innerText = isOnline ? `${data.fsr} ADC` : "N/A";
    if (sigFsrSub) sigFsrSub.innerText = isOnline ? (data.fsr_level || "Calibrated Baseline") : "Awaiting sensor...";
  }

  // Factor 2: Orientation Change (Vertical to Horizontal)
  const sigCardOrient = document.getElementById("sig-card-orient");
  const sigOrientState = document.getElementById("sig-orient-state");
  const sigOrientVal = document.getElementById("sig-orient-val");
  const sigOrientDeg = document.getElementById("sig-orient-deg");
  const tiltDeg = typeof data.tilt_deg === "number" ? data.tilt_deg : 0.0;

  if (sigOrientState) {
    if ((data.orientation_change || data.orientation === "HORIZONTAL") && isOnline) {
      sigOrientState.className = "signal-pill pill-yes";
      sigOrientState.innerText = `HORIZONTAL (${tiltDeg.toFixed(1)}°)`;
      sigCardOrient?.classList.add("signal-active");
    } else {
      sigOrientState.className = "signal-pill pill-no";
      sigOrientState.innerText = isOnline ? `${data.orientation || "VERTICAL"} (${tiltDeg.toFixed(1)}°)` : "OFFLINE";
      sigCardOrient?.classList.remove("signal-active");
    }
    if (sigOrientVal) sigOrientVal.innerText = isOnline ? (data.orientation || "VERTICAL") : "OFFLINE";
    if (sigOrientDeg) sigOrientDeg.innerText = isOnline ? `Tilt: ${tiltDeg.toFixed(1)}° from vertical` : "Awaiting tilt...";
  }

  // Animate Posture Avatar
  const postureAvatar = document.getElementById("posture-avatar");
  if (postureAvatar) {
    const clampedDeg = isOnline ? Math.min(90, Math.max(0, tiltDeg)) : 0;
    postureAvatar.style.transform = `rotate(${clampedDeg}deg)`;
  }
  const postureTiltEl = document.getElementById("posture-tilt-deg");
  if (postureTiltEl) postureTiltEl.innerText = isOnline ? `${tiltDeg.toFixed(1)}°` : "N/A";

  const postureZoneEl = document.getElementById("posture-zone-text");
  if (postureZoneEl) {
    if (!isOnline) {
      postureZoneEl.innerText = "Sensor Offline";
      postureZoneEl.className = "text-muted";
    } else if (data.orientation === "HORIZONTAL" || tiltDeg >= 55) {
      postureZoneEl.innerText = "Horizontal (Fallen Posture)";
      postureZoneEl.className = "text-rose";
    } else if (data.orientation === "TILTED" || tiltDeg >= 35) {
      postureZoneEl.innerText = "Tilted / Leaning";
      postureZoneEl.className = "text-amber";
    } else {
      postureZoneEl.innerText = "Upright Posture (Normal)";
      postureZoneEl.className = "text-green";
    }
  }

  const postureAccelEl = document.getElementById("posture-accel-val");
  const badgeOrient = document.getElementById("badge-orientation");

  if (!isOnline) {
    if (postureAccelEl) postureAccelEl.innerText = "N/A";
    if (badgeOrient) {
      badgeOrient.innerText = "OFFLINE";
      badgeOrient.className = "badge badge-urgent";
    }
  } else {
    if (postureAccelEl) {
      postureAccelEl.innerText = `${(data.total_accel || 1.0).toFixed(2)} g | Gyro: ${(data.total_gyro || 0.0).toFixed(1)} °/s`;
    }
    if (badgeOrient) {
      badgeOrient.innerText = `${data.orientation || "VERTICAL"} (${tiltDeg.toFixed(0)}°)`;
      badgeOrient.className = (data.orientation === "HORIZONTAL") ? "badge badge-urgent" : "badge badge-good";
    }
  }

  // Factor 3: Supporting Evidence (Impact + Stillness)
  const sigCardEvidence = document.getElementById("sig-card-evidence");
  const sigEvidenceState = document.getElementById("sig-evidence-state");
  const sigAccelVal = document.getElementById("sig-accel-val");
  const sigGyroVal = document.getElementById("sig-gyro-val");

  if (sigEvidenceState) {
    if (data.impact_inactivity && isOnline) {
      sigEvidenceState.className = "signal-pill pill-yes";
      sigEvidenceState.innerText = "CONFIRMED (Shock+Still)";
      sigCardEvidence?.classList.add("signal-active");
    } else {
      sigEvidenceState.className = "signal-pill pill-no";
      sigEvidenceState.innerText = isOnline ? "NO EVIDENCE" : "OFFLINE";
      sigCardEvidence?.classList.remove("signal-active");
    }
    if (sigAccelVal) sigAccelVal.innerText = isOnline ? `${(data.total_accel || 1.0).toFixed(2)} g` : "N/A";
    if (sigGyroVal) sigGyroVal.innerText = isOnline ? `Gyro: ${(data.total_gyro || 0.0).toFixed(1)} deg/s` : "N/A";
  }

  // 10. Truth Table Row Highlighter
  highlightTruthTableRow(decision);

  // 11. Heart Rate Card
  const metricHR = document.getElementById("metric-heart-rate");
  const badgeHR  = document.getElementById("badge-heart-rate");
  const noteHR   = document.getElementById("hr-status-note");
  const hrIllBox = document.getElementById("hr-illness-box");
  const hrIllText = document.getElementById("hr-illness-text");

  if (!isOnline) {
    metricHR.innerText = "N/A";
    badgeHR.className = "badge badge-urgent";
    badgeHR.innerText = "OFFLINE";
    noteHR.innerText = "Sensor Offline – Awaiting fresh packets";
    noteHR.style.color = "var(--text-secondary)";
    if (hrIllBox) hrIllBox.style.display = "none";
  } else if (!data.heart_rate || data.heart_rate <= 0 || !data.finger_present) {
    metricHR.innerText = "N/A";
    badgeHR.className = "badge badge-simulated";
    badgeHR.innerText = "NO FINGER";
    noteHR.innerText = "Finger is not placed (No fabricated readings)";
    noteHR.style.color = "var(--text-secondary)";
    if (hrIllBox) hrIllBox.style.display = "none";
  } else {
    metricHR.innerText = data.heart_rate;
    badgeHR.className = data.is_simulated ? "badge badge-good" : "badge badge-real";
    badgeHR.innerText = data.is_simulated ? "NORMAL" : "REAL";

    if (data.heart_rate < 52 || data.illness_alert === "SUDDEN_BRADYCARDIA") {
      noteHR.innerText = "Sudden Bradycardia (< 52 BPM) - Syncope Risk";
      noteHR.style.color = "var(--accent-rose)";
      if (hrIllBox) {
        hrIllBox.style.display = "block";
        hrIllText.innerText = "SUDDEN HEART RATE COLLAPSE DETECTED";
      }
    } else if (data.heart_rate > 105) {
      noteHR.innerText = "Tachycardia (Elevated Heart Rate)";
      noteHR.style.color = "var(--accent-amber)";
      if (hrIllBox) {
        hrIllBox.style.display = "block";
        hrIllText.innerText = "ELEVATED HEART RATE WARNING";
      }
    } else {
      noteHR.innerText = "Normal Resting Rhythm (60 - 100 BPM)";
      noteHR.style.color = "var(--accent-green)";
      if (hrIllBox) hrIllBox.style.display = "none";
    }
  }

  // 12. SpO2 Blood Oxygen Card
  const metricSpO2 = document.getElementById("metric-spo2");
  const badgeSpO2  = document.getElementById("badge-spo2");
  const noteSpO2   = document.getElementById("spo2-status-note");
  const fillSpO2   = document.getElementById("spo2-progress-fill");
  const spo2IllBox = document.getElementById("spo2-illness-box");
  const spo2IllText = document.getElementById("spo2-illness-text");

  if (!isOnline) {
    metricSpO2.innerText = "N/A";
    badgeSpO2.className = "badge badge-urgent";
    badgeSpO2.innerText = "OFFLINE";
    noteSpO2.innerText = "Sensor Offline";
    noteSpO2.style.color = "var(--text-secondary)";
    fillSpO2.style.width = "0%";
    if (spo2IllBox) spo2IllBox.style.display = "none";
  } else if (!data.spo2 || data.spo2 <= 0 || !data.finger_present) {
    metricSpO2.innerText = "N/A";
    badgeSpO2.className = "badge badge-simulated";
    badgeSpO2.innerText = "NO FINGER";
    noteSpO2.innerText = "Finger is not placed";
    noteSpO2.style.color = "var(--text-secondary)";
    fillSpO2.style.width = "0%";
    if (spo2IllBox) spo2IllBox.style.display = "none";
  } else {
    metricSpO2.innerText = data.spo2;
    badgeSpO2.className = "badge badge-good";
    badgeSpO2.innerText = "NORMAL";

    if (data.spo2 < 90 || data.illness_alert === "SUDDEN_HYPOXIA") {
      noteSpO2.innerText = "Sudden Hypoxia (< 90%) - Respiratory Crisis";
      noteSpO2.style.color = "var(--accent-rose)";
      fillSpO2.className = "progress-bar-fill fill-rose";
      if (spo2IllBox) {
        spo2IllBox.style.display = "block";
        spo2IllText.innerText = "SUDDEN OXYGEN LEVEL FALL DETECTED";
      }
    } else if (data.spo2 < 95) {
      noteSpO2.innerText = "Mild Hypoxia Caution (< 95%)";
      noteSpO2.style.color = "var(--accent-amber)";
      fillSpO2.className = "progress-bar-fill fill-amber";
      if (spo2IllBox) spo2IllBox.style.display = "none";
    } else {
      noteSpO2.innerText = "Optimal Saturation (95% - 100%)";
      noteSpO2.style.color = "var(--accent-green)";
      fillSpO2.className = "progress-bar-fill fill-green";
      if (spo2IllBox) spo2IllBox.style.display = "none";
    }
    fillSpO2.style.width = `${Math.min(100, Math.max(0, data.spo2))}%`;
  }

  // 13. Ultrasonic Obstacle Radar
  const metricDist = document.getElementById("metric-distance");
  const badgeDist  = document.getElementById("badge-distance");
  const noteDist   = document.getElementById("distance-status-note");
  const zones      = document.querySelectorAll(".zone-step");

  zones.forEach(z => z.classList.remove("active-urgent", "active-approach", "active-safe", "active-clear"));

  if (!isOnline) {
    metricDist.innerText = "N/A";
    badgeDist.className = "badge badge-urgent";
    badgeDist.innerText = "OFFLINE";
    noteDist.innerText = "Sensor Offline";
  } else {
    const dist = data.distance;
    if (dist > 90 || dist < 0) {
      metricDist.innerText = "> 90";
      badgeDist.className = "badge badge-clear";
      badgeDist.innerText = "PATH CLEAR";
      noteDist.innerText = "Above 90 cm: Path clear";
      zones[3]?.classList.add("active-clear");
    } else if (dist > 45) {
      metricDist.innerText = dist.toFixed(1);
      badgeDist.className = "badge badge-good";
      badgeDist.innerText = "SAFE RANGE";
      noteDist.innerText = "46 - 90 cm: Safe clearance";
      zones[2]?.classList.add("active-safe");
    } else if (dist > 15) {
      metricDist.innerText = dist.toFixed(1);
      badgeDist.className = "badge badge-simulated";
      badgeDist.innerText = "APPROACHING";
      noteDist.innerText = "16 - 45 cm: Caution Warning";
      zones[1]?.classList.add("active-approach");
    } else {
      metricDist.innerText = dist.toFixed(1);
      badgeDist.className = "badge badge-urgent";
      badgeDist.innerText = "URGENT OBSTACLE";
      noteDist.innerText = "15 cm or less: Rapid Urgent Alarm!";
      zones[0]?.classList.add("active-urgent");
    }
  }

  // 14. Footwear Insole FSR Card
  const metricFSR = document.getElementById("metric-fsr");
  const noteFSR   = document.getElementById("fsr-status-note");
  const fillFSR   = document.getElementById("fsr-progress-fill");

  if (!isOnline) {
    if (metricFSR) metricFSR.innerText = "N/A";
    if (noteFSR) noteFSR.innerText = "Sensor Offline";
    if (fillFSR) fillFSR.style.width = "0%";
  } else {
    if (metricFSR) metricFSR.innerText = data.fsr;
    if (noteFSR) noteFSR.innerText = data.fsr_level || "Calibrated Baseline";
    if (fillFSR) {
      const fsrPercent = Math.min(100, (data.fsr / 1023) * 100);
      fillFSR.style.width = `${fsrPercent}%`;
    }
  }

  // 15. Ambient Light LDR Card
  const metricLDR = document.getElementById("metric-ldr");
  const badgeLDR  = document.getElementById("badge-ldr");
  const noteLDR   = document.getElementById("ldr-status-note");
  const fillLDR   = document.getElementById("ldr-progress-fill");

  if (!isOnline) {
    if (metricLDR) metricLDR.innerText = "N/A";
    if (badgeLDR) { badgeLDR.innerText = "OFFLINE"; badgeLDR.className = "badge badge-urgent"; }
    if (noteLDR) noteLDR.innerText = "Sensor Offline";
    if (fillLDR) fillLDR.style.width = "0%";
  } else {
    if (metricLDR) metricLDR.innerText = data.ldr;
    if (data.is_dark) {
      badgeLDR.className = "badge badge-urgent";
      badgeLDR.innerText = "DARK ROOM";
      noteLDR.innerText = "Warning: Poor lighting! Tripping hazard.";
      noteLDR.style.color = "var(--accent-rose)";
    } else {
      badgeLDR.className = "badge badge-good";
      badgeLDR.innerText = "WELL LIT";
      noteLDR.innerText = "Safe ambient illumination level.";
      noteLDR.style.color = "var(--text-secondary)";
    }
    if (fillLDR) {
      const ldrPercent = Math.min(100, (data.ldr / 1023) * 100);
      fillLDR.style.width = `${ldrPercent}%`;
    }
  }

  // 16. Last Sync readout
  const lastSyncEl = document.getElementById("metric-last-sync");
  if (lastSyncEl && data.last_update) {
    lastSyncEl.innerText = data.last_update;
  }
}

// --------------------------------------------------------------------------
// Individual Sensor Health Status Badges (Phase 2 Requirement)
// --------------------------------------------------------------------------
function updateSensorHealthBadges(data, isOnline) {
  const bMax   = document.getElementById("sensor-stat-max30102");
  const bMpu   = document.getElementById("sensor-stat-mpu");
  const bFsr   = document.getElementById("sensor-stat-fsr");
  const bSonar = document.getElementById("sensor-stat-hcsr04");
  const bLdr   = document.getElementById("sensor-stat-ldr");

  if (!isOnline) {
    if (bMax)   { bMax.innerText = "Offline"; bMax.className = "sensor-health-badge badge-health-offline"; }
    if (bMpu)   { bMpu.innerText = "Offline"; bMpu.className = "sensor-health-badge badge-health-offline"; }
    if (bFsr)   { bFsr.innerText = "Offline"; bFsr.className = "sensor-health-badge badge-health-offline"; }
    if (bSonar) { bSonar.innerText = "Offline"; bSonar.className = "sensor-health-badge badge-health-offline"; }
    if (bLdr)   { bLdr.innerText = "Offline"; bLdr.className = "sensor-health-badge badge-health-offline"; }
    return;
  }

  // MAX30102
  if (bMax) {
    if (!data.finger_present || data.heart_rate <= 0) {
      bMax.innerText = "No Finger";
      bMax.className = "sensor-health-badge badge-health-awaiting";
    } else {
      bMax.innerText = `Active (${data.heart_rate} BPM)`;
      bMax.className = "sensor-health-badge badge-health-online";
    }
  }

  // MPU-6050
  if (bMpu) {
    if (data.orientation === "HORIZONTAL") {
      bMpu.innerText = "Fallen (>55°)";
      bMpu.className = "sensor-health-badge badge-health-fault";
    } else {
      bMpu.innerText = "Calibrated";
      bMpu.className = "sensor-health-badge badge-health-online";
    }
  }

  // FSR
  if (bFsr) {
    bFsr.innerText = `${data.fsr} ADC`;
    bFsr.className = "sensor-health-badge badge-health-online";
  }

  // HC-SR04
  if (bSonar) {
    if (data.distance > 0 && data.distance <= 15) {
      bSonar.innerText = "Obstacle Urgent";
      bSonar.className = "sensor-health-badge badge-health-fault";
    } else {
      bSonar.innerText = "Radar Clear";
      bSonar.className = "sensor-health-badge badge-health-online";
    }
  }

  // LDR
  if (bLdr) {
    if (data.is_dark) {
      bLdr.innerText = "Dark Hazard";
      bLdr.className = "sensor-health-badge badge-health-awaiting";
    } else {
      bLdr.innerText = "Well Lit";
      bLdr.className = "sensor-health-badge badge-health-online";
    }
  }
}

// --------------------------------------------------------------------------
// Truth Table Row Active Highlighter
// --------------------------------------------------------------------------
function highlightTruthTableRow(decision) {
  let targetRow = 5;
  if (decision === "HIGH_CONFIDENCE_FALL") targetRow = 1;
  else if (decision === "POSSIBLE_FALL_COLLECTING_EVIDENCE") targetRow = 2;
  else if (decision === "PRESSURE_DROP_WARNING") targetRow = 3;
  else if (decision === "POSSIBLE_FALL_EVAL_MOVEMENT") targetRow = 4;
  else if (decision === "SENSOR_FAULT_WARNING") targetRow = 6;
  else targetRow = 5;

  for (let r = 1; r <= 6; r++) {
    const rowEl = document.getElementById(`table-row-r${r}`) || document.getElementById(`table-row-${r}`);
    if (rowEl) {
      const statusLbl = rowEl.querySelector(".status-lbl");
      if (r === targetRow) {
        rowEl.classList.add("row-active");
        if (statusLbl) statusLbl.innerText = "Active Now";
      } else {
        rowEl.classList.remove("row-active");
        if (statusLbl) statusLbl.innerText = "Idle";
      }
    }
  }
}

// --------------------------------------------------------------------------
// Real Time-Series Canvas Charts Engine (Phase 2 Requirement)
// --------------------------------------------------------------------------
async function fetchTimeseriesData() {
  try {
    const resp = await fetch(`${API_BASE}/api/timeseries-data?limit=25`);
    if (!resp.ok) return;
    const records = await resp.json();
    timeseriesCache = records || [];
    renderAllCharts(timeseriesCache);
  } catch (err) {
    console.debug("[Timeseries Fetch Error]:", err);
  }
}

function renderAllCharts(records) {
  if (!records || records.length === 0) return;

  const hrPoints = records.map(r => r.heart_rate || 0);
  const spo2Points = records.map(r => r.spo2 || 0);
  const tiltPoints = records.map(r => (typeof r.tilt_deg === "number" ? r.tilt_deg : 0));
  const fsrPoints = records.map(r => r.fsr || 0);

  // Update latest readout in card headers
  const last = records[records.length - 1] || {};
  const elHr = document.getElementById("chart-val-hr");
  const elSp = document.getElementById("chart-val-spo2");
  const elTi = document.getElementById("chart-val-tilt");
  const elFs = document.getElementById("chart-val-fsr");

  if (elHr) elHr.innerText = last.heart_rate > 0 ? `${last.heart_rate} BPM` : "-- BPM";
  if (elSp) elSp.innerText = last.spo2 > 0 ? `${last.spo2} %` : "-- %";
  if (elTi) elTi.innerText = `${(last.tilt_deg || 0).toFixed(1)} °`;
  if (elFs) elFs.innerText = `${last.fsr || 0} ADC`;

  // Draw 4 distinct Canvas time-series charts
  drawTimeSeriesCanvas("canvas-hr", hrPoints, {
    strokeColor: "#F43F5E",
    fillColorStart: "rgba(244, 63, 94, 0.35)",
    fillColorEnd: "rgba(244, 63, 94, 0.0)",
    minY: 40,
    maxY: 130,
    unit: "BPM",
    referenceLine: 100
  });

  drawTimeSeriesCanvas("canvas-spo2", spo2Points, {
    strokeColor: "#06B6D4",
    fillColorStart: "rgba(6, 182, 212, 0.35)",
    fillColorEnd: "rgba(6, 182, 212, 0.0)",
    minY: 75,
    maxY: 100,
    unit: "%",
    referenceLine: 95
  });

  drawTimeSeriesCanvas("canvas-tilt", tiltPoints, {
    strokeColor: "#F59E0B",
    fillColorStart: "rgba(245, 158, 11, 0.35)",
    fillColorEnd: "rgba(245, 158, 11, 0.0)",
    minY: 0,
    maxY: 90,
    unit: "°",
    referenceLine: 55 // 55 degrees = fallen threshold
  });

  drawTimeSeriesCanvas("canvas-fsr", fsrPoints, {
    strokeColor: "#10B981",
    fillColorStart: "rgba(16, 185, 129, 0.35)",
    fillColorEnd: "rgba(16, 185, 129, 0.0)",
    minY: 0,
    maxY: 1023,
    unit: "ADC",
    referenceLine: 700
  });
}

function drawTimeSeriesCanvas(canvasId, dataPoints, opts) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  // Handle Retina & High-DPI Scaling
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = rect.width || canvas.clientWidth || 400;
  const height = rect.height || canvas.clientHeight || 180;

  canvas.width = width * dpr;
  canvas.height = height * dpr;
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, width, height);

  const padLeft = 40;
  const padRight = 16;
  const padTop = 16;
  const padBottom = 24;
  const graphW = width - padLeft - padRight;
  const graphH = height - padTop - padBottom;

  const minY = opts.minY || 0;
  const maxY = opts.maxY || 100;

  // Background Grid Lines & Labels
  ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
  ctx.fillStyle = "rgba(148, 163, 184, 0.75)";
  ctx.font = "10px JetBrains Mono, monospace";
  ctx.lineWidth = 1;

  const gridSteps = 4;
  for (let i = 0; i <= gridSteps; i++) {
    const yVal = minY + (i / gridSteps) * (maxY - minY);
    const yPos = padTop + graphH - (i / gridSteps) * graphH;

    ctx.beginPath();
    ctx.moveTo(padLeft, yPos);
    ctx.lineTo(width - padRight, yPos);
    ctx.stroke();

    ctx.fillText(`${Math.round(yVal)}`, 6, yPos + 3);
  }

  // Draw Reference Threshold Line
  if (typeof opts.referenceLine === "number") {
    const refRatio = (opts.referenceLine - minY) / (maxY - minY);
    if (refRatio >= 0 && refRatio <= 1) {
      const refY = padTop + graphH - refRatio * graphH;
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "rgba(244, 63, 94, 0.4)";
      ctx.beginPath();
      ctx.moveTo(padLeft, refY);
      ctx.lineTo(width - padRight, refY);
      ctx.stroke();
      ctx.restore();
    }
  }

  if (dataPoints.length < 2) return;

  // Plot Points Mapping
  const coords = [];
  const stepX = graphW / (dataPoints.length - 1);
  for (let i = 0; i < dataPoints.length; i++) {
    const val = dataPoints[i];
    const clamped = Math.max(minY, Math.min(maxY, val));
    const ratio = (clamped - minY) / (maxY - minY);
    const x = padLeft + i * stepX;
    const y = padTop + graphH - ratio * graphH;
    coords.push({ x, y, val });
  }

  // Fill Gradient Under Curve
  const grad = ctx.createLinearGradient(0, padTop, 0, padTop + graphH);
  grad.addColorStop(0, opts.fillColorStart);
  grad.addColorStop(1, opts.fillColorEnd);

  ctx.beginPath();
  ctx.moveTo(coords[0].x, coords[0].y);
  for (let i = 1; i < coords.length; i++) {
    ctx.lineTo(coords[i].x, coords[i].y);
  }
  ctx.lineTo(coords[coords.length - 1].x, padTop + graphH);
  ctx.lineTo(coords[0].x, padTop + graphH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Stroke Line Curve
  ctx.beginPath();
  ctx.moveTo(coords[0].x, coords[0].y);
  for (let i = 1; i < coords.length; i++) {
    ctx.lineTo(coords[i].x, coords[i].y);
  }
  ctx.strokeStyle = opts.strokeColor;
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  ctx.stroke();

  // Draw Value Dots
  coords.forEach((pt, idx) => {
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, (idx === coords.length - 1) ? 4.5 : 2.5, 0, Math.PI * 2);
    ctx.fillStyle = (idx === coords.length - 1) ? "#FFF" : opts.strokeColor;
    ctx.fill();
    if (idx === coords.length - 1) {
      ctx.strokeStyle = opts.strokeColor;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  });
}

// --------------------------------------------------------------------------
// 2. Fetch Historical Records & Audit Log Table
// --------------------------------------------------------------------------
async function fetchHistory() {
  try {
    const response = await fetch(`${API_BASE}/api/history`);
    if (!response.ok) return;
    const data = await response.json();
    renderHistoryTable(data);
  } catch (err) {
    console.debug("[History Fetch Ignored]:", err);
  }
}

function renderHistoryTable(historyData) {
  const tbody = document.getElementById("history-table-body");
  if (!tbody) return;

  const telemetry = historyData.telemetry || [];
  const alerts = historyData.alerts || [];

  if (telemetry.length === 0 && alerts.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">Awaiting telemetry records...</td></tr>`;
    return;
  }

  let rowsHtml = "";

  // Show emergency alerts and illness events at top
  alerts.slice(0, 8).forEach(alert => {
    const isCancelled = alert.status === "CANCELLED_FALSE_ALARM";
    const isAck = alert.status === "ACKNOWLEDGED";
    const isIllness = (alert.alert_type || "").includes("ILLNESS");
    let badgeClass = "badge-urgent";
    let badgeText = "FALL EVENT";

    if (isCancelled) {
      badgeClass = "badge-good";
      badgeText = "FALSE ALERT CANCELLED";
    } else if (isAck) {
      badgeClass = "badge-good";
      badgeText = "ACKNOWLEDGED SAFE";
    } else if (isIllness) {
      badgeClass = "badge-warning";
      badgeText = alert.alert_type.replace("ILLNESS_", "");
    }

    const rowBg = isCancelled
      ? "rgba(16, 185, 129, 0.08)"
      : (isAck ? "rgba(59, 130, 246, 0.08)" : (isIllness ? "rgba(245, 158, 11, 0.08)" : "rgba(244, 63, 94, 0.08)"));

    rowsHtml += `
      <tr style="background: ${rowBg}">
        <td><strong>${alert.timestamp || 'Just now'}</strong></td>
        <td><span class="badge ${badgeClass}">${badgeText}</span></td>
        <td>--</td>
        <td>--</td>
        <td>--</td>
        <td>--</td>
        <td>
          <strong>${alert.notes || (isCancelled ? 'Wrong alert turned off by patient' : 'Active Alert')}</strong>
          ${alert.cancellation_timestamp ? `<br><small class="text-muted">Intervention at: ${alert.cancellation_timestamp}</small>` : ''}
        </td>
      </tr>
    `;
  });

  // Then show regular telemetry records
  telemetry.slice(0, 12).forEach(row => {
    rowsHtml += `
      <tr>
        <td>${row.timestamp || 'Recorded'}</td>
        <td><span class="decision-pill pill-success">Telemetry Sync</span></td>
        <td>${row.orientation || 'VERTICAL'} <small class="text-muted">(${row.tilt_deg || 0}°)</small></td>
        <td>${row.fsr} ADC</td>
        <td>${row.heart_rate > 0 ? row.heart_rate + ' BPM' : '--'}</td>
        <td>${row.spo2 > 0 ? row.spo2 + '%' : '--'}</td>
        <td><span class="status-good">${row.monitoring_status || 'ACTIVE'}</span></td>
      </tr>
    `;
  });

  tbody.innerHTML = rowsHtml;
}

// --------------------------------------------------------------------------
// 3. Caregiver Alert Acknowledgement (Phase 2 Requirement)
// --------------------------------------------------------------------------
function openCaregiverAckModal() {
  const modal = document.getElementById("caregiver-modal");
  if (modal) modal.classList.remove("modal-hidden");
}

function closeCaregiverAckModal() {
  const modal = document.getElementById("caregiver-modal");
  if (modal) modal.classList.add("modal-hidden");
}

async function submitCaregiverAck() {
  const nameInput = document.getElementById("ack-caregiver-name");
  const notesInput = document.getElementById("ack-notes");
  const caregiverName = nameInput ? nameInput.value.trim() : "Caregiver On-Duty";
  const notes = notesInput ? notesInput.value.trim() : "Acknowledged by caregiver";

  try {
    const resp = await fetch(`${API_BASE}/api/acknowledge-alert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ caregiver_name: caregiverName, notes: notes })
    });
    if (resp.ok) {
      closeCaregiverAckModal();
      fetchSensorData();
      fetchHistory();
    }
  } catch (err) {
    alert("Could not submit acknowledgment: " + err);
  }
}

// --------------------------------------------------------------------------
// 4. Patient Profile & Emergency Contacts (Phase 3 Requirement)
// --------------------------------------------------------------------------
async function fetchPatientProfile() {
  try {
    const resp = await fetch(`${API_BASE}/api/patient-profile`);
    if (!resp.ok) return;
    const p = await resp.json();

    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el && val !== undefined && val !== null) el.value = val;
    };

    setVal("prof-patient-name", p.patient_name);
    setVal("prof-age", p.age);
    setVal("prof-blood-type", p.blood_type);
    setVal("prof-room", p.room_number);
    setVal("prof-conditions", p.medical_conditions);
    setVal("prof-notes", p.notes);

    setVal("prof-caregiver", p.primary_caregiver);
    setVal("prof-caregiver-phone", p.caregiver_phone);
    setVal("prof-caregiver-email", p.caregiver_email);
    setVal("prof-doctor", p.emergency_doctor);
    setVal("prof-doctor-phone", p.doctor_phone);
    setVal("prof-emergency-service", p.emergency_service);
    setVal("prof-service-phone", p.service_phone);

    // SMS config controls
    const chkEnabled = document.getElementById("cfg-sms-enabled");
    const chkDryRun = document.getElementById("cfg-sms-dry-run");
    const txtRecipients = document.getElementById("cfg-recipients");
    const badgeSmsMode = document.getElementById("badge-sms-mode");

    if (chkEnabled) chkEnabled.checked = Boolean(p.sms_enabled);
    if (chkDryRun) chkDryRun.checked = Boolean(p.sms_dry_run);
    if (txtRecipients) txtRecipients.value = p.sms_recipients || p.caregiver_phone || "";
    if (badgeSmsMode) {
      badgeSmsMode.innerText = p.sms_dry_run ? "DRY-RUN SAFE" : "LIVE CARRIER ACTIVE";
      badgeSmsMode.className = p.sms_dry_run ? "badge badge-good" : "badge badge-urgent";
    }
  } catch (err) {
    console.debug("[Profile Fetch Error]:", err);
  }
}

async function savePatientProfile(e) {
  if (e) e.preventDefault();

  const getVal = id => {
    const el = document.getElementById(id);
    return el ? el.value.trim() : "";
  };

  const payload = {
    patient_name: getVal("prof-patient-name"),
    age: parseInt(getVal("prof-age")) || 75,
    blood_type: getVal("prof-blood-type"),
    room_number: getVal("prof-room"),
    medical_conditions: getVal("prof-conditions"),
    notes: getVal("prof-notes"),
    primary_caregiver: getVal("prof-caregiver"),
    caregiver_phone: getVal("prof-caregiver-phone"),
    caregiver_email: getVal("prof-caregiver-email"),
    emergency_doctor: getVal("prof-doctor"),
    doctor_phone: getVal("prof-doctor-phone"),
    emergency_service: getVal("prof-emergency-service"),
    service_phone: getVal("prof-service-phone")
  };

  try {
    const resp = await fetch(`${API_BASE}/api/patient-profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const statusMsg = document.getElementById("profile-save-status") || document.getElementById("contacts-save-status");
    if (resp.ok) {
      if (statusMsg) {
        statusMsg.innerText = "✓ Saved Successfully";
        statusMsg.style.color = "var(--accent-green)";
        setTimeout(() => { statusMsg.innerText = ""; }, 3000);
      }
    }
  } catch (err) {
    alert("Could not save profile: " + err);
  }
}

// --------------------------------------------------------------------------
// 5. SMS Configuration & Dispatch Audit (Phase 3 Requirement)
// --------------------------------------------------------------------------
async function saveSMSConfig(e) {
  if (e) e.preventDefault();

  const chkEnabled = document.getElementById("cfg-sms-enabled");
  const chkDryRun = document.getElementById("cfg-sms-dry-run");
  const txtRecipients = document.getElementById("cfg-recipients");
  const statusMsg = document.getElementById("sms-cfg-status");

  const payload = {
    sms_enabled: chkEnabled ? chkEnabled.checked : true,
    sms_dry_run: chkDryRun ? chkDryRun.checked : true,
    sms_recipients: txtRecipients ? txtRecipients.value.trim() : ""
  };

  try {
    const resp = await fetch(`${API_BASE}/api/patient-profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (resp.ok) {
      if (statusMsg) {
        statusMsg.innerText = "✓ SMS Config Updated";
        statusMsg.style.color = "var(--accent-green)";
        setTimeout(() => { statusMsg.innerText = ""; }, 3000);
      }
      fetchPatientProfile();
    }
  } catch (err) {
    alert("Could not save SMS config: " + err);
  }
}

async function triggerTestSMS(isDryRun = true) {
  const recipientsVal = document.getElementById("cfg-recipients")?.value;
  try {
    const resp = await fetch(`${API_BASE}/api/sms/send-test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: isDryRun, recipient: recipientsVal })
    });
    if (resp.ok) {
      const data = await resp.json();
      fetchSMSLogs();
      const statusMsg = document.getElementById("sms-cfg-status");
      if (statusMsg) {
        statusMsg.innerText = `✓ Test Alert Queued (${data.results?.length || 1} contact(s))`;
        statusMsg.style.color = "var(--accent-cyan)";
        setTimeout(() => { statusMsg.innerText = ""; }, 4000);
      }
    }
  } catch (err) {
    alert("Could not send test SMS: " + err);
  }
}

async function fetchSMSLogs() {
  const tbody = document.getElementById("sms-log-table-body");
  if (!tbody) return;

  try {
    const resp = await fetch(`${API_BASE}/api/sms-logs`);
    if (!resp.ok) return;
    const logs = await resp.json();

    if (!logs || logs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Awaiting SMS dispatch records...</td></tr>`;
      return;
    }

    let html = "";
    logs.forEach(log => {
      let badgeClass = "badge-good";
      if (log.status === "QUEUED") badgeClass = "badge-simulated";
      else if (log.status === "SENT_TO_GATEWAY") badgeClass = "badge-good";
      else if (log.status === "DELIVERED") badgeClass = "badge-real";
      else if (log.status === "SUPPRESSED_DUPLICATE") badgeClass = "badge-warning";
      else badgeClass = "badge-urgent";

      const dryBadge = log.dry_run
        ? `<span class="badge badge-good">Dry-Run</span>`
        : `<span class="badge badge-urgent">Live</span>`;

      // Only display Simulate Handset Delivery button if not already DELIVERED
      let actionBtn = "";
      if (log.status === "SENT_TO_GATEWAY" || log.status === "QUEUED") {
        actionBtn = `<button class="btn-refresh-sm" onclick="simulateDeliveryReceipt(${log.id})" title="Simulate carrier handset delivery receipt">Verify Delivered</button>`;
      } else if (log.status === "DELIVERED") {
        actionBtn = `<span class="text-green font-mono">✓ Handset Confirmed</span>`;
      } else {
        actionBtn = `<span class="text-muted">--</span>`;
      }

      html += `
        <tr>
          <td><strong>${log.timestamp || 'Recorded'}</strong></td>
          <td><code class="text-accent">${log.recipient}</code></td>
          <td><span class="badge ${badgeClass}">${log.status}</span></td>
          <td>${dryBadge}</td>
          <td><small>${log.provider_response || log.message || '--'}</small></td>
          <td>${actionBtn}</td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
  } catch (err) {
    console.debug("[SMS Logs Fetch Error]:", err);
  }
}

async function simulateDeliveryReceipt(smsId) {
  try {
    const resp = await fetch(`${API_BASE}/api/sms/delivery-receipt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sms_id: smsId, receipt: "DELIVERY_REPORT_HANDSET_CONFIRMED" })
    });
    if (resp.ok) {
      fetchSMSLogs();
    }
  } catch (err) {
    alert("Could not confirm delivery: " + err);
  }
}

// --------------------------------------------------------------------------
// 6. Interactive Testing & Simulation Handlers
// --------------------------------------------------------------------------
async function simulateRow(rowNum) {
  try {
    const resp = await fetch(`${API_BASE}/api/test/decision-row/${rowNum}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    if (resp.ok) {
      fetchSensorData();
      fetchHistory();
      fetchTimeseriesData();
    }
  } catch (err) {
    alert("Could not contact server: " + err);
  }
}

async function simulateIllness(illnessType) {
  try {
    const resp = await fetch(`${API_BASE}/api/test/illness/${illnessType}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    if (resp.ok) {
      fetchSensorData();
      fetchHistory();
      fetchTimeseriesData();
    }
  } catch (err) {
    alert("Could not contact server: " + err);
  }
}

async function triggerCancelAlert() {
  try {
    const resp = await fetch(`${API_BASE}/api/cancel-alert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "Wrong Alert Turned Off by Patient – Alarm Silenced" })
    });
    if (resp.ok) {
      fetchSensorData();
      fetchHistory();
      fetchTimeseriesData();
    }
  } catch (err) {
    alert("Could not contact server: " + err);
  }
}

async function triggerToggleMonitoring() {
  try {
    const resp = await fetch(`${API_BASE}/api/monitoring-status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    if (resp.ok) {
      fetchSensorData();
      fetchHistory();
    }
  } catch (err) {
    alert("Could not contact server: " + err);
  }
}

async function triggerManualSOS() {
  if (!confirm("Are you sure you want to trigger an Emergency SOS Alert?")) return;
  try {
    const resp = await fetch(`${API_BASE}/api/sos/trigger`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes: "Manual SOS triggered by patient/user" })
    });
    if (resp.ok) {
      fetchSensorData();
      fetchHistory();
      fetchTimeseriesData();
      fetchSMSLogs();
    }
  } catch (err) {
    alert("Could not contact server: " + err);
  }
}

async function triggerCancelSOS() {
  try {
    const resp = await fetch(`${API_BASE}/api/sos/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes: "Manual SOS cancelled by patient/caregiver" })
    });
    if (resp.ok) {
      fetchSensorData();
      fetchHistory();
      fetchTimeseriesData();
    }
  } catch (err) {
    alert("Could not contact server: " + err);
  }
}
