import { useMemo } from 'react';
import { CheckCircle2, ListTree } from 'lucide-react';
import { useVitalsStore } from '@/store/vitalsStore';
import { useSessionStore } from '@/store/sessionStore';
import { deriveLedger, FIELD_TO_VITAL_GROUP } from '@/lib/ledger';
import { VITAL_COLORS } from '@/features/calibration/RoiCanvas';
import type { VitalField } from '@/types/vitals';

const FIELD_LABELS: Record<VitalField, string> = {
  hr: 'Heart Rate',
  spo2: 'SpO₂',
  nibpSystolic: 'NIBP Systolic',
  nibpDiastolic: 'NIBP Diastolic',
  nibpMean: 'NIBP Mean',
  etco2: 'EtCO₂',
  temp: 'Temp',
  rr: 'Resp Rate',
};

const FIELD_UNITS: Record<VitalField, string> = {
  hr: 'bpm', spo2: '%', nibpSystolic: 'mmHg', nibpDiastolic: 'mmHg',
  nibpMean: 'mmHg', etco2: 'mmHg', temp: '°C', rr: '/min',
};

function formatClock(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/**
 * M5.7. THE live, chronological OBSERVATION LEDGER -- the operator-facing
 * view of the same persisted timeline Archive/Review read (see
 * useVitalsStore.observations + GET /api/sessions/{id}/readings). One line
 * per genuinely CONFIRMED value change, not one line per WS tick or
 * persisted row -- see src/lib/ledger.ts's deriveLedger for why a steady
 * HR=75 held for 30s produces exactly one entry, not thirty.
 *
 * Newest first, so the operator sees what just happened without scrolling.
 * Empty state is explicit about WHY it's empty (no camera calibrated for
 * this case vs. genuinely nothing confirmed yet) rather than a bare blank
 * panel that reads as broken.
 */
export function LedgerPanel() {
  const observations = useVitalsStore((s) => s.observations);
  const cameraMode = useSessionStore((s) => s.cameraMode);

  const entries = useMemo(() => deriveLedger(observations).slice().reverse(), [observations]);

  return (
    <aside className="flex flex-col w-80 flex-shrink-0 bg-white border border-slate-200 rounded-2xl overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-200 flex-shrink-0">
        <ListTree size={14} className="text-clinical-primary" />
        <span className="font-display text-xs font-bold uppercase tracking-wider text-slate-600">Observation Ledger</span>
        <span className="ml-auto font-mono text-[10px] text-slate-400">{entries.length}</span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 px-6 text-center py-10">
            <CheckCircle2 size={22} className="text-slate-300" />
            <p className="font-display text-xs text-slate-400">
              {cameraMode
                ? 'No confirmed observations yet — the ledger fills in as the camera reads the monitor.'
                : 'No camera calibrated for this case — the ledger only records genuine camera observations.'}
            </p>
          </div>
        ) : (
          <ul>
            {entries.map((entry) => (
              <li key={entry.id} className="flex items-center gap-2.5 px-4 py-2 border-b border-slate-100 last:border-0">
                <div className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: VITAL_COLORS[FIELD_TO_VITAL_GROUP[entry.field]] }} />
                <div className="min-w-0 flex-1">
                  <div className="font-display text-[11px] font-medium text-slate-700 truncate">{FIELD_LABELS[entry.field]}</div>
                  <div className="font-mono text-[10px] text-slate-400">{formatClock(entry.timestamp)}</div>
                </div>
                <div className="text-right flex-shrink-0">
                  <div className="font-mono text-sm font-semibold text-slate-800">
                    {entry.value}<span className="font-display text-[9px] text-slate-400 ml-0.5">{FIELD_UNITS[entry.field]}</span>
                  </div>
                  {entry.confidence != null && (
                    <div className="font-mono text-[9px] text-slate-400">{Math.round(entry.confidence)}%</div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
