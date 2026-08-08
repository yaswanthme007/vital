import { useState, useEffect } from 'react';
import { useVitalsStore } from '@/store/vitalsStore';
import { useAlertStore } from '@/store/alertStore';
import { VitalCard } from '@/components/vitals/VitalCard';
import { getAlarmSeverity } from '@/types/vitals';
import { clamp } from '@/lib/utils';

// ─── Simulated AI confidence ──────────────────────────────────────────────────

interface ConfMap { hr: number; spo2: number; nibp: number; etco2: number; temp: number; rr: number }

function useSimulatedConfidence(): ConfMap {
  const [conf, setConf] = useState<ConfMap>({ hr: 94, spo2: 97, nibp: 88, etco2: 93, temp: 91, rr: 86 });

  useEffect(() => {
    const id = setInterval(() => {
      setConf(p => ({
        hr:   Math.round(clamp(p.hr   + (Math.random() - 0.48) * 3, 82, 99)),
        spo2: Math.round(clamp(p.spo2 + (Math.random() - 0.48) * 2, 88, 99)),
        nibp: Math.round(clamp(p.nibp + (Math.random() - 0.5)  * 4, 76, 96)),
        etco2:Math.round(clamp(p.etco2+ (Math.random() - 0.48) * 3, 82, 98)),
        temp: Math.round(clamp(p.temp + (Math.random() - 0.48) * 2, 84, 98)),
        rr:   Math.round(clamp(p.rr   + (Math.random() - 0.5)  * 3, 79, 97)),
      }));
    }, 2800);
    return () => clearInterval(id);
  }, []);

  return conf;
}

// ─── Trend helper ─────────────────────────────────────────────────────────────

type TrendDir = 'up' | 'down' | 'stable';

function useTrend(history: ReturnType<typeof useVitalsStore.getState>['history']) {
  return (key: 'hr' | 'spo2' | 'etco2' | 'temp' | 'rr', threshold = 2): TrendDir => {
    if (history.length < 5) return 'stable';
    const slice = history.slice(-8);
    const diff  = (slice[slice.length - 1][key] as number) - (slice[0][key] as number);
    const t     = key === 'temp' ? 0.15 : threshold;
    if (diff >  t) return 'up';
    if (diff < -t) return 'down';
    return 'stable';
  };
}

// ─── Component ────────────────────────────────────────────────────────────────

export function VitalsGrid() {
  const current       = useVitalsStore(s => s.current);
  const history       = useVitalsStore(s => s.history);
  const limits        = useVitalsStore(s => s.alarmLimits);
  const nibpAt        = useVitalsStore(s => s.nibpLastMeasuredAt);
  const nibpMeasuring = useVitalsStore(s => s.nibpMeasuring);
  const triggerNibp   = useVitalsStore(s => s.triggerNibpMeasurement);

  const conf  = useSimulatedConfidence();
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
        color="#16A34A"
        glowColor="rgba(22,163,74,0.1)"
        alarmStatus={v ? getAlarmSeverity('hr', v.hr, limits) : 'normal'}
        trend={trend('hr')}
        confidence={conf.hr}
        className="flex-1 min-h-0"
      />

      {/* ── SpO₂ ────────────────────────────────────────────────────────── */}
      <VitalCard
        label="SpO₂"
        value={v?.spo2 ?? '—'}
        unit="%"
        color="#0284C7"
        glowColor="rgba(2,132,199,0.1)"
        alarmStatus={v ? getAlarmSeverity('spo2', v.spo2, limits) : 'normal'}
        trend={trend('spo2')}
        confidence={conf.spo2}
        className="flex-1 min-h-0"
      />

      {/* ── NIBP ────────────────────────────────────────────────────────── */}
      <VitalCard
        label="NIBP"
        value={v ? `${v.nibpSystolic}/${v.nibpDiastolic}` : '—'}
        unit="mmHg"
        color="#DC2626"
        glowColor="rgba(220,38,38,0.1)"
        secondaryValue={v ? `${v.nibpMean}` : undefined}
        secondaryLabel="MAP"
        alarmStatus={v ? getAlarmSeverity('nibp', v.nibpSystolic, limits) : 'normal'}
        trend="stable"
        confidence={conf.nibp}
        actionLabel={nibpMeasuring ? undefined : `Measure${nibpSubLabel ? `  ·  ${nibpSubLabel}` : ''}`}
        onAction={nibpMeasuring ? undefined : triggerNibp}
        actionBusy={nibpMeasuring}
        className="flex-1 min-h-0"
      />

      {/* ── EtCO₂ ───────────────────────────────────────────────────────── */}
      <VitalCard
        label="EtCO₂"
        value={v?.etco2 ?? '—'}
        unit="mmHg"
        color="#D97706"
        glowColor="rgba(217,119,6,0.1)"
        alarmStatus={v ? getAlarmSeverity('etco2', v.etco2, limits) : 'normal'}
        trend={trend('etco2')}
        confidence={conf.etco2}
        className="flex-1 min-h-0"
      />

      {/* ── Temp + RR ───────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        <VitalCard
          label="Temp"
          value={v?.temp?.toFixed(1) ?? '—'}
          unit="°C"
          color="#EA580C"
          glowColor="rgba(234,88,12,0.1)"
          alarmStatus={v ? getAlarmSeverity('temp', v.temp, limits) : 'normal'}
          trend={trend('temp', 0.15)}
          confidence={conf.temp}
          className="flex-1 min-h-0"
        />
        <VitalCard
          label="Resp Rate"
          value={v?.rr ?? '—'}
          unit="/min"
          color="#7C3AED"
          glowColor="rgba(124,58,237,0.1)"
          alarmStatus={v ? getAlarmSeverity('rr', v.rr, limits) : 'normal'}
          trend={trend('rr')}
          confidence={conf.rr}
          className="flex-1 min-h-0"
        />
      </div>
    </div>
  );
}
