import { create } from 'zustand';
import { api } from '@/lib/api';
import { useToastStore } from '@/store/toastStore';
import type { Session, SessionFormData, SessionNote, ArchivedSession } from '@/types/session';

function reportApiFailure(title: string) {
  useToastStore.getState().add({
    type: 'error',
    title,
    description: "Couldn't reach the backend — check it's running (docker compose up) and try again.",
    duration: 6000,
  });
}

interface SessionState {
  activeSession: Session | null;
  archivedSessions: ArchivedSession[];

  startSession: (data: SessionFormData) => Promise<void>;
  pauseSession: () => void;
  resumeSession: () => void;
  endSession: () => void;
  addNote: (note: Omit<SessionNote, 'id' | 'timestamp'>) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  activeSession: null,
  archivedSessions: [],

  startSession: async (data) => {
    const session = await api.createSession(data);
    set({ activeSession: session });
  },

  pauseSession: () => {
    const id = get().activeSession?.id;
    set((state) => ({
      activeSession: state.activeSession
        ? { ...state.activeSession, status: 'paused' }
        : null,
    }));
    if (id) api.pauseSession(id).catch((err) => {
      console.error('Failed to pause session', err);
      reportApiFailure('Pause not saved');
    });
  },

  resumeSession: () => {
    const id = get().activeSession?.id;
    set((state) => ({
      activeSession: state.activeSession
        ? { ...state.activeSession, status: 'active' }
        : null,
    }));
    if (id) api.resumeSession(id).catch((err) => {
      console.error('Failed to resume session', err);
      reportApiFailure('Resume not saved');
    });
  },

  endSession: () => {
    const id = get().activeSession?.id;
    set({ activeSession: null });
    if (!id) return;
    api.endSession(id)
      .then((archived) => set((state) => ({ archivedSessions: [archived, ...state.archivedSessions] })))
      .catch((err) => {
        console.error('Failed to end session', err);
        reportApiFailure("Session end didn't save to the archive");
      });
  },

  addNote: (noteData) => {
    const id = get().activeSession?.id;
    if (!id) return;
    const note: SessionNote = {
      ...noteData,
      id: `NOTE-${Date.now()}`,
      timestamp: Date.now(),
    };
    set((state) => ({
      activeSession: state.activeSession
        ? { ...state.activeSession, notes: [...state.activeSession.notes, note] }
        : null,
    }));
    api.addNote(id, noteData).catch((err) => {
      console.error('Failed to save note', err);
      reportApiFailure('Note not saved');
    });
  },
}));
