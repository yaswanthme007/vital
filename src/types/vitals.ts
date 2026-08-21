export type VitalType = 'hr' | 'spo2' | 'nibp' | 'etco2' | 'temp' | 'rr';

// M5.8: every numeric field is nullable. A camera session starts with NO
// confirmed values at all and reports null for any field the camera has not
// yet genuinely read (backend field_status 'unknown') -- see
// backend/app/validation/reconcile.py's allow_baseline parameter. Before
// M5.8 the backend substituted a clinically-normal placeholder (HR 75,
// Temp 36.8, RR 14) in that situation, which the UI then displayed as a
// real, "held" reading. Nullable is what lets the UI show nothing instead
// of inventing a number. Demo Mode and synthetic sessions still populate
// every field, so nothing there changes.
export interface VitalReading {
  hr: number | null;
  spo2: number | null;
  nibpSystolic: number | null;
  nibpDiastolic: number | null;
  nibpMean: number | null;
  etco2: number | null;
  temp: number | null;
  rr: number | null;
  timestamp: number;
}

// M5.7. Mirrors backend/app/validation/reconcile.py's field_status out-param
// exactly -- see that docstring for what each value means. 'confirmed' is
// the only status that ever becomes a persisted OBSERVATION; 'held' and
// 'baseline' are LIVE-STATE-only (what the card is currently displaying),
// never patient history.
// M5.8 adds 'unknown': the field has NEVER been confirmed and there is
// nothing to display -- distinct from 'held' (a real earlier observation
// still on screen) and from 'baseline' (the synthetic/replay placeholder,
// which the camera path no longer uses at all).
export type FieldStatus = 'confirmed' | 'held' | 'baseline' | 'unknown';

// The 8 numeric VitalReading fields, i.e. backend/app/validation/rules.py's
// FIELDS tuple -- what reconcile()'s field_status/reconcile()'s NIBP-group
// keys use (nibp's 3 sub-fields, not the single 'nibp' VitalType).
export type VitalField = 'hr' | 'spo2' | 'nibpSystolic' | 'nibpDiastolic' | 'nibpMean' | 'etco2' | 'temp' | 'rr';

// One persisted row from GET /api/sessions/{id}/readings -- mirrors
// backend/app/db/repo.list_readings' dict shape exactly. Sparse by design:
// a field this tick did NOT confirm is simply null on the row (M5.7's
// confirmed-only persistence gate), never a fabricated held/baseline value.
export interface VitalObservationRow {
  hr: number | null;
  spo2: number | null;
  nibpSystolic: number | null;
  nibpDiastolic: number | null;
  nibpMean: number | null;
  etco2: number | null;
  temp: number | null;
  rr: number | null;
  timestamp: number;
  confidence: number | null;
  provenance: string | null;
  perVitalConfidence: Record<string, number> | null;
  source: 'camera' | 'synthetic' | 'replay' | null;
  fieldStatus: Partial<Record<VitalField, FieldStatus>> | null;
}

export type AlarmSeverity = 'normal' | 'warning' | 'critical' | 'off';

export interface AlarmLimit {
  vitalType: VitalType;
  highCritical?: number;
  highWarning?: number;
  lowWarning?: number;
  lowCritical?: number;
}

export const DEFAULT_ALARM_LIMITS: AlarmLimit[] = [
  { vitalType: 'hr',    highCritical: 130, highWarning: 110, lowWarning: 50, lowCritical: 40 },
  { vitalType: 'spo2',  highWarning: 100,  lowWarning: 94,   lowCritical: 90 },
  { vitalType: 'etco2', highCritical: 60,  highWarning: 50,  lowWarning: 25, lowCritical: 20 },
  { vitalType: 'temp',  highCritical: 39.5, highWarning: 38.5, lowWarning: 35.5, lowCritical: 34.5 },
  { vitalType: 'rr',   highCritical: 30,  highWarning: 24,  lowWarning: 8,  lowCritical: 5 },
  { vitalType: 'nibp',  highCritical: 180, highWarning: 160, lowWarning: 90, lowCritical: 70 },
];

export function getAlarmSeverity(
  vitalType: VitalType,
  value: number | null | undefined,
  limits: AlarmLimit[],
): AlarmSeverity {
  const limit = limits.find((l) => l.vitalType === vitalType);
  // M5.8: no value observed yet -> no alarm state. A card with nothing on
  // it must never be styled as critical.
  if (!limit || value == null) return 'normal';

  if (limit.highCritical !== undefined && value >= limit.highCritical) return 'critical';
  if (limit.lowCritical  !== undefined && value <= limit.lowCritical)  return 'critical';
  if (limit.highWarning  !== undefined && value >= limit.highWarning)  return 'warning';
  if (limit.lowWarning   !== undefined && value <= limit.lowWarning)   return 'warning';
  return 'normal';
}
