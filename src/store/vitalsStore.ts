import { create } from 'zustand';
import type { VitalReading, AlarmLimit } from '@/types/vitals';
import { DEFAULT_ALARM_LIMITS } from '@/types/vitals';

const MAX_HISTORY = 360;

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

  updateVitals: (reading) =>
    set((state) => ({
      current: reading,
      history: [...state.history, reading].slice(-MAX_HISTORY),
    })),

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

  clearHistory: () => set({ history: [], current: null, nibpLastMeasuredAt: null }),
}));
