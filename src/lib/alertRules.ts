import type { VitalReading } from '@/types/vitals';
import type { Alert } from '@/store/alertStore';

type NewAlert = Omit<Alert, 'id' | 'timestamp' | 'acknowledged'>;

export const ALERT_THROTTLE_WINDOW_MS = 30_000;

// Mirrors backend/app/alerts/rules.py's check_alerts() exactly — same
// thresholds, same messages, same severities, same else-if exclusivity per
// vital (e.g. HR fires at most one of its 4 branches). rr and nibp are
// intentionally NOT checked here, matching the backend. Runs on every
// updateVitals() call regardless of source (live camera feed or Demo Mode),
// so both paths raise the same alerts the same way.
export function checkAlerts(reading: VitalReading): NewAlert[] {
  const alerts: NewAlert[] = [];

  if (reading.spo2 <= 90) {
    alerts.push({ vitalType: 'spo2', severity: 'critical', message: 'SpO₂ CRITICALLY LOW', value: reading.spo2, unit: '%' });
  } else if (reading.spo2 <= 94) {
    alerts.push({ vitalType: 'spo2', severity: 'warning', message: 'SpO₂ Low', value: reading.spo2, unit: '%' });
  }

  if (reading.hr >= 130) {
    alerts.push({ vitalType: 'hr', severity: 'critical', message: 'Heart Rate HIGH', value: reading.hr, unit: 'bpm' });
  } else if (reading.hr >= 110) {
    alerts.push({ vitalType: 'hr', severity: 'warning', message: 'Heart Rate Elevated', value: reading.hr, unit: 'bpm' });
  } else if (reading.hr <= 40) {
    alerts.push({ vitalType: 'hr', severity: 'critical', message: 'Heart Rate CRITICALLY LOW', value: reading.hr, unit: 'bpm' });
  } else if (reading.hr <= 50) {
    alerts.push({ vitalType: 'hr', severity: 'warning', message: 'Heart Rate Low', value: reading.hr, unit: 'bpm' });
  }

  if (reading.etco2 >= 55) {
    alerts.push({ vitalType: 'etco2', severity: 'critical', message: 'EtCO₂ CRITICALLY HIGH', value: reading.etco2, unit: 'mmHg' });
  } else if (reading.etco2 >= 50) {
    alerts.push({ vitalType: 'etco2', severity: 'warning', message: 'EtCO₂ Elevated', value: reading.etco2, unit: 'mmHg' });
  }

  if (reading.temp >= 39.5) {
    alerts.push({ vitalType: 'temp', severity: 'critical', message: 'Hyperthermia', value: reading.temp, unit: '°C' });
  } else if (reading.temp >= 38.5) {
    alerts.push({ vitalType: 'temp', severity: 'warning', message: 'Fever', value: reading.temp, unit: '°C' });
  } else if (reading.temp <= 35.5) {
    alerts.push({ vitalType: 'temp', severity: 'warning', message: 'Hypothermia Risk', value: reading.temp, unit: '°C' });
  }

  return alerts;
}
