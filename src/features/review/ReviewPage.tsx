import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle, AlertCircle, CheckCircle2, ChevronRight, ChevronLeft,
  Edit3, Check, X, Lock, Activity,
  ArrowRight, Eye, Clock, Info, Printer, LineChart, TrendingUp,
  ClipboardList, User, Download, PlayCircle, StopCircle, FileText, Pill,
  HeartPulse, Droplets, Gauge, Wind, Thermometer,
} from 'lucide-react';
import {
  LineChart as RLineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { deriveLedger, FIELD_TO_VITAL_GROUP, VITAL_FIELDS } from '@/lib/ledger';
import { computeFieldSummaries, computeObservationStats, VITALS_FIELD_LABELS, VITALS_FIELD_UNITS } from '@/lib/vitalSummary';
import type { FieldSummary } from '@/lib/vitalSummary';
import { VITAL_COLORS } from '@/features/calibration/RoiCanvas';
import { formatTime, formatTimeShort, formatDate, formatDuration, cn } from '@/lib/utils';
import type { VitalObservationRow, VitalField, VitalType } from '@/types/vitals';
import type { Session } from '@/types/session';
import type { AlertDto } from '@/lib/api';
import { Button } from '@/design-system/components/Button';
import { ConfidenceBadge, ConfidencePill } from '@/design-system/components/ConfidenceBadge';
import { ProgressBar, CircularProgress } from '@/design-system/components/Progress';
import { Dialog } from '@/design-system/components/Dialog';
import { useSessionStore } from '@/store/sessionStore';
import { useToast } from '@/store/toastStore';
import { api } from '@/lib/api';

// ─── Types ────────────────────────────────────────────────────────────────────

type VitalKey = VitalType;
type ItemStatus = 'pending' | 'corrected' | 'dismissed';
type SignState = 'idle' | 'confirming' | 'signing' | 'locked';
type TabId = 'overview' | 'trends' | 'observations' | 'timeline';

interface FlaggedReading {
  id: string;
  timestamp: number;
  vital: VitalKey;
  aiValue: string;
  suggestedValue: string;
  unit: string;
  confidence: number;
  severity: 'critical' | 'warning';
  status: ItemStatus;
  correctedValue?: string;
  frameNote: string;
}

// ─── Config ───────────────────────────────────────────────────────────────────

const VITAL_CFG: Record<VitalKey, { label: string; color: string; unit: string }> = {
  hr:    { label: 'Heart Rate', color: VITAL_COLORS.hr,    unit: 'bpm'  },
  spo2:  { label: 'SpO₂',      color: VITAL_COLORS.spo2,  unit: '%'    },
  nibp:  { label: 'NIBP',      color: VITAL_COLORS.nibp,  unit: 'mmHg' },
  etco2: { label: 'EtCO₂',     color: VITAL_COLORS.etco2, unit: 'mmHg' },
  temp:  { label: 'Temp',      color: VITAL_COLORS.temp,  unit: '°C'   },
  rr:    { label: 'Resp Rate', color: VITAL_COLORS.rr,    unit: '/min' },
};

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: 'overview',     label: 'Overview',     icon: ClipboardList },
  { id: 'trends',       label: 'Trends',       icon: TrendingUp    },
  { id: 'observations', label: 'Observations', icon: LineChart     },
  { id: 'timeline',     label: 'Timeline',     icon: Clock         },
];

// ─── Exceptions & Alerts (M-final: redesigned Flagged Readings sidebar) ──────
//
// The OCR pipeline raises a FlaggedReading for TWO structurally different
// situations (see backend/app/validation/reconcile.py's severity assignment):
// 'critical' means the raw OCR read was REJECTED this tick (implausible
// range / rejected jump) -- the displayed value may be a held-over reading,
// so a human genuinely needs to look. 'warning' covers everything else,
// including values the pipeline already ACCEPTED at medium confidence or
// via temporal corroboration -- informational review context, not a
// blocker. Gating "Sign Off" on every flagged item (the old behaviour)
// made autonomous continuous monitoring look broken; gating it on genuinely
// BLOCKING (pending + critical) items only matches the product's real
// premise: autonomous monitoring with human final sign-off, not a human
// re-reading every OCR frame.

type ExceptionCategory = 'critical' | 'lowConfidence' | 'corrected' | 'resolved';

const EXCEPTION_CATEGORY_CFG: Record<ExceptionCategory, { label: string; icon: React.ElementType; color: string }> = {
  critical:      { label: 'Critical alerts', icon: AlertTriangle, color: '#FF3B30' },
  lowConfidence: { label: 'Low-confidence',  icon: AlertCircle,   color: '#FF9500' },
  corrected:     { label: 'Corrected',       icon: Edit3,         color: '#30D158' },
  resolved:      { label: 'Resolved',        icon: CheckCircle2,  color: '#64748B' },
};

function categorize(items: FlaggedReading[]): Record<ExceptionCategory, FlaggedReading[]> {
  return {
    critical: items.filter((i) => i.severity === 'critical' && i.status === 'pending'),
    lowConfidence: items.filter((i) => i.severity === 'warning' && i.status === 'pending'),
    corrected: items.filter((i) => i.status === 'corrected'),
    resolved: items.filter((i) => i.status === 'dismissed'),
  };
}

const EMPTY_CATEGORY_MESSAGE: Record<ExceptionCategory, string> = {
  critical: 'No critical exceptions — nothing blocking sign-off.',
  lowConfidence: 'No pending low-confidence OCR events.',
  corrected: 'No corrected readings on this session.',
  resolved: 'No dismissed readings on this session.',
};

// ─── Case Timeline (M-final): derived strictly from persisted data ──────────
//
// Every event below comes from a real, already-fetched source -- session
// start/end/signedAt, session.notes (the same rows Active Operation's
// quick-mark buttons write), the persisted readings timeline's own first/
// last rows, and the first persisted critical alert. Nothing here is a
// synthetic/interpretive timestamp: if a source has no data (no readings,
// no critical alert, not yet signed), its event is simply omitted.

type CaseEventKind = 'start' | 'observation' | 'alert' | 'note' | 'drug' | 'end' | 'signoff';

interface CaseEvent {
  id: string;
  timestamp: number;
  kind: CaseEventKind;
  title: string;
  description?: string;
  severity?: 'critical' | 'warning';
  vital?: VitalKey;
}

function summarizeConfirmedFields(row: VitalObservationRow): string {
  const parts: string[] = [];
  for (const field of VITAL_FIELDS) {
    const v = row[field];
    if (v == null) continue;
    parts.push(`${VITALS_FIELD_LABELS[field]} ${v}${VITALS_FIELD_UNITS[field]}`);
  }
  return parts.join(', ') || 'No fields confirmed this tick.';
}

function buildCaseEvents(session: Session, readings: VitalObservationRow[], alerts: AlertDto[]): CaseEvent[] {
  const events: CaseEvent[] = [
    { id: 'EVT-start', timestamp: session.startTime, kind: 'start', title: 'Session started', description: 'Case commenced.' },
  ];

  if (readings.length > 0) {
    events.push({
      id: 'EVT-first-obs', timestamp: readings[0].timestamp, kind: 'observation',
      title: 'First camera observation confirmed', description: summarizeConfirmedFields(readings[0]),
    });
  }

  const firstCritical = alerts.filter((a) => a.severity === 'critical').sort((a, b) => a.timestamp - b.timestamp)[0];
  if (firstCritical) {
    events.push({
      id: `EVT-alert-${firstCritical.id}`, timestamp: firstCritical.timestamp, kind: 'alert',
      title: 'First critical alert', description: firstCritical.message, severity: 'critical',
      vital: firstCritical.vitalType === 'system' ? undefined : firstCritical.vitalType,
    });
  }

  for (const n of session.notes) {
    events.push({
      id: `EVT-note-${n.id}`, timestamp: n.timestamp,
      kind: n.category === 'drug' ? 'drug' : n.category === 'alarm' ? 'alert' : 'note',
      title: n.text, severity: n.category === 'alarm' ? 'warning' : undefined,
    });
  }

  if (readings.length > 1) {
    const last = readings[readings.length - 1];
    events.push({
      id: 'EVT-last-obs', timestamp: last.timestamp, kind: 'observation',
      title: 'Last camera observation confirmed', description: summarizeConfirmedFields(last),
    });
  }

  if (session.endTime != null) {
    events.push({ id: 'EVT-end', timestamp: session.endTime, kind: 'end', title: 'Operation ended' });
  }

  if (session.signedAt != null) {
    events.push({
      id: 'EVT-signoff', timestamp: session.signedAt, kind: 'signoff', title: 'Signed off',
      description: `Signed by ${session.signedBy ?? session.anesthetist}`,
    });
  }

  return events.sort((a, b) => a.timestamp - b.timestamp);
}

const CASE_EVENT_CFG: Record<CaseEventKind, { icon: React.ElementType; color: string }> = {
  start:       { icon: PlayCircle,     color: '#30D158' },
  observation: { icon: Activity,       color: '#0EA5E9' },
  alert:       { icon: AlertTriangle,  color: '#FF3B30' },
  note:        { icon: FileText,       color: '#64748B' },
  drug:        { icon: Pill,           color: '#8B5CF6' },
  end:         { icon: StopCircle,     color: '#FF3B30' },
  signoff:     { icon: Lock,           color: '#30D158' },
};

const VITAL_EVENT_ICONS: Record<VitalKey, React.ElementType> = {
  hr: HeartPulse, spo2: Droplets, nibp: Gauge, etco2: Wind, temp: Thermometer, rr: Activity,
};

function CaseTimelinePanel({ events }: { events: CaseEvent[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
        <Clock size={28} className="opacity-30" />
        <p className="font-display text-sm">No timeline events yet</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-5">
      <p className="font-display text-[10px] text-slate-500 uppercase tracking-wider mb-4">
        Monitoring Timeline · {events.length} event{events.length === 1 ? '' : 's'} from persisted case data
      </p>
      <div className="space-y-0 max-w-2xl">
        {events.map((e, i) => {
          const cfg = CASE_EVENT_CFG[e.kind];
          const Icon = e.vital ? VITAL_EVENT_ICONS[e.vital] : cfg.icon;
          const color = e.severity === 'critical' ? '#FF3B30' : e.severity === 'warning' ? '#FF9500' : cfg.color;
          const isLast = i === events.length - 1;
          const expanded = expandedId === e.id;
          return (
            <motion.div key={e.id} className="flex gap-3"
              initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.03, duration: 0.2 }}>
              <div className="flex flex-col items-center flex-shrink-0">
                <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
                  style={{ background: `${color}18`, border: `1px solid ${color}40` }}>
                  <Icon size={12} style={{ color }} />
                </div>
                {!isLast && <div className="w-px flex-1 mt-1 mb-1 bg-slate-200" />}
              </div>
              <div className={cn('flex-1 min-w-0', isLast ? 'pb-0' : 'pb-4')}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-display text-xs font-semibold text-slate-700">{e.title}</span>
                    {e.severity === 'critical' && (
                      <span className="font-display text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-50 border border-red-200 text-red-600">Critical</span>
                    )}
                    {e.severity === 'warning' && (
                      <span className="font-display text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-50 border border-amber-200 text-amber-600">Alarm</span>
                    )}
                  </div>
                  <span className="font-mono text-[10px] text-slate-400 flex-shrink-0 tabular-nums">{formatTime(e.timestamp)}</span>
                </div>
                {e.description && (
                  <>
                    <button onClick={() => setExpandedId(expanded ? null : e.id)}
                      className="flex items-center gap-1 font-display text-[10px] text-slate-400 hover:text-slate-600 transition-colors mt-0.5">
                      <ChevronRight size={10} className={cn('transition-transform', expanded && 'rotate-90')} />
                      {expanded ? 'Hide details' : 'Show details'}
                    </button>
                    <AnimatePresence>
                      {expanded && (
                        <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                          className="overflow-hidden">
                          <div className="mt-1.5 p-2.5 rounded-lg bg-slate-50 border border-slate-100">
                            <p className="font-display text-[11px] text-slate-500 leading-relaxed">{e.description}</p>
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </>
                )}
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Observation Ledger tab (renamed: "Observations") ─────────────────────────
//
// M5.7/M5.8: the real, persisted, camera-confirmed observation timeline for
// this session -- backend/app/db/repo.list_readings via GET
// /api/sessions/{id}/readings, projected through the SAME deriveLedger()
// the Active Operation workspace's live ledger uses (src/lib/ledger.ts), so
// Review shows exactly the history that was actually recorded.

function ObservationLedgerTab({ readings, loading }: { readings: VitalObservationRow[]; loading: boolean }) {
  const entries = useMemo(() => deriveLedger(readings).slice().reverse(), [readings]);
  const sources = useMemo(() => new Set(readings.map((r) => r.source).filter(Boolean)), [readings]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        <p className="font-display text-sm">Loading observation ledger…</p>
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
        <LineChart size={28} className="opacity-30" />
        <p className="font-display text-sm">No confirmed camera observations recorded for this case</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mb-3 flex items-center gap-2">
        <span className="font-display text-xs text-slate-400">
          {readings.length} persisted reading{readings.length === 1 ? '' : 's'} · {entries.length} confirmed value change{entries.length === 1 ? '' : 's'}
          {sources.size > 0 ? ` · source: ${Array.from(sources).join(', ')}` : ''}
        </span>
      </div>
      <ul className="divide-y divide-slate-100">
        {entries.map((entry) => (
          <li key={entry.id} className="flex items-center gap-3 py-2">
            <div className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: VITAL_COLORS[FIELD_TO_VITAL_GROUP[entry.field]] }} />
            <span className="font-mono text-xs text-slate-400 w-20 flex-shrink-0">{formatTime(entry.timestamp)}</span>
            <span className="font-display text-xs text-slate-600 flex-1">{VITALS_FIELD_LABELS[entry.field]}</span>
            <span className="font-mono text-sm font-semibold text-slate-800">
              {entry.value} <span className="font-display text-[10px] text-slate-400">{VITALS_FIELD_UNITS[entry.field]}</span>
            </span>
            {entry.confidence != null && (
              <span className="font-mono text-[10px] text-slate-400 w-10 text-right">{Math.round(entry.confidence)}%</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─── Overview tab ───────────────────────────────────────────────────────────

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-100">
      <div className="font-display text-[10px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className="font-display text-xs font-medium mt-0.5 text-slate-800">{value}</div>
    </div>
  );
}

function MonitoringOverviewCards({ summaries }: { summaries: FieldSummary[] }) {
  const byField = useMemo(() => Object.fromEntries(summaries.map((s) => [s.field, s])) as Record<VitalField, FieldSummary>, [summaries]);
  const sys = byField.nibpSystolic, dia = byField.nibpDiastolic;

  const cards: { key: VitalKey; label: string; value: string; unit: string; hasData: boolean }[] = [
    { key: 'hr',    label: 'Heart Rate', value: byField.hr.last != null ? String(byField.hr.last) : '—',       unit: 'bpm',  hasData: byField.hr.count > 0 },
    { key: 'spo2',  label: 'SpO₂',       value: byField.spo2.last != null ? String(byField.spo2.last) : '—',   unit: '%',    hasData: byField.spo2.count > 0 },
    { key: 'nibp',  label: 'NIBP',       value: sys.last != null && dia.last != null ? `${sys.last}/${dia.last}` : '—', unit: 'mmHg', hasData: sys.count > 0 || dia.count > 0 },
    { key: 'etco2', label: 'EtCO₂',      value: byField.etco2.last != null ? String(byField.etco2.last) : '—', unit: 'mmHg', hasData: byField.etco2.count > 0 },
    { key: 'temp',  label: 'Temp',       value: byField.temp.last != null ? String(byField.temp.last) : '—',   unit: '°C',   hasData: byField.temp.count > 0 },
    { key: 'rr',    label: 'Resp Rate',  value: byField.rr.last != null ? String(byField.rr.last) : '—',       unit: '/min', hasData: byField.rr.count > 0 },
  ];

  return (
    <div className="grid grid-cols-3 md:grid-cols-6 gap-2.5">
      {cards.map((c) => (
        <div key={c.key} className={cn('rounded-xl border p-3 text-center', c.hasData ? 'border-slate-200 bg-white' : 'border-slate-100 bg-slate-50')}>
          <div className="font-display text-[10px] text-slate-400 uppercase tracking-wider mb-1">{c.label}</div>
          <div className="font-mono text-lg font-semibold" style={{ color: c.hasData ? VITAL_COLORS[c.key] : '#CBD5E1' }}>{c.value}</div>
          <div className="font-display text-[9px] text-slate-400 mt-0.5">{c.hasData ? c.unit : 'no data'}</div>
        </div>
      ))}
    </div>
  );
}

interface LifecycleStep { id: string; label: string; done: boolean; hint?: string }

function LifecycleFlow({ steps }: { steps: LifecycleStep[] }) {
  return (
    <div className="flex items-stretch gap-1 overflow-x-auto pb-1">
      {steps.map((s, i) => (
        <div key={s.id} className="flex items-center flex-shrink-0">
          <div className={cn(
            'flex flex-col items-center justify-center gap-1 px-3 py-2 rounded-xl border min-w-[96px] h-[62px]',
            s.done ? 'bg-emerald-50 border-emerald-200' : 'bg-slate-50 border-slate-200',
          )}>
            {s.done ? <CheckCircle2 size={14} className="text-emerald-500" /> : <div className="w-3.5 h-3.5 rounded-full border-2 border-slate-300" />}
            <span className={cn('font-display text-[10px] text-center leading-tight', s.done ? 'text-emerald-700 font-medium' : 'text-slate-400')}>
              {s.label}
            </span>
            {s.hint && <span className="font-mono text-[9px] text-slate-400">{s.hint}</span>}
          </div>
          {i < steps.length - 1 && <ChevronRight size={12} className="text-slate-300 mx-0.5 flex-shrink-0" />}
        </div>
      ))}
    </div>
  );
}

function OverviewTab({
  session, readings, alerts, loadingAlerts, isSigned,
}: {
  session: Session;
  readings: VitalObservationRow[];
  alerts: AlertDto[];
  loadingAlerts: boolean;
  isSigned: boolean;
}) {
  const fieldSummaries = useMemo(() => computeFieldSummaries(readings), [readings]);
  const stats = useMemo(() => computeObservationStats(readings), [readings]);
  const duration = session.endTime != null ? formatDuration(session.endTime - session.startTime) : '—';
  const criticalAlerts = alerts.filter((a) => a.severity === 'critical');
  const warningAlerts = alerts.filter((a) => a.severity === 'warning');
  const infoAlerts = alerts.filter((a) => a.severity !== 'critical' && a.severity !== 'warning');

  const cameraSourced = stats.source === 'camera' || stats.source === 'mixed';
  const lifecycleSteps: LifecycleStep[] = [
    { id: 'calibration', label: 'Calibration', done: cameraSourced, hint: cameraSourced ? undefined : 'n/a' },
    { id: 'started', label: 'Monitoring Started', done: true },
    { id: 'observation', label: 'Continuous Observation', done: readings.length > 0, hint: readings.length > 0 ? `${readings.length} readings` : undefined },
    { id: 'alerts', label: 'Alerts / Exceptions', done: session.endTime != null, hint: `${alerts.length} alert${alerts.length === 1 ? '' : 's'}` },
    { id: 'ended', label: 'Monitoring Ended', done: session.endTime != null },
    { id: 'review', label: 'Review', done: true },
    { id: 'signoff', label: 'Sign-off', done: isSigned },
    { id: 'archived', label: 'Archived', done: isSigned },
  ];

  return (
    <div className="h-full overflow-y-auto p-5 space-y-6">
      {/* Operation Summary */}
      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <ClipboardList size={12} /> Operation Summary
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
          <InfoRow label="Patient / Case ID" value={session.patient.id} />
          <InfoRow label="Procedure" value={session.procedure} />
          <InfoRow label="ASA Class" value={session.patient.asa ? `ASA ${session.patient.asa}` : '—'} />
          <InfoRow label="Operator / Anaesthetist" value={session.anesthetist} />
          <InfoRow label="Start Time" value={`${formatDate(session.startTime)} · ${formatTime(session.startTime)}`} />
          <InfoRow label="End Time" value={session.endTime != null ? `${formatDate(session.endTime)} · ${formatTime(session.endTime)}` : '—'} />
          <InfoRow label="Duration" value={duration} />
          <InfoRow label="Status" value={session.signedAt != null ? 'Signed & locked' : session.status === 'completed' ? 'Completed · pending sign-off' : session.status} />
        </div>
      </section>

      {/* Monitoring Overview */}
      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <Activity size={12} /> Monitoring Overview
        </h3>
        <MonitoringOverviewCards summaries={fieldSummaries} />
      </section>

      {/* Operation Lifecycle */}
      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <TrendingUp size={12} /> Operation Lifecycle
        </h3>
        <LifecycleFlow steps={lifecycleSteps} />
      </section>

      {/* Observation quality */}
      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <Eye size={12} /> Observation Quality
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
          <InfoRow label="Persisted readings" value={String(stats.readingsCount)} />
          <InfoRow label="Confirmed field values" value={String(stats.confirmedObservations)} />
          <InfoRow label="Avg. OCR confidence" value={stats.avgConfidence != null ? `${stats.avgConfidence.toFixed(1)}%` : '—'} />
          <InfoRow label="Source" value={stats.source ? stats.source[0].toUpperCase() + stats.source.slice(1) : '—'} />
        </div>
      </section>

      {/* Alerts / critical events */}
      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <AlertTriangle size={12} /> Alerts &amp; Critical Events
        </h3>
        {loadingAlerts ? (
          <p className="font-display text-xs text-slate-400">Loading alerts…</p>
        ) : alerts.length === 0 ? (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-50 border border-emerald-100">
            <CheckCircle2 size={13} className="text-emerald-500" />
            <span className="font-display text-xs text-slate-600">No alerts recorded for this operation.</span>
          </div>
        ) : (
          <div className="space-y-1.5">
            <div className="flex items-center gap-3 mb-1">
              {criticalAlerts.length > 0 && (
                <span className="font-display text-[10px] font-bold px-2 py-0.5 rounded bg-red-50 border border-red-200 text-red-600">
                  {criticalAlerts.length} critical
                </span>
              )}
              {warningAlerts.length > 0 && (
                <span className="font-display text-[10px] font-bold px-2 py-0.5 rounded bg-amber-50 border border-amber-200 text-amber-600">
                  {warningAlerts.length} warning
                </span>
              )}
              {infoAlerts.length > 0 && (
                <span className="font-display text-[10px] font-bold px-2 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-600">
                  {infoAlerts.length} informational
                </span>
              )}
            </div>
            {alerts.map((a) => (
              <div key={a.id} className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-lg border',
                a.severity === 'critical' ? 'bg-red-50 border-red-100' : a.severity === 'warning' ? 'bg-amber-50 border-amber-100' : 'bg-blue-50 border-blue-100',
              )}>
                <span className="font-mono text-[10px] text-slate-400 w-16 flex-shrink-0">{formatTime(a.timestamp)}</span>
                <span className="font-display text-xs text-slate-700 flex-1">
                  {a.message}
                  {a.value != null && <span className="ml-1 font-mono text-slate-500">{a.value}{a.unit}</span>}
                </span>
                {a.acknowledged && <span className="font-display text-[9px] text-slate-400 uppercase tracking-wider">Acked</span>}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

// ─── Trends tab ─────────────────────────────────────────────────────────────

function fieldSeries(readings: VitalObservationRow[], field: VitalField) {
  return readings.filter((r) => r[field] != null).map((r) => ({ timestamp: r.timestamp, value: r[field] as number }));
}

function EmptyChartState({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50 p-3">
      <span className="font-display text-xs font-semibold text-slate-500">{title}</span>
      <p className="font-display text-[11px] text-slate-400 mt-2">{message}</p>
    </div>
  );
}

function MiniTrendChart({
  title, unit, data, lines,
}: {
  title: string;
  unit: string;
  data: Array<Record<string, number | null>>;
  lines: { key: string; color: string; label: string }[];
}) {
  if (data.length === 0) {
    return <EmptyChartState title={title} message="No persisted observations available." />;
  }
  if (data.length === 1) {
    return <EmptyChartState title={title} message={`Only 1 observation recorded — not enough points to chart a trend.`} />;
  }

  return (
    <div className="rounded-xl border border-slate-200 p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="font-display text-xs font-semibold text-slate-700">{title}</span>
        {lines.length === 1 && <span className="font-mono text-[10px] text-slate-400">{unit}</span>}
      </div>
      <ResponsiveContainer width="100%" height={130}>
        <RLineChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="timestamp" type="number" domain={['dataMin', 'dataMax']}
            tickFormatter={(v) => formatTimeShort(v)} tick={{ fontSize: 9, fill: '#94A3B8' }}
            axisLine={{ stroke: '#E2E8F0' }} tickLine={false} />
          <YAxis tick={{ fontSize: 9, fill: '#94A3B8' }} axisLine={false} tickLine={false} width={32} />
          <Tooltip labelFormatter={(v) => formatTime(Number(v))} contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #E2E8F0' }} />
          {lines.length > 1 && <Legend wrapperStyle={{ fontSize: 9 }} />}
          {lines.map((l) => (
            <Line key={l.key} dataKey={l.key} name={l.label} stroke={l.color} strokeWidth={1.75} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
          ))}
        </RLineChart>
      </ResponsiveContainer>
    </div>
  );
}

function RangeStrip({ summaries }: { summaries: FieldSummary[] }) {
  const withData = summaries.filter((s) => s.count > 0);
  if (withData.length === 0) {
    return <p className="font-display text-xs text-slate-400">No persisted observations available.</p>;
  }
  return (
    <div className="space-y-2.5">
      {withData.map((s) => {
        const span = (s.max! - s.min!) || 1;
        const latestPct = Math.max(0, Math.min(100, ((s.last! - s.min!) / span) * 100));
        return (
          <div key={s.field} className="flex items-center gap-3">
            <span className="w-24 font-display text-[11px] text-slate-500 flex-shrink-0 truncate">{s.label}</span>
            <span className="font-mono text-[10px] text-slate-400 w-12 text-right flex-shrink-0">{s.min}</span>
            <div className="flex-1 h-1.5 rounded-full bg-slate-100 relative min-w-[60px]">
              <div className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full border-2 border-white shadow"
                style={{ left: `calc(${latestPct}% - 5px)`, backgroundColor: VITAL_COLORS[FIELD_TO_VITAL_GROUP[s.field]] }}
                title={`Latest: ${s.last} ${s.unit}`} />
            </div>
            <span className="font-mono text-[10px] text-slate-400 w-12 flex-shrink-0">{s.max}</span>
            <span className="font-mono text-[11px] text-slate-700 font-semibold w-20 text-right flex-shrink-0">{s.last} {s.unit}</span>
          </div>
        );
      })}
    </div>
  );
}

function DistributionBars({ summaries }: { summaries: FieldSummary[] }) {
  const byField = Object.fromEntries(summaries.map((s) => [s.field, s])) as Record<VitalField, FieldSummary>;
  const groups: { key: VitalKey; label: string; count: number }[] = [
    { key: 'hr',    label: 'Heart Rate', count: byField.hr.count },
    { key: 'rr',    label: 'Resp Rate',  count: byField.rr.count },
    { key: 'spo2',  label: 'SpO₂',       count: byField.spo2.count },
    { key: 'etco2', label: 'EtCO₂',      count: byField.etco2.count },
    { key: 'temp',  label: 'Temp',       count: byField.temp.count },
    { key: 'nibp',  label: 'NIBP',       count: byField.nibpSystolic.count },
  ];
  const max = Math.max(1, ...groups.map((g) => g.count));
  if (groups.every((g) => g.count === 0)) {
    return <p className="font-display text-xs text-slate-400">No persisted observations available.</p>;
  }
  return (
    <div className="space-y-1.5">
      {groups.map((g) => (
        <div key={g.key} className="flex items-center gap-2">
          <span className="w-20 font-display text-[11px] text-slate-500 flex-shrink-0">{g.label}</span>
          <div className="flex-1 h-3 rounded bg-slate-50 overflow-hidden">
            <motion.div className="h-full rounded" style={{ backgroundColor: VITAL_COLORS[g.key] }}
              initial={{ width: 0 }} animate={{ width: `${(g.count / max) * 100}%` }} transition={{ duration: 0.4 }} />
          </div>
          <span className="w-8 text-right font-mono text-[10px] text-slate-500 flex-shrink-0">{g.count}</span>
        </div>
      ))}
    </div>
  );
}

function TrendsTab({ readings }: { readings: VitalObservationRow[] }) {
  const fieldSummaries = useMemo(() => computeFieldSummaries(readings), [readings]);

  const nibpData = useMemo(
    () => readings
      .filter((r) => r.nibpSystolic != null || r.nibpDiastolic != null || r.nibpMean != null)
      .map((r) => ({ timestamp: r.timestamp, nibpSystolic: r.nibpSystolic, nibpDiastolic: r.nibpDiastolic, nibpMean: r.nibpMean })),
    [readings],
  );

  if (readings.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
        <TrendingUp size={28} className="opacity-30" />
        <p className="font-display text-sm">No persisted observations available for trend analysis</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-5 space-y-6">
      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <TrendingUp size={12} /> Vital Trends
        </h3>
        <p className="font-display text-[11px] text-slate-400 mb-3">
          Each line connects only genuinely confirmed camera observations — gaps in coverage are left as gaps, never filled in.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <MiniTrendChart title="Heart Rate" unit="bpm" data={fieldSeries(readings, 'hr').map((d) => ({ timestamp: d.timestamp, value: d.value }))}
            lines={[{ key: 'value', color: VITAL_COLORS.hr, label: 'HR' }]} />
          <MiniTrendChart title="SpO₂" unit="%" data={fieldSeries(readings, 'spo2')}
            lines={[{ key: 'value', color: VITAL_COLORS.spo2, label: 'SpO₂' }]} />
          <MiniTrendChart title="NIBP" unit="mmHg" data={nibpData}
            lines={[
              { key: 'nibpSystolic', color: VITAL_COLORS.nibp, label: 'Systolic' },
              { key: 'nibpDiastolic', color: '#FF9B8A', label: 'Diastolic' },
              { key: 'nibpMean', color: '#8A1F1B', label: 'Mean' },
            ]} />
          <MiniTrendChart title="EtCO₂" unit="mmHg" data={fieldSeries(readings, 'etco2')}
            lines={[{ key: 'value', color: VITAL_COLORS.etco2, label: 'EtCO₂' }]} />
          <MiniTrendChart title="Temp" unit="°C" data={fieldSeries(readings, 'temp')}
            lines={[{ key: 'value', color: VITAL_COLORS.temp, label: 'Temp' }]} />
          <MiniTrendChart title="Resp Rate" unit="/min" data={fieldSeries(readings, 'rr')}
            lines={[{ key: 'value', color: VITAL_COLORS.rr, label: 'RR' }]} />
        </div>
      </section>

      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <Activity size={12} /> Min / Max Range Overview
        </h3>
        <div className="rounded-xl border border-slate-200 p-4">
          <RangeStrip summaries={fieldSummaries} />
        </div>
      </section>

      <section>
        <h3 className="font-display text-xs font-semibold text-slate-600 uppercase tracking-wider mb-3 flex items-center gap-1.5">
          <LineChart size={12} /> Observation Distribution
        </h3>
        <div className="rounded-xl border border-slate-200 p-4">
          <DistributionBars summaries={fieldSummaries} />
        </div>
      </section>
    </div>
  );
}

// ─── Queue Card ───────────────────────────────────────────────────────────────

function QueueCard({ item, selected, onSelect }: {
  item: FlaggedReading; selected: boolean; onSelect: () => void;
}) {
  const cfg = VITAL_CFG[item.vital];
  const isCrit = item.severity === 'critical';
  const resolved = item.status !== 'pending';

  return (
    <motion.button
      onClick={onSelect}
      className={cn(
        'w-full text-left p-3 border-b border-slate-100 transition-colors duration-150',
        selected ? 'bg-slate-50' : 'bg-white hover:bg-slate-50/70',
        resolved && 'opacity-55',
      )}
      style={{ borderLeft: selected ? `3px solid ${cfg.color}` : '3px solid transparent' }}
      whileTap={{ scale: 0.998 }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          {resolved ? (
            <CheckCircle2 size={13} className="text-slate-300 flex-shrink-0 mt-0.5" />
          ) : isCrit ? (
            <motion.div animate={{ opacity: [1, 0.3, 1] }} transition={{ repeat: Infinity, duration: 1 }}>
              <AlertTriangle size={13} className="text-[#FF3B30] flex-shrink-0 mt-0.5" />
            </motion.div>
          ) : (
            <AlertCircle size={13} className="text-[#FF9500] flex-shrink-0 mt-0.5" />
          )}
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="font-display text-xs font-semibold" style={{ color: resolved ? '#94A3B8' : cfg.color }}>
                {cfg.label}
              </span>
              {!resolved && (
                <span className="font-display text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded"
                  style={{
                    background: isCrit ? 'rgba(255,59,48,0.08)' : 'rgba(255,149,0,0.08)',
                    color:      isCrit ? '#FF3B30'             : '#FF9500',
                    border: `1px solid ${isCrit ? 'rgba(255,59,48,0.25)' : 'rgba(255,149,0,0.25)'}`,
                  }}>
                  {item.severity}
                </span>
              )}
              {resolved && (
                <span className="font-display text-[9px] uppercase tracking-wider text-slate-400">{item.status}</span>
              )}
            </div>
            <div className="flex items-center gap-1 mt-0.5">
              <span className="font-mono text-[11px] text-slate-400 line-through">{item.aiValue}</span>
              <ChevronRight size={9} className="text-slate-300" />
              <span className="font-mono text-[11px] text-slate-700 font-medium">
                {item.correctedValue ?? item.suggestedValue}
              </span>
              <span className="font-mono text-[10px] text-slate-400">{item.unit}</span>
            </div>
          </div>
        </div>
        <ConfidencePill value={item.confidence} className="flex-shrink-0 mt-0.5" />
      </div>
      <div className="ml-5 mt-1">
        <span className="font-mono text-[10px] text-slate-400">{formatTimeShort(item.timestamp)}</span>
      </div>
    </motion.button>
  );
}

// ─── Reading Inspector ────────────────────────────────────────────────────────

function ReadingInspector({ item, allItems, currentIndex, onNavigate, onCorrect, onDismiss, locked }: {
  item: FlaggedReading;
  allItems: FlaggedReading[];
  currentIndex: number;
  onNavigate: (dir: 1 | -1) => void;
  onCorrect: (id: string, value: string) => void;
  onDismiss: (id: string) => void;
  locked: boolean;
}) {
  const [editValue, setEditValue] = useState(item.suggestedValue);
  const cfg = VITAL_CFG[item.vital];
  const resolved = item.status !== 'pending';

  return (
    <div className="flex flex-col border-t border-slate-200 bg-white flex-shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <Eye size={12} className="text-slate-400" />
          <span className="font-display text-xs font-semibold text-slate-700">Inspector</span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => onNavigate(-1)} disabled={currentIndex <= 0}
            aria-label="Previous item"
            className="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:pointer-events-none transition-colors">
            <ChevronLeft size={13} aria-hidden="true" />
          </button>
          <span className="font-mono text-[10px] text-slate-400 tabular-nums" aria-live="polite" aria-atomic="true">{currentIndex + 1} / {allItems.length}</span>
          <button onClick={() => onNavigate(1)} disabled={currentIndex === -1 || currentIndex === allItems.length - 1}
            aria-label="Next item"
            className="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:pointer-events-none transition-colors">
            <ChevronRight size={13} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="p-4 space-y-3 overflow-y-auto" style={{ maxHeight: 260 }}>
        {/* Value + confidence */}
        <div className="flex items-start justify-between">
          <div>
            <div className="font-display text-[10px] text-slate-500 uppercase tracking-wider">{cfg.label} — AI read</div>
            <div className="font-mono mt-0.5" style={{ fontSize: 28, color: cfg.color, lineHeight: 1 }}>
              {item.aiValue}
              <span className="text-sm text-slate-400 ml-1.5">{item.unit}</span>
            </div>
          </div>
          <ConfidenceBadge value={item.confidence} showBar showLabel size="md" />
        </div>

        {/* Reason note */}
        <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
          <p className="font-display text-[11px] text-slate-500 leading-relaxed">{item.frameNote}</p>
        </div>

        {/* Correction field */}
        {!resolved && !locked && (
          <div>
            <label htmlFor="reading-correction-input" className="font-display text-[10px] text-slate-500 uppercase tracking-wider block mb-1.5">
              Correct to ({item.unit})
            </label>
            <input
              id="reading-correction-input"
              type="text"
              value={editValue}
              onChange={e => setEditValue(e.target.value)}
              aria-label={`Corrected value for ${VITAL_CFG[item.vital].label} in ${item.unit}`}
              className="w-full h-8 px-3 rounded-lg border border-slate-200 bg-white font-mono text-sm text-slate-800 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-all"
            />
          </div>
        )}

        {/* Actions */}
        {resolved ? (
          <div className="flex items-center gap-2 p-2.5 rounded-lg bg-emerald-50 border border-emerald-200">
            <CheckCircle2 size={13} className="text-emerald-500 flex-shrink-0" />
            <span className="font-display text-xs text-slate-600">
              {item.status === 'corrected'
                ? `Corrected → ${item.correctedValue} ${item.unit}`
                : 'Dismissed as correct'}
            </span>
          </div>
        ) : locked ? (
          <div className="flex items-center gap-2 p-2.5 rounded-lg bg-slate-50 border border-slate-200">
            <Lock size={13} className="text-slate-400 flex-shrink-0" />
            <span className="font-display text-xs text-slate-500">Session signed &amp; locked — no further corrections</span>
          </div>
        ) : (
          <div className="flex gap-2">
            <Button variant="success" size="sm" icon={<Check size={12} />} className="flex-1"
              onClick={() => onCorrect(item.id, editValue || item.suggestedValue)}>
              Apply Correction
            </Button>
            <Button variant="ghost" size="sm" icon={<X size={12} />} className="text-slate-500"
              onClick={() => onDismiss(item.id)}>
              Dismiss
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Sign Off Dialog ──────────────────────────────────────────────────────────

function SignDialog({ open, onClose, onConfirm, session, criticalTotal, informationalCount, signing }: {
  open: boolean; onClose: () => void; onConfirm: () => void;
  session: Session | null;
  criticalTotal: number;
  informationalCount: number;
  signing: boolean;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="Sign Off Operation"
      description="This action is irreversible. Review the record carefully before proceeding."
      size="md" closeOnBackdrop={false}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={signing}>Cancel</Button>
          <Button variant="clinical" icon={<Lock size={13} />} onClick={onConfirm} loading={signing}>
            Sign Off Operation
          </Button>
        </>
      }>
      <div className="space-y-4">
        <div className="p-3 rounded-xl flex gap-3"
          style={{ background: 'rgba(255,149,0,0.08)', border: '1px solid rgba(255,149,0,0.3)' }}>
          <AlertTriangle size={16} style={{ color: '#FF9500' }} className="flex-shrink-0 mt-0.5" />
          <div>
            <p className="font-display text-sm font-semibold text-[#E8F1FF]">Legal Document Warning</p>
            <p className="font-display text-xs leading-relaxed mt-0.5" style={{ color: '#7A90AA' }}>
              By signing off, you certify this anaesthesia record is accurate and complete.
              The record will be locked and become a binding legal medical document.
              All blocking exceptions have been reviewed and resolved.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'Patient ID',   value: session?.patient.id ?? '—' },
            { label: 'ASA Class',    value: session?.patient.asa ? `ASA ${session.patient.asa}` : '—' },
            { label: 'Procedure',    value: session?.procedure ?? '—' },
            { label: 'Anaesthetist', value: session?.anesthetist ?? '—' },
          ].map(({ label, value }) => (
            <div key={label} className="p-2.5 rounded-lg"
              style={{ background: 'rgba(14,28,48,0.6)', border: '1px solid rgba(18,32,52,0.9)' }}>
              <div className="font-display text-[10px] uppercase tracking-wider" style={{ color: '#3D5570' }}>{label}</div>
              <div className="font-display text-xs font-medium mt-0.5 truncate" style={{ color: '#E8F1FF' }}>{value}</div>
            </div>
          ))}
        </div>

        <div className="flex items-center gap-2.5 p-2.5 rounded-xl"
          style={{ background: 'rgba(48,209,88,0.08)', border: '1px solid rgba(48,209,88,0.25)' }}>
          <CheckCircle2 size={14} style={{ color: '#30D158' }} />
          <p className="font-display text-xs" style={{ color: '#30D158' }}>
            {criticalTotal > 0
              ? `All ${criticalTotal} blocking exception${criticalTotal === 1 ? '' : 's'} reviewed and resolved — chain of custody verified`
              : 'No blocking exceptions on this session — chain of custody verified'}
          </p>
        </div>

        {informationalCount > 0 && (
          <p className="font-display text-[11px] leading-relaxed" style={{ color: '#7A90AA' }}>
            {informationalCount} informational low-/medium-confidence OCR event{informationalCount === 1 ? '' : 's'} were logged and
            did not require review — autonomous monitoring accepted these values within tolerance.
          </p>
        )}
      </div>
    </Dialog>
  );
}

// ─── PDF Progress Dialog ──────────────────────────────────────────────────────

function PdfDialog({ signState, onClose, sessionId, onGoArchive, session }: {
  signState: SignState; onClose: () => void; sessionId: string | null; onGoArchive: () => void; session: Session | null;
}) {
  const { toast } = useToast();

  const handleDownload = () => {
    if (!sessionId) return;
    // The real PDF that api.signSession()'s backend call just wrote to
    // disk — no Content-Disposition header on the backend, so this opens
    // it inline in a new tab rather than forcing a save-as, which is fine:
    // it's the real document either way, not a placeholder.
    window.open(api.reportPdfUrl(sessionId), '_blank', 'noopener');
  };

  const handlePrint = async () => {
    if (!sessionId) return;
    try {
      const res = await fetch(api.reportPdfUrl(sessionId));
      if (!res.ok) throw new Error(`report.pdf failed: ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const iframe = document.createElement('iframe');
      iframe.style.position = 'fixed';
      iframe.style.width = '0';
      iframe.style.height = '0';
      iframe.style.border = 'none';
      iframe.src = url;
      iframe.onload = () => {
        iframe.contentWindow?.focus();
        iframe.contentWindow?.print();
      };
      document.body.appendChild(iframe);
    } catch (err) {
      console.error('Failed to print report', err);
      toast.error('Could not print', {
        description: "Couldn't reach the backend — check it's running and try again.",
      });
    }
  };

  return (
    <Dialog open={signState === 'signing' || signState === 'locked'} onClose={onClose}
      showClose={false} closeOnBackdrop={false} size="sm">
      <div className="py-4 flex flex-col items-center gap-4">
        <AnimatePresence mode="wait">
          {signState === 'signing' ? (
            // Real awaited POST /sign, not a fabricated progress bar — there's
            // no meaningful percentage for a single request/response, so this
            // shows an indeterminate spinner for however long it actually takes.
            <motion.div key="signing" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="w-full flex flex-col items-center gap-4">
              <motion.div
                className="w-16 h-16 rounded-full flex items-center justify-center"
                style={{ background: 'rgba(48,209,88,0.1)', border: '2px solid rgba(48,209,88,0.4)' }}
                animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1.1, ease: 'linear' }}>
                <Lock size={24} style={{ color: '#30D158' }} />
              </motion.div>
              <div className="text-center">
                <p className="font-display font-semibold text-[#E8F1FF]">Signing off & generating PDF…</p>
                <p className="font-display text-xs mt-1" style={{ color: '#7A90AA' }}>Calling the backend — this is a real request</p>
              </div>
            </motion.div>
          ) : (
            <motion.div key="locked" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="w-full flex flex-col items-center gap-4">
              <motion.div
                initial={{ scale: 0, rotate: -180 }}
                animate={{ scale: 1, rotate: 0 }}
                transition={{ type: 'spring', damping: 11, stiffness: 190 }}
                className="w-16 h-16 rounded-full flex items-center justify-center"
                style={{ background: 'rgba(48,209,88,0.1)', border: '2px solid rgba(48,209,88,0.4)' }}>
                <Lock size={28} style={{ color: '#30D158' }} />
              </motion.div>
              <div className="text-center">
                <p className="font-display font-semibold text-[#E8F1FF]">Operation Signed &amp; Locked</p>
                <p className="font-display text-xs mt-1" style={{ color: '#7A90AA' }}>
                  Signed by {session?.signedBy ?? session?.anesthetist ?? '—'}
                  {session?.signedAt != null ? ` · ${formatTime(session.signedAt)}` : ''}
                </p>
                <p className="font-display text-xs mt-1" style={{ color: '#7A90AA' }}>
                  Anaesthesia PDF generated and sealed — this case now lives in Archive
                </p>
              </div>
              <div className="flex gap-2 flex-wrap justify-center">
                <Button variant="success" size="sm" icon={<Download size={13} />} onClick={handleDownload}>Download PDF</Button>
                <Button variant="outline" size="sm" icon={<Printer size={13} />} onClick={handlePrint}>Print</Button>
                <Button variant="clinical" size="sm" icon={<ArrowRight size={13} />} onClick={onGoArchive}>Go to Archive</Button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Dialog>
  );
}

// ─── Empty / guard states ──────────────────────────────────────────────────────

function ReviewEmptyState({ icon: Icon, title, description, actions }: {
  icon: React.ElementType; title: string; description: string; actions?: React.ReactNode;
}) {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-3 text-center px-6">
      <Icon size={32} className="text-slate-300" />
      <div>
        <p className="font-display text-sm font-semibold text-slate-600">{title}</p>
        <p className="font-display text-xs text-slate-400 mt-1 max-w-sm">{description}</p>
      </div>
      {actions && <div className="flex gap-2 mt-2">{actions}</div>}
    </div>
  );
}

// ─── Review Page ──────────────────────────────────────────────────────────────

export function ReviewPage() {
  const params = useParams<{ sessionId?: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const lastEndedSessionId = useSessionStore((s) => s.lastEndedSessionId);
  const storeActiveSession = useSessionStore((s) => s.activeSession);

  // M5.8.1: Review represents a COMPLETED operation and must load it fresh
  // from the backend by id -- never from transient React/zustand state.
  // A URL param (End Operation's own navigation, Archive's "Continue to
  // Review") always wins; the bare /review nav link falls back to the last
  // session this browser tab actually ended, which survives a reload
  // because sessionStore persists it (see its own docstring).
  const sessionId = params.sessionId ?? lastEndedSessionId ?? null;

  const [session, setSession] = useState<Session | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState<'not_found' | 'unreachable' | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setSession(null);
      setSessionLoading(false);
      setSessionError(null);
      return;
    }
    let cancelled = false;
    setSessionLoading(true);
    setSessionError(null);

    // End Operation navigates here the instant its POST /end fires, without
    // waiting for that request to resolve (same optimistic pattern
    // sessionStore.endSession uses elsewhere). A GET landing in that short
    // window could still see status='active' on the backend. A few short
    // retries absorb that race without masking a GENUINELY still-active
    // session for more than about a second -- the graceful "still in
    // progress" state below is still reached for that case, just not on a
    // false positive caused by request ordering.
    const fetchWithRetry = async () => {
      try {
        let s: Session = await api.getSession(sessionId);
        for (let attempt = 0; attempt < 4 && s.status !== 'completed' && !cancelled; attempt++) {
          await new Promise((r) => setTimeout(r, 350));
          if (cancelled) return;
          s = await api.getSession(sessionId);
        }
        if (!cancelled) setSession(s);
      } catch (err) {
        console.error('Failed to load session for review', err);
        if (cancelled) return;
        setSession(null);
        setSessionError(err instanceof Error && err.message.includes('404') ? 'not_found' : 'unreachable');
      } finally {
        if (!cancelled) setSessionLoading(false);
      }
    };
    void fetchWithRetry();

    return () => { cancelled = true; };
  }, [sessionId]);

  const [flaggedItems, setFlaggedItems]   = useState<FlaggedReading[]>([]);
  const [category, setCategory]           = useState<ExceptionCategory>('critical');
  const [selectedId, setSelectedId]       = useState<string>('');
  const [activeTab, setActiveTab]         = useState<TabId>('overview');
  const [signState, setSignState]         = useState<SignState>('idle');
  const [signing, setSigning]             = useState(false);
  const [loadingFlagged, setLoadingFlagged] = useState(true);
  const [readings, setReadings]           = useState<VitalObservationRow[]>([]);
  const [loadingReadings, setLoadingReadings] = useState(true);
  const [alerts, setAlerts]               = useState<AlertDto[]>([]);
  const [loadingAlerts, setLoadingAlerts] = useState(true);

  const timelineEvents = useMemo(
    () => (session ? buildCaseEvents(session, readings, alerts) : []),
    [session, readings, alerts]
  );

  // Fetch the real flagged list whenever the resolved session changes.
  useEffect(() => {
    if (!session?.id) {
      setFlaggedItems([]);
      setSelectedId('');
      setLoadingFlagged(false);
      return;
    }
    let cancelled = false;
    setLoadingFlagged(true);
    api.getFlagged(session.id)
      .then((flagged) => {
        if (cancelled) return;
        setFlaggedItems(flagged);
      })
      .catch((err) => {
        console.error('Failed to load review data', err);
        if (!cancelled) {
          toast.error('Could not load flagged readings', {
            description: "Couldn't reach the backend — check it's running and try again.",
          });
        }
      })
      .finally(() => { if (!cancelled) setLoadingFlagged(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id]);

  // The real persisted vitals timeline, same endpoint the Active Operation
  // workspace's ledger hydrates from — works for a completed session too.
  useEffect(() => {
    if (!session?.id) {
      setReadings([]);
      setLoadingReadings(false);
      return;
    }
    let cancelled = false;
    setLoadingReadings(true);
    api.getReadings(session.id)
      .then((rows) => { if (!cancelled) setReadings(rows); })
      .catch((err) => {
        console.error('Failed to load vitals timeline', err);
        if (!cancelled) {
          toast.error('Could not load the observation ledger', {
            description: "Couldn't reach the backend — check it's running and try again.",
          });
        }
      })
      .finally(() => { if (!cancelled) setLoadingReadings(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id]);

  // Real, backend-evaluated alert history for this session.
  useEffect(() => {
    if (!session?.id) {
      setAlerts([]);
      setLoadingAlerts(false);
      return;
    }
    let cancelled = false;
    setLoadingAlerts(true);
    api.getAlerts(session.id)
      .then((rows) => { if (!cancelled) setAlerts(rows); })
      .catch((err) => {
        console.error('Failed to load alerts', err);
        if (!cancelled) setAlerts([]);
      })
      .finally(() => { if (!cancelled) setLoadingAlerts(false); });
    return () => { cancelled = true; };
  }, [session?.id]);

  const categorized = useMemo(() => categorize(flaggedItems), [flaggedItems]);
  const categoryCounts = useMemo(() => ({
    critical: categorized.critical.length,
    lowConfidence: categorized.lowConfidence.length,
    corrected: categorized.corrected.length,
    resolved: categorized.resolved.length,
  }), [categorized]);
  const visibleItems = categorized[category];

  // Pick a sensible default category the first time this session's flagged
  // items finish loading -- prioritising whichever bucket actually needs
  // attention -- without overriding a category the clinician has since
  // clicked into as items get corrected/dismissed during the session.
  useEffect(() => {
    if (loadingFlagged) return;
    if (categorized.critical.length > 0) setCategory('critical');
    else if (categorized.lowConfidence.length > 0) setCategory('lowConfidence');
    else if (categorized.corrected.length > 0) setCategory('corrected');
    else setCategory('resolved');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingFlagged, session?.id]);

  // Keep selection valid whenever the category or its contents change.
  useEffect(() => {
    if (visibleItems.length === 0) {
      if (selectedId !== '') setSelectedId('');
      return;
    }
    if (!visibleItems.some((i) => i.id === selectedId)) setSelectedId(visibleItems[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, visibleItems]);

  const selectedItem  = visibleItems.find(i => i.id === selectedId) ?? visibleItems[0];
  const selectedIndex = visibleItems.findIndex(i => i.id === selectedId);

  const isCompleted = session?.status === 'completed';
  const isSigned    = session?.signedAt != null;

  const criticalTotal = flaggedItems.filter((i) => i.severity === 'critical').length;
  const criticalPendingCount = categoryCounts.critical;
  const criticalResolvedCount = criticalTotal - criticalPendingCount;
  const criticalPct = criticalTotal > 0 ? (criticalResolvedCount / criticalTotal) * 100 : 100;
  const readyToSign = criticalPendingCount === 0;
  const informationalCount = categoryCounts.lowConfidence + categoryCounts.corrected + categoryCounts.resolved;

  const handleCorrect = useCallback(async (id: string, correctedValue: string) => {
    const item = flaggedItems.find(i => i.id === id);
    if (!item) return;

    setFlaggedItems(prev => prev.map(i => i.id === id ? { ...i, status: 'corrected', correctedValue } : i));

    try {
      await api.correctFlagged(id, correctedValue, session?.anesthetist);
    } catch (err) {
      console.error('Failed to save correction', err);
      toast.error('Correction not saved', {
        description: "Couldn't reach the backend — check it's running and try again.",
      });
      setFlaggedItems(prev => prev.map(i => i.id === id ? { ...i, status: 'pending', correctedValue: undefined } : i));
    }
  }, [flaggedItems, session?.anesthetist, toast]);

  const handleDismiss = useCallback(async (id: string) => {
    const item = flaggedItems.find(i => i.id === id);
    if (!item) return;

    setFlaggedItems(prev => prev.map(i => i.id === id ? { ...i, status: 'dismissed' } : i));

    try {
      await api.dismissFlagged(id, session?.anesthetist);
    } catch (err) {
      console.error('Failed to save dismissal', err);
      toast.error('Dismissal not saved', {
        description: "Couldn't reach the backend — check it's running and try again.",
      });
      setFlaggedItems(prev => prev.map(i => i.id === id ? { ...i, status: 'pending' } : i));
    }
  }, [flaggedItems, session?.anesthetist, toast]);

  const handleNavigate = useCallback((dir: 1 | -1) => {
    const idx = Math.max(0, Math.min(visibleItems.length - 1, selectedIndex + dir));
    if (visibleItems[idx]) setSelectedId(visibleItems[idx].id);
  }, [visibleItems, selectedIndex]);

  const handleSignConfirm = useCallback(async () => {
    if (!session?.id) return;
    setSigning(true);
    setSignState('signing');
    try {
      const signed = await api.signSession(session.id, session.anesthetist, 'typed');
      setSession(signed);
      setSignState('locked');
    } catch (err) {
      console.error('Failed to sign session', err);
      const message = err instanceof Error ? err.message : '';
      toast.error('Could not sign off', {
        description: message.includes('409')
          ? 'The session must be ended before it can be signed off — use "End Operation" first.'
          : "Couldn't reach the backend — check it's running and try again.",
      });
      setSignState('idle');
    } finally {
      setSigning(false);
    }
  }, [session, toast]);

  // ── Guard states ────────────────────────────────────────────────────────

  if (!sessionId) {
    return (
      <div className="h-full" style={{ background: '#F8FAFC' }}>
        <ReviewEmptyState
          icon={ClipboardList}
          title="No completed operation to review yet"
          description="Review & Sign-off shows a case once it has been ended. End the active operation, or open a previously recorded one from Archive."
          actions={
            <>
              {storeActiveSession && (
                <Button variant="primary" size="sm" onClick={() => navigate('/operation')}>Go to Active Operation</Button>
              )}
              <Button variant="outline" size="sm" onClick={() => navigate('/archive')}>Open Archive</Button>
            </>
          }
        />
      </div>
    );
  }

  if (sessionLoading) {
    return (
      <div className="h-full flex items-center justify-center" style={{ background: '#F8FAFC' }}>
        <p className="font-display text-sm text-slate-400">Loading operation record…</p>
      </div>
    );
  }

  if (sessionError || !session) {
    return (
      <div className="h-full" style={{ background: '#F8FAFC' }}>
        <ReviewEmptyState
          icon={AlertTriangle}
          title={sessionError === 'not_found' ? 'This operation could not be found' : "Couldn't reach the backend"}
          description={sessionError === 'not_found'
            ? 'The session this link points to no longer exists.'
            : "Check the backend is running and try again."}
          actions={<Button variant="outline" size="sm" onClick={() => navigate('/archive')}>Open Archive</Button>}
        />
      </div>
    );
  }

  if (!isCompleted) {
    const isThisTheLiveSession = storeActiveSession?.id === session.id;
    return (
      <div className="h-full" style={{ background: '#F8FAFC' }}>
        <ReviewEmptyState
          icon={Info}
          title="This operation is still in progress"
          description="Review & Sign-off only opens once an operation has been ended — there's nothing to review yet."
          actions={isThisTheLiveSession
            ? <Button variant="primary" size="sm" onClick={() => navigate('/operation')}>Go to Active Operation</Button>
            : undefined}
        />
      </div>
    );
  }

  // ── Completed operation: full Review & Sign-off ────────────────────────

  return (
    <motion.div className="h-full flex flex-col" style={{ background: '#F8FAFC' }}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.22 }}>

      {/* ── Transition banner ────────────────────────────────────────────── */}
      <div className={cn(
        'flex items-center gap-3 px-5 py-2.5 flex-shrink-0 border-b',
        isSigned ? 'bg-emerald-50 border-emerald-100' : 'bg-blue-50 border-blue-100',
      )}>
        {isSigned ? <Lock size={15} className="text-emerald-600 flex-shrink-0" /> : <CheckCircle2 size={15} className="text-blue-600 flex-shrink-0" />}
        <div className="min-w-0">
          <p className="font-display text-xs font-semibold text-slate-700">
            {isSigned ? 'Signed & locked' : 'Operation completed'}
          </p>
          <p className="font-display text-[11px] text-slate-500">
            {isSigned
              ? `Signed by ${session.signedBy ?? session.anesthetist} · ${session.signedAt != null ? formatTime(session.signedAt) : ''} — this record now lives in Archive.`
              : 'Review the recorded observations before signing off.'}
          </p>
        </div>
        {isSigned && (
          <Button variant="outline" size="xs" className="ml-auto flex-shrink-0" icon={<ArrowRight size={11} />} onClick={() => navigate('/archive')}>
            View in Archive
          </Button>
        )}
      </div>

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <header className="flex items-center flex-shrink-0 bg-white border-b border-slate-200" style={{ height: 56 }}>
        {/* Patient info */}
        <div className="flex items-center gap-3 px-5 h-full border-r border-slate-200 flex-shrink-0" style={{ minWidth: 300 }}>
          <div className="w-8 h-8 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center flex-shrink-0">
            <User size={14} className="text-slate-500" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-slate-800">{session.patient.id}</span>
              {session.patient.asa && (
                <span className="font-display text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-600">
                  ASA {session.patient.asa}
                </span>
              )}
            </div>
            <p className="font-display text-[10px] text-slate-500 truncate">
              {session.procedure} · {session.anesthetist}
            </p>
          </div>
        </div>

        {/* Review progress */}
        <div className="flex items-center gap-4 px-5 h-full border-r border-slate-200 flex-1">
          <div className="flex items-center gap-3 w-full max-w-sm">
            <CircularProgress
              value={criticalPct}
              size={34} strokeWidth={3} color="#30D158" trackColor="#E2E8F0"
              label={<span className="font-mono text-[9px] font-bold text-slate-700">{criticalResolvedCount}/{criticalTotal}</span>}
            />
            <div className="flex-1 min-w-0">
              <p className="font-display text-xs text-slate-700 font-medium">
                {loadingFlagged
                  ? 'Loading exceptions…'
                  : criticalPendingCount > 0
                    ? `${criticalPendingCount} blocking exception${criticalPendingCount === 1 ? '' : 's'} pending`
                    : criticalTotal > 0
                      ? 'All blocking exceptions resolved'
                      : 'No blocking exceptions'}
              </p>
              <ProgressBar value={criticalPct}
                color="#30D158" size="xs" trackColor="#E2E8F0" className="mt-1" />
            </div>
          </div>
          {!loadingFlagged && categoryCounts.lowConfidence > 0 && (
            <span className="font-display text-[10px] text-slate-400 flex-shrink-0 hidden lg:inline">
              +{categoryCounts.lowConfidence} informational (no action required)
            </span>
          )}
          <AnimatePresence>
            {readyToSign && !loadingFlagged && !isSigned && (
              <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-50 border border-emerald-200 flex-shrink-0">
                <CheckCircle2 size={12} className="text-emerald-600" />
                <span className="font-display text-[10px] font-semibold text-emerald-700">Ready to sign off</span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Sign off button */}
        <div className="flex items-center px-5 h-full flex-shrink-0">
          <Button variant={readyToSign && !isSigned ? 'clinical' : 'secondary'} size="sm"
            icon={<Lock size={13} />}
            disabled={loadingFlagged || !readyToSign || isSigned || signState !== 'idle'}
            onClick={() => setSignState('confirming')}>
            {isSigned ? 'Signed & Locked' : 'Sign Off Operation'}
          </Button>
        </div>
      </header>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden min-h-0">

        {/* ── Left sidebar: Exceptions & Alerts ───────────────────────────── */}
        <aside className="flex flex-col flex-shrink-0 bg-white border-r border-slate-200 overflow-hidden" style={{ width: 360 }}>
          <div className="px-4 py-3 border-b border-slate-200 flex-shrink-0">
            <div className="flex items-center gap-2 mb-2.5">
              <AlertTriangle size={13} className="text-slate-400" />
              <span className="font-display text-xs font-semibold text-slate-700">Exceptions &amp; Alerts</span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {(Object.keys(EXCEPTION_CATEGORY_CFG) as ExceptionCategory[]).map((cat) => {
                const cfg = EXCEPTION_CATEGORY_CFG[cat];
                const count = categoryCounts[cat];
                const selected = category === cat;
                const Icon = cfg.icon;
                return (
                  <button key={cat} onClick={() => setCategory(cat)}
                    className={cn(
                      'flex items-center justify-between gap-1.5 px-2.5 py-1.5 rounded-lg border text-left transition-colors',
                      selected ? 'bg-slate-50 border-slate-300' : 'border-slate-100 hover:bg-slate-50',
                    )}>
                    <span className="flex items-center gap-1.5 min-w-0">
                      <Icon size={11} style={{ color: cfg.color }} className="flex-shrink-0" />
                      <span className="font-display text-[10px] text-slate-600 truncate">{cfg.label}</span>
                    </span>
                    <span className="font-mono text-[11px] font-bold flex-shrink-0" style={{ color: count > 0 ? cfg.color : '#CBD5E1' }}>
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Category list */}
          <div className="overflow-y-auto flex-1 min-h-0">
            {loadingFlagged ? (
              <div className="flex flex-col items-center justify-center h-32 text-slate-400 gap-2">
                <p className="font-display text-xs">Loading exceptions…</p>
              </div>
            ) : visibleItems.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-32 text-slate-400 gap-2 px-4 text-center">
                <CheckCircle2 size={22} className="opacity-30" />
                <p className="font-display text-xs">{EMPTY_CATEGORY_MESSAGE[category]}</p>
              </div>
            ) : (
              visibleItems.map(item => (
                <QueueCard key={item.id} item={item} selected={selectedId === item.id}
                  onSelect={() => setSelectedId(item.id)} />
              ))
            )}
          </div>

          {/* Inspector */}
          {selectedItem && (
            <ReadingInspector key={selectedItem.id}
              item={selectedItem} allItems={visibleItems}
              currentIndex={selectedIndex}
              onNavigate={handleNavigate}
              onCorrect={handleCorrect}
              onDismiss={handleDismiss}
              locked={isSigned}
            />
          )}
        </aside>

        {/* ── Right panel ──────────────────────────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {/* Tab bar */}
          <div className="bg-white border-b border-slate-200 flex-shrink-0">
            <div className="flex items-end px-2" role="tablist" aria-label="Review panels">
              {TABS.map(({ id, label, icon: Icon }) => (
                <button key={id} onClick={() => setActiveTab(id)}
                  role="tab"
                  aria-selected={activeTab === id}
                  aria-controls={`review-panel-${id}`}
                  id={`review-tab-${id}`}
                  className={cn(
                    'relative flex items-center gap-2 px-4 py-3.5 font-display text-xs transition-colors',
                    activeTab === id ? 'text-slate-800 font-semibold' : 'text-slate-500 hover:text-slate-700',
                  )}>
                  <Icon size={12} aria-hidden="true" />
                  {label}
                  {activeTab === id && (
                    <motion.div layoutId="review-tab-indicator"
                      className="absolute bottom-0 left-0 right-0 h-0.5 bg-slate-800" />
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-hidden min-h-0">
            <AnimatePresence mode="wait">
              <motion.div key={activeTab} className="h-full"
                role="tabpanel"
                id={`review-panel-${activeTab}`}
                aria-labelledby={`review-tab-${activeTab}`}
                initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -3 }} transition={{ duration: 0.14 }}>
                {activeTab === 'overview' && (
                  <OverviewTab session={session} readings={readings} alerts={alerts} loadingAlerts={loadingAlerts} isSigned={isSigned} />
                )}
                {activeTab === 'trends' && (
                  loadingReadings
                    ? <div className="flex items-center justify-center h-full text-slate-400"><p className="font-display text-sm">Loading trends…</p></div>
                    : <TrendsTab readings={readings} />
                )}
                {activeTab === 'observations' && (
                  <ObservationLedgerTab readings={readings} loading={loadingReadings} />
                )}
                {activeTab === 'timeline' && (
                  <CaseTimelinePanel events={timelineEvents} />
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </div>

      {/* ── Dialogs ───────────────────────────────────────────────────────── */}
      <SignDialog
        open={signState === 'confirming'}
        onClose={() => setSignState('idle')}
        onConfirm={handleSignConfirm}
        session={session}
        criticalTotal={criticalTotal}
        informationalCount={informationalCount}
        signing={signing}
      />
      <PdfDialog
        signState={signState}
        onClose={() => setSignState('idle')}
        sessionId={session.id}
        onGoArchive={() => navigate('/archive')}
        session={session}
      />
    </motion.div>
  );
}
