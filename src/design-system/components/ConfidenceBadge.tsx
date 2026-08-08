import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';

// ─── Types ─────────────────────────────────────────────────────────────────────

interface ConfidenceBadgeProps {
  value: number;        // 0–100
  showBar?: boolean;
  showLabel?: boolean;
  size?: 'sm' | 'md';
  className?: string;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function getLevel(v: number): { label: string; color: string; trackColor: string; bg: string; border: string } {
  if (v >= 95) return {
    label: 'High Conf.',
    color: '#30D158',
    trackColor: 'rgba(48,209,88,0.15)',
    bg:    'rgba(48,209,88,0.08)',
    border:'rgba(48,209,88,0.3)',
  };
  if (v >= 80) return {
    label: 'Med. Conf.',
    color: '#FFD600',
    trackColor: 'rgba(255,214,0,0.15)',
    bg:    'rgba(255,214,0,0.08)',
    border:'rgba(255,214,0,0.3)',
  };
  return {
    label: 'Low Conf.',
    color: '#FF3B30',
    trackColor: 'rgba(255,59,48,0.15)',
    bg:    'rgba(255,59,48,0.08)',
    border:'rgba(255,59,48,0.3)',
  };
}

// ─── Component ─────────────────────────────────────────────────────────────────

export function ConfidenceBadge({
  value,
  showBar = true,
  showLabel = false,
  size = 'sm',
  className,
}: ConfidenceBadgeProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const { label, color, trackColor, bg, border } = getLevel(clamped);
  const isSmall = size === 'sm';

  return (
    <div
      className={cn(
        'inline-flex flex-col gap-1 rounded-lg border font-display',
        isSmall ? 'px-2 py-1' : 'px-2.5 py-1.5',
        className
      )}
      style={{ background: bg, borderColor: border }}
      title={`CV Detection Confidence: ${clamped}%`}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn('font-mono font-normal tabular-nums', isSmall ? 'text-[11px]' : 'text-xs')}
          style={{ color }}
        >
          <AnimatePresence mode="wait">
            <motion.span
              key={clamped}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={{ duration: 0.15 }}
            >
              {clamped}%
            </motion.span>
          </AnimatePresence>
        </span>
        {showLabel && (
          <span
            className="text-[9px] uppercase tracking-wider opacity-70"
            style={{ color }}
          >
            {label}
          </span>
        )}
      </div>

      {showBar && (
        <div
          className="h-0.5 rounded-full overflow-hidden"
          style={{ background: trackColor }}
        >
          <motion.div
            className="h-full rounded-full"
            style={{ backgroundColor: color }}
            initial={{ width: 0 }}
            animate={{ width: `${clamped}%` }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
      )}
    </div>
  );
}

// ─── Inline version (for table cells etc.) ────────────────────────────────────

export function ConfidencePill({ value, className }: { value: number; className?: string }) {
  const { color, bg, border } = getLevel(value);
  return (
    <span
      className={cn('inline-flex items-center gap-1 px-1.5 py-0.5 rounded font-mono text-[10px]', className)}
      style={{ background: bg, borderColor: border, color, border: `1px solid ${border}` }}
    >
      {value}%
    </span>
  );
}
