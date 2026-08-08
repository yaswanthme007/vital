import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

// ─── Linear Progress ───────────────────────────────────────────────────────────

interface ProgressBarProps {
  value?: number;           // 0–100, undefined = indeterminate
  color?: string;
  trackColor?: string;
  size?: 'xs' | 'sm' | 'md' | 'lg';
  label?: string;
  showValue?: boolean;
  striped?: boolean;
  animated?: boolean;
  className?: string;
}

const barHeight = { xs: 'h-1', sm: 'h-1.5', md: 'h-2', lg: 'h-3' };

export function ProgressBar({
  value, color = '#32ADE6', trackColor = 'rgba(24,43,66,1)',
  size = 'md', label, showValue, striped, animated, className,
}: ProgressBarProps) {
  const isIndeterminate = value === undefined;

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {(label !== undefined || showValue) && (
        <div className="flex items-center justify-between">
          {label && <span className="font-display text-vital-xs text-[#7A90AA] uppercase tracking-wider">{label}</span>}
          {showValue && !isIndeterminate && (
            <span className="font-mono text-vital-xs" style={{ color }}>{Math.round(value ?? 0)}%</span>
          )}
        </div>
      )}
      <div
        className={cn('relative w-full rounded-full overflow-hidden', barHeight[size])}
        style={{ backgroundColor: trackColor }}
        role="progressbar"
        aria-valuenow={isIndeterminate ? undefined : value}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        {isIndeterminate ? (
          <div
            className={cn('absolute inset-y-0 rounded-full w-[45%] animate-progress-indeterminate')}
            style={{ backgroundColor: color }}
          />
        ) : (
          <motion.div
            className={cn('h-full rounded-full')}
            style={{
              backgroundColor: color,
              backgroundImage: striped
                ? 'linear-gradient(45deg, rgba(255,255,255,0.12) 25%, transparent 25%, transparent 50%, rgba(255,255,255,0.12) 50%, rgba(255,255,255,0.12) 75%, transparent 75%)'
                : undefined,
              backgroundSize: striped ? '1rem 1rem' : undefined,
              animation: striped && animated ? 'skeleton-wave 1s linear infinite' : undefined,
            }}
            initial={{ width: 0 }}
            animate={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          />
        )}
      </div>
    </div>
  );
}

// ─── Circular Progress ─────────────────────────────────────────────────────────

interface CircularProgressProps {
  value?: number;
  size?: number;
  strokeWidth?: number;
  color?: string;
  trackColor?: string;
  label?: React.ReactNode;
  className?: string;
}

export function CircularProgress({
  value, size = 56, strokeWidth = 4,
  color = '#32ADE6', trackColor = 'rgba(24,43,66,1)',
  label, className,
}: CircularProgressProps) {
  const r   = (size - strokeWidth) / 2;
  const circ = 2 * Math.PI * r;
  const offset = value !== undefined ? circ * (1 - Math.max(0, Math.min(100, value)) / 100) : 0;

  return (
    <div className={cn('relative inline-flex items-center justify-center', className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} fill="none" className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke={trackColor} strokeWidth={strokeWidth} />
        {value !== undefined ? (
          <motion.circle
            cx={size / 2} cy={size / 2} r={r}
            stroke={color} strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circ}
            initial={{ strokeDashoffset: circ }}
            animate={{ strokeDashoffset: offset }}
            transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          />
        ) : (
          <circle
            cx={size / 2} cy={size / 2} r={r}
            stroke={color} strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={`${circ * 0.3} ${circ * 0.7}`}
            className="origin-center animate-spin-slow"
          />
        )}
      </svg>
      {label && (
        <div className="absolute inset-0 flex items-center justify-center">
          {label}
        </div>
      )}
    </div>
  );
}

// ─── Step Progress ─────────────────────────────────────────────────────────────

interface StepProgressProps {
  steps:   string[];
  current: number;
  color?:  string;
  className?: string;
}

export function StepProgress({ steps, current, color = '#32ADE6', className }: StepProgressProps) {
  return (
    <div className={cn('flex items-center', className)}>
      {steps.map((step, i) => {
        const done   = i < current;
        const active = i === current;
        return (
          <div key={step} className="flex items-center flex-1 last:flex-none">
            <div className="flex flex-col items-center gap-1.5">
              <motion.div
                className={cn(
                  'w-6 h-6 rounded-full border-2 flex items-center justify-center',
                  'font-display text-[10px] font-bold transition-all duration-300',
                )}
                style={{
                  borderColor: done || active ? color : '#182B42',
                  backgroundColor: done ? color : active ? `${color}18` : 'transparent',
                  color: done ? '#070D1A' : active ? color : '#3D5570',
                  boxShadow: active ? `0 0 0 3px ${color}25` : 'none',
                }}
                animate={active ? { scale: [1, 1.08, 1] } : {}}
                transition={{ duration: 0.4, repeat: 0 }}
              >
                {done ? '✓' : i + 1}
              </motion.div>
              <span
                className="font-display text-vital-xs whitespace-nowrap"
                style={{ color: active ? color : done ? '#7A90AA' : '#3D5570' }}
              >
                {step}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className="flex-1 h-px mx-2 mb-5 relative overflow-hidden" style={{ backgroundColor: '#182B42' }}>
                <motion.div
                  className="absolute inset-y-0 left-0"
                  style={{ backgroundColor: color }}
                  animate={{ width: i < current ? '100%' : '0%' }}
                  transition={{ duration: 0.4, delay: 0.1 }}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
