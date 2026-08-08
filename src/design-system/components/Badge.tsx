import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import type { Severity, VitalKey } from '../tokens';
import { vitals, semantic } from '../tokens';

// ─── Status Badge ──────────────────────────────────────────────────────────────

type BadgeSize = 'xs' | 'sm' | 'md';

interface BadgeProps {
  variant?: Severity | 'recording' | 'live' | 'offline' | 'neutral';
  size?: BadgeSize;
  pulse?: boolean;
  children: React.ReactNode;
  className?: string;
}

const badgeStyles: Record<string, string> = {
  critical:  'bg-[rgba(255,59,48,0.12)]  border-[rgba(255,59,48,0.38)]  text-[#FF6B62]',
  warning:   'bg-[rgba(255,149,0,0.12)]  border-[rgba(255,149,0,0.38)]  text-[#FFB340]',
  info:      'bg-[rgba(50,173,230,0.12)] border-[rgba(50,173,230,0.38)] text-[#5AC8FA]',
  success:   'bg-[rgba(48,209,88,0.12)]  border-[rgba(48,209,88,0.38)]  text-[#34C759]',
  neutral:   'bg-white/[0.05] border-white/[0.1] text-[#7A90AA]',
  recording: 'bg-[rgba(255,59,48,0.1)]   border-[rgba(255,59,48,0.3)]   text-[#FF3B30]',
  live:      'bg-[rgba(0,255,136,0.1)]   border-[rgba(0,255,136,0.3)]   text-[#00FF88]',
  offline:   'bg-white/[0.04] border-white/[0.08] text-[#3D5570]',
};

const dotColors: Record<string, string> = {
  critical: 'bg-[#FF3B30]',
  warning:  'bg-[#FF9500]',
  info:     'bg-[#32ADE6]',
  success:  'bg-[#30D158]',
  neutral:  'bg-[#7A90AA]',
  recording:'bg-[#FF3B30]',
  live:     'bg-[#00FF88]',
  offline:  'bg-[#3D5570]',
};

const badgeSizes: Record<BadgeSize, string> = {
  xs: 'px-1.5 py-0.5 text-[9px]  gap-1',
  sm: 'px-2   py-0.5 text-[11px] gap-1.5',
  md: 'px-2.5 py-1   text-xs     gap-1.5',
};

export function Badge({ variant = 'neutral', size = 'sm', pulse, children, className }: BadgeProps) {
  const dotClass = dotColors[variant] ?? 'bg-white/40';
  const isPulse  = pulse || variant === 'recording' || variant === 'live';

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border font-display font-medium uppercase tracking-wider',
        badgeSizes[size],
        badgeStyles[variant] ?? badgeStyles.neutral,
        className
      )}
    >
      {isPulse && (
        <span className="relative flex-shrink-0 w-1.5 h-1.5">
          <span className={cn('absolute inset-0 rounded-full animate-recording', dotClass)} />
          <span className={cn('relative block w-1.5 h-1.5 rounded-full', dotClass)} />
        </span>
      )}
      {children}
    </span>
  );
}

// ─── Vital Badge ───────────────────────────────────────────────────────────────

interface VitalBadgeProps {
  vital: VitalKey;
  size?: BadgeSize;
  children?: React.ReactNode;
  className?: string;
}

export function VitalBadge({ vital, size = 'sm', children, className }: VitalBadgeProps) {
  const v = vitals[vital];
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border font-display font-semibold uppercase tracking-wider',
        badgeSizes[size],
        className
      )}
      style={{
        background: v.bg,
        borderColor: v.glow,
        color: v.color,
      }}
    >
      {children ?? v.label}
    </span>
  );
}

// ─── Status Dot ────────────────────────────────────────────────────────────────

interface StatusDotProps {
  status: 'active' | 'paused' | 'offline' | 'critical' | 'warning';
  label?: string;
  className?: string;
}

const dotStatus: Record<string, { dot: string; text: string; pulse?: boolean }> = {
  active:   { dot: 'bg-[#30D158]', text: 'text-[#30D158]', pulse: true },
  paused:   { dot: 'bg-[#FF9500]', text: 'text-[#FF9500]' },
  offline:  { dot: 'bg-[#3D5570]', text: 'text-[#3D5570]' },
  critical: { dot: 'bg-[#FF3B30]', text: 'text-[#FF3B30]', pulse: true },
  warning:  { dot: 'bg-[#FF9500]', text: 'text-[#FF9500]', pulse: true },
};

export function StatusDot({ status, label, className }: StatusDotProps) {
  const s = dotStatus[status];
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className="relative w-2 h-2">
        {s.pulse && (
          <span className={cn('absolute inset-0 rounded-full opacity-40 animate-recording', s.dot)} />
        )}
        <span className={cn('relative block w-2 h-2 rounded-full', s.dot)} />
      </span>
      {label && (
        <span className={cn('font-display text-vital-xs uppercase tracking-wider', s.text)}>{label}</span>
      )}
    </span>
  );
}

// ─── Alert Count Badge ─────────────────────────────────────────────────────────

interface AlertCountBadgeProps {
  count: number;
  severity?: 'critical' | 'warning';
  className?: string;
}

export function AlertCountBadge({ count, severity = 'critical', className }: AlertCountBadgeProps) {
  if (count === 0) return null;
  return (
    <motion.span
      className={cn(
        'inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full',
        'font-display text-[10px] font-bold',
        severity === 'critical'
          ? 'bg-[#FF3B30] text-white animate-pulse-critical'
          : 'bg-[#FF9500] text-black',
        className
      )}
      initial={{ scale: 0 }}
      animate={{ scale: 1 }}
      transition={{ type: 'spring', stiffness: 500, damping: 20 }}
    >
      {count > 99 ? '99+' : count}
    </motion.span>
  );
}
