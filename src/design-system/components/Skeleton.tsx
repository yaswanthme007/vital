import { cn } from '@/lib/utils';

// ─── Base Skeleton ─────────────────────────────────────────────────────────────

interface SkeletonProps {
  className?: string;
  rounded?: 'sm' | 'md' | 'lg' | 'full';
}

const roundedMap = { sm: 'rounded-md', md: 'rounded-lg', lg: 'rounded-xl', full: 'rounded-full' };

export function Skeleton({ className, rounded = 'md' }: SkeletonProps) {
  return (
    <div
      className={cn(
        'relative overflow-hidden bg-monitor-border',
        roundedMap[rounded],
        className
      )}
      style={{
        background: 'linear-gradient(90deg, #182B42 25%, #1E3A56 50%, #182B42 75%)',
        backgroundSize: '800px 100%',
        animation: 'skeleton-wave 1.6s ease-in-out infinite',
      }}
      aria-hidden="true"
    />
  );
}

// ─── Text skeleton ─────────────────────────────────────────────────────────────

export function SkeletonText({ lines = 2, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn('h-3', i === lines - 1 && lines > 1 ? 'w-3/4' : 'w-full')}
        />
      ))}
    </div>
  );
}

// ─── Vital card skeleton ───────────────────────────────────────────────────────

export function SkeletonVitalCard({ className }: { className?: string }) {
  return (
    <div className={cn('rounded-2xl border border-monitor-border bg-monitor-card p-4 space-y-3', className)}>
      <div className="flex items-center justify-between">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-3 w-8" />
      </div>
      <Skeleton className="h-14 w-28" rounded="lg" />
      <Skeleton className="h-2.5 w-20" />
    </div>
  );
}

// ─── Table row skeleton ────────────────────────────────────────────────────────

export function SkeletonTableRow({ cols = 5, className }: { cols?: number; className?: string }) {
  return (
    <div className={cn('flex items-center gap-4 px-4 py-3 border-b border-monitor-border', className)}>
      {Array.from({ length: cols }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn('h-3 flex-1', i === 0 ? 'max-w-[80px]' : i === cols - 1 ? 'max-w-[60px]' : '')}
        />
      ))}
    </div>
  );
}

// ─── Card skeleton ─────────────────────────────────────────────────────────────

export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={cn('rounded-2xl border border-monitor-border bg-monitor-card p-5 space-y-4', className)}>
      <div className="flex items-center gap-3">
        <Skeleton className="h-9 w-9 flex-shrink-0" rounded="lg" />
        <div className="flex-1 space-y-1.5">
          <Skeleton className="h-3.5 w-32" />
          <Skeleton className="h-2.5 w-48" />
        </div>
      </div>
      <SkeletonText lines={3} />
      <div className="flex gap-2 pt-1">
        <Skeleton className="h-8 w-20" rounded="lg" />
        <Skeleton className="h-8 w-16" rounded="lg" />
      </div>
    </div>
  );
}

// ─── Timeline skeleton ─────────────────────────────────────────────────────────

export function SkeletonTimeline({ items = 4, className }: { items?: number; className?: string }) {
  return (
    <div className={cn('space-y-0', className)}>
      {Array.from({ length: items }).map((_, i) => (
        <div key={i} className="flex gap-4 pb-6 last:pb-0">
          <div className="flex flex-col items-center">
            <Skeleton className="w-3 h-3 flex-shrink-0" rounded="full" />
            {i < items - 1 && <div className="w-px flex-1 mt-2 bg-monitor-border" />}
          </div>
          <div className="flex-1 pt-0 space-y-2 pb-1">
            <div className="flex items-center gap-3">
              <Skeleton className="h-2.5 w-16" />
              <Skeleton className="h-5 w-12" rounded="sm" />
            </div>
            <Skeleton className="h-3 w-3/4" />
            <Skeleton className="h-2.5 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}
