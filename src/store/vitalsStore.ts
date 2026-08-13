import { create } from 'zustand';
import type { VitalReading, AlarmLimit } from '@/types/vitals';
import { DEFAULT_ALARM_LIMITS } from '@/types/vitals';
import { checkAlerts, ALERT_THROTTLE_WINDOW_MS } from '@/lib/alertRules';
import { useAlertStore } from '@/store/alertStore';

const MAX_HISTORY = 360;

// One throttle map per app lifetime, reset on clearHistory() (called on
// session end/start) — mirrors backend/app/alerts/rules.py's AlertThrottle,
// "one instance per active stream/session".
let lastAlertAt: Record<string, number> = {};

interface VitalsState {
  current: VitalReading | null;
  history: VitalReading[];
  alarmLimits: AlarmLimit[];
  nibpLastMeasuredAt: number | null;
  nibpMeasuring: boolean;

  updateVitals: (reading: VitalReading) => void;
  updateAlarmLimit: (limit: AlarmLimit) => void;
  triggerNibpMeasurement: () => void;
  setNibpMeasuring: (v: boolean) => void;
  clearHistory: () => void;
}

export const useVitalsStore = create<VitalsState>((set) => ({
  current: null,
  history: [],
  alarmLimits: DEFAULT_ALARM_LIMITS,
  nibpLastMeasuredAt: null,
  nibpMeasuring: false,

  updateVitals: (reading) => {
    set((state) => ({
      current: reading,
      history: [...state.history, reading].slice(-MAX_HISTORY),
    }));

    const now = Date.now();
    for (const alert of checkAlerts(reading)) {
      const key = `${alert.vitalType}-${alert.severity}`;
      if (now - (lastAlertAt[key] ?? 0) > ALERT_THROTTLE_WINDOW_MS) {
        lastAlertAt[key] = now;
        useAlertStore.getState().addAlert(alert);
      }
    }
  },

  updateAlarmLimit: (limit) =>
    set((state) => ({
      alarmLimits: state.alarmLimits.map((al) =>
        al.vitalType === limit.vitalType ? limit : al
      ),
    })),

  triggerNibpMeasurement: () => set({ nibpMeasuring: true }),

  setNibpMeasuring: (v) =>
    set((state) => ({
      nibpMeasuring: v,
      nibpLastMeasuredAt: v ? state.nibpLastMeasuredAt : Date.now(),
    })),

  clearHistory: () => {
    lastAlertAt = {};
    set({ history: [], current: null, nibpLastMeasuredAt: null });
  },
}));
