import { create } from 'zustand';

export type ToastType = 'success' | 'error' | 'warning' | 'info' | 'clinical';

export interface ToastData {
  id: string;
  type: ToastType;
  title: string;
  description?: string;
  duration: number;
  action?: { label: string; onClick: () => void };
  createdAt: number;
}

type NewToast = Omit<ToastData, 'id' | 'createdAt'>;

interface ToastState {
  toasts: ToastData[];
  add: (t: NewToast) => string;
  remove: (id: string) => void;
  removeAll: () => void;
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],

  add: (toastData) => {
    const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    const item: ToastData = { ...toastData, id, createdAt: Date.now() };
    set((s) => ({ toasts: [item, ...s.toasts].slice(0, 5) }));
    return id;
  },

  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  removeAll: () => set({ toasts: [] }),
}));

// ─── Convenience hook ──────────────────────────────────────────────────────────
export function useToast() {
  const { add, remove, removeAll } = useToastStore();

  type Opts = Partial<Pick<ToastData, 'description' | 'duration' | 'action'>>;

  const toast = {
    success:  (title: string, opts?: Opts) => add({ type: 'success',  title, duration: 4000, ...opts }),
    error:    (title: string, opts?: Opts) => add({ type: 'error',    title, duration: 6000, ...opts }),
    warning:  (title: string, opts?: Opts) => add({ type: 'warning',  title, duration: 5000, ...opts }),
    info:     (title: string, opts?: Opts) => add({ type: 'info',     title, duration: 4000, ...opts }),
    clinical: (title: string, opts?: Opts) => add({ type: 'clinical', title, duration: 0,    ...opts }),
  };

  return { toast, dismiss: remove, dismissAll: removeAll };
}
