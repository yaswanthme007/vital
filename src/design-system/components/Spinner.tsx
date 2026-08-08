import { cn } from '@/lib/utils';

type Size    = 'xs' | 'sm' | 'md' | 'lg' | 'xl';
type Variant = 'default' | 'clinical' | 'vital';

interface SpinnerProps {
  size?: Size;
  variant?: Variant;
  color?: string;
  label?: string;
  className?: string;
}

const sizeMap: Record<Size, { ring: string; track: string; label: string }> = {
  xs: { ring: 'w-3 h-3 border',     track: 'w-3 h-3 border',     label: 'text-[10px]' },
  sm: { ring: 'w-4 h-4 border',     track: 'w-4 h-4 border',     label: 'text-[11px]' },
  md: { ring: 'w-5 h-5 border-2',   track: 'w-5 h-5 border-2',   label: 'text-[12px]' },
  lg: { ring: 'w-7 h-7 border-2',   track: 'w-7 h-7 border-2',   label: 'text-[13px]' },
  xl: { ring: 'w-10 h-10 border-[3px]', track: 'w-10 h-10 border-[3px]', label: 'text-sm' },
};

export function Spinner({ size = 'md', variant = 'default', color, label, className }: SpinnerProps) {
  const { ring, label: labelSize } = sizeMap[size];

  const trackColor = 'border-white/10';
  const spinColor  = color
    ? undefined
    : variant === 'clinical' ? 'border-t-[#32ADE6]'
    : variant === 'vital'    ? 'border-t-[#00FF88]'
    :                          'border-t-[#7A90AA]';

  return (
    <span className={cn('inline-flex flex-col items-center gap-1.5', className)}>
      <span className="relative inline-block">
        {/* Track */}
        <span
          className={cn('block rounded-full', ring, trackColor)}
          aria-hidden="true"
        />
        {/* Spin */}
        <span
          className={cn(
            'absolute inset-0 block rounded-full animate-spin',
            ring,
            'border-transparent',
            spinColor
          )}
          style={color ? { borderTopColor: color } : undefined}
          aria-hidden="true"
        />
      </span>
      {label && (
        <span className={cn('font-display text-[#7A90AA]', labelSize)}>{label}</span>
      )}
    </span>
  );
}

// Full-page loading overlay
export function LoadingOverlay({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-monitor-bg/80 backdrop-blur-sm z-50">
      <Spinner size="lg" variant="clinical" label={label} />
    </div>
  );
}
