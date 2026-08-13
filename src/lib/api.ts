import type { ArchivedSession, Session, SessionNote, SessionFormData } from '@/types/session';

// The backend always listens on :8000 on whatever host this page itself was
// loaded from — localhost when opened on this machine, the LAN IP when
// opened from another device (e.g. a phone reaching this dev server over
// WiFi). Hardcoding 'localhost' here would break the moment the page loads
// from anywhere else, since 'localhost' on the *client* always means the
// client's own machine, not this one.
const API_BASE = `http://${window.location.hostname}:8000`;
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

  readFrame: async (frame: Blob): Promise<PipelineReadFrameResult> => {
    const form = new FormData();
    form.append('file', frame, 'frame.jpg');
    const res = await fetch(`${API_BASE}/api/pipeline/read-frame`, { method: 'POST', body: form });
    if (!res.ok) {
      throw new Error(`POST /api/pipeline/read-frame failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<PipelineReadFrameResult>;
  },
};

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

export function vitalsWsUrl(sessionId: string) {
  return `${WS_BASE}/ws/vitals/${sessionId}?source=synthetic`;
}
