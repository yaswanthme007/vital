import { memo, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { TrendingUp, TrendingDown, Minus, Activity, CheckCircle2, PauseCircle, Search } from 'lucide-react';
import { cn, clamp, formatTime } from '@/lib/utils';
import type { AlarmSeverity, FieldStatus } from '@/types/vitals';
import { semanticUi } from '@/design-system/tokens';

// ─── Types ────────────────────────────────────────────────────────────────────

interface VitalCardProps {
  label:           string;
  value:           number | string;
  unit:            string;
  color:           string;
  glowColor:       string;
  secondaryValue?: string;
  secondaryLabel?: string;
  alarmStatus?:    AlarmSeverity;
  trend?:          'up' | 'down' | 'stable';
  limits?:         { high?: number; low?: number };
  confidence?:     number;
  actionLabel?:    string;
  onAction?:       () => void;
  actionBusy?:     boolean;
  className?:      string;
  // M5.7. 'confirmed' | 'held' | 'baseline' | undefined -- straight off
  // backend/app/validation/reconcile.py's field_status (via the WS 'reading'
  // envelope). undefined means no field-level status is known for this
  // vital at all (Demo Mode, or before the first real frame): renders
  // nothing, never a fabricated status. 'held'/'baseline' both render as
  // the same visible "Held" state -- the operator-facing distinction that
  // matters is "genuinely observed just now" vs "not", not WHY it wasn't.
  fieldStatus?:    FieldStatus;
  // Epoch ms of the last tick this field was GENUINELY confirmed (straight
  // off the WS envelope's lastConfirmedAt) -- what a held card's caption
  // reads. Present even while fieldStatus is 'held', since reconcile()
  // never forgets the last real confirmation.
  lastConfirmedAt?: number;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function valueFontSize(val: string): number {
  const n = val.length;
  if (n <= 2) return 52;
  if (n <= 3) return 44;
  if (n <= 4) return 36;
  if (n <= 5) return 30;
  return 24;
}

function confidenceColor(c: number) {
  if (c >= 90) return semanticUi.ok;
  if (c >= 75) return semanticUi.warning;
  return semanticUi.critical;
}

// ─── Component ────────────────────────────────────────────────────────────────

export const VitalCard = memo(function VitalCard({
  label, value, unit, color, glowColor,
  secondaryValue, secondaryLabel,
  alarmStatus = 'normal', trend = 'stable',
  limits, confidence, actionLabel, onAction, actionBusy,
  className, fieldStatus, lastConfirmedAt,
}: VitalCardProps) {
  const prevRef   = useRef<number | string>(value);
  const [display, setDisplay] = useState(value);
  const [flipKey,  setFlipKey] = useState(0);

  useEffect(() => {
    if (String(value) !== String(prevRef.current)) {
      prevRef.current = value;
      setDisplay(value);
      setFlipKey(k => k + 1);
    }
  }, [value]);

  const isCritical = alarmStatus === 'critical';
  const isWarning  = alarmStatus === 'warning';

  const valueColor = isCritical ? semanticUi.critical : isWarning ? semanticUi.warning : color;
  const displayStr = String(display);
  const fontSize   = valueFontSize(displayStr);

  const TrendIcon  = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : Minus;
  const trendColor = trend === 'stable' ? '#CBD5E1' : trend === 'up' ? semanticUi.warning : '#0284C7';

  const cardBg     = isCritical ? '#FEF2F2' : isWarning ? '#FFFBEB' : '#FFFFFF';
  const borderClr  = isCritical ? '#FECACA' : isWarning ? '#FDE68A' : '#E2E8F0';

  return (
    <div
      className={cn(
        'relative flex flex-col overflow-hidden select-none transition-all duration-300',
        isCritical ? 'animate-glow-critical' : '',
        className,
      )}
      style={{
        background:  cardBg,
        borderBottom: `1px solid ${borderClr}`,
        borderRight:  '1px solid #F1F5F9',
      }}
      role="region"
      aria-label={`${label}: ${displayStr} ${unit}`}
      aria-live={isCritical ? 'assertive' : 'polite'}
      aria-atomic="true"
    >
      {/* Colored top accent strip */}
      <div
        className="absolute top-0 left-0 right-0 z-10"
        aria-hidden="true"
        style={{
          height: 3,
          backgroundColor: valueColor,
          opacity: isCritical ? 1 : isWarning ? 0.9 : 0.7,
          boxShadow: isCritical ? `0 2px 8px ${valueColor}50` : 'none',
          transition: 'all 0.3s',
        }}
      />

      {/* Critical background glow */}
      {(isCritical || isWarning) && (
        <div
          className="absolute inset-0 pointer-events-none"
          aria-hidden="true"
          style={{
            background: `radial-gradient(ellipse at top, ${valueColor}10 0%, transparent 65%)`,
          }}
        />
      )}

      {/* Row 1 — label + trend */}
      <div className="flex items-center justify-between px-4 pt-4">
        <span
          className="font-display text-[10px] font-bold uppercase tracking-[0.18em]"
          style={{ color: '#94A3B8' }}
        >
          {label}
        </span>
        <TrendIcon
          size={12}
          style={{ color: trendColor }}
          aria-label={`Trend: ${trend}`}
        />
      </div>

      {/* Row 2 — main value */}
      <div className="flex-1 flex items-center px-4 min-h-0">
        <AnimatePresence mode="wait">
          <motion.span
            key={flipKey}
            className={cn('font-mono leading-none tabular-nums', isCritical ? 'animate-pulse-critical' : '')}
            style={{
              fontSize,
              color: valueColor,
              fontWeight: 700,
              transition: 'color 0.3s',
            }}
            initial={{ opacity: 0.4, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
          >
            {displayStr}
          </motion.span>
        </AnimatePresence>
      </div>

      {/* Row 3 — unit + secondary */}
      <div className="flex items-end justify-between px-4 pb-1">
        <span
          className="font-display text-[11px] uppercase tracking-wider font-semibold"
          style={{ color: valueColor, opacity: 0.7 }}
        >
          {unit}
        </span>
        {secondaryValue && (
          <span className="font-mono text-[11px] text-slate-500">
            {secondaryLabel && (
              <span className="font-display text-[9px] uppercase tracking-wider mr-1 text-slate-400">{secondaryLabel}</span>
            )}
            {secondaryValue}
          </span>
        )}
      </div>

      {/* M5.7: confirmed/held status -- never omitted in favour of styling a
          held value as fresh (the core product requirement this exists
          for). Absent entirely (not even the container div) when
          fieldStatus is undefined, e.g. Demo Mode. */}
      {fieldStatus && (
        <div className="px-4 pb-1.5">
          {fieldStatus === 'confirmed' ? (
            <span className="flex items-center gap-1 font-display text-[9px] font-semibold" style={{ color: semanticUi.ok }}>
              <CheckCircle2 size={9} />
              Confirmed{lastConfirmedAt ? ` ${formatTime(lastConfirmedAt)}` : ''}
            </span>
          ) : fieldStatus === 'unknown' ? (
            /* M5.8: nothing has ever been observed for this field. It must
               NOT read "Held" -- there is no earlier reading being held, and
               saying so is exactly the fabrication this milestone removed
               (the card used to show a seeded HR 75 / Temp 36.8 / RR 14 as
               "Held · last confirmed hh:mm:ss"). */
            <span className="flex items-center gap-1 font-display text-[9px] font-semibold text-slate-400">
              <Search size={9} />
              Waiting for camera
            </span>
          ) : (
            <span className="flex items-center gap-1 font-display text-[9px] font-semibold text-amber-600">
              <PauseCircle size={9} />
              Held{lastConfirmedAt ? ` · last confirmed ${formatTime(lastConfirmedAt)}` : ''}
            </span>
          )}
        </div>
      )}

      {/* Action button (NIBP measure) */}
      {onAction && (
        <div className="px-3 pb-2.5">
          <motion.button
            onClick={onAction}
            disabled={actionBusy}
            aria-label={actionBusy ? 'Measuring blood pressure' : `${actionLabel ?? 'Measure'} blood pressure`}
            aria-busy={actionBusy}
            className="w-full flex items-center justify-center gap-1.5 py-1 rounded-lg font-display text-[9px] font-bold uppercase tracking-widest transition-colors"
            style={{
              backgroundColor: actionBusy ? `${color}10` : `${color}0C`,
              border: `1px solid ${color}30`,
              color: actionBusy ? `${color}70` : color,
            }}
            whileHover={actionBusy ? {} : { backgroundColor: `${color}18` }}
            whileTap={actionBusy ? {} : { scale: 0.98 }}
          >
            {actionBusy ? (
              <>
                <motion.span animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1, ease: 'linear' }} aria-hidden="true">
                  <Activity size={9} />
                </motion.span>
                Measuring…
              </>
            ) : (actionLabel ?? 'Measure')}
          </motion.button>
        </div>
      )}

      {/* AI Confidence bar */}
      {confidence !== undefined && (
        <div className="px-4 pb-2" aria-label={`AI confidence ${Math.round(confidence)}%`}>
          <div className="flex items-center justify-between mb-1">
            <span className="font-display text-[8px] uppercase tracking-[0.2em] text-slate-300" aria-hidden="true">AI</span>
            <span className="font-mono text-[8px]" style={{ color: confidenceColor(confidence) }} aria-hidden="true">
              {Math.round(confidence)}%
            </span>
          </div>
          <div
            className="h-0.5 rounded-full overflow-hidden"
            role="progressbar"
            aria-valuenow={Math.round(confidence)}
            aria-valuemin={0}
            aria-valuemax={100}
            style={{ backgroundColor: '#F1F5F9' }}
          >
            <motion.div
              className="h-full rounded-full"
              style={{ backgroundColor: confidenceColor(confidence) }}
              animate={{ width: `${clamp(confidence, 0, 100)}%` }}
              transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
        </div>
      )}

      {/* Limit ticks */}
      {limits && (
        <div className="px-4 pb-2 flex justify-between" aria-hidden="true">
          {limits.low  !== undefined && <span className="font-mono text-[8px] text-slate-300">▼{limits.low}</span>}
          {limits.high !== undefined && <span className="font-mono text-[8px] text-slate-300">▲{limits.high}</span>}
        </div>
      )}
    </div>
  );
});
