export type AsaClass = 1 | 2 | 3 | 4 | 5 | 6;

export interface Patient {
  id: string;
  age?: number;
  weight?: number;
  asa?: AsaClass;
}

export interface Session {
  id: string;
  patient: Patient;
  procedure: string;
  anesthetist: string;
  startTime: number;
  endTime?: number;
  notes: SessionNote[];
  status: 'active' | 'paused' | 'completed';

  // Additive (mirrors backend/app/models/session.py's Session, which
  // labels these the same way — "not present in frontend TS type" until
  // M5.7 needed flaggedCount for Archive's real AI-processing panel).
  vitalsCount?: number;
  flaggedCount?: number;

  // Additive (M5.8.1): the durable sign-off record backend/app/db/repo.py's
  // sign_session sets — null/undefined until POST /sign actually runs. Read
  // by Review (to show "signed & locked" instead of a re-signable session)
  // and Archive (to stop claiming every completed session is "✓ Signed"
  // when only a pdfUrl-bearing one actually is).
  signedAt?: number | null;
  signedBy?: string | null;
  pdfUrl?: string | null;
}

export interface SessionNote {
  id: string;
  text: string;
  timestamp: number;
  category: 'drug' | 'event' | 'observation' | 'alarm';
}

export interface SessionFormData {
  patientId: string;
  patientAge?: number;
  patientWeight?: number;
  asa?: AsaClass;
  procedure: string;
  anesthetist: string;
}

// M5.7. Mirrors backend/app/models/session.py's ObservationStats -- real
// numbers computed from this session's own persisted vital_readings rows,
// replacing what used to be Archive's hardcoded 'AI Processing' panel. See
// that Pydantic model's docstring for why confirmedObservations is not a
// filtered subset of readingsCount -- post-M5.7 persistence, it IS
// readingsCount's field-level twin (every persisted row is confirmed-only).
export interface ObservationStats {
  readingsCount: number;
  confirmedObservations: number;
  avgConfidence?: number | null;
  source?: 'camera' | 'synthetic' | 'replay' | 'mixed' | null;
}

export interface ArchivedSession extends Session {
  vitalSummary: {
    avgHr: number;
    minSpo2: number;
    avgEtco2: number;
    durationMin: number;
  };
  observationStats: ObservationStats;
}
