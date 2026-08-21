import { create } from 'zustand';
import type { VitalReading, AlarmLimit, VitalType, FieldStatus, VitalField, VitalObservationRow } from '@/types/vitals';
import type { TrackingState } from '@/types/calibration';
import { DEFAULT_ALARM_LIMITS } from '@/types/vitals';
import { checkAlerts, ALERT_THROTTLE_WINDOW_MS } from '@/lib/alertRules';
import { VITAL_FIELDS } from '@/lib/ledger';
import { useAlertStore } from '@/store/alertStore';

const MAX_HISTORY = 360;
// M5.7: the OBSERVED TIMELINE kept in memory for the live ledger, bounded
// for a multi-hour operation -- SQLite (via GET /readings) is the real
// source of truth Archive/Review read from; this is only what the running
// tab needs to render the ledger without re-fetching on every tick. At the
// camera pipeline's measured ~1 row/1-3.4s cadence this comfortably covers
// several hours before the oldest entries roll off.
const MAX_OBSERVATIONS = 20_000;

// M5.7: builds the CONFIRMED-ONLY view of a reading — a held/baseline field
// is simply omitted, never passed through at its stale/fabricated value.
// Used for (a) alert-checking, so a held value can never raise a new
// alert, matching backend/app/ws/vitals.py's send_loop; and (b) appending a
// synthetic observation row to the ledger, so the ledger only ever grows
// from genuine observations. Two different reasons to consume it does not
// mean two different function.
function confirmedFieldsOnly(
  reading: VitalReading,
  fieldStatus: Partial<Record<VitalField, FieldStatus>> | null | undefined,
): Partial<VitalReading> {
  if (!fieldStatus) return reading; // no field_status at all (Demo Mode) -- pass through unchanged, pre-M5.7 behaviour
  const out: Partial<VitalReading> = { timestamp: reading.timestamp };
  for (const field of VITAL_FIELDS) {
    if (fieldStatus[field] === 'confirmed') out[field] = reading[field];
  }
  return out;
}

// One throttle map per app lifetime, reset on clearHistory() (called on
// session end/start) — mirrors backend/app/alerts/rules.py's AlertThrottle,
// "one instance per active stream/session".
let lastAlertAt: Record<string, number> = {};

interface VitalsState {
  current: VitalReading | null;
  history: VitalReading[];
  alarmLimits: AlarmLimit[];
  nibpLastMeasuredAt: number | null;
  nibpMeasuring: boolean;
  // The real per-vital OCR confidence from the backend WS 'reading' envelope
  // (msg.confidence). null until the first real frame arrives, or while
  // Demo Mode is active and nothing populates it — VitalsGrid falls back to
  // useSimulatedConfidence() only in that case, never mixing the two.
  currentConfidence: Record<VitalType, number> | null;
  // M5.3 layout-tracking lock state, straight off the WS 'reading' envelope.
  // null means the backend is not tracking at all (no calibration reference
  // frame, or LAYOUT_TRACKING=off) -- which must render differently from
  // "tracking and currently unlocked".
  tracking: TrackingState | null;

  // M5.7: per-field confirmed/held/baseline straight off the WS 'reading'
  // envelope (backend/app/validation/reconcile.py's field_status). null
  // before the first real frame, or for a source that never populates it
  // (Demo Mode) -- VitalCard must render a value as plain/fresh in that
  // case, not silently as "held".
  fieldStatus: Partial<Record<VitalField, FieldStatus>> | null;
  // M5.7: per field, the epoch-ms timestamp of the last tick that field was
  // GENUINELY confirmed (not merely displayed) -- straight off the WS
  // envelope's lastConfirmedAt. What a held card's "last confirmed 12:44:21"
  // caption reads from.
  lastConfirmedAt: Partial<Record<VitalField, number>> | null;
  // M5.7: the OBSERVED TIMELINE -- append-only, one entry per WS tick that
  // confirmed at least one field (see appendObservationFromTick), plus
  // whatever GET /api/sessions/{id}/readings hydrates in on mount/reconnect
  // (see hydrateObservations). src/lib/ledger.ts's deriveLedger() projects
  // this into the operator-facing "one line per value change" ledger. This
  // is genuinely the same timeline SQLite holds -- Archive/Review read it
  // via the same GET /readings endpoint, not a second write path.
  observations: VitalObservationRow[];

  updateVitals: (reading: VitalReading, fieldStatus?: Partial<Record<VitalField, FieldStatus>>) => void;
  setConfidence: (confidence: Record<string, number> | undefined) => void;
  setTracking: (tracking: TrackingState | undefined) => void;
  setLastConfirmedAt: (lastConfirmedAt: Partial<Record<VitalField, number>> | undefined) => void;
  appendObservationFromTick: (
    reading: VitalReading,
    fieldStatus: Partial<Record<VitalField, FieldStatus>> | undefined,
    confidence: Record<string, number> | undefined,
    source: 'camera' | 'synthetic' | 'replay' | null,
  ) => void;
  hydrateObservations: (rows: VitalObservationRow[]) => void;
  updateAlarmLimit: (limit: AlarmLimit) => void;
  triggerNibpMeasurement: () => void;
  setNibpMeasuring: (v: boolean) => void;
  clearHistory: () => void;
}

export const useVitalsStore = create<VitalsState>((set) => ({
  current: null,
  history: [],
  alarmLimits: DEFAULT_ALARM_LIMITS,
  nibpLastMeasuredAt: null,
  nibpMeasuring: false,
  currentConfidence: null,
  tracking: null,
  fieldStatus: null,
  lastConfirmedAt: null,
  observations: [],

  updateVitals: (reading, fieldStatus) => {
    set((state) => ({
      current: reading,
      history: [...state.history, reading].slice(-MAX_HISTORY),
      fieldStatus: fieldStatus ?? null,
    }));

    // M5.7: a held/baseline field must never raise a NEW alert -- see
    // confirmedFieldsOnly's own docstring. fieldStatus is undefined for a
    // source that never populates it (Demo Mode), in which case this is
    // exactly the pre-M5.7 "check the whole displayed reading" behaviour.
    const alertableReading = confirmedFieldsOnly(reading, fieldStatus);
    const now = Date.now();
    for (const alert of checkAlerts(alertableReading)) {
      const key = `${alert.vitalType}-${alert.severity}`;
      if (now - (lastAlertAt[key] ?? 0) > ALERT_THROTTLE_WINDOW_MS) {
        lastAlertAt[key] = now;
        useAlertStore.getState().addAlert(alert);
      }
    }
  },

  setConfidence: (confidence) =>
    set(confidence ? { currentConfidence: confidence as Record<VitalType, number> } : {}),

  setLastConfirmedAt: (lastConfirmedAt) => set({ lastConfirmedAt: lastConfirmedAt ?? null }),

  appendObservationFromTick: (reading, fieldStatus, confidence, source) => {
    if (!fieldStatus) return; // no field-level status at all (Demo Mode) -- this source has no observed timeline
    const confirmed = confirmedFieldsOnly(reading, fieldStatus);
    const hasConfirmedField = VITAL_FIELDS.some((f) => confirmed[f] != null);
    if (!hasConfirmedField) return; // nothing genuinely observed this tick -- the ledger must not grow

    const row: VitalObservationRow = {
      hr: confirmed.hr ?? null,
      spo2: confirmed.spo2 ?? null,
      nibpSystolic: confirmed.nibpSystolic ?? null,
      nibpDiastolic: confirmed.nibpDiastolic ?? null,
      nibpMean: confirmed.nibpMean ?? null,
      etco2: confirmed.etco2 ?? null,
      temp: confirmed.temp ?? null,
      rr: confirmed.rr ?? null,
      timestamp: reading.timestamp,
      confidence: confidence ? Object.values(confidence).reduce((a, b) => a + b, 0) / Object.values(confidence).length : null,
      provenance: null,
      perVitalConfidence: confidence ?? null,
      source,
      fieldStatus,
    };

    set((state) => ({ observations: [...state.observations, row].slice(-MAX_OBSERVATIONS) }));
  },

  hydrateObservations: (rows) =>
    set((state) => {
      // Merge, don't replace: a live tick can already have appended rows
      // (the WS connects independently of this fetch) before hydration
      // resolves -- overwriting would silently drop them. Dedupe on
      // timestamp (each tick's epoch-ms is effectively unique) and keep the
      // merged list in chronological order, same invariant deriveLedger
      // relies on.
      const seen = new Set(state.observations.map((o) => o.timestamp));
      const merged = [...rows.filter((r) => !seen.has(r.timestamp)), ...state.observations];
      merged.sort((a, b) => a.timestamp - b.timestamp);
      return { observations: merged.slice(-MAX_OBSERVATIONS) };
    }),

  // Unlike setConfidence, an ABSENT tracking key is meaningful and is stored
  // as null: it says the backend is not tracking at all, which the overlay
  // must show differently from a tracker that is running but unlocked.
  setTracking: (tracking) => set({ tracking: tracking ?? null }),

  updateAlarmLimit: (limit) =>
    set((state) => ({
      alarmLimits: state.alarmLimits.map((al) =>
        al.vitalType === limit.vitalType ? limit : al
      ),
    })),

  triggerNibpMeasurement: () => set({ nibpMeasuring: true }),

  setNibpMeasuring: (v) =>
    set((state) => ({
      nibpMeasuring: v,
      nibpLastMeasuredAt: v ? state.nibpLastMeasuredAt : Date.now(),
    })),

  clearHistory: () => {
    lastAlertAt = {};
    set({
      history: [], current: null, nibpLastMeasuredAt: null, currentConfidence: null,
      fieldStatus: null, lastConfirmedAt: null, observations: [],
    });
  },
}));
