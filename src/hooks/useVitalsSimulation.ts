import { useEffect } from 'react';
import { useVitalsStore } from '@/store/vitalsStore';
import { useSessionStore } from '@/store/sessionStore';
import { api, vitalsWsUrl } from '@/lib/api';
import type { VitalReading, VitalField, FieldStatus } from '@/types/vitals';
import type { TrackingState } from '@/types/calibration';

type WsMessage =
  | {
      type: 'reading';
      reading: VitalReading;
      confidence?: Record<string, number>;
      provenance?: string;
      // M5.3, additive: present only while layout tracking is actually
      // running (backend/app/ws/vitals.py TrackingState.envelope).
      tracking?: TrackingState;
      // M5.7, additive: per-field confirmed/held/baseline and, per field,
      // the timestamp it was last genuinely confirmed (backend/app/ws/
      // vitals.py send_loop). Always present for a real (non-Demo-Mode)
      // connection since reconcile() now computes field_status
      // unconditionally.
      fieldStatus?: Partial<Record<VitalField, FieldStatus>>;
      lastConfirmedAt?: Partial<Record<VitalField, number>>;
    }
  | { type: 'alert' }
  | { type: 'flagged' | 'error' | 'nibp_measuring' | 'alert_acknowledged' };

export function useVitalsSimulation() {
  const { activeSession, cameraMode, hydrated } = useSessionStore();
  const {
    updateVitals, setConfidence, setTracking, setNibpMeasuring,
    setLastConfirmedAt, appendObservationFromTick, hydrateObservations,
  } = useVitalsStore();

  useEffect(() => {
    // M5.7 (F6): also wait for sessionStore.hydrated -- a session just
    // rehydrated from localStorage hasn't been re-confirmed against the
    // backend yet (see rehydrateActiveSession), so opening a WS against it
    // this early could be for a session that's about to be cleared as
    // stale a moment later.
    //
    // M5.7.1: also require cameraMode. New Case now always creates the
    // session BEFORE routing to mandatory Calibration (see StartPage/
    // OperationPage's guard), so there is a real window -- the whole time
    // the operator is drawing/verifying ROIs -- where activeSession.status
    // is already 'active' but calibration has not yet completed. Without
    // this guard, THIS hook would open a source='synthetic' connection
    // during that window and persist fabricated readings into the same
    // session's real observation history before the operator has even
    // finished Calibration -- exactly the "no synthetic reading pretending
    // to be a camera observation" and "Archive contains only genuine
    // confirmed camera observations" invariants this milestone requires.
    // Calibration is setup, not observation; nothing should be persisted
    // until it succeeds and Active Operation actually begins.
    if (!hydrated || !activeSession || activeSession.status !== 'active' || !cameraMode) return;

    // Exactly one source once this guard passes: camera. (A pre-M5.7.1
    // build could reach here with cameraMode false and fall back to
    // 'synthetic' -- that branch is now unreachable by construction, since
    // Active Operation cannot be entered without cameraMode true, but the
    // ternary is kept rather than deleted so a future relaxation of the
    // guard above degrades safely instead of silently mislabeling data.)
    const source = cameraMode ? 'camera' : 'synthetic';

    // M5.7 (F6, durability): hydrate the ledger from whatever this session
    // has ALREADY had persisted -- covers a browser reload mid-operation
    // (the WS/store both start empty again, but SQLite doesn't) and simply
    // re-arriving at this session from Review/Archive. Merges with
    // whatever the WS below may have already appended (see
    // hydrateObservations' own docstring) rather than racing it.
    api.getReadings(activeSession.id).then(hydrateObservations).catch((err) => {
      console.error('Failed to hydrate observation history', err);
    });

    let stopped = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let backoffMs = 1000;

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(vitalsWsUrl(activeSession.id, source));

      ws.onopen = () => {
        backoffMs = 1000; // a clean connection resets the backoff
      };

      ws.onmessage = (event) => {
        const msg: WsMessage = JSON.parse(event.data);
        if (msg.type === 'reading') {
          updateVitals(msg.reading, msg.fieldStatus);
          setConfidence(msg.confidence);
          setTracking(msg.tracking);
          setLastConfirmedAt(msg.lastConfirmedAt);
          // M5.7: append to the OBSERVED TIMELINE off the SAME tick the
          // live cards just rendered — see appendObservationFromTick's own
          // docstring for why this stays a live no-poll append rather than
          // re-fetching GET /readings on every message.
          appendObservationFromTick(msg.reading, msg.fieldStatus, msg.confidence, source === 'camera' ? 'camera' : 'synthetic');
          // The backend's 'nibp_measuring' ack just confirms the trigger was
          // received — the real completion signal is the next full reading
          // that arrives after it, so reset the busy state here rather than
          // on a client-side timer.
          if (useVitalsStore.getState().nibpMeasuring) {
            setNibpMeasuring(false);
          }
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

    // Bridges the NIBP "Measure" button (which only touches the store, not
    // this WS instance) to the actual socket: a non-React subscription so
    // it fires the moment nibpMeasuring flips false→true, regardless of
    // which component owns the button.
    const unsubscribeNibp = useVitalsStore.subscribe((state, prevState) => {
      if (state.nibpMeasuring && !prevState.nibpMeasuring && ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'trigger_nibp' }));
      }
    });

    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      unsubscribeNibp();
      ws?.close();
    };
  }, [activeSession?.id, activeSession?.status, cameraMode, hydrated]);
}
