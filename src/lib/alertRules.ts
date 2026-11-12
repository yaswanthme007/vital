// VITAL · src/lib/alertRules.ts
import type { VitalReading } from '@/types/vitals';
import type { Alert } from '@/store/alertStore';

type NewAlert = Omit<Alert, 'id' | 'timestamp' | 'acknowledged'>;

export const ALERT_THROTTLE_WINDOW_MS = 30_000;

// Mirrors backend/app/alerts/rules.py's check_alerts() exactly — same
// thresholds, same messages, same severities, same else-if exclusivity per
// vital (e.g. HR fires at most one of its 4 branches). NIBP (MAP/systolic)
// and RR alerts were added on both sides together — before, hypotension
// coloured the NIBP tile but never raised an alert. Runs on every
// updateVitals() call regardless of source (live camera feed or Demo Mode),
// so both paths raise the same alerts the same way.
//
// M5.7: param widened to Partial<VitalReading> so vitalsStore.updateVitals
// can pass a CONFIRMED-ONLY view (held/baseline fields simply absent) for a
// camera session. Each field is destructured down to `?? NaN` below — every
// comparison against NaN is false, so a missing field is silently skipped
// (the same effective "no data, don't alert" outcome the backend's own
// None-check gives), and it keeps every value passed into an Alert's
// `value: number` field actually typed as `number`, not `number|undefined`.
// A type-level relaxation only: no behaviour change for any existing
// fully-populated caller (Demo Mode, synthetic).
export function checkAlerts(reading: Partial<VitalReading>): NewAlert[] {
  const alerts: NewAlert[] = [];
  const spo2 = reading.spo2 ?? NaN;
  const hr = reading.hr ?? NaN;
  const etco2 = reading.etco2 ?? NaN;
  const temp = reading.temp ?? NaN;

  if (spo2 <= 90) {
    alerts.push({ vitalType: 'spo2', severity: 'critical', message: 'SpO₂ CRITICALLY LOW', value: spo2, unit: '%' });
  } else if (spo2 <= 94) {
    alerts.push({ vitalType: 'spo2', severity: 'warning', message: 'SpO₂ Low', value: spo2, unit: '%' });
  }

  if (hr >= 130) {
    alerts.push({ vitalType: 'hr', severity: 'critical', message: 'Heart Rate HIGH', value: hr, unit: 'bpm' });
  } else if (hr >= 110) {
    alerts.push({ vitalType: 'hr', severity: 'warning', message: 'Heart Rate Elevated', value: hr, unit: 'bpm' });
  } else if (hr <= 40) {
    alerts.push({ vitalType: 'hr', severity: 'critical', message: 'Heart Rate CRITICALLY LOW', value: hr, unit: 'bpm' });
  } else if (hr <= 50) {
    alerts.push({ vitalType: 'hr', severity: 'warning', message: 'Heart Rate Low', value: hr, unit: 'bpm' });
  }

  if (etco2 >= 55) {
    alerts.push({ vitalType: 'etco2', severity: 'critical', message: 'EtCO₂ CRITICALLY HIGH', value: etco2, unit: 'mmHg' });
  } else if (etco2 >= 50) {
    alerts.push({ vitalType: 'etco2', severity: 'warning', message: 'EtCO₂ Elevated', value: etco2, unit: 'mmHg' });
  }

  if (temp >= 39.5) {
    alerts.push({ vitalType: 'temp', severity: 'critical', message: 'Hyperthermia', value: temp, unit: '°C' });
  } else if (temp >= 38.5) {
    alerts.push({ vitalType: 'temp', severity: 'warning', message: 'Fever', value: temp, unit: '°C' });
  } else if (temp <= 35.5) {
    alerts.push({ vitalType: 'temp', severity: 'warning', message: 'Hypothermia Risk', value: temp, unit: '°C' });
  }

  const nibp = nibpAlert(reading);
  if (nibp) alerts.push(nibp);

  const rr = reading.rr ?? NaN;
  if (rr >= RR_CRITICAL_HIGH) {
    alerts.push({ vitalType: 'rr', severity: 'critical', message: 'Resp Rate HIGH', value: rr, unit: '/min' });
  } else if (rr >= RR_WARNING_HIGH) {
    alerts.push({ vitalType: 'rr', severity: 'warning', message: 'Resp Rate Elevated', value: rr, unit: '/min' });
  } else if (rr <= RR_CRITICAL_LOW) {
    alerts.push({ vitalType: 'rr', severity: 'critical', message: 'Resp Rate CRITICALLY LOW', value: rr, unit: '/min' });
  } else if (rr <= RR_WARNING_LOW) {
    alerts.push({ vitalType: 'rr', severity: 'warning', message: 'Resp Rate Low', value: rr, unit: '/min' });
  }

  return alerts;
}

// NIBP + RR thresholds — mirror backend/app/alerts/rules.py's constants and
// src/types/vitals.ts DEFAULT_ALARM_LIMITS (which colours the tiles), plus
// the usual MAP floor of 65 mmHg (warning) / 55 mmHg (critical).
export const MAP_CRITICAL_LOW = 55;
export const MAP_WARNING_LOW = 65;
export const SBP_CRITICAL_LOW = 70;
export const SBP_WARNING_LOW = 90;
export const SBP_WARNING_HIGH = 160;
export const SBP_CRITICAL_HIGH = 180;

export const RR_CRITICAL_HIGH = 30;
export const RR_WARNING_HIGH = 24;
export const RR_WARNING_LOW = 8;
export const RR_CRITICAL_LOW = 5;

/**
 * At most ONE NIBP alert per check. Low pressure outranks high, and MAP
 * outranks systolic when both are present (MAP is what perfuses organs).
 * Either sub-field may be absent/null (not confirmed this tick) — it becomes
 * NaN, every comparison is false, and it is skipped. Exact port of
 * backend/app/alerts/rules.py's _nibp_alert().
 */
function nibpAlert(reading: Partial<VitalReading>): NewAlert | null {
  const mean = reading.nibpMean ?? NaN;
  const sbp = reading.nibpSystolic ?? NaN;
  const alert = (severity: NewAlert['severity'], message: string, value: number): NewAlert =>
    ({ vitalType: 'nibp', severity, message, value, unit: 'mmHg' });

  if (mean <= MAP_CRITICAL_LOW) return alert('critical', 'MAP CRITICALLY LOW', mean);
  if (sbp <= SBP_CRITICAL_LOW) return alert('critical', 'Systolic BP CRITICALLY LOW', sbp);
  if (mean <= MAP_WARNING_LOW) return alert('warning', 'Hypotension (MAP)', mean);
  if (sbp <= SBP_WARNING_LOW) return alert('warning', 'Hypotension (Systolic)', sbp);
  if (sbp >= SBP_CRITICAL_HIGH) return alert('critical', 'Systolic BP CRITICALLY HIGH', sbp);
  if (sbp >= SBP_WARNING_HIGH) return alert('warning', 'Hypertension', sbp);
  return null;
}
