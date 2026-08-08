import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import type { VitalKey } from '../tokens';
import { vitals } from '../tokens';
import { cardMotion } from '../motion';

// ─── Base Card ─────────────────────────────────────────────────────────────────

interface CardProps {
  children: React.ReactNode;
  className?: string;
  interactive?: boolean;
  elevated?: boolean;
  padding?: 'none' | 'sm' | 'md' | 'lg';
  onClick?: () => void;
}

const paddingMap = { none: '', sm: 'p-3', md: 'p-4', lg: 'p-5' };

export function Card({ children, className, interactive, elevated, padding = 'md', onClick }: CardProps) {
  const content = (
    <div
      onClick={onClick}
      className={cn(
        'rounded-2xl border border-monitor-border bg-monitor-card',
        elevated ? 'shadow-elevation-2' : 'shadow-elevation-1',
        interactive && 'cursor-pointer',
        paddingMap[padding],
        className
      )}
    >
      {children}
    </div>
  );

  if (!interactive) return content;

  return (
    <motion.div
      whileHover={{ y: -2, boxShadow: '0 8px 24px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06)' }}
      whileTap={{ scale: 0.99 }}
      transition={{ duration: 0.15, ease: [0, 0, 0.2, 1] }}
    >
      {content}
    </motion.div>
  );
}

// ─── Vital Card ────────────────────────────────────────────────────────────────

interface VitalCardDSProps {
  vital: VitalKey;
  value: string | number;
  unit: string;
  label?: string;
  secondary?: string;
  secondaryLabel?: string;
  trend?: 'up' | 'down' | 'stable';
  alarm?: 'normal' | 'warning' | 'critical';
  children?: React.ReactNode;
  className?: string;
}

export function VitalCardDS({
  vital, value, unit, label, secondary, secondaryLabel, trend = 'stable', alarm = 'normal', children, className,
}: VitalCardDSProps) {
  const v = vitals[vital];
  const isCritical = alarm === 'critical';
  const isWarning  = alarm === 'warning';

  return (
    <motion.div
      className={cn(
        'relative flex flex-col rounded-2xl border overflow-hidden',
        isCritical ? 'border-[rgba(255,59,48,0.45)] animate-glow-critical' :
        isWarning  ? 'border-[rgba(255,149,0,0.35)]  animate-glow-warning' :
                     'border-monitor-border',
        'bg-monitor-card',
        className
      )}
      whileHover={{ scale: 1.005 }}
      transition={{ duration: 0.15 }}
    >
      {/* Top accent */}
      <div className="absolute top-0 left-0 right-0 h-[2px]"
           style={{ background: v.color, opacity: isCritical ? 1 : 0.45 }} />

      {/* Glow overlay for critical */}
      {isCritical && (
        <div className="absolute inset-0 pointer-events-none rounded-2xl"
             style={{ background: `radial-gradient(ellipse at top, ${v.glow} 0%, transparent 65%)`, opacity: 0.5 }} />
      )}

      <div className="p-4 flex flex-col gap-2">
        {/* Header */}
        <div className="flex items-center justify-between">
          <span className="font-display text-vital-xs font-semibold uppercase tracking-[0.18em]"
                style={{ color: v.color, opacity: 0.72 }}>
            {label ?? v.fullLabel}
          </span>
          <span className="text-[11px]" style={{ color: v.dim }}>
            {trend === 'up' ? '↑' : trend === 'down' ? '↓' : '→'}
          </span>
        </div>

        {/* Value */}
        <div className="flex items-end gap-1.5 leading-none">
          <span
            className="font-mono"
            style={{
              color:   isCritical ? '#FF3B30' : isWarning ? '#FF9500' : v.color,
              fontSize: String(value).length > 5 ? '40px' : '56px',
              lineHeight: 1,
              textShadow: `0 0 24px ${v.color}55`,
            }}
          >
            {value}
          </span>
          <span className="font-display text-vital-sm mb-1 opacity-50" style={{ color: v.color }}>{unit}</span>
        </div>

        {/* Secondary */}
        {secondary && (
          <div className="font-mono text-vital-xs opacity-55" style={{ color: v.color }}>
            {secondaryLabel && <span className="font-display uppercase tracking-wider opacity-70 mr-1">{secondaryLabel}</span>}
            {secondary}
          </div>
        )}
      </div>

      {children}
    </motion.div>
  );
}

// ─── Stat Card ─────────────────────────────────────────────────────────────────

interface StatCardProps {
  label: string;
  value: string | number;
  unit?: string;
  delta?: number;
  color?: string;
  icon?: React.ReactNode;
  variant?: 'dark' | 'light';
  className?: string;
}

export function StatCard({ label, value, unit, delta, color = '#32ADE6', icon, variant = 'dark', className }: StatCardProps) {
  const isDark = variant === 'dark';

  return (
    <motion.div
      className={cn(
        'rounded-2xl border p-4',
        isDark
          ? 'bg-monitor-card border-monitor-border text-[#E8F1FF]'
          : 'bg-white border-slate-200 text-slate-900',
        className
      )}
      whileHover={{ y: -1, transition: { duration: 0.12 } }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <span className={cn('font-display text-vital-xs uppercase tracking-wider',
          isDark ? 'text-[#7A90AA]' : 'text-slate-500')}>{label}</span>
        {icon && (
          <span className="text-current opacity-60" style={{ color }}>{icon}</span>
        )}
      </div>
      <div className="flex items-end gap-1.5">
        <span className="font-mono text-vital-xl" style={{ color }}>{value}</span>
        {unit && <span className={cn('font-display text-vital-xs mb-1', isDark ? 'text-[#3D5570]' : 'text-slate-400')}>{unit}</span>}
      </div>
      {delta !== undefined && (
        <div className={cn(
          'mt-2 font-display text-vital-xs',
          delta > 0 ? 'text-[#FF4757]' : delta < 0 ? 'text-[#30D158]' : isDark ? 'text-[#3D5570]' : 'text-slate-400'
        )}>
          {delta > 0 ? '↑' : delta < 0 ? '↓' : '→'}
          {' '}{Math.abs(delta)} from baseline
        </div>
      )}
    </motion.div>
  );
}
