import type { VitalField, VitalObservationRow, VitalType } from '@/types/vitals';

// The 8 numeric fields, in the fixed display order the ledger renders them
// within a single persisted row (backend/app/validation/rules.py's FIELDS
// tuple order).
export const VITAL_FIELDS: VitalField[] = [
  'hr', 'spo2', 'nibpSystolic', 'nibpDiastolic', 'nibpMean', 'etco2', 'temp', 'rr',
];

// Maps each of the 8 numeric fields down to its 6 vital "group" -- NIBP's 3
// sub-fields share one OCR crop and one per_vital_confidence entry, mirroring
// backend/app/validation/rules.py's FIELD_TO_VITAL_GROUP exactly.
export const FIELD_TO_VITAL_GROUP: Record<VitalField, VitalType> = {
  hr: 'hr',
  spo2: 'spo2',
  etco2: 'etco2',
  temp: 'temp',
  rr: 'rr',
  nibpSystolic: 'nibp',
  nibpDiastolic: 'nibp',
  nibpMean: 'nibp',
};

export interface LedgerEntry {
  /** Stable React key -- unique per (field, timestamp) pair. */
  id: string;
  field: VitalField;
  value: number;
  timestamp: number;
  confidence: number | null;
}

/**
 * Projects a chronological list of persisted VitalObservationRow (from
 * GET /api/sessions/{id}/readings, or appended live off the WS 'reading'
 * envelope -- see src/store/vitalsStore.ts) into the OPERATOR-FACING
 * ledger: one entry per field per genuine VALUE CHANGE, not one entry per
 * persisted row.
 *
 * Why this distinction matters: backend/app/ws/vitals.py's _PersistenceGate
 * writes a row at least once a second OR whenever a confirmed value
 * changes -- so a steady HR=75 held for 30s of a case still produces many
 * persisted rows (durability), all with hr=75. Feeding those straight into
 * a UI list would spam "HR 75" over and over. This function collapses that
 * to exactly one "HR 75" entry, and only emits a new one when the CONFIRMED
 * value actually differs from the last one this function saw for that
 * field -- matching the product requirement verbatim: "if HR stays at 75
 * for 30 seconds, don't create 30 meaningless entries; if it changes to 76,
 * record the new observation with its timestamp."
 *
 * A field that is null/absent on a row was NOT confirmed that tick (M5.7
 * persistence only ever writes confirmed fields -- see repo.save_reading)
 * and is simply skipped; it never resets `lastValue`, so a later row that
 * re-confirms the SAME value it held before still correctly produces no new
 * entry.
 *
 * Pure and side-effect-free so it can run identically over historical rows
 * (hydration from GET /readings) and over rows appended live from the
 * WebSocket -- see vitalsStore.appendObservation, which calls this
 * incrementally rather than re-deriving the whole history every tick.
 */
export function deriveLedger(rows: VitalObservationRow[]): LedgerEntry[] {
  const entries: LedgerEntry[] = [];
  const lastValue: Partial<Record<VitalField, number>> = {};

  for (const row of rows) {
    for (const field of VITAL_FIELDS) {
      const value = row[field];
      if (value == null) continue;
      if (lastValue[field] === value) continue;
      lastValue[field] = value;

      const group = FIELD_TO_VITAL_GROUP[field];
      const confidence = row.perVitalConfidence?.[group] ?? row.confidence ?? null;

      entries.push({ id: `${field}-${row.timestamp}`, field, value, timestamp: row.timestamp, confidence });
    }
  }

  return entries;
}
