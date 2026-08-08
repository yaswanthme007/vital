import { create } from 'zustand';
import type { VitalType } from '@/types/vitals';

export type AlertSeverity = 'critical' | 'warning' | 'info';

export interface Alert {
  id: string;
  vitalType: VitalType | 'system';
  severity: AlertSeverity;
  message: string;
  value?: number;
  unit?: string;
  timestamp: number;
  acknowledged: boolean;
}

type NewAlert = Omit<Alert, 'id' | 'timestamp' | 'acknowledged'>;

interface AlertState {
  active: Alert[];
  history: Alert[];

  addAlert: (alert: NewAlert) => void;
  acknowledgeAlert: (id: string) => void;
  acknowledgeAll: () => void;
  dismissAlert: (id: string) => void;
  clearAll: () => void;
}

export const useAlertStore = create<AlertState>((set) => ({
  active: [],
  history: [],

  addAlert: (alertData) => {
    const alert: Alert = {
      ...alertData,
      id: `ALERT-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: Date.now(),
      acknowledged: false,
    };
    set((state) => ({
      active: [alert, ...state.active].slice(0, 15),
      history: [alert, ...state.history].slice(0, 500),
    }));
  },

  acknowledgeAlert: (id) =>
    set((state) => ({
      active: state.active.map((a) =>
        a.id === id ? { ...a, acknowledged: true } : a
      ),
    })),

  acknowledgeAll: () =>
    set((state) => ({
      active: state.active.map((a) => ({ ...a, acknowledged: true })),
    })),

  dismissAlert: (id) =>
    set((state) => ({
      active: state.active.filter((a) => a.id !== id),
    })),

  clearAll: () => set({ active: [] }),
}));
