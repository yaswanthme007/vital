import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { slideUpVariants } from '../motion';

// ─── Types ─────────────────────────────────────────────────────────────────────

interface EmptyStateProps {
  icon?:        React.ReactNode;
  title:        string;
  description?: string;
  action?:      React.ReactNode;
  size?:        'sm' | 'md' | 'lg';
  variant?:     'dark' | 'light';
  className?:   string;
}

// ─── Presets ───────────────────────────────────────────────────────────────────

const sizeMap = {
  sm: { icon: 'w-10 h-10', title: 'text-vital-base', desc: 'text-vital-xs', py: 'py-6' },
  md: { icon: 'w-14 h-14', title: 'text-vital-lg',   desc: 'text-vital-sm', py: 'py-10' },
  lg: { icon: 'w-20 h-20', title: 'text-vital-xl',   desc: 'text-vital-base', py: 'py-16' },
};

const variantMap = {
  dark: {
    iconWrap: 'bg-monitor-card border-monitor-border',
    icon:     'text-[#3D5570]',
    title:    'text-[#7A90AA]',
    desc:     'text-[#3D5570]',
  },
  light: {
    iconWrap: 'bg-slate-100 border-slate-200',
    icon:     'text-slate-300',
    title:    'text-slate-600',
    desc:     'text-slate-400',
  },
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function EmptyState({
  icon, title, description, action, size = 'md', variant = 'dark', className,
}: EmptyStateProps) {
  const s = sizeMap[size];
  const v = variantMap[variant];

  return (
    <motion.div
      className={cn('flex flex-col items-center justify-center text-center', s.py, className)}
      variants={slideUpVariants}
      initial="hidden"
      animate="visible"
    >
      {icon && (
        <motion.div
          className={cn(
            'flex items-center justify-center rounded-2xl border mb-4',
            s.icon, v.iconWrap, v.icon
          )}
          whileHover={{ scale: 1.05, rotate: 3 }}
          transition={{ duration: 0.2 }}
        >
          {icon}
        </motion.div>
      )}

      <h3 className={cn('font-display font-semibold mb-1.5', s.title, v.title)}>{title}</h3>

      {description && (
        <p className={cn('font-display leading-relaxed max-w-64 mb-5', s.desc, v.desc)}>
          {description}
        </p>
      )}

      {action && <div>{action}</div>}
    </motion.div>
  );
}

// ─── Preset empty states ───────────────────────────────────────────────────────

import { Activity, Search, Archive, Bell, Camera } from 'lucide-react';

export function NoDataEmpty({ onAction }: { onAction?: () => void }) {
  return (
    <EmptyState
      icon={<Activity size={24} />}
      title="No data recorded"
      description="Start a surgery session to begin capturing vital signs."
    />
  );
}

export function NoSearchResultsEmpty({ query }: { query: string }) {
  return (
    <EmptyState
      icon={<Search size={22} />}
      title="No results found"
      description={`Nothing matched "${query}". Try a different search term.`}
      size="sm"
    />
  );
}

export function NoSessionsEmpty() {
  return (
    <EmptyState
      icon={<Archive size={22} />}
      title="No archived sessions"
      description="Completed sessions will appear here once your first surgery is finished."
      size="md"
    />
  );
}

export function NoAlertsEmpty() {
  return (
    <EmptyState
      icon={<Bell size={18} />}
      title="No active alerts"
      description="All vitals within normal limits."
      size="sm"
    />
  );
}

export function NoCameraEmpty({ onSetup }: { onSetup?: () => void }) {
  return (
    <EmptyState
      icon={<Camera size={24} />}
      title="Camera not configured"
      description="Set up your capture camera in the Calibration screen."
      size="md"
    />
  );
}
