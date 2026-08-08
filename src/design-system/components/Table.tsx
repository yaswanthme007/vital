import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { SkeletonTableRow } from './Skeleton';
import { stagger, listItemVariants } from '../motion';

// ─── Types ─────────────────────────────────────────────────────────────────────

type SortDir  = 'asc' | 'desc' | null;
type Align    = 'left' | 'center' | 'right';

export interface Column<T extends Record<string, unknown>> {
  key:       keyof T | string;
  header:    string;
  width?:    number | string;
  sortable?: boolean;
  align?:    Align;
  render?:   (value: unknown, row: T, index: number) => React.ReactNode;
  className?: string;
}

interface TableProps<T extends Record<string, unknown>> {
  columns:     Column<T>[];
  data:        T[];
  loading?:    boolean;
  emptyState?: React.ReactNode;
  onRowClick?: (row: T, index: number) => void;
  rowKey?:     keyof T | ((row: T) => string);
  className?:  string;
  variant?:    'dark' | 'light';
  stickyHeader?: boolean;
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const alignClass: Record<Align, string> = {
  left:   'text-left',
  center: 'text-center',
  right:  'text-right',
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function Table<T extends Record<string, unknown>>({
  columns, data, loading, emptyState, onRowClick, rowKey = 'id',
  className, variant = 'dark', stickyHeader,
}: TableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>(null);

  const isDark = variant === 'dark';

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => d === 'asc' ? 'desc' : d === 'desc' ? null : 'asc');
      if (sortDir === 'desc') setSortKey(null);
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  const sortedData = sortKey && sortDir
    ? [...data].sort((a, b) => {
        const av = a[sortKey as keyof T];
        const bv = b[sortKey as keyof T];
        const cmp = typeof av === 'number' && typeof bv === 'number'
          ? av - bv
          : String(av ?? '').localeCompare(String(bv ?? ''));
        return sortDir === 'asc' ? cmp : -cmp;
      })
    : data;

  const getKey = (row: T, i: number): string =>
    typeof rowKey === 'function' ? rowKey(row) : (String(row[rowKey]) || String(i));

  return (
    <div
      className={cn(
        'overflow-hidden rounded-2xl border',
        isDark ? 'border-monitor-border bg-monitor-card' : 'border-slate-200 bg-white',
        className
      )}
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          {/* Head */}
          <thead
            className={cn(
              isDark ? 'bg-monitor-surface border-b border-monitor-border' : 'bg-slate-50 border-b border-slate-200',
              stickyHeader && 'sticky top-0 z-10'
            )}
          >
            <tr>
              {columns.map((col) => {
                const canSort  = col.sortable !== false;
                const isActive = sortKey === String(col.key);
                return (
                  <th
                    key={String(col.key)}
                    className={cn(
                      'px-4 py-2.5 font-display text-vital-xs font-semibold uppercase tracking-wider',
                      isDark ? 'text-[#7A90AA]' : 'text-slate-500',
                      alignClass[col.align ?? 'left'],
                      canSort && 'cursor-pointer select-none',
                      col.className
                    )}
                    style={{ width: col.width }}
                    onClick={() => canSort && handleSort(String(col.key))}
                  >
                    <div className={cn('inline-flex items-center gap-1', alignClass[col.align ?? 'left'])}>
                      {col.header}
                      {canSort && (
                        <motion.span
                          className={cn('flex-shrink-0', isDark ? 'text-[#3D5570]' : 'text-slate-400')}
                          animate={{ color: isActive ? '#32ADE6' : isDark ? '#3D5570' : '#94A3B8' }}
                        >
                          {isActive && sortDir === 'asc'  ? <ChevronUp size={12} /> :
                           isActive && sortDir === 'desc' ? <ChevronDown size={12} /> :
                           <ChevronsUpDown size={12} />}
                        </motion.span>
                      )}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>

          {/* Body */}
          <tbody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className={isDark ? 'border-b border-monitor-border' : 'border-b border-slate-100'}>
                  <td colSpan={columns.length} className="p-0">
                    <SkeletonTableRow cols={columns.length} className="border-none" />
                  </td>
                </tr>
              ))
            ) : sortedData.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>
                  {emptyState ?? (
                    <div className={cn(
                      'flex flex-col items-center justify-center py-12',
                      isDark ? 'text-[#3D5570]' : 'text-slate-300'
                    )}>
                      <span className="font-display text-vital-sm">No data</span>
                    </div>
                  )}
                </td>
              </tr>
            ) : (
              <AnimatePresence initial={false}>
                {sortedData.map((row, i) => (
                  <motion.tr
                    key={getKey(row, i)}
                    className={cn(
                      'border-b last:border-b-0 transition-colors duration-100',
                      isDark
                        ? 'border-monitor-border hover:bg-monitor-card-hover'
                        : 'border-slate-100 hover:bg-slate-50',
                      onRowClick && 'cursor-pointer'
                    )}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.15, delay: i * 0.03 }}
                    onClick={() => onRowClick?.(row, i)}
                  >
                    {columns.map((col) => {
                      const raw = row[col.key as keyof T];
                      return (
                        <td
                          key={String(col.key)}
                          className={cn(
                            'px-4 py-3 font-display text-vital-sm',
                            isDark ? 'text-[#E8F1FF]' : 'text-slate-700',
                            alignClass[col.align ?? 'left'],
                            col.className
                          )}
                        >
                          {col.render ? col.render(raw, row, i) : String(raw ?? '—')}
                        </td>
                      );
                    })}
                  </motion.tr>
                ))}
              </AnimatePresence>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
