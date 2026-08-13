import { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useQuery } from '@tanstack/react-query';
import {
  Search, Clock, User, Activity, Archive, Stethoscope,
  Download, Eye, FileText, Filter, X, LayoutList,
  Table2, CheckCircle, Lock, Printer,
} from 'lucide-react';
import { api } from '@/lib/api';
import { formatDate, formatDuration, cn } from '@/lib/utils';
import { Button } from '@/design-system/components/Button';
import { Badge } from '@/design-system/components/Badge';
import { ProgressBar, CircularProgress } from '@/design-system/components/Progress';
import type { ArchivedSession } from '@/types/session';

// ─── Constants ────────────────────────────────────────────────────────────────

const ASA_COLORS: Record<number, string> = {
  1: '#00FF88', 2: '#00D4FF', 3: '#FFD600', 4: '#FF9500', 5: '#FF3B30', 6: '#FF3B30',
};

const DOCTORS = ['All Doctors', 'Dr. Priya Sharma', 'Dr. Arjun Mehta'];
const ASA_OPTIONS = ['All ASA', 'ASA I', 'ASA II', 'ASA III', 'ASA IV', 'ASA V', 'ASA VI'];

// ─── Helpers ─────────────────────────────────────────────────────────────────

function asaLabel(cls?: number) {
  if (!cls) return '—';
  const map: Record<number, string> = { 1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI' };
  return map[cls] ?? '—';
}

function durationOf(s: ArchivedSession) {
  return formatDuration((s.endTime ?? s.startTime) - s.startTime);
}

// ─── PDF Preview Component ────────────────────────────────────────────────────

function PdfPreview({ session, onClose }: { session: ArchivedSession; onClose: () => void }) {
  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [ready, setReady] = useState(false);
  const ivRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const generate = () => {
    setGenerating(true);
    setProgress(0);
    let p = 0;
    ivRef.current = setInterval(() => {
      p += Math.random() * 8 + 4;
      if (p >= 100) {
        setProgress(100);
        setReady(true);
        setGenerating(false);
        clearInterval(ivRef.current);
      } else {
        setProgress(p);
      }
    }, 100);
  };

  useEffect(() => () => clearInterval(ivRef.current), []);

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        className="w-full max-w-2xl bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col"
        style={{ maxHeight: 'calc(100vh - 3rem)' }}
        initial={{ scale: 0.94, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.96, opacity: 0 }}
        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      >
        {/* PDF toolbar */}
        <div className="flex items-center justify-between px-5 py-3 bg-slate-800 flex-shrink-0">
          <div className="flex items-center gap-2">
            <FileText size={16} className="text-slate-300" />
            <span className="font-display text-sm font-medium text-slate-200">
              Anaesthesia Record — {session.patient.id}
            </span>
            {ready && <Badge variant="success" size="xs">Ready</Badge>}
          </div>
          <div className="flex items-center gap-1.5">
            {ready && (
              <>
                <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 transition-colors font-display text-xs">
                  <Printer size={13} /> Print
                </button>
                <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors font-display text-xs">
                  <Download size={13} /> Download
                </button>
              </>
            )}
            {!ready && !generating && (
              <button
                onClick={generate}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors font-display text-xs"
              >
                <FileText size={13} /> Generate PDF
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition-colors ml-1"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* PDF generation progress */}
        {generating && (
          <div className="px-5 py-3 bg-slate-700 border-t border-slate-600 flex items-center gap-3 flex-shrink-0">
            <div className="flex-1">
              <ProgressBar value={progress} size="sm" color="#32ADE6" animated />
            </div>
            <span className="font-mono text-xs text-slate-300 w-10 text-right">{Math.round(progress)}%</span>
          </div>
        )}

        {/* PDF document */}
        <div className="flex-1 overflow-y-auto bg-slate-100 p-6">
          <div className="bg-white shadow-md rounded-lg max-w-[595px] mx-auto overflow-hidden">
            {/* Hospital header */}
            <div className="bg-slate-800 px-8 py-5 flex items-start justify-between">
              <div>
                <div className="font-display font-bold text-white text-lg tracking-wide">VITAL</div>
                <div className="font-display text-slate-400 text-xs mt-0.5 tracking-wider uppercase">Smart Anaesthesia Vitals Digitizer</div>
              </div>
              <div className="text-right">
                <div className="font-display text-xs text-slate-400">Record ID</div>
                <div className="font-mono text-sm text-white">{session.id.slice(0, 18)}</div>
              </div>
            </div>

            {/* Watermark / status bar */}
            <div className="flex items-center gap-2 px-8 py-2 bg-green-50 border-b border-green-200">
              <Lock size={12} className="text-green-600" />
              <span className="font-display text-xs text-green-700 font-medium">Digitally signed · Read-only · {formatDate(session.startTime)}</span>
            </div>

            <div className="px-8 py-6 space-y-6">
              {/* Patient & procedure */}
              <section>
                <h3 className="font-display text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <User size={11} /> Patient Information
                </h3>
                <div className="grid grid-cols-2 gap-x-8 gap-y-2">
                  {[
                    ['Patient ID', session.patient.id],
                    ['Procedure', session.procedure],
                    ['Anaesthetist', session.anesthetist],
                    ['ASA Class', `ASA ${asaLabel(session.patient.asa)}`],
                    ['Age', session.patient.age ? `${session.patient.age} years` : '—'],
                    ['Weight', session.patient.weight ? `${session.patient.weight} kg` : '—'],
                    ['Date', formatDate(session.startTime)],
                    ['Duration', durationOf(session)],
                  ].map(([label, value]) => (
                    <div key={label} className="flex flex-col">
                      <span className="font-display text-[10px] text-slate-400 uppercase tracking-wider">{label}</span>
                      <span className="font-display text-sm text-slate-800 font-medium">{value}</span>
                    </div>
                  ))}
                </div>
              </section>

              <hr className="border-slate-100" />

              {/* Vital summary */}
              <section>
                <h3 className="font-display text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <Activity size={11} /> Vital Signs Summary
                </h3>
                <div className="grid grid-cols-4 gap-3">
                  {[
                    { label: 'Avg HR',    value: `${session.vitalSummary.avgHr}`,    unit: 'bpm',  color: '#00AA55' },
                    { label: 'Min SpO₂',  value: `${session.vitalSummary.minSpo2}`,  unit: '%',    color: '#0088AA' },
                    { label: 'Avg EtCO₂', value: `${session.vitalSummary.avgEtco2}`, unit: 'mmHg', color: '#AA8800' },
                    { label: 'Duration',  value: `${session.vitalSummary.durationMin}`, unit: 'min', color: '#7744AA' },
                  ].map(({ label, value, unit, color }) => (
                    <div
                      key={label}
                      className="rounded-xl border p-3 text-center"
                      style={{ borderColor: `${color}30`, backgroundColor: `${color}08` }}
                    >
                      <div className="font-display text-xl font-semibold" style={{ color }}>{value}</div>
                      <div className="font-display text-[10px] mt-0.5" style={{ color, opacity: 0.7 }}>{unit}</div>
                      <div className="font-display text-[10px] text-slate-400 mt-1">{label}</div>
                    </div>
                  ))}
                </div>
              </section>

              <hr className="border-slate-100" />

              {/* AI OCR summary */}
              <section>
                <h3 className="font-display text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <FileText size={11} /> AI Processing Summary
                </h3>
                <div className="space-y-2">
                  {[
                    { label: 'Total frames processed', value: '3,847' },
                    { label: 'OCR confidence (avg)',   value: '94.2%' },
                    { label: 'Flagged readings',        value: '4' },
                    { label: 'Human corrections',       value: '3' },
                    { label: 'Auto-dismissed',          value: '1' },
                  ].map(({ label, value }) => (
                    <div key={label} className="flex items-center justify-between py-1.5 border-b border-slate-50">
                      <span className="font-display text-xs text-slate-500">{label}</span>
                      <span className="font-display text-xs font-semibold text-slate-800">{value}</span>
                    </div>
                  ))}
                </div>
              </section>

              <hr className="border-slate-100" />

              {/* Signature block */}
              <section>
                <h3 className="font-display text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <CheckCircle size={11} /> Sign-off
                </h3>
                <div className="rounded-xl border border-green-200 bg-green-50 p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <CheckCircle size={14} className="text-green-600" />
                    <span className="font-display text-xs font-semibold text-green-700">Record reviewed and signed</span>
                  </div>
                  <div className="font-display text-xs text-green-600">{session.anesthetist}</div>
                  <div className="font-display text-[10px] text-green-500 mt-0.5">{formatDate(session.endTime ?? session.startTime)} · Digital signature verified</div>
                </div>
              </section>
            </div>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

// ─── Session card (list view) ─────────────────────────────────────────────────

function SessionCard({
  session, selected, onClick,
}: {
  session: ArchivedSession;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <motion.button
      onClick={onClick}
      className={cn(
        'w-full flex flex-col px-4 py-3.5 border-b border-slate-50 text-left transition-all duration-100',
        selected
          ? 'bg-blue-50 border-l-2 border-l-blue-500'
          : 'hover:bg-slate-50 border-l-2 border-l-transparent'
      )}
      whileHover={{ x: selected ? 0 : 1 }}
      transition={{ duration: 0.1 }}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <div
            className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold"
            style={{
              backgroundColor: ASA_COLORS[session.patient.asa ?? 1],
              color: (session.patient.asa ?? 1) <= 2 ? '#000' : '#fff',
            }}
          >
            {asaLabel(session.patient.asa)}
          </div>
          <span className="font-mono text-vital-sm text-slate-700 font-medium">{session.patient.id}</span>
        </div>
        <span className="font-display text-[10px] text-slate-400">{formatDate(session.startTime)}</span>
      </div>
      <div className="mt-1.5 font-display text-vital-xs text-slate-500 truncate">{session.procedure}</div>
      <div className="mt-1.5 flex items-center gap-3 text-[10px] font-display text-slate-400">
        <span className="flex items-center gap-1"><Clock size={10} />{durationOf(session)}</span>
        <span className="flex items-center gap-1"><Stethoscope size={10} />{session.anesthetist.replace('Dr. ', '')}</span>
        <span className="ml-auto font-mono text-green-600">✓ Signed</span>
      </div>
    </motion.button>
  );
}

// ─── Table row ────────────────────────────────────────────────────────────────

function TableRow({
  session, index, selected, onClick, onPdf,
}: {
  session: ArchivedSession;
  index: number;
  selected: boolean;
  onClick: () => void;
  onPdf: (e: React.MouseEvent) => void;
}) {
  return (
    <motion.tr
      onClick={onClick}
      className={cn(
        'cursor-pointer transition-colors',
        selected ? 'bg-blue-50' : 'hover:bg-slate-50'
      )}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04 }}
    >
      <td className="px-4 py-3 font-mono text-sm text-slate-700">{session.patient.id}</td>
      <td className="px-4 py-3 font-display text-xs text-slate-600 max-w-[180px] truncate">{session.procedure}</td>
      <td className="px-4 py-3 font-display text-xs text-slate-500">{session.anesthetist.replace('Dr. ', '')}</td>
      <td className="px-4 py-3">
        <span
          className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold font-display"
          style={{
            backgroundColor: `${ASA_COLORS[session.patient.asa ?? 1]}20`,
            color: ASA_COLORS[session.patient.asa ?? 1],
          }}
        >
          ASA {asaLabel(session.patient.asa)}
        </span>
      </td>
      <td className="px-4 py-3 font-display text-xs text-slate-500">{formatDate(session.startTime)}</td>
      <td className="px-4 py-3 font-display text-xs text-slate-500">{durationOf(session)}</td>
      <td className="px-4 py-3">
        <span className="inline-flex items-center gap-1 text-[10px] font-display text-green-600">
          <CheckCircle size={10} /> Signed
        </span>
      </td>
      <td className="px-4 py-3">
        <button
          onClick={onPdf}
          className="p-1.5 rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
          title="View PDF"
        >
          <Eye size={14} />
        </button>
      </td>
    </motion.tr>
  );
}

// ─── Detail panel ─────────────────────────────────────────────────────────────

function DetailPanel({ session, onPdf }: { session: ArchivedSession; onPdf: () => void }) {
  return (
    <motion.div
      key={session.id}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      className="space-y-4"
    >
      {/* Session header */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="font-display font-bold text-slate-900 text-xl">{session.patient.id}</h2>
            <p className="font-display text-slate-500 text-vital-sm mt-0.5">{session.procedure}</p>
          </div>
          <div
            className="px-3 py-1.5 rounded-xl font-display text-vital-xs font-bold"
            style={{
              backgroundColor: `${ASA_COLORS[session.patient.asa ?? 1]}20`,
              color: ASA_COLORS[session.patient.asa ?? 1],
              border: `1px solid ${ASA_COLORS[session.patient.asa ?? 1]}40`,
            }}
          >
            ASA {asaLabel(session.patient.asa)}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 text-vital-sm font-display mb-4">
          {[
            { icon: Stethoscope, label: 'Anaesthetist', value: session.anesthetist },
            { icon: Clock,       label: 'Date',         value: formatDate(session.startTime) },
            { icon: Clock,       label: 'Duration',     value: durationOf(session) },
            { icon: User,        label: 'Age / Weight', value: `${session.patient.age ?? '—'} yrs / ${session.patient.weight ?? '—'} kg` },
          ].map(({ icon: Icon, label, value }) => (
            <div key={label} className="flex items-center gap-2.5">
              <Icon size={14} className="text-slate-400 flex-shrink-0" />
              <div>
                <div className="text-[10px] text-slate-400 uppercase tracking-wider">{label}</div>
                <div className="text-slate-700 text-sm">{value}</div>
              </div>
            </div>
          ))}
        </div>
        {/* Actions */}
        <div className="flex gap-2 pt-3 border-t border-slate-100">
          <Button variant="outline" size="sm" icon={<Eye size={14} />} onClick={onPdf} className="flex-1 justify-center">
            View Record
          </Button>
          <Button variant="outline" size="sm" icon={<Download size={14} />} onClick={onPdf} className="flex-1 justify-center">
            Download PDF
          </Button>
        </div>
      </div>

      {/* Vital summary */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <h3 className="font-display font-semibold text-slate-800 mb-4 text-sm">Vital Signs Summary</h3>
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Avg HR',    value: session.vitalSummary.avgHr,      unit: 'bpm',  color: '#00AA55' },
            { label: 'Min SpO₂',  value: session.vitalSummary.minSpo2,    unit: '%',    color: '#0088AA' },
            { label: 'Avg EtCO₂', value: session.vitalSummary.avgEtco2,   unit: 'mmHg', color: '#AA8800' },
            { label: 'Duration',  value: session.vitalSummary.durationMin, unit: 'min',  color: '#7744AA' },
          ].map(({ label, value, unit, color }) => (
            <div
              key={label}
              className="rounded-xl border p-3 text-center"
              style={{ borderColor: `${color}30`, backgroundColor: `${color}08` }}
            >
              <div className="font-display text-2xl font-semibold" style={{ color }}>{value}</div>
              <div className="font-display text-[10px] mt-0.5" style={{ color, opacity: 0.7 }}>{unit}</div>
              <div className="font-display text-[10px] text-slate-400 mt-1">{label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* AI processing */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <h3 className="font-display font-semibold text-slate-800 mb-3 text-sm">AI Processing</h3>
        <div className="space-y-2.5">
          {[
            { label: 'Frames processed', value: '3,847', pct: 100 },
            { label: 'Avg confidence',   value: '94.2%', pct: 94  },
            { label: 'Human review',     value: '4 items', pct: 100 },
          ].map(({ label, value, pct }) => (
            <div key={label}>
              <div className="flex items-center justify-between mb-1">
                <span className="font-display text-xs text-slate-500">{label}</span>
                <span className="font-display text-xs font-medium text-slate-700">{value}</span>
              </div>
              <ProgressBar value={pct} size="sm" color="#30D158" />
            </div>
          ))}
        </div>
      </div>

      {/* Sign-off */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-green-50 border border-green-200">
          <CheckCircle size={16} className="text-green-500 flex-shrink-0" />
          <div>
            <div className="font-display text-xs font-semibold text-green-700">Signed & locked</div>
            <div className="font-display text-[10px] text-green-500 mt-0.5">{session.anesthetist} · {formatDate(session.startTime)}</div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export function ArchivePage() {
  const { data: archivedSessions = [] } = useQuery({
    queryKey: ['sessions', 'completed'],
    queryFn: () => api.listSessions('completed'),
  });
  const [search, setSearch]       = useState('');
  const [selected, setSelected]   = useState<string | null>(null);
  const [viewMode, setViewMode]   = useState<'list' | 'table'>('list');
  const [doctorFilter, setDoctorFilter] = useState('All Doctors');
  const [asaFilter, setAsaFilter]       = useState('All ASA');
  const [showFilters, setShowFilters]   = useState(false);
  const [pdfSession, setPdfSession]     = useState<ArchivedSession | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Keyboard shortcut: / to focus search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT') {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if (e.key === 'Escape' && document.activeElement === searchRef.current) {
        searchRef.current?.blur();
        setSearch('');
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // Select the most recent session once the archive loads
  useEffect(() => {
    if (selected === null && archivedSessions.length > 0) {
      setSelected(archivedSessions[0].id);
    }
  }, [archivedSessions, selected]);

  const filtered = archivedSessions.filter((s) => {
    const searchHit =
      s.patient.id.toLowerCase().includes(search.toLowerCase()) ||
      s.procedure.toLowerCase().includes(search.toLowerCase()) ||
      s.anesthetist.toLowerCase().includes(search.toLowerCase());
    const doctorHit = doctorFilter === 'All Doctors' || s.anesthetist === doctorFilter;
    const asaHit    = asaFilter === 'All ASA' || `ASA ${asaLabel(s.patient.asa)}` === asaFilter;
    return searchHit && doctorHit && asaHit;
  });

  const selectedSession = archivedSessions.find((s) => s.id === selected) ?? null;

  const activeFilterCount =
    (doctorFilter !== 'All Doctors' ? 1 : 0) +
    (asaFilter !== 'All ASA' ? 1 : 0);

  const openPdf = useCallback((s: ArchivedSession) => setPdfSession(s), []);

  return (
    <motion.div
      className="h-full bg-[#F0F4F8] flex flex-col"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-5 py-3 flex items-center gap-3 flex-shrink-0">
        <div className="flex items-center gap-2 mr-2">
          <Archive size={18} className="text-slate-500" />
          <h1 className="font-display font-semibold text-slate-900 text-lg">Session Archive</h1>
        </div>

        {/* Search */}
        <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 w-64 group focus-within:border-blue-400 focus-within:bg-white transition-all">
          <Search size={14} className="text-slate-400 flex-shrink-0 group-focus-within:text-blue-500 transition-colors" />
          <input
            ref={searchRef}
            type="text"
            placeholder="Search… (press /)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-transparent font-display text-sm text-slate-700 placeholder-slate-400 focus:outline-none flex-1 min-w-0"
          />
          {search && (
            <button onClick={() => setSearch('')} className="text-slate-300 hover:text-slate-500 transition-colors">
              <X size={12} />
            </button>
          )}
        </div>

        {/* Filter toggle */}
        <button
          onClick={() => setShowFilters(v => !v)}
          className={cn(
            'flex items-center gap-1.5 px-3 py-2 rounded-xl border font-display text-xs transition-all',
            showFilters || activeFilterCount > 0
              ? 'bg-blue-50 border-blue-300 text-blue-700'
              : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-slate-100'
          )}
        >
          <Filter size={13} />
          Filters
          {activeFilterCount > 0 && (
            <span className="ml-0.5 w-4 h-4 rounded-full bg-blue-500 text-white text-[10px] flex items-center justify-center font-bold">
              {activeFilterCount}
            </span>
          )}
        </button>

        <div className="flex-1" />

        {/* View toggle */}
        <div className="flex items-center gap-0.5 bg-slate-100 rounded-xl p-0.5">
          {([['list', LayoutList], ['table', Table2]] as const).map(([mode, Icon]) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={cn(
                'p-2 rounded-lg transition-all',
                viewMode === mode ? 'bg-white text-slate-700 shadow-sm' : 'text-slate-400 hover:text-slate-600'
              )}
              title={mode === 'list' ? 'List view' : 'Table view'}
            >
              <Icon size={15} />
            </button>
          ))}
        </div>

        <span className="font-display text-xs text-slate-400">{filtered.length} sessions</span>
      </header>

      {/* Filter bar */}
      <AnimatePresence>
        {showFilters && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden bg-white border-b border-slate-200"
          >
            <div className="px-5 py-3 flex items-center gap-3 flex-wrap">
              <span className="font-display text-xs text-slate-500 uppercase tracking-wider">Filter by:</span>

              {/* Doctor filter */}
              <div className="flex items-center gap-1 flex-wrap">
                {DOCTORS.map((d) => (
                  <button
                    key={d}
                    onClick={() => setDoctorFilter(d)}
                    className={cn(
                      'px-3 py-1 rounded-lg border font-display text-xs transition-all',
                      doctorFilter === d
                        ? 'bg-blue-50 border-blue-300 text-blue-700 font-medium'
                        : 'bg-slate-50 border-slate-200 text-slate-500 hover:bg-slate-100'
                    )}
                  >
                    {d}
                  </button>
                ))}
              </div>

              <div className="w-px h-5 bg-slate-200" />

              {/* ASA filter */}
              <div className="flex items-center gap-1 flex-wrap">
                {ASA_OPTIONS.map((a) => (
                  <button
                    key={a}
                    onClick={() => setAsaFilter(a)}
                    className={cn(
                      'px-3 py-1 rounded-lg border font-display text-xs transition-all',
                      asaFilter === a
                        ? 'bg-blue-50 border-blue-300 text-blue-700 font-medium'
                        : 'bg-slate-50 border-slate-200 text-slate-500 hover:bg-slate-100'
                    )}
                  >
                    {a}
                  </button>
                ))}
              </div>

              {activeFilterCount > 0 && (
                <button
                  onClick={() => { setDoctorFilter('All Doctors'); setAsaFilter('All ASA'); }}
                  className="ml-auto flex items-center gap-1 px-3 py-1 rounded-lg text-xs font-display text-slate-400 hover:text-red-500 transition-colors"
                >
                  <X size={11} /> Clear all
                </button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* ── Left panel: list or table ── */}
        <div className={cn(
          'flex flex-col flex-shrink-0 bg-white border-r border-slate-200',
          viewMode === 'list' ? 'w-80' : 'flex-1'
        )}>
          {viewMode === 'list' ? (
            <div className="flex-1 overflow-y-auto">
              {filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-slate-300">
                  <Archive size={28} className="mb-2" />
                  <p className="font-display text-sm text-slate-400">No sessions found</p>
                  {(search || activeFilterCount > 0) && (
                    <button
                      onClick={() => { setSearch(''); setDoctorFilter('All Doctors'); setAsaFilter('All ASA'); }}
                      className="mt-2 font-display text-xs text-blue-500 hover:underline"
                    >
                      Clear filters
                    </button>
                  )}
                </div>
              ) : (
                filtered.map((session, i) => (
                  <motion.div
                    key={session.id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.04, duration: 0.2 }}
                  >
                    <SessionCard
                      session={session}
                      selected={selected === session.id}
                      onClick={() => setSelected(session.id === selected ? null : session.id)}
                    />
                  </motion.div>
                ))
              )}
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              {filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-slate-300">
                  <Archive size={28} className="mb-2" />
                  <p className="font-display text-sm text-slate-400">No sessions found</p>
                </div>
              ) : (
                <table className="w-full text-left">
                  <thead className="sticky top-0 bg-white border-b border-slate-200">
                    <tr>
                      {['Patient', 'Procedure', 'Anaesthetist', 'ASA', 'Date', 'Duration', 'Status', ''].map((h) => (
                        <th key={h} className="px-4 py-2.5 font-display text-[10px] text-slate-400 uppercase tracking-wider font-semibold whitespace-nowrap">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {filtered.map((session, i) => (
                      <TableRow
                        key={session.id}
                        session={session}
                        index={i}
                        selected={selected === session.id}
                        onClick={() => setSelected(session.id === selected ? null : session.id)}
                        onPdf={(e) => { e.stopPropagation(); openPdf(session); }}
                      />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>

        {/* ── Right panel: detail (only in list mode) ── */}
        {viewMode === 'list' && (
          <div className="flex-1 overflow-y-auto p-5">
            <AnimatePresence mode="wait">
              {selectedSession ? (
                <DetailPanel
                  key={selectedSession.id}
                  session={selectedSession}
                  onPdf={() => openPdf(selectedSession)}
                />
              ) : (
                <motion.div
                  key="empty"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="flex flex-col items-center justify-center h-full"
                >
                  <Archive size={40} className="text-slate-200 mb-3" />
                  <p className="font-display text-slate-400">Select a session to view details</p>
                  <p className="font-display text-xs text-slate-300 mt-1">
                    {archivedSessions.length} sessions in archive
                  </p>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}
      </div>

      {/* PDF Preview modal */}
      <AnimatePresence>
        {pdfSession && (
          <PdfPreview session={pdfSession} onClose={() => setPdfSession(null)} />
        )}
      </AnimatePresence>
    </motion.div>
  );
}
