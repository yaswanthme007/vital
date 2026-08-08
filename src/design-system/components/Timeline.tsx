import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Pill, AlertTriangle, Activity, FileText, Gauge, PlayCircle, StopCircle, ChevronDown,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatTime } from '@/lib/utils';
import { semantic } from '../tokens';

// ─── Types ─────────────────────────────────────────────────────────────────────

export type TimelineEventType =
  | 'drug' | 'alert' | 'vital_change' | 'note' | 'measurement' | 'start' | 'end';

export interface TimelineEvent {
  id:          string;
  timestamp:   number;
  type:        TimelineEventType;
  title:       string;
  description?: string;
  value?:      string;
  severity?:   'critical' | 'warning' | 'info' | 'normal';
  details?:    Record<string, string | number>;
}

interface TimelineProps {
  events:    TimelineEvent[];
  compact?:  boolean;
  className?: string;
}

// ─── Event config ──────────────────────────────────────────────────────────────

const eventConfig: Record<TimelineEventType, {
  icon:    React.ElementType;
  color:   string;
  bg:      string;
  border:  string;
  dotColor: string;
}> = {
  drug: {
    icon: Pill, color: '#5AC8FA', bg: 'rgba(50,173,230,0.08)', border: 'rgba(50,173,230,0.25)', dotColor: '#32ADE6',
  },
  alert: {
    icon: AlertTriangle, color: '#FF6B62', bg: 'rgba(255,59,48,0.08)', border: 'rgba(255,59,48,0.25)', dotColor: '#FF3B30',
  },
  vital_change: {
    icon: Activity, color: '#00FF88', bg: 'rgba(0,255,136,0.06)', border: 'rgba(0,255,136,0.2)', dotColor: '#00FF88',
  },
  note: {
    icon: FileText, color: '#7A90AA', bg: 'rgba(122,144,170,0.08)', border: 'rgba(122,144,170,0.2)', dotColor: '#7A90AA',
  },
  measurement: {
    icon: Gauge, color: '#FFD600', bg: 'rgba(255,214,0,0.07)', border: 'rgba(255,214,0,0.25)', dotColor: '#FFD600',
  },
  start: {
    icon: PlayCircle, color: '#30D158', bg: 'rgba(48,209,88,0.08)', border: 'rgba(48,209,88,0.25)', dotColor: '#30D158',
  },
  end: {
    icon: StopCircle, color: '#FF3B30', bg: 'rgba(255,59,48,0.08)', border: 'rgba(255,59,48,0.25)', dotColor: '#FF3B30',
  },
};

// ─── Timeline Item ─────────────────────────────────────────────────────────────

function TimelineItem({ event, isLast, compact }: { event: TimelineEvent; isLast: boolean; compact?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = eventConfig[event.type];
  const Icon = cfg.icon;
  const hasExtra = !!(event.description ?? event.details);

  return (
    <motion.div
      className="flex gap-3"
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
    >
      {/* Spine */}
      <div className="flex flex-col items-center flex-shrink-0">
        <motion.div
          className="w-6 h-6 rounded-full border flex items-center justify-center flex-shrink-0 z-10"
          style={{ backgroundColor: cfg.bg, borderColor: cfg.border, color: cfg.color }}
          whileHover={{ scale: 1.15 }}
          transition={{ duration: 0.12 }}
        >
          <Icon size={11} />
        </motion.div>
        {!isLast && (
          <div className="w-px flex-1 mt-1.5 mb-1" style={{ backgroundColor: '#182B42' }} />
        )}
      </div>

      {/* Content */}
      <div className={cn('flex-1', isLast ? 'pb-0' : compact ? 'pb-3' : 'pb-4')}>
        {/* Header row */}
        <div className="flex items-start justify-between gap-2 mb-0.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-display text-vital-sm text-[#E8F1FF] font-medium leading-tight">{event.title}</span>
            {event.value && (
              <span
                className="font-mono text-vital-xs px-1.5 py-0.5 rounded"
                style={{ color: cfg.color, backgroundColor: cfg.bg }}
              >
                {event.value}
              </span>
            )}
            {event.severity === 'critical' && (
              <span className="font-display text-[9px] uppercase tracking-wider text-[#FF6B62] border border-[rgba(255,59,48,0.3)] px-1.5 py-0.5 rounded">CRITICAL</span>
            )}
          </div>
          <span className="font-mono text-vital-xs text-[#3D5570] flex-shrink-0 mt-0.5">
            {formatTime(event.timestamp)}
          </span>
        </div>

        {/* Expandable body */}
        {hasExtra && !compact && (
          <>
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-1 font-display text-vital-xs text-[#3D5570] hover:text-[#7A90AA] transition-colors mt-0.5"
            >
              <motion.span animate={{ rotate: expanded ? 180 : 0 }} transition={{ duration: 0.18 }}>
                <ChevronDown size={12} />
              </motion.span>
              {expanded ? 'Hide details' : 'Show details'}
            </button>

            <AnimatePresence>
              {expanded && (
                <motion.div
                  className="mt-2 rounded-ds-lg border p-3 space-y-1.5"
                  style={{ backgroundColor: cfg.bg, borderColor: cfg.border }}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.22 }}
                >
                  {event.description && (
                    <p className="font-display text-vital-xs text-[#7A90AA] leading-relaxed">{event.description}</p>
                  )}
                  {event.details && Object.entries(event.details).map(([k, val]) => (
                    <div key={k} className="flex items-center justify-between">
                      <span className="font-display text-vital-xs text-[#3D5570] uppercase tracking-wider">{k}</span>
                      <span className="font-mono text-vital-xs" style={{ color: cfg.color }}>{val}</span>
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </>
        )}
        {compact && event.description && (
          <p className="font-display text-vital-xs text-[#3D5570] leading-relaxed">{event.description}</p>
        )}
      </div>
    </motion.div>
  );
}

// ─── Timeline ─────────────────────────────────────────────────────────────────

export function Timeline({ events, compact, className }: TimelineProps) {
  if (events.length === 0) {
    return (
      <div className={cn('flex items-center justify-center py-8 text-[#3D5570]', className)}>
        <span className="font-display text-vital-sm">No events recorded</span>
      </div>
    );
  }

  return (
    <div className={cn('space-y-0', className)}>
      {events.map((event, i) => (
        <TimelineItem
          key={event.id}
          event={event}
          isLast={i === events.length - 1}
          compact={compact}
        />
      ))}
    </div>
  );
}
