import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/lib/api';
import { useToastStore } from '@/store/toastStore';
import type { Session, SessionFormData, SessionNote, ArchivedSession } from '@/types/session';
import type { CameraSourceMode } from '@/hooks/useCameraCapture';

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
  // Whether the active case is ingesting vitals from the real camera/OCR
  // pipeline (vs. the synthetic feed). Set once, at the end of a completed
  // Calibration run — Calibration doesn't otherwise persist a formal
  // profile, so this flag (plus which capture method was calibrated) is the
  // only piece of camera state the rest of the app actually needs.
  cameraMode: boolean;
  cameraSourceMode: CameraSourceMode | null;
  // M5.8.1: the id of the most recently ended (not necessarily signed)
  // session -- set the moment End Operation's POST /end resolves. This is
  // what the bare /review route (no session id in the URL, e.g. the
  // persistent "Review & Sign-off" nav link) resolves to, and it survives a
  // reload (persisted below) so the "refresh Review, same session" contract
  // holds even without a session id in the address bar. A specific
  // /review/:sessionId link (End Operation's own navigation, or Archive's
  // "Continue to Review & Sign-off") always wins over this when present.
  lastEndedSessionId: string | null;
  // M5.7 (F6, durability): true once rehydrateActiveSession() has finished
  // reconciling a persisted activeSession against the backend after a page
  // load. CameraCaptureController/useVitalsSimulation must not act on a
  // rehydrated-from-localStorage activeSession before this happens -- it
  // could be a session that was ended or signed in a different tab/browser
  // since this one last wrote it.
  hydrated: boolean;

  startSession: (data: SessionFormData) => Promise<void>;
  pauseSession: () => void;
  resumeSession: () => void;
  endSession: () => void;
  addNote: (note: Omit<SessionNote, 'id' | 'timestamp'>) => void;
  setCameraMode: (enabled: boolean, sourceMode?: CameraSourceMode) => void;
  rehydrateActiveSession: () => Promise<void>;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      activeSession: null,
      archivedSessions: [],
      cameraMode: false,
      cameraSourceMode: null,
      lastEndedSessionId: null,
      hydrated: false,

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
        // Optimistically flip status only (same pattern as pause/resume) —
        // the backend requires status='completed' before /sign will accept
        // a signature, and Review reads `activeSession ?? archivedSessions[0]`,
        // so clearing activeSession to null immediately (the previous
        // behavior) left Review with nothing to show for the gap between
        // this call and the archive response landing. Keeping the same
        // object (status flipped) means Review's session id never changes
        // underneath it, so nothing needs to re-fetch.
        set((state) => ({
          activeSession: state.activeSession ? { ...state.activeSession, status: 'completed' } : null,
        }));
        if (!id) return;
        api.endSession(id)
          .then((archived) => set((state) => ({
            activeSession: null,
            archivedSessions: [archived, ...state.archivedSessions],
            // A finished case shouldn't silently hand its camera setup to
            // whatever the next case turns out to be — the next case starts
            // on synthetic until it's explicitly (re)calibrated. Camera
            // capture stops here too -- CameraCaptureController's own
            // `enabled` condition includes cameraMode.
            cameraMode: false,
            cameraSourceMode: null,
            lastEndedSessionId: archived.id,
          })))
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

      setCameraMode: (enabled, sourceMode) =>
        set({ cameraMode: enabled, cameraSourceMode: enabled ? (sourceMode ?? null) : null }),

      // M5.7 (F6). Called once at app start (src/App.tsx's AppShell). A
      // MediaStream cannot survive a page reload -- the browser drops it,
      // full stop -- so this does NOT resume camera capture automatically;
      // the operator sees "Reconnect camera" and grants getUserMedia again
      // (CameraFeedPanel/CameraOverlay's existing disconnected state).
      // What this DOES restore: the session itself stays active server-
      // side (the backend has no idea the tab reloaded), so re-fetching it
      // here means the workspace, the ledger (via GET /readings), and the
      // vitals WS all resume against the SAME session rather than the
      // operator being bounced to the New Case form.
      rehydrateActiveSession: async () => {
        const id = get().activeSession?.id;
        if (!id) {
          set({ hydrated: true });
          return;
        }
        try {
          const fresh = await api.getSession(id);
          if (fresh.status === 'completed') {
            // Signed/ended elsewhere since this tab last wrote localStorage
            // -- there is nothing to resume into.
            set({ activeSession: null, cameraMode: false, cameraSourceMode: null, hydrated: true });
          } else {
            set({ activeSession: fresh, hydrated: true });
          }
        } catch (err) {
          // 404 (session genuinely gone) or backend unreachable at startup
          // -- either way, do not keep presenting a session that can't be
          // confirmed as real. A reachability failure self-heals: the next
          // action that hits the API will surface its own toast.
          console.error('Failed to rehydrate active session', err);
          set({ activeSession: null, cameraMode: false, cameraSourceMode: null, hydrated: true });
        }
      },
    }),
    {
      name: 'vital-session',
      // Only the fields needed to resume a session across a reload —
      // archivedSessions is refetched fresh wherever it's needed (Archive's
      // own useQuery), not carried in localStorage.
      partialize: (state) => ({
        activeSession: state.activeSession,
        cameraMode: state.cameraMode,
        cameraSourceMode: state.cameraSourceMode,
        lastEndedSessionId: state.lastEndedSessionId,
      }),
    },
  ),
);
