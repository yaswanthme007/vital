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

export interface ArchivedSession extends Session {
  vitalSummary: {
    avgHr: number;
    minSpo2: number;
    avgEtco2: number;
    durationMin: number;
  };
}
