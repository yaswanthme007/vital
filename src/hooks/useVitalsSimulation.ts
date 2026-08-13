import { useEffect } from 'react';
import { useVitalsStore } from '@/store/vitalsStore';
import { useSessionStore } from '@/store/sessionStore';
import { useDemoStore } from '@/store/demoStore';
import { vitalsWsUrl } from '@/lib/api';
import type { VitalReading } from '@/types/vitals';

type WsMessage =
  | { type: 'reading'; reading: VitalReading; confidence?: Record<string, number>; provenance?: string }
  | { type: 'alert' }
  | { type: 'flagged' | 'error' | 'nibp_measuring' | 'alert_acknowledged' };

export function useVitalsSimulation() {
  const { activeSession } = useSessionStore();
  const { updateVitals } = useVitalsStore();
  const demoActive = useDemoStore((s) => s.active);

  useEffect(() => {
    // Demo Mode drives the vitals store itself (see DemoMode.tsx). Not just
    // ignoring this feed's messages but not even opening the connection —
    // the two used to race on every tick (this feed's independent random
    // walk vs. Demo Mode's scenario values), and the discarded connection
    // was also the source of the "Vitals WebSocket error" console noise,
    // since nothing here was ever consuming it while demo was active.
    if (!activeSession || activeSession.status !== 'active' || demoActive) return;

    let stopped = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let backoffMs = 1000;

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(vitalsWsUrl(activeSession.id));

      ws.onopen = () => {
        backoffMs = 1000; // a clean connection resets the backoff
      };

      ws.onmessage = (event) => {
        const msg: WsMessage = JSON.parse(event.data);
        if (msg.type === 'reading') {
          updateVitals(msg.reading);
        }
      };

      ws.onerror = (event) => {
        console.error('Vitals WebSocket error', event);
      };

      // A previous version of this hook had no reconnect logic at all — a
      // single backend restart or network blip left live vitals silently
      // dead for the rest of the session until a manual page refresh.
      // onclose (not onerror) is the reliable signal a connection is
      // actually gone, so reconnect from there, with capped exponential
      // backoff so a persistently-down backend doesn't spin a tight retry
      // loop.
      ws.onclose = () => {
        if (stopped) return;
        retryTimer = setTimeout(connect, backoffMs);
        backoffMs = Math.min(backoffMs * 2, 10_000);
      };
    };

    connect();

    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, [activeSession?.id, activeSession?.status, demoActive]);
}
