import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, XCircle, AlertTriangle, Info, Activity, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useToastStore } from '@/store/toastStore';
import type { ToastData, ToastType } from '@/store/toastStore';

// ─── Config ────────────────────────────────────────────────────────────────────

const toastConfig: Record<ToastType, {
  icon:   React.ElementType;
  color:  string;
  bg:     string;
  border: string;
  progress: string;
}> = {
  success:  { icon: CheckCircle2,  color: '#30D158', bg: 'rgba(48,209,88,0.08)',   border: 'rgba(48,209,88,0.3)',   progress: '#30D158' },
  error:    { icon: XCircle,       color: '#FF6B62', bg: 'rgba(255,59,48,0.1)',    border: 'rgba(255,59,48,0.4)',   progress: '#FF3B30' },
  warning:  { icon: AlertTriangle, color: '#FFB340', bg: 'rgba(255,149,0,0.08)',   border: 'rgba(255,149,0,0.3)',   progress: '#FF9500' },
  info:     { icon: Info,          color: '#5AC8FA', bg: 'rgba(50,173,230,0.08)',  border: 'rgba(50,173,230,0.3)',  progress: '#32ADE6' },
  clinical: { icon: Activity,      color: '#FF6B62', bg: 'rgba(255,59,48,0.12)',   border: 'rgba(255,59,48,0.5)',   progress: '#FF3B30' },
};

// ─── Toast Item ────────────────────────────────────────────────────────────────

function ToastItem({ toast }: { toast: ToastData }) {
  const { remove } = useToastStore();
  const cfg   = toastConfig[toast.type];
  const Icon  = cfg.icon;
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (toast.duration > 0) {
      timerRef.current = setTimeout(() => remove(toast.id), toast.duration);
    }
    return () => clearTimeout(timerRef.current);
  }, [toast.id, toast.duration, remove]);

  return (
    <motion.div
      layout
      className={cn(
        'relative flex items-start gap-3 rounded-2xl border px-4 py-3.5 shadow-elevation-4',
        'font-display overflow-hidden w-[340px] max-w-full',
        toast.type === 'clinical' && 'animate-glow-critical'
      )}
      style={{ background: `linear-gradient(135deg, #0C1625 0%, #101C2E 100%)`, borderColor: cfg.border }}
      initial={{ opacity: 0, x: 60, scale: 0.9 }}
      animate={{ opacity: 1, x: 0, scale: 1 }}
      exit={{ opacity: 0, x: 60, scale: 0.9, transition: { duration: 0.18 } }}
      transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
      whileHover={{ x: -2 }}
    >
      {/* Color accent */}
      <div className="absolute left-0 top-3 bottom-3 w-0.5 rounded-r" style={{ backgroundColor: cfg.color }} />

      {/* Icon */}
      <span className="flex-shrink-0 mt-0.5" style={{ color: cfg.color }}>
        <Icon size={16} />
      </span>

      {/* Text */}
      <div className="flex-1 min-w-0">
        <p className="text-vital-sm text-[#E8F1FF] font-medium leading-snug">{toast.title}</p>
        {toast.description && (
          <p className="text-vital-xs text-[#7A90AA] mt-0.5 leading-relaxed">{toast.description}</p>
        )}
        {toast.action && (
          <button
            onClick={() => { toast.action!.onClick(); remove(toast.id); }}
            className="mt-2 text-vital-xs font-semibold underline underline-offset-2 transition-opacity hover:opacity-80"
            style={{ color: cfg.color }}
          >
            {toast.action.label}
          </button>
        )}
      </div>

      {/* Close */}
      <motion.button
        onClick={() => remove(toast.id)}
        className="flex-shrink-0 text-[#3D5570] hover:text-[#7A90AA] transition-colors p-0.5 -mr-0.5 -mt-0.5"
        whileHover={{ scale: 1.15 }}
        whileTap={{ scale: 0.85 }}
      >
        <X size={14} />
      </motion.button>

      {/* Auto-dismiss progress bar */}
      {toast.duration > 0 && (
        <motion.div
          className="absolute bottom-0 left-0 h-[2px] rounded-b-2xl"
          style={{ backgroundColor: cfg.progress, opacity: 0.5 }}
          initial={{ width: '100%' }}
          animate={{ width: '0%' }}
          transition={{ duration: toast.duration / 1000, ease: 'linear' }}
        />
      )}
    </motion.div>
  );
}

// ─── Toast Container ───────────────────────────────────────────────────────────

function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);

  return (
    <div
      className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2 items-end pointer-events-none"
      aria-live="polite"
      aria-label="Notifications"
    >
      <AnimatePresence mode="popLayout" initial={false}>
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto">
            <ToastItem toast={t} />
          </div>
        ))}
      </AnimatePresence>
    </div>
  );
}

// ─── Provider (add to App.tsx) ─────────────────────────────────────────────────

export function ToastProvider({ children }: { children: React.ReactNode }) {
  return (
    <>
      {children}
      {createPortal(<ToastContainer />, document.body)}
    </>
  );
}
