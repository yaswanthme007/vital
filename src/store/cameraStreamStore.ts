import { create } from 'zustand';

// Live status of the SurgeryPage capture loop (useCameraStreaming), read by
// CameraOverlay/SurgeryHeader to show what's actually happening — not
// prop-drilled because the loop and its display live in different branches
// of the Surgery tree. Deliberately separate from sessionStore's cameraMode
// (the *selection*, persisted across navigation) — this is ephemeral,
// per-mount status that resets every time the capture loop (re)starts.
export type CameraStreamStatus =
  | 'idle'          // camera mode off, or not yet attempted
  | 'connecting'    // awaiting getUserMedia/getDisplayMedia permission
  | 'active'        // stream live, frames being captured/uploaded on schedule
  | 'uploading'     // a push-frame request is currently in flight
  | 'upload_error'  // most recent push-frame failed (stream may still be live)
  | 'disconnected'; // camera/screen-share stream ended (permission revoked, tab-share stopped, etc.)

interface CameraStreamState {
  status: CameraStreamStatus;
  lastFrameSentAt: number | null;
  lastError: string | null;
  // M5.7: the raw MediaStream the app-root CameraCaptureController is
  // currently capturing frames from (see src/hooks/useCameraCapture.ts,
  // which is what actually sets this). A MediaStream can back more than
  // one <video> element at once, so any page that wants to show the LIVE
  // feed (the Active Operation workspace) attaches this directly to its
  // own visible <video> rather than opening a second getUserMedia stream
  // or needing the capture loop to live inside that page at all -- the
  // capture loop keeps running across navigation regardless of which page,
  // if any, is currently displaying it. null whenever no camera/screen
  // stream is connected.
  mediaStream: MediaStream | null;
  setStatus: (status: CameraStreamStatus, error?: string | null) => void;
  frameSent: () => void;
  setMediaStream: (stream: MediaStream | null) => void;
  reset: () => void;
}

export const useCameraStreamStore = create<CameraStreamState>((set) => ({
  status: 'idle',
  lastFrameSentAt: null,
  lastError: null,
  mediaStream: null,

  setStatus: (status, error = null) => set({ status, lastError: error }),
  frameSent: () => set({ lastFrameSentAt: Date.now() }),
  setMediaStream: (stream) => set({ mediaStream: stream }),
  reset: () => set({ status: 'idle', lastFrameSentAt: null, lastError: null, mediaStream: null }),
}));
