import { useState, useRef, useId } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';

// ─── Types ─────────────────────────────────────────────────────────────────────

type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right';
type TooltipVariant   = 'dark' | 'light' | 'vital';

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactElement;
  placement?: TooltipPlacement;
  delay?: number;
  variant?: TooltipVariant;
  maxWidth?: number;
  disabled?: boolean;
}

// ─── Placement styles ──────────────────────────────────────────────────────────

const placementConfig: Record<TooltipPlacement, {
  wrapper: string;
  initial: { x?: number; y?: number; scale: number };
}> = {
  top:    { wrapper: 'bottom-full left-1/2 -translate-x-1/2 mb-2',   initial: { y: 4,  scale: 0.92 } },
  bottom: { wrapper: 'top-full left-1/2 -translate-x-1/2 mt-2',      initial: { y: -4, scale: 0.92 } },
  left:   { wrapper: 'right-full top-1/2 -translate-y-1/2 mr-2',     initial: { x: 4,  scale: 0.92 } },
  right:  { wrapper: 'left-full top-1/2 -translate-y-1/2 ml-2',      initial: { x: -4, scale: 0.92 } },
};

const arrowConfig: Record<TooltipPlacement, string> = {
  top:    'left-1/2 -translate-x-1/2 top-full border-t-[#1E3A56] border-x-transparent border-b-transparent',
  bottom: 'left-1/2 -translate-x-1/2 bottom-full border-b-[#1E3A56] border-x-transparent border-t-transparent',
  left:   'top-1/2 -translate-y-1/2 left-full border-l-[#1E3A56] border-y-transparent border-r-transparent',
  right:  'top-1/2 -translate-y-1/2 right-full border-r-[#1E3A56] border-y-transparent border-l-transparent',
};

const variantStyles: Record<TooltipVariant, { bg: string; border: string; text: string }> = {
  dark:  { bg: 'bg-monitor-card',  border: 'border-monitor-border-bright', text: 'text-[#E8F1FF]' },
  light: { bg: 'bg-white',         border: 'border-slate-200',             text: 'text-slate-800' },
  vital: { bg: 'bg-monitor-surface', border: 'border-[#32ADE6]/30',        text: 'text-[#5AC8FA]' },
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function Tooltip({
  content, children, placement = 'top', delay = 250, variant = 'dark', maxWidth = 220, disabled,
}: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const timerRef  = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const tooltipId = useId();
  const cfg = placementConfig[placement];
  const v   = variantStyles[variant];

  const show = () => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setVisible(true), delay);
  };
  const hide = () => {
    clearTimeout(timerRef.current);
    setVisible(false);
  };

  if (disabled) return children;

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      <AnimatePresence>
        {visible && (
          <motion.div
            role="tooltip"
            id={tooltipId}
            className={cn('absolute z-50 pointer-events-none', cfg.wrapper)}
            style={{ maxWidth }}
            initial={{ opacity: 0, ...cfg.initial }}
            animate={{ opacity: 1, x: 0, y: 0, scale: 1 }}
            exit={{ opacity: 0, ...cfg.initial }}
            transition={{ duration: 0.14, ease: [0.16, 1, 0.3, 1] }}
          >
            <div
              className={cn(
                'rounded-ds-lg border px-2.5 py-1.5 shadow-elevation-3',
                'font-display text-vital-xs leading-snug whitespace-normal text-center',
                v.bg, v.border, v.text
              )}
            >
              {content}
            </div>
            {/* Arrow */}
            <div
              className={cn('absolute w-0 h-0 border-4', arrowConfig[placement])}
              aria-hidden="true"
            />
          </motion.div>
        )}
      </AnimatePresence>
    </span>
  );
}
