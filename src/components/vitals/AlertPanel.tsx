import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, AlertCircle, Info, X, BellOff } from 'lucide-react';
import { useAlertStore } from '@/store/alertStore';
import { formatTime } from '@/lib/utils';
import { cn } from '@/lib/utils';

const icons = {
  critical: AlertTriangle,
  warning:  AlertCircle,
  info:     Info,
};

const styles = {
  critical: {
    border: 'border-[rgba(255,59,48,0.5)]',
    bg:     'bg-[rgba(255,59,48,0.08)]',
    icon:   'text-[#FF3B30]',
    text:   'text-[#FF6B62]',
  },
  warning: {
    border: 'border-[rgba(255,149,0,0.4)]',
    bg:     'bg-[rgba(255,149,0,0.07)]',
    icon:   'text-[#FF9500]',
    text:   'text-[#FFB340]',
  },
  info: {
    border: 'border-[rgba(50,173,230,0.35)]',
    bg:     'bg-[rgba(50,173,230,0.06)]',
    icon:   'text-[#32ADE6]',
    text:   'text-[#5AC8FA]',
  },
};

export function AlertPanel() {
  const { active, dismissAlert, acknowledgeAll } = useAlertStore();

  const unacked = active.filter((a) => !a.acknowledged);

  if (active.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-[#3D5570]">
        <BellOff size={14} />
        <span className="font-display text-vital-xs uppercase tracking-wider">No active alerts</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 overflow-x-auto overflow-y-hidden py-1 px-1">
      {unacked.length > 0 && (
        <button
          onClick={acknowledgeAll}
          className="flex-shrink-0 px-2 py-1 rounded-md border border-monitor-border text-[#7A90AA]
                     hover:text-[#E8F1FF] hover:border-monitor-border-bright transition-colors
                     font-display text-vital-xs uppercase tracking-wider whitespace-nowrap"
        >
          Ack All
        </button>
      )}

      <AnimatePresence mode="popLayout">
        {active.slice(0, 6).map((alert) => {
          const s = styles[alert.severity];
          const Icon = icons[alert.severity];
          return (
            <motion.div
              key={alert.id}
              layout
              initial={{ opacity: 0, x: 20, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: -20, scale: 0.9 }}
              transition={{ duration: 0.2 }}
              className={cn(
                'flex-shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-lg border',
                s.border, s.bg,
                alert.acknowledged ? 'opacity-40' : '',
                alert.severity === 'critical' && !alert.acknowledged ? 'animate-glow-critical' : ''
              )}
            >
              <Icon size={13} className={s.icon} />
              <span className={cn('font-display text-vital-xs whitespace-nowrap', s.text)}>
                {alert.message}
                {alert.value !== undefined && (
                  <span className="ml-1 font-mono opacity-80">
                    {alert.value}{alert.unit}
                  </span>
                )}
              </span>
              <span className="font-mono text-[10px] text-[#3D5570] whitespace-nowrap">
                {formatTime(alert.timestamp)}
              </span>
              <button
                onClick={() => dismissAlert(alert.id)}
                className="text-[#3D5570] hover:text-[#7A90AA] transition-colors"
              >
                <X size={12} />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>

      {active.length > 6 && (
        <span className="flex-shrink-0 font-display text-vital-xs text-[#3D5570]">
          +{active.length - 6} more
        </span>
      )}
    </div>
  );
}
