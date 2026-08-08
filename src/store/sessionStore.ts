import { create } from 'zustand';
import type { Session, SessionFormData, SessionNote, ArchivedSession } from '@/types/session';

interface SessionState {
  activeSession: Session | null;
  archivedSessions: ArchivedSession[];

  startSession: (data: SessionFormData) => void;
  pauseSession: () => void;
  resumeSession: () => void;
  endSession: () => void;
  addNote: (note: Omit<SessionNote, 'id' | 'timestamp'>) => void;
}

const MOCK_ARCHIVES: ArchivedSession[] = [
  {
    id: 'SESSION-1720000000000',
    patient: { id: 'PT-2024-001', age: 45, weight: 72, asa: 2 },
    procedure: 'Laparoscopic Cholecystectomy',
    anesthetist: 'Dr. Priya Sharma',
    startTime: Date.now() - 86400000 * 2,
    endTime: Date.now() - 86400000 * 2 + 7200000,
    notes: [],
    status: 'completed',
    vitalSummary: { avgHr: 74, minSpo2: 96, avgEtco2: 37, durationMin: 120 },
  },
  {
    id: 'SESSION-1719000000000',
    patient: { id: 'PT-2024-002', age: 61, weight: 85, asa: 3 },
    procedure: 'Total Knee Replacement',
    anesthetist: 'Dr. Arjun Mehta',
    startTime: Date.now() - 86400000 * 5,
    endTime: Date.now() - 86400000 * 5 + 10800000,
    notes: [],
    status: 'completed',
    vitalSummary: { avgHr: 68, minSpo2: 97, avgEtco2: 39, durationMin: 180 },
  },
  {
    id: 'SESSION-1718000000000',
    patient: { id: 'PT-2024-003', age: 38, weight: 64, asa: 1 },
    procedure: 'Appendectomy',
    anesthetist: 'Dr. Priya Sharma',
    startTime: Date.now() - 86400000 * 8,
    endTime: Date.now() - 86400000 * 8 + 5400000,
    notes: [],
    status: 'completed',
    vitalSummary: { avgHr: 80, minSpo2: 98, avgEtco2: 36, durationMin: 90 },
  },
];

export const useSessionStore = create<SessionState>((set) => ({
  activeSession: null,
  archivedSessions: MOCK_ARCHIVES,

  startSession: (data) => {
    const session: Session = {
      id: `SESSION-${Date.now()}`,
      patient: {
        id: data.patientId,
        age: data.patientAge,
        weight: data.patientWeight,
        asa: data.asa,
      },
      procedure: data.procedure,
      anesthetist: data.anesthetist,
      startTime: Date.now(),
      notes: [],
      status: 'active',
    };
    set({ activeSession: session });
  },

  pauseSession: () =>
    set((state) => ({
      activeSession: state.activeSession
        ? { ...state.activeSession, status: 'paused' }
        : null,
    })),

  resumeSession: () =>
    set((state) => ({
      activeSession: state.activeSession
        ? { ...state.activeSession, status: 'active' }
        : null,
    })),

  endSession: () =>
    set((state) => {
      if (!state.activeSession) return {};
      const ended: ArchivedSession = {
        ...state.activeSession,
        endTime: Date.now(),
        status: 'completed',
        vitalSummary: { avgHr: 75, minSpo2: 97, avgEtco2: 38, durationMin: 0 },
      };
      return {
        activeSession: null,
        archivedSessions: [ended, ...state.archivedSessions],
      };
    }),

  addNote: (noteData) =>
    set((state) => {
      if (!state.activeSession) return {};
      const note: SessionNote = {
        ...noteData,
        id: `NOTE-${Date.now()}`,
        timestamp: Date.now(),
      };
      return {
        activeSession: {
          ...state.activeSession,
          notes: [...state.activeSession.notes, note],
        },
      };
    }),
}));
