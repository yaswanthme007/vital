export type VitalType = 'hr' | 'spo2' | 'nibp' | 'etco2' | 'temp' | 'rr';

export interface VitalReading {
  hr: number;
  spo2: number;
  nibpSystolic: number;
  nibpDiastolic: number;
  nibpMean: number;
  etco2: number;
  temp: number;
  rr: number;
  timestamp: number;
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

export function getAlarmSeverity(vitalType: VitalType, value: number, limits: AlarmLimit[]): AlarmSeverity {
  const limit = limits.find((l) => l.vitalType === vitalType);
  if (!limit) return 'normal';

  if (limit.highCritical !== undefined && value >= limit.highCritical) return 'critical';
  if (limit.lowCritical  !== undefined && value <= limit.lowCritical)  return 'critical';
  if (limit.highWarning  !== undefined && value >= limit.highWarning)  return 'warning';
  if (limit.lowWarning   !== undefined && value <= limit.lowWarning)   return 'warning';
  return 'normal';
}
