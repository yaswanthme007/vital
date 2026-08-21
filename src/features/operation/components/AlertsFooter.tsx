import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, AlertCircle, Info, X, BellOff, CheckCheck } from 'lucide-react';
import { useAlertStore } from '@/store/alertStore';
import { useSessionStore } from '@/store/sessionStore';
import { formatTime, cn } from '@/lib/utils';

// ─── Config ───────────────────────────────────────────────────────────────────

const cfg = {
  critical: {
    icon: AlertTriangle,
    dot: '#DC2626',
    text: '#DC2626',
    bg: '#FEF2F2',
    border: '#FECACA',
  },
  warning: {
    icon: AlertCircle,
    dot: '#D97706',
    text: '#D97706',
    bg: '#FFFBEB',
    border: '#FDE68A',
  },
  info: {
    icon: Info,
    dot: '#0284C7',
    text: '#0284C7',
    bg: '#EFF6FF',
    border: '#BFDBFE',
  },
} as const;

// ─── Component ────────────────────────────────────────────────────────────────

export function AlertsFooter() {
  const { active, dismissAlert, acknowledgeAlert, acknowledgeAll } = useAlertStore();
  const { activeSession, addNote } = useSessionStore();

  const unacked    = active.filter(a => !a.acknowledged);
  const hasCrit    = active.some(a => a.severity === 'critical' && !a.acknowledged);

  // Quick event markers
  const quickEvents = ['Intubation', 'Incision', 'Drug: Propofol', 'Drug: Fentanyl', 'Extubation'];
  const logEvent = (label: string) => {
    addNote({ text: label, category: 'event' });
  };

  return (
    <footer
      className="flex-shrink-0 flex flex-col transition-colors duration-300"
      style={{
        background: hasCrit ? '#FEF2F2' : '#FFFFFF',
        borderTop: `1px solid ${hasCrit ? '#FECACA' : '#E2E8F0'}`,
        transition: 'border-color 0.4s, background 0.4s',
      }}
    >
      {/* ── Alert strip ─────────────────────────────────────────────────── */}
      <div className="flex items-center h-12 gap-0">

        {/* "ALERTS" label */}
        <div
          className="flex-shrink-0 flex items-center justify-center h-full px-3"
          style={{ borderRight: '1px solid #E2E8F0', minWidth: 72 }}
        >
          <span className="font-display text-[9px] font-bold uppercase tracking-[0.22em] text-slate-400">Alerts</span>
        </div>

        {/* Scrollable alert chips */}
        <div className="flex-1 flex items-center gap-2 overflow-x-auto overflow-y-hidden px-3 py-1.5"
             style={{ scrollbarWidth: 'none' }}>
          {active.length === 0 ? (
            <div className="flex items-center gap-1.5 text-slate-400">
              <BellOff size={11} />
              <span className="font-display text-[10px] uppercase tracking-wider">All clear</span>
            </div>
          ) : (
            <AnimatePresence mode="popLayout" initial={false}>
              {active.slice(0, 8).map(alert => {
                const s = cfg[alert.severity];
                const Icon = s.icon;
                return (
                  <motion.div
                    key={alert.id}
                    layout
                    initial={{ opacity: 0, x: 16, scale: 0.92 }}
                    animate={{ opacity: alert.acknowledged ? 0.38 : 1, x: 0, scale: 1 }}
                    exit={{ opacity: 0, x: -12, scale: 0.88, transition: { duration: 0.18 } }}
                    transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                    className={cn(
                      'flex-shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded-lg cursor-default',
                      alert.severity === 'critical' && !alert.acknowledged ? 'animate-glow-critical' : ''
                    )}
                    style={{ background: s.bg, border: `1px solid ${s.border}` }}
                  >
                    {/* Pulse dot for unacked critical */}
                    {!alert.acknowledged && alert.severity === 'critical' && (
                      <motion.div
                        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                        style={{ backgroundColor: s.dot }}
                        animate={{ opacity: [1, 0.2, 1] }}
                        transition={{ repeat: Infinity, duration: 0.7 }}
                      />
                    )}
                    <Icon size={11} style={{ color: s.dot }} />
                    <span className="font-display text-[10px] whitespace-nowrap" style={{ color: s.text }}>
                      {alert.message}
                      {alert.value !== undefined && (
                        <span className="ml-1 font-mono opacity-80">
                          {alert.value}{alert.unit}
                        </span>
                      )}
                    </span>
                    <span className="font-mono text-[9px] text-slate-400 whitespace-nowrap">
                      {formatTime(alert.timestamp)}
                    </span>
                    {/* Acknowledge single */}
                    {!alert.acknowledged && (
                      <motion.button
                        onClick={() => acknowledgeAlert(alert.id)}
                        className="text-slate-400 hover:text-slate-600 transition-colors p-0.5"
                        whileHover={{ scale: 1.2 }}
                        whileTap={{ scale: 0.85 }}
                        aria-label={`Acknowledge: ${alert.message}`}
                        title="Acknowledge"
                      >
                        <CheckCheck size={10} aria-hidden="true" />
                      </motion.button>
                    )}
                    {/* Dismiss */}
                    <motion.button
                      onClick={() => dismissAlert(alert.id)}
                      className="text-slate-400 hover:text-slate-600 transition-colors p-0.5"
                      whileHover={{ scale: 1.2 }}
                      whileTap={{ scale: 0.85 }}
                      aria-label={`Dismiss: ${alert.message}`}
                      title="Dismiss"
                    >
                      <X size={10} aria-hidden="true" />
                    </motion.button>
                  </motion.div>
                );
              })}
              {active.length > 8 && (
                <span className="flex-shrink-0 font-display text-[10px] text-slate-400">
                  +{active.length - 8} more
                </span>
              )}
            </AnimatePresence>
          )}
        </div>

        {/* Ack All + divider */}
        {unacked.length > 0 && (
          <div className="flex-shrink-0 flex items-center h-full" style={{ borderLeft: '1px solid #E2E8F0' }}>
            <motion.button
              onClick={acknowledgeAll}
              className="flex items-center gap-1.5 h-full px-3 font-display text-[10px] text-slate-400 hover:text-slate-600 uppercase tracking-wider transition-colors"
              whileHover={{ backgroundColor: '#F8FAFC' }}
              whileTap={{ scale: 0.97 }}
              aria-label="Acknowledge all alerts"
            >
              <CheckCheck size={11} aria-hidden="true" />
              Ack All
            </motion.button>
          </div>
        )}

        {/* ── Quick event markers ──────────────────────────────────────── */}
        <div
          className="flex-shrink-0 flex items-center gap-1.5 h-full px-3"
          style={{ borderLeft: '1px solid #E2E8F0' }}
        >
          <span className="font-display text-[8px] text-slate-400 uppercase tracking-[0.18em] mr-0.5">Event</span>
          {quickEvents.map(evt => (
            <motion.button
              key={evt}
              onClick={() => activeSession && logEvent(evt)}
              disabled={!activeSession}
              className="flex-shrink-0 px-2 py-1 rounded font-display text-[9px] uppercase tracking-wider text-slate-500 hover:text-clinical-primary transition-colors"
              style={{ background: '#F8FAFC', border: '1px solid #E2E8F0' }}
              whileHover={{ borderColor: '#BFDBFE', backgroundColor: '#EFF6FF' }}
              whileTap={{ scale: 0.93 }}
              title={`Mark: ${evt}`}
            >
              {evt.startsWith('Drug:') ? evt.slice(6) : evt}
            </motion.button>
          ))}
        </div>
      </div>
    </footer>
  );
}
