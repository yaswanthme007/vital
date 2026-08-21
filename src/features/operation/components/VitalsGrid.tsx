import { useVitalsStore } from '@/store/vitalsStore';
import { VitalCard } from '@/components/vitals/VitalCard';
import { getAlarmSeverity } from '@/types/vitals';
import type { FieldStatus, VitalField } from '@/types/vitals';
import { vitalsUi } from '@/design-system/tokens';

// M5.7: NIBP's card represents 3 underlying fields (systolic/diastolic/
// mean) that each carry their own confirmed/held status. The card shows
// ONE status: 'confirmed' if any of the three was genuinely confirmed this
// tick (a real NIBP measurement just landed), else 'held' -- paired with
// the MOST RECENT of the three lastConfirmedAt timestamps, so "last
// confirmed" always reflects the freshest of the group, not an arbitrarily
// chosen one.
function nibpStatus(
  fieldStatus: Partial<Record<VitalField, FieldStatus>> | null,
  lastConfirmedAt: Partial<Record<VitalField, number>> | null,
): { status: FieldStatus | undefined; at: number | undefined } {
  if (!fieldStatus) return { status: undefined, at: undefined };
  const fields: VitalField[] = ['nibpSystolic', 'nibpDiastolic', 'nibpMean'];
  // M5.8: 'unknown' wins when NOTHING in the group has ever been confirmed,
  // so the card says "waiting for the camera" instead of "held" over a
  // reading that never existed.
  const status: FieldStatus = fields.some((f) => fieldStatus[f] === 'confirmed')
    ? 'confirmed'
    : fields.every((f) => fieldStatus[f] === 'unknown')
      ? 'unknown'
      : 'held';
  const at = Math.max(...fields.map((f) => lastConfirmedAt?.[f] ?? 0)) || undefined;
  return { status, at };
}

interface ConfMap { hr: number; spo2: number; nibp: number; etco2: number; temp: number; rr: number }

// ─── Trend helper ─────────────────────────────────────────────────────────────

type TrendDir = 'up' | 'down' | 'stable';

function useTrend(history: ReturnType<typeof useVitalsStore.getState>['history']) {
  return (key: 'hr' | 'spo2' | 'etco2' | 'temp' | 'rr', threshold = 2): TrendDir => {
    // M5.8: compare the oldest and newest readings that actually HAVE this
    // field. A camera session's early history is full of nulls for a field
    // the camera hasn't confirmed yet; treating those as 0 produced a
    // fabricated "trend" arrow out of missing data.
    const observed = history.slice(-8).map((r) => r[key]).filter((v): v is number => v != null);
    if (observed.length < 5) return 'stable';
    const diff  = observed[observed.length - 1] - observed[0];
    const t     = key === 'temp' ? 0.15 : threshold;
    if (diff >  t) return 'up';
    if (diff < -t) return 'down';
    return 'stable';
  };
}

// ─── Component ────────────────────────────────────────────────────────────────

export function VitalsGrid() {
  const current           = useVitalsStore(s => s.current);
  const history           = useVitalsStore(s => s.history);
  const limits            = useVitalsStore(s => s.alarmLimits);
  const nibpAt             = useVitalsStore(s => s.nibpLastMeasuredAt);
  const nibpMeasuring     = useVitalsStore(s => s.nibpMeasuring);
  const triggerNibp       = useVitalsStore(s => s.triggerNibpMeasurement);
  const realConfidence    = useVitalsStore(s => s.currentConfidence);
  const fieldStatus       = useVitalsStore(s => s.fieldStatus);
  const lastConfirmedAt   = useVitalsStore(s => s.lastConfirmedAt);
  const nibp               = nibpStatus(fieldStatus, lastConfirmedAt);

  // The real per-vital confidence off the WS 'reading' envelope. `undefined`
  // (before the first real frame lands) hides each card's confidence bar
  // rather than showing a fabricated number.
  const conf: Partial<ConfMap> = realConfidence ?? {};
  const trend = useTrend(history);
  const v     = current;

  // Nibp last measured label
  const nibpSubLabel = nibpAt
    ? `${Math.round((Date.now() - nibpAt) / 60000)}m ago`
    : undefined;

  const cardBase: React.CSSProperties = { borderTop: 'none', borderLeft: 'none', borderRight: 'none' };

  return (
    <div className="flex flex-col h-full bg-white">
      {/* ── HR ──────────────────────────────────────────────────────────── */}
      <VitalCard
        label="Heart Rate"
        value={v?.hr ?? '—'}
        unit="bpm"
        color={vitalsUi.hr.color}
        glowColor="rgba(22,163,74,0.1)"
        alarmStatus={getAlarmSeverity('hr', v?.hr, limits)}
        trend={trend('hr')}
        confidence={conf.hr}
        fieldStatus={fieldStatus?.hr}
        lastConfirmedAt={lastConfirmedAt?.hr}
        className="flex-1 min-h-0"
      />

      {/* ── SpO₂ ────────────────────────────────────────────────────────── */}
      <VitalCard
        label="SpO₂"
        value={v?.spo2 ?? '—'}
        unit="%"
        color={vitalsUi.spo2.color}
        glowColor="rgba(2,132,199,0.1)"
        alarmStatus={getAlarmSeverity('spo2', v?.spo2, limits)}
        trend={trend('spo2')}
        confidence={conf.spo2}
        fieldStatus={fieldStatus?.spo2}
        lastConfirmedAt={lastConfirmedAt?.spo2}
        className="flex-1 min-h-0"
      />

      {/* ── NIBP ────────────────────────────────────────────────────────── */}
      <VitalCard
        label="NIBP"
        value={v?.nibpSystolic != null && v?.nibpDiastolic != null ? `${v.nibpSystolic}/${v.nibpDiastolic}` : '—'}
        unit="mmHg"
        color={vitalsUi.nibp.color}
        glowColor="rgba(220,38,38,0.1)"
        secondaryValue={v?.nibpMean != null ? `${v.nibpMean}` : undefined}
        secondaryLabel="MAP"
        alarmStatus={getAlarmSeverity('nibp', v?.nibpSystolic, limits)}
        trend="stable"
        confidence={conf.nibp}
        fieldStatus={nibp.status}
        lastConfirmedAt={nibp.at}
        actionLabel={`Measure${nibpSubLabel ? `  ·  ${nibpSubLabel}` : ''}`}
        onAction={triggerNibp}
        actionBusy={nibpMeasuring}
        className="flex-1 min-h-0"
      />

      {/* ── EtCO₂ ───────────────────────────────────────────────────────── */}
      <VitalCard
        label="EtCO₂"
        value={v?.etco2 ?? '—'}
        unit="mmHg"
        color={vitalsUi.etco2.color}
        glowColor="rgba(217,119,6,0.1)"
        alarmStatus={getAlarmSeverity('etco2', v?.etco2, limits)}
        trend={trend('etco2')}
        confidence={conf.etco2}
        fieldStatus={fieldStatus?.etco2}
        lastConfirmedAt={lastConfirmedAt?.etco2}
        className="flex-1 min-h-0"
      />

      {/* ── Temp + RR ───────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        <VitalCard
          label="Temp"
          value={v?.temp?.toFixed(1) ?? '—'}
          unit="°C"
          color={vitalsUi.temp.color}
          glowColor="rgba(234,88,12,0.1)"
          alarmStatus={getAlarmSeverity('temp', v?.temp, limits)}
          trend={trend('temp', 0.15)}
          confidence={conf.temp}
          fieldStatus={fieldStatus?.temp}
          lastConfirmedAt={lastConfirmedAt?.temp}
          className="flex-1 min-h-0"
        />
        <VitalCard
          label="Resp Rate"
          value={v?.rr ?? '—'}
          unit="/min"
          color={vitalsUi.rr.color}
          glowColor="rgba(124,58,237,0.1)"
          alarmStatus={getAlarmSeverity('rr', v?.rr, limits)}
          trend={trend('rr')}
          confidence={conf.rr}
          fieldStatus={fieldStatus?.rr}
          lastConfirmedAt={lastConfirmedAt?.rr}
          className="flex-1 min-h-0"
        />
      </div>
    </div>
  );
}
