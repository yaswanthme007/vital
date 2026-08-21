import type { VitalField, VitalObservationRow } from '@/types/vitals';
import { VITAL_FIELDS } from '@/lib/ledger';

// M5.8.1. Shared by Review & Sign-off and Archive so "what did VITAL
// actually record" is computed identically in both places, from the same
// persisted rows GET /api/sessions/{id}/readings returns — never from
// vitalsStore's live/held display state. See ledger.ts's own docstring for
// why a null field on a row means "not confirmed this tick", not zero.

export const VITALS_FIELD_LABELS: Record<VitalField, string> = {
  hr: 'Heart Rate', spo2: 'SpO₂', nibpSystolic: 'NIBP Systolic', nibpDiastolic: 'NIBP Diastolic',
  nibpMean: 'NIBP Mean', etco2: 'EtCO₂', temp: 'Temp', rr: 'Resp Rate',
};

export const VITALS_FIELD_UNITS: Record<VitalField, string> = {
  hr: 'bpm', spo2: '%', nibpSystolic: 'mmHg', nibpDiastolic: 'mmHg', nibpMean: 'mmHg', etco2: 'mmHg', temp: '°C', rr: '/min',
};

export interface FieldSummary {
  field: VitalField;
  label: string;
  unit: string;
  first: number | null;
  firstAt: number | null;
  last: number | null;
  lastAt: number | null;
  min: number | null;
  max: number | null;
  /** Count of genuinely confirmed, persisted observations of this field —
   * i.e. rows where it is non-null. Never a count of ledger "changes". */
  count: number;
}

/**
 * First/last/min/max/count per field, computed ONLY over rows where that
 * field was genuinely confirmed (non-null). A field the camera never
 * confirmed for this session comes back with count 0 and null extrema —
 * shown honestly as "no observations", never backfilled or hidden.
 */
export function computeFieldSummaries(rows: VitalObservationRow[]): FieldSummary[] {
  return VITAL_FIELDS.map((field): FieldSummary => {
    let first: number | null = null;
    let firstAt: number | null = null;
    let last: number | null = null;
    let lastAt: number | null = null;
    let min: number | null = null;
    let max: number | null = null;
    let count = 0;

    for (const row of rows) {
      const value = row[field];
      if (value == null) continue;
      count += 1;
      if (first === null) {
        first = value;
        firstAt = row.timestamp;
      }
      last = value;
      lastAt = row.timestamp;
      min = min === null ? value : Math.min(min, value);
      max = max === null ? value : Math.max(max, value);
    }

    return {
      field,
      label: VITALS_FIELD_LABELS[field],
      unit: VITALS_FIELD_UNITS[field],
      first, firstAt, last, lastAt, min, max, count,
    };
  });
}

export interface ObservationStatsSummary {
  readingsCount: number;
  confirmedObservations: number;
  avgConfidence: number | null;
  source: 'camera' | 'synthetic' | 'replay' | 'mixed' | null;
}

/**
 * Mirrors backend/app/db/repo.py's _observation_stats exactly, computed
 * client-side from the SAME rows GET /readings returns — so Review can show
 * it without a second endpoint, and it can never drift from what
 * end_session's own ArchivedSession.observationStats would report for the
 * identical data.
 */
export function computeObservationStats(rows: VitalObservationRow[]): ObservationStatsSummary {
  if (rows.length === 0) {
    return { readingsCount: 0, confirmedObservations: 0, avgConfidence: null, source: null };
  }

  let confirmedObservations = 0;
  for (const row of rows) {
    for (const field of VITAL_FIELDS) {
      if (row[field] != null) confirmedObservations += 1;
    }
  }

  const confidences = rows.map((r) => r.confidence).filter((c): c is number => c != null);
  const avgConfidence = confidences.length > 0
    ? confidences.reduce((a, b) => a + b, 0) / confidences.length
    : null;

  const sources = new Set(rows.map((r) => r.source).filter((s): s is NonNullable<typeof s> => s != null));
  const source: ObservationStatsSummary['source'] =
    sources.size === 1 ? [...sources][0] : sources.size > 1 ? 'mixed' : null;

  return { readingsCount: rows.length, confirmedObservations, avgConfidence, source };
}
