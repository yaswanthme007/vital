import type { ArchivedSession, Session, SessionNote, SessionFormData } from '@/types/session';
import type { CalibrationProfile, CalibrationVerifyResult, NormalizedBox, VitalKey } from '@/types/calibration';
import type { VitalObservationRow } from '@/types/vitals';

// The backend listens on :8000 on whatever host this page itself was
// loaded from — localhost when opened on this machine, the LAN IP when
// opened from another device (e.g. a phone reaching this dev server over
// WiFi). Hardcoding 'localhost' here would break the moment the page loads
// from anywhere else, since 'localhost' on the *client* always means the
// client's own machine, not this one.
//
// VITE_API_PORT overrides the port only. It exists so an end-to-end run can
// drive a THROWAWAY backend (its own port, its own scratch database)
// without touching a live demo instance already serving :8000 — see
// scripts/m5_7_1_flow_e2e.mjs. Unset (every normal run, dev or built) it is
// exactly the 8000 this line always used.
const API_PORT = import.meta.env.VITE_API_PORT ?? '8000';
const API_BASE = `http://${window.location.hostname}:${API_PORT}`;
const WS_BASE = API_BASE.replace(/^http/, 'ws');

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`${init?.method ?? 'GET'} ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  createSession: (data: SessionFormData) =>
    request<Session>('/api/sessions', { method: 'POST', body: JSON.stringify(data) }),

  listSessions: (status?: string) =>
    request<ArchivedSession[]>(`/api/sessions${status ? `?status=${status}` : ''}`),

  // M5.7 (F6, durability): used by sessionStore.rehydrateActiveSession to
  // re-confirm a session persisted in localStorage still exists/is still
  // active after a page reload.
  getSession: (id: string) => request<Session>(`/api/sessions/${id}`),

  // M5.7: the OBSERVED TIMELINE -- every persisted, genuinely camera-
  // confirmed vital reading for this session, oldest first. What the
  // operation workspace's ledger hydrates from on mount/reconnect, and
  // what Review/Archive read for the session's real vitals history. See
  // backend/app/api/sessions.py's GET /readings docstring.
  getReadings: (sessionId: string, params?: { since?: number; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.since != null) q.set('since', String(params.since));
    if (params?.limit != null) q.set('limit', String(params.limit));
    const qs = q.toString();
    return request<VitalObservationRow[]>(`/api/sessions/${sessionId}/readings${qs ? `?${qs}` : ''}`);
  },

  pauseSession: (id: string) =>
    request<Session>(`/api/sessions/${id}/pause`, { method: 'POST' }),

  resumeSession: (id: string) =>
    request<Session>(`/api/sessions/${id}/resume`, { method: 'POST' }),

  endSession: (id: string) =>
    request<ArchivedSession>(`/api/sessions/${id}/end`, { method: 'POST' }),

  addNote: (id: string, note: Omit<SessionNote, 'id' | 'timestamp'>) =>
    request<SessionNote>(`/api/sessions/${id}/notes`, { method: 'POST', body: JSON.stringify(note) }),

  signSession: (id: string, author: string, signatureMethod: string) =>
    request<Session>(`/api/sessions/${id}/sign`, {
      method: 'POST',
      body: JSON.stringify({ author, signatureMethod }),
    }),

  reportPdfUrl: (id: string) => `${API_BASE}/api/sessions/${id}/report.pdf`,

  getFlagged: (sessionId: string) =>
    request<FlaggedReadingDto[]>(`/api/sessions/${sessionId}/flagged`),

  getAudit: (sessionId: string) =>
    request<AuditEntryDto[]>(`/api/sessions/${sessionId}/audit`),

  correctFlagged: (flaggedId: string, correctedValue: string, author?: string) =>
    request<FlaggedReadingDto>(`/api/flagged/${flaggedId}/correct`, {
      method: 'POST',
      body: JSON.stringify({ correctedValue, author }),
    }),

  dismissFlagged: (flaggedId: string, author?: string) =>
    request<FlaggedReadingDto>(`/api/flagged/${flaggedId}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({ author }),
    }),

  // M5.8.1: the session's real, backend-evaluated alert history (app/alerts/
  // rules.py, persisted via repo.save_alert) — for Review/Archive's "Alerts
  // & Critical Events" section. Independent of the client-side alertStore
  // used during live monitoring (which is display-only and never
  // persisted); this is what survives after the operation ends.
  getAlerts: (sessionId: string) =>
    request<AlertDto[]>(`/api/sessions/${sessionId}/alerts`),

  readFrame: async (frame: Blob): Promise<PipelineReadFrameResult> => {
    const form = new FormData();
    form.append('file', frame, 'frame.jpg');
    const res = await fetch(`${API_BASE}/api/pipeline/read-frame`, { method: 'POST', body: form });
    if (!res.ok) {
      throw new Error(`POST /api/pipeline/read-frame failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<PipelineReadFrameResult>;
  },

  // M5.2: calibration profile lifecycle. See backend/app/api/calibration.py.
  verifyCalibrationCandidate: async (
    frame: Blob,
    referenceWidth: number,
    referenceHeight: number,
    roiBoxes: Partial<Record<VitalKey, NormalizedBox>>,
  ): Promise<CalibrationVerifyResult> => {
    const form = new FormData();
    form.append('payload', JSON.stringify({ referenceWidth, referenceHeight, roiBoxes }));
    form.append('file', frame, 'frame.jpg');
    const res = await fetch(`${API_BASE}/api/calibration/verify`, { method: 'POST', body: form });
    if (!res.ok) {
      throw new Error(`POST /api/calibration/verify failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<CalibrationVerifyResult>;
  },

  // M5.7.2: the multi-frame version of verifyCalibrationCandidate above --
  // same request shape, `frames` (several, spaced-apart captures of the
  // SAME live setup) instead of one. See backend/app/pipeline/
  // burst_verify.py for why several independently-captured frames, not
  // just one, and backend/app/api/calibration.py's verify_burst_candidate
  // for the response shape (additive `burst` metadata over
  // CalibrationVerifyResult's existing reading/confidence/diagnostics).
  verifyCalibrationBurst: async (
    frames: Blob[],
    referenceWidth: number,
    referenceHeight: number,
    roiBoxes: Partial<Record<VitalKey, NormalizedBox>>,
  ): Promise<CalibrationVerifyResult> => {
    const form = new FormData();
    form.append('payload', JSON.stringify({ referenceWidth, referenceHeight, roiBoxes }));
    frames.forEach((frame, i) => form.append('files', frame, `frame${i}.jpg`));
    const res = await fetch(`${API_BASE}/api/calibration/verify-burst`, { method: 'POST', body: form });
    if (!res.ok) {
      throw new Error(`POST /api/calibration/verify-burst failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<CalibrationVerifyResult>;
  },

  saveCalibrationProfile: (data: {
    referenceWidth: number;
    referenceHeight: number;
    roiBoxes: Partial<Record<VitalKey, NormalizedBox>>;
    fieldMeta: CalibrationProfile['fieldMeta'];
  }) =>
    request<CalibrationProfile>('/api/calibration', { method: 'POST', body: JSON.stringify(data) }),

  getActiveCalibrationProfile: async (): Promise<CalibrationProfile | null> => {
    const res = await fetch(`${API_BASE}/api/calibration/active`);
    if (res.status === 404) return null;
    if (!res.ok) {
      throw new Error(`GET /api/calibration/active failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<CalibrationProfile>;
  },

  // M5.3: attaches the reference frame -- the exact frame Verify ran
  // against -- to the just-saved active profile, enabling layout tracking.
  // A separate call from saveCalibrationProfile because the profile is
  // perfectly valid without one (it simply runs untracked, exactly as M5.2
  // did), so a failure here degrades rather than invalidating the save.
  attachCalibrationReferenceFrame: async (frame: Blob): Promise<{ referenceFrameSha256: string }> => {
    const form = new FormData();
    form.append('file', frame, 'reference.jpg');
    const res = await fetch(`${API_BASE}/api/calibration/active/reference-frame`, { method: 'PUT', body: form });
    if (!res.ok) {
      throw new Error(`PUT /api/calibration/active/reference-frame failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<{ referenceFrameSha256: string }>;
  },

  invalidateCalibrationProfile: async (): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/calibration/active`, { method: 'DELETE' });
    if (!res.ok && res.status !== 404) {
      throw new Error(`DELETE /api/calibration/active failed: ${res.status} ${res.statusText}`);
    }
  },

  // Ingestion side of the live-camera path: hands one captured frame to the
  // backend's FrameQueue (channel = session id). CameraSource drains it on
  // its own ~1s interval inside the `?source=camera` WS connection — this
  // call does not itself run OCR/reconcile/persistence, and must stay that
  // way (see backend/app/api/pipeline.py's push-frame docstring).
  pushFrame: async (sessionId: string, frame: Blob): Promise<{ channel: string; seq: number }> => {
    const form = new FormData();
    form.append('file', frame, 'frame.jpg');
    const res = await fetch(`${API_BASE}/api/pipeline/push-frame/${sessionId}`, { method: 'POST', body: form });
    if (!res.ok) {
      throw new Error(`POST /api/pipeline/push-frame/${sessionId} failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<{ channel: string; seq: number }>;
  },
};

export interface FlaggedReadingDto {
  id: string;
  timestamp: number;
  vital: 'hr' | 'spo2' | 'nibp' | 'etco2' | 'temp' | 'rr';
  aiValue: string;
  suggestedValue: string;
  unit: string;
  confidence: number;
  severity: 'critical' | 'warning';
  status: 'pending' | 'corrected' | 'dismissed';
  correctedValue?: string;
  frameNote: string;
}

export interface AlertDto {
  id: string;
  vitalType: 'hr' | 'spo2' | 'nibp' | 'etco2' | 'temp' | 'rr' | 'system';
  severity: 'critical' | 'warning' | 'info';
  message: string;
  value?: number | null;
  unit?: string | null;
  timestamp: number;
  acknowledged: boolean;
}

export interface AuditEntryDto {
  id: string;
  timestamp: number;
  action: 'ai_detect' | 'human_correct' | 'human_dismiss';
  vital: 'hr' | 'spo2' | 'nibp' | 'etco2' | 'temp' | 'rr';
  value: string;
  prevValue?: string;
  author: string;
  confidence?: number;
}

export interface PipelineReadFrameResult {
  reading: {
    hr: number | null;
    spo2: number | null;
    nibpSystolic: number | null;
    nibpDiastolic: number | null;
    nibpMean: number | null;
    etco2: number | null;
    temp: number | null;
    rr: number | null;
  };
  confidence: Record<string, number>;
}

export type VitalsWsSource = 'synthetic' | 'camera';

export function vitalsWsUrl(sessionId: string, source: VitalsWsSource = 'synthetic') {
  return `${WS_BASE}/ws/vitals/${sessionId}?source=${source}`;
}
