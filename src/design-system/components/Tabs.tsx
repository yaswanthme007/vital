import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';

// ─── Types ─────────────────────────────────────────────────────────────────────

type TabsVariant = 'underline' | 'pill' | 'boxed';

export interface TabItem {
  id:       string;
  label:    string;
  icon?:    React.ReactNode;
  badge?:   number | string;
  disabled?: boolean;
  content?: React.ReactNode;
}

interface TabsProps {
  items:     TabItem[];
  value?:    string;
  onChange?: (id: string) => void;
  variant?:  TabsVariant;
  size?:     'sm' | 'md';
  fullWidth?: boolean;
  className?: string;
  contentClassName?: string;
}

// ─── Styles by variant ─────────────────────────────────────────────────────────

const containerStyles: Record<TabsVariant, string> = {
  underline: 'border-b border-monitor-border',
  pill:      'bg-monitor-card rounded-ds-xl p-1 gap-0.5',
  boxed:     'border-b border-monitor-border',
};

const tabBase = 'relative inline-flex items-center gap-2 font-display font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#32ADE6] focus-visible:ring-offset-2 focus-visible:ring-offset-monitor-bg disabled:opacity-40 disabled:pointer-events-none select-none';

const tabStyles: Record<TabsVariant, { active: string; inactive: string; size: Record<string, string> }> = {
  underline: {
    active:   'text-[#E8F1FF]',
    inactive: 'text-[#7A90AA] hover:text-[#B0C4D8]',
    size:     { sm: 'px-3 py-2 text-vital-xs', md: 'px-4 py-2.5 text-vital-sm' },
  },
  pill: {
    active:   'text-[#E8F1FF] bg-monitor-border-bright rounded-ds-lg',
    inactive: 'text-[#7A90AA] hover:text-[#B0C4D8] rounded-ds-lg',
    size:     { sm: 'px-3 py-1.5 text-vital-xs', md: 'px-4 py-2 text-vital-sm' },
  },
  boxed: {
    active:   'text-[#E8F1FF] bg-monitor-card rounded-t-ds-lg border border-b-0 border-monitor-border',
    inactive: 'text-[#7A90AA] hover:text-[#B0C4D8]',
    size:     { sm: 'px-3 py-2 text-vital-xs', md: 'px-4 py-2.5 text-vital-sm' },
  },
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function Tabs({
  items, value: controlled, onChange, variant = 'underline',
  size = 'md', fullWidth, className, contentClassName,
}: TabsProps) {
  const [internal, setInternal] = useState(items[0]?.id ?? '');
  const active = controlled ?? internal;

  const setActive = (id: string) => {
    if (controlled === undefined) setInternal(id);
    onChange?.(id);
  };

  const s  = tabStyles[variant];
  const sz = s.size[size];
  const activeItem = items.find((t) => t.id === active);

  return (
    <div className={cn('flex flex-col', className)}>
      {/* Tab list */}
      <div
        role="tablist"
        className={cn(
          'flex',
          fullWidth ? 'w-full' : 'w-fit',
          containerStyles[variant]
        )}
      >
        {items.map((item) => {
          const isActive = item.id === active;
          return (
            <button
              key={item.id}
              role="tab"
              aria-selected={isActive}
              aria-controls={`tabpanel-${item.id}`}
              id={`tab-${item.id}`}
              disabled={item.disabled}
              onClick={() => setActive(item.id)}
              className={cn(
                tabBase, sz,
                fullWidth && 'flex-1 justify-center',
                isActive ? s.active : s.inactive
              )}
            >
              {item.icon && <span className="text-current opacity-80">{item.icon}</span>}
              {item.label}
              {item.badge !== undefined && (
                <span className={cn(
                  'inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full font-display text-[9px] font-bold',
                  isActive
                    ? 'bg-[#32ADE6] text-white'
                    : 'bg-monitor-border text-[#7A90AA]'
                )}>
                  {item.badge}
                </span>
              )}

              {/* Underline indicator */}
              {variant === 'underline' && isActive && (
                <motion.div
                  className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#32ADE6] rounded-full"
                  layoutId="tab-underline"
                  transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
                />
              )}
              {/* Pill indicator */}
              {variant === 'pill' && isActive && (
                <motion.div
                  className="absolute inset-0 bg-monitor-border-bright rounded-ds-lg -z-10"
                  layoutId="tab-pill"
                  transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {activeItem?.content !== undefined && (
        <AnimatePresence mode="wait">
          <motion.div
            key={active}
            role="tabpanel"
            id={`tabpanel-${active}`}
            aria-labelledby={`tab-${active}`}
            className={contentClassName}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18 }}
          >
            {activeItem.content}
          </motion.div>
        </AnimatePresence>
      )}
    </div>
  );
}
