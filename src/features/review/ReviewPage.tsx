import { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle, AlertCircle, CheckCircle2, ChevronRight, ChevronLeft,
  Edit3, Check, X, Lock, FileText, ZoomIn, ZoomOut,
  Plus, Pill, Activity, Scissors, ShieldCheck,
  Download, ArrowRight, Eye, Camera, Clock, Info,
} from 'lucide-react';
import { Button } from '@/design-system/components/Button';
import { ConfidenceBadge, ConfidencePill } from '@/design-system/components/ConfidenceBadge';
import { Timeline } from '@/design-system/components/Timeline';
import type { TimelineEvent } from '@/design-system/components/Timeline';
import { ProgressBar, CircularProgress } from '@/design-system/components/Progress';
import { Dialog } from '@/design-system/components/Dialog';
import { useSessionStore } from '@/store/sessionStore';
import { formatTimeShort, cn } from '@/lib/utils';

// ─── Types ────────────────────────────────────────────────────────────────────

type VitalKey = 'hr' | 'spo2' | 'nibp' | 'etco2' | 'temp' | 'rr';
type ItemStatus = 'pending' | 'corrected' | 'dismissed';
type SignState = 'idle' | 'confirming' | 'signing' | 'locked';
type TabId = 'frame' | 'timeline' | 'audit' | 'events';

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

interface AuditEntry {
  id: string;
  timestamp: number;
  action: 'ai_detect' | 'human_correct' | 'human_dismiss';
  vital: VitalKey;
  value: string;
  prevValue?: string;
  author: string;
  confidence?: number;
}

interface EventMarker {
  id: string;
  timestamp: number;
  label: string;
  category: 'drug' | 'event' | 'observation';
}

// ─── Config ───────────────────────────────────────────────────────────────────

const VITAL_CFG: Record<VitalKey, { label: string; color: string; unit: string }> = {
  hr:    { label: 'Heart Rate', color: '#00FF88', unit: 'bpm'  },
  spo2:  { label: 'SpO₂',      color: '#00D4FF', unit: '%'    },
  nibp:  { label: 'NIBP',      color: '#FF4757', unit: 'mmHg' },
  etco2: { label: 'EtCO₂',     color: '#FFD600', unit: 'mmHg' },
  temp:  { label: 'Temp',      color: '#FF9500', unit: '°C'   },
  rr:    { label: 'Resp Rate', color: '#BF5AF2', unit: '/min' },
};

// SVG frame: 500 x 300. Vital boxes in right panel [x, y, w, h]
const FRAME_BOUNDS: Record<VitalKey, [number, number, number, number]> = {
  hr:    [322, 22,  174, 68],
  spo2:  [322, 92,  174, 68],
  etco2: [322, 162, 174, 68],
  nibp:  [322, 232, 174, 66],
  temp:  [0, 0, 0, 0],
  rr:    [0, 0, 0, 0],
};

// ─── Mock Data ────────────────────────────────────────────────────────────────

const T0 = Date.now() - 3600000;

const INITIAL_FLAGGED: FlaggedReading[] = [
  {
    id: 'FLAG-001', timestamp: T0 + 1200000, vital: 'hr',
    aiValue: '142', suggestedValue: '74', unit: 'bpm',
    confidence: 58, severity: 'critical', status: 'pending',
    frameNote: 'OCR read "142" — likely digit "1" preceding "42" from adjacent channel label. Sharpness score 0.38.',
  },
  {
    id: 'FLAG-002', timestamp: T0 + 2400000, vital: 'spo2',
    aiValue: '91', suggestedValue: '97', unit: '%',
    confidence: 47, severity: 'critical', status: 'pending',
    frameNote: 'Low confidence on digit boundary. Segment "91" may be "97" — partial occlusion on right edge of display.',
  },
  {
    id: 'FLAG-003', timestamp: T0 + 1800000, vital: 'etco2',
    aiValue: '18', suggestedValue: '34', unit: 'mmHg',
    confidence: 64, severity: 'warning', status: 'pending',
    frameNote: 'Characters "3" and "1" overlap in EtCO₂ zone. Image sharpness 0.41, below threshold 0.60.',
  },
  {
    id: 'FLAG-004', timestamp: T0 + 3000000, vital: 'nibp',
    aiValue: '165/100', suggestedValue: '128/82', unit: 'mmHg',
    confidence: 71, severity: 'warning', status: 'pending',
    frameNote: 'NIBP systolic delta >30 mmHg vs previous reading. Possible misread of leading digit.',
  },
];

const MOCK_TIMELINE: TimelineEvent[] = [
  { id: 'EVT-1', timestamp: T0,           type: 'start',        title: 'Session started',          description: 'Laparoscopic Cholecystectomy commenced. PT-2024-001, ASA II.' },
  { id: 'EVT-2', timestamp: T0 + 360000,  type: 'drug',         title: 'Propofol administered',     value: '200 mg',  description: 'Induction agent. 2.8 mg/kg IV.',          details: { Dose: '200 mg', Route: 'IV' } },
  { id: 'EVT-3', timestamp: T0 + 600000,  type: 'drug',         title: 'Fentanyl administered',     value: '100 µg',  description: 'Pre-operative opioid analgesia.' },
  { id: 'EVT-4', timestamp: T0 + 720000,  type: 'note',         title: 'Intubation',                description: 'ETT placement confirmed via capnography. Cuff inflated.' },
  { id: 'EVT-5', timestamp: T0 + 900000,  type: 'note',         title: 'Incision',                  description: 'Surgical incision made. Anaesthesia depth confirmed adequate.' },
  { id: 'EVT-6', timestamp: T0 + 1200000, type: 'alert',        title: 'HR anomaly flagged',        value: '142 bpm', severity: 'critical', description: 'AI detected HR=142. Previous 74 bpm. Delta exceeds threshold.' },
  { id: 'EVT-7', timestamp: T0 + 1800000, type: 'measurement',  title: 'NIBP measurement',          value: '128/82',  description: 'Routine NIBP cycle triggered by operator.' },
  { id: 'EVT-8', timestamp: T0 + 2400000, type: 'alert',        title: 'SpO₂ anomaly flagged',      value: '91%',     severity: 'critical', description: 'AI detected SpO₂=91%. Previous 97%. Flagged for review.' },
  { id: 'EVT-9', timestamp: T0 + 3000000, type: 'drug',         title: 'Rocuronium administered',   value: '50 mg',   description: 'Neuromuscular blockade maintained.',       details: { Dose: '50 mg', Route: 'IV' } },
];

const INITIAL_AUDIT: AuditEntry[] = [
  { id: 'AUD-1', timestamp: T0 + 1200000, action: 'ai_detect', vital: 'hr',    value: '142',     author: 'AI Vision', confidence: 58 },
  { id: 'AUD-2', timestamp: T0 + 2400000, action: 'ai_detect', vital: 'spo2',  value: '91',      author: 'AI Vision', confidence: 47 },
  { id: 'AUD-3', timestamp: T0 + 1800000, action: 'ai_detect', vital: 'etco2', value: '18',      author: 'AI Vision', confidence: 64 },
  { id: 'AUD-4', timestamp: T0 + 3000000, action: 'ai_detect', vital: 'nibp',  value: '165/100', author: 'AI Vision', confidence: 71 },
];

const INITIAL_EVENTS: EventMarker[] = [
  { id: 'EM-1', timestamp: T0 + 360000,  label: 'Drug: Propofol',  category: 'drug'  },
  { id: 'EM-2', timestamp: T0 + 600000,  label: 'Drug: Fentanyl',  category: 'drug'  },
  { id: 'EM-3', timestamp: T0 + 720000,  label: 'Intubation',      category: 'event' },
  { id: 'EM-4', timestamp: T0 + 900000,  label: 'Incision',        category: 'event' },
];

const QUICK_EVENTS = [
  { label: 'Intubation',      icon: Activity, category: 'event'  as const },
  { label: 'Extubation',      icon: Activity, category: 'event'  as const },
  { label: 'Incision',        icon: Scissors, category: 'event'  as const },
  { label: 'Drug: Propofol',  icon: Pill,     category: 'drug'   as const },
  { label: 'Drug: Fentanyl',  icon: Pill,     category: 'drug'   as const },
  { label: 'Drug: Rocuronium',icon: Pill,     category: 'drug'   as const },
];

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: 'frame',    label: 'Frame Viewer',  icon: Camera   },
  { id: 'timeline', label: 'Case Timeline', icon: Clock    },
  { id: 'audit',    label: 'Audit Trail',   icon: FileText },
  { id: 'events',   label: 'Event Markers', icon: Activity },
];

// ─── Monitor SVG Content ──────────────────────────────────────────────────────

const ECG_PATH = [
  'M 0,40 L 8,40',
  'C 10,40 11,35 13,33 C 15,33 16,40 18,40',
  'L 21,40 L 23,44 L 27,6 L 31,58 L 35,40',
  'L 38,40 C 42,40 45,30 48,28 C 51,28 54,40 58,40',
  'L 88,40 C 90,40 91,35 93,33 C 95,33 96,40 98,40',
  'L 101,40 L 103,44 L 107,6 L 111,58 L 115,40',
  'L 118,40 C 122,40 125,30 128,28 C 131,28 134,40 138,40',
  'L 168,40 C 170,40 171,35 173,33 C 175,33 176,40 178,40',
  'L 181,40 L 183,44 L 187,6 L 191,58 L 195,40',
  'L 198,40 C 202,40 205,30 208,28 C 211,28 214,40 218,40',
  'L 310,40',
].join(' ');

const PLETH_PATH = [
  'M 0,30',
  'C 15,30 20,8 30,7 C 40,7 45,30 60,30',
  'C 75,30 80,8 90,7 C 100,7 105,30 120,30',
  'C 135,30 140,8 150,7 C 160,7 165,30 180,30',
  'C 195,30 200,8 210,7 C 220,7 225,30 240,30',
  'C 255,30 260,8 270,7 C 280,7 285,30 300,30 L 310,30',
].join(' ');

const CAPNO_PATH = [
  'M 0,38 L 8,38 L 8,8 L 45,8 L 45,38',
  'L 65,38 L 65,8 L 102,8 L 102,38',
  'L 122,38 L 122,8 L 159,8 L 159,38',
  'L 179,38 L 179,8 L 216,8 L 216,38',
  'L 236,38 L 236,8 L 273,8 L 273,38 L 310,38',
].join(' ');

interface MonitorContentProps {
  highlightVital?: VitalKey;
  showOCR: boolean;
  flaggedItems: FlaggedReading[];
}

function MonitorContent({ highlightVital, showOCR, flaggedItems }: MonitorContentProps) {

  const monitorValues: Record<VitalKey, string> = {
    hr: '74', spo2: '97', etco2: '34', nibp: '128/82', temp: '36.8', rr: '14',
  };

  return (
    <>
      {/* Background */}
      <rect width="500" height="300" rx="6" fill="#060C18" />

      {/* Header bar */}
      <rect width="500" height="22" fill="#0A1525" />
      <text x="8" y="15" fill="#3D5570" fontFamily="Share Tech Mono" fontSize="8.5" letterSpacing="0.8">
        VITAL ANAESTHESIA MONITOR · PT-2024-001 · ASA II
      </text>

      {/* Faint grid in waveform area */}
      <g opacity="0.07" stroke="#4A90D9" strokeWidth="0.5">
        {[60, 100, 140, 180, 220, 260, 295].map(y => (
          <line key={y} x1="0" y1={y} x2="316" y2={y} />
        ))}
        {[62, 125, 187, 250].map(x => (
          <line key={x} x1={x} y1="22" x2={x} y2="300" />
        ))}
      </g>

      {/* ECG waveform */}
      <g transform="translate(4, 56)">
        <text x="0" y="-4" fill="#2D4A2D" fontFamily="Share Tech Mono" fontSize="8">ECG II</text>
        <path d={ECG_PATH} stroke="#00FF88" strokeWidth="1.5" fill="none"
          style={{ filter: 'drop-shadow(0 0 4px rgba(0,255,136,0.7))' }} />
      </g>

      {/* Pleth SpO2 waveform */}
      <g transform="translate(4, 148)">
        <text x="0" y="-4" fill="#1A3A4A" fontFamily="Share Tech Mono" fontSize="8">SpO₂</text>
        <path d={PLETH_PATH} stroke="#00D4FF" strokeWidth="1.5" fill="none"
          style={{ filter: 'drop-shadow(0 0 4px rgba(0,212,255,0.7))' }} />
      </g>

      {/* Capno EtCO2 waveform */}
      <g transform="translate(4, 234)">
        <text x="0" y="-4" fill="#3A3000" fontFamily="Share Tech Mono" fontSize="8">EtCO₂</text>
        <path d={CAPNO_PATH} stroke="#FFD600" strokeWidth="1.5" fill="none"
          style={{ filter: 'drop-shadow(0 0 4px rgba(255,214,0,0.6))' }} />
      </g>

      {/* Divider */}
      <line x1="318" y1="22" x2="318" y2="300" stroke="#182B42" strokeWidth="1" />

      {/* Vital value boxes */}
      {(Object.entries(FRAME_BOUNDS) as [VitalKey, [number,number,number,number]][])
        .filter(([, b]) => b[2] > 0)
        .map(([vital, [bx, by, bw, bh]]) => {
          const color = VITAL_CFG[vital].color;
          const label = VITAL_CFG[vital].label;
          const unit  = VITAL_CFG[vital].unit;
          return (
            <g key={vital}>
              <rect x={bx} y={by + 2} width={bw} height={bh - 4} rx="3" fill="#0A1525" />
              <rect x={bx} y={by + 2} width="3" height={bh - 4} rx="1" fill={color} opacity="0.8" />
              <text x={bx + 9} y={by + 15} fill="#3D5570" fontFamily="Share Tech Mono" fontSize="8">
                {label.toUpperCase()}
              </text>
              <text x={bx + 9} y={by + 50} fill={color} fontFamily="Share Tech Mono" fontSize="28" fontWeight="bold"
                style={{ filter: `drop-shadow(0 0 8px ${color}55)` }}>
                {monitorValues[vital]}
              </text>
              <text x={bx + 9} y={by + 62} fill="#3D5570" fontFamily="Share Tech Mono" fontSize="8">{unit}</text>
            </g>
          );
        })}

      {/* OCR overlay boxes */}
      {showOCR && flaggedItems.map(item => {
        const bounds = FRAME_BOUNDS[item.vital];
        if (bounds[2] === 0) return null;
        const [bx, by, bw] = bounds;
        const isHl = highlightVital === item.vital;
        const cc = item.confidence >= 80 ? '#30D158' : item.confidence >= 65 ? '#FFD600' : '#FF3B30';
        const ox = bx + 8; const oy = by + 26; const ow = 112; const oh = 30;

        return (
          <g key={item.id}>
            <rect x={ox} y={oy} width={ow} height={oh} rx="2"
              fill={`${cc}1A`} stroke={cc} strokeWidth={isHl ? 2.5 : 1.5}
              strokeDasharray={isHl ? undefined : '4 2'} />
            <text x={ox + 4} y={oy + 22} fill={cc} fontFamily="Share Tech Mono" fontSize="15" fontWeight="bold">
              {item.aiValue}
            </text>
            <rect x={bx + bw - 40} y={oy} width="38" height="14" rx="2" fill={`${cc}30`} />
            <text x={bx + bw - 38} y={oy + 10} fill={cc} fontFamily="Share Tech Mono" fontSize="8" fontWeight="bold">
              {item.confidence}%
            </text>
            {isHl && (
              <rect x={bx + 2} y={by + 4} width={bw - 4} height={62} rx="3"
                fill="none" stroke={cc} strokeWidth="1" opacity="0.4" />
            )}
          </g>
        );
      })}
    </>
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

function ReadingInspector({ item, allItems, currentIndex, onNavigate, onCorrect, onDismiss }: {
  item: FlaggedReading;
  allItems: FlaggedReading[];
  currentIndex: number;
  onNavigate: (dir: 1 | -1) => void;
  onCorrect: (id: string, value: string) => void;
  onDismiss: (id: string) => void;
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
          <button onClick={() => onNavigate(-1)} disabled={currentIndex === 0}
            aria-label="Previous flagged reading"
            className="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:pointer-events-none transition-colors">
            <ChevronLeft size={13} aria-hidden="true" />
          </button>
          <span className="font-mono text-[10px] text-slate-400 tabular-nums" aria-live="polite" aria-atomic="true">{currentIndex + 1} / {allItems.length}</span>
          <button onClick={() => onNavigate(1)} disabled={currentIndex === allItems.length - 1}
            aria-label="Next flagged reading"
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
        {!resolved && (
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

// ─── Frame Viewer ─────────────────────────────────────────────────────────────

function FrameViewer({ selectedVital, flaggedItems }: {
  selectedVital?: VitalKey;
  flaggedItems: FlaggedReading[];
}) {
  const [showOCR, setShowOCR] = useState(true);
  const [zoom, setZoom] = useState(1);

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200 bg-white flex-shrink-0">
        <div className="flex items-center gap-3">
          <Camera size={13} className="text-slate-400" />
          <span className="font-display text-xs font-medium text-slate-700">Captured Frame</span>
          <span className="font-mono text-[10px] px-2 py-0.5 rounded"
            style={{ background: 'rgba(50,173,230,0.08)', color: '#32ADE6', border: '1px solid rgba(50,173,230,0.2)' }}>
            Frame 0241 · T+20:00
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowOCR(v => !v)}
            className={cn(
              'flex items-center gap-1.5 px-2.5 py-1 rounded-lg font-display text-[11px] border transition-colors',
              showOCR ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-slate-100 text-slate-500 border-slate-200',
            )}>
            <Eye size={11} /> OCR Overlay
          </button>
          <div className="flex items-center border border-slate-200 rounded-lg overflow-hidden" role="group" aria-label="Zoom controls">
            <button onClick={() => setZoom(z => Math.max(0.55, +(z - 0.1).toFixed(1)))}
              aria-label="Zoom out"
              className="px-2 py-1.5 hover:bg-slate-100 text-slate-500 transition-colors"><ZoomOut size={12} aria-hidden="true" /></button>
            <span className="font-mono text-[10px] text-slate-500 px-1.5 tabular-nums" aria-live="polite" aria-atomic="true" aria-label={`Zoom level ${Math.round(zoom * 100)}%`}>{Math.round(zoom * 100)}%</span>
            <button onClick={() => setZoom(z => Math.min(1.8, +(z + 0.1).toFixed(1)))}
              aria-label="Zoom in"
              className="px-2 py-1.5 hover:bg-slate-100 text-slate-500 transition-colors"><ZoomIn size={12} aria-hidden="true" /></button>
          </div>
        </div>
      </div>

      {/* SVG frame */}
      <div className="flex-1 overflow-auto p-6 bg-slate-100 flex items-start justify-center">
        <svg
          viewBox="0 0 500 300"
          width={500 * zoom}
          height={300 * zoom}
          style={{ display: 'block', filter: 'drop-shadow(0 8px 40px rgba(0,0,0,0.35))', flexShrink: 0 }}
        >
          <MonitorContent
            highlightVital={selectedVital}
            showOCR={showOCR}
            flaggedItems={flaggedItems.filter(i => i.status === 'pending')}
          />
        </svg>
      </div>

      {/* OCR confidence legend */}
      {showOCR && (
        <div className="px-4 py-2 border-t border-slate-200 bg-white flex items-center gap-4 flex-shrink-0">
          <span className="font-display text-[10px] text-slate-500 uppercase tracking-wider">OCR confidence:</span>
          {([['≥80%', '#30D158'], ['65–79%', '#FFD600'], ['<65%', '#FF3B30']] as const).map(([lbl, col]) => (
            <div key={lbl} className="flex items-center gap-1.5">
              <div className="w-4 h-0.5 rounded-full" style={{ background: col }} />
              <span className="font-mono text-[10px] text-slate-500">{lbl}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Audit Trail ─────────────────────────────────────────────────────────────

function AuditTrail({ entries }: { entries: AuditEntry[] }) {
  const initialIdsRef = useRef<Set<string>>(new Set(entries.map(e => e.id)));

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
        <FileText size={28} className="opacity-30" />
        <p className="font-display text-sm">No audit entries yet</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-5">
      <p className="font-display text-[10px] text-slate-500 uppercase tracking-wider mb-4">Chain of Custody · All actions logged</p>
      <div className="space-y-0">
        {entries.map((entry, i) => {
          const cfg = VITAL_CFG[entry.vital];
          const isHuman = entry.action !== 'ai_detect';
          const ac = entry.action === 'human_correct' ? '#30D158' : entry.action === 'human_dismiss' ? '#94A3B8' : '#32ADE6';
          const aLabel = entry.action === 'ai_detect' ? 'AI Detected' : entry.action === 'human_correct' ? 'Corrected' : 'Dismissed';
          const isNew = !initialIdsRef.current.has(entry.id);

          return (
            <motion.div key={entry.id}
              initial={isNew ? { opacity: 0, y: 6 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: isNew ? 0 : i * 0.05, duration: 0.2 }} className="flex gap-3">
              <div className="flex flex-col items-center">
                <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
                  style={{ background: `${ac}18`, border: `1px solid ${ac}40` }}>
                  {isHuman
                    ? <Edit3 size={11} style={{ color: ac }} />
                    : <Camera size={11} style={{ color: ac }} />}
                </div>
                {i < entries.length - 1 && <div className="w-px flex-1 mt-1 mb-1 bg-slate-200" />}
              </div>
              <div className={cn('flex-1', i < entries.length - 1 ? 'pb-4' : 'pb-0')}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-display text-xs font-semibold text-slate-700">{aLabel}</span>
                    <span className="font-display text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded"
                      style={{ background: `${cfg.color}15`, color: cfg.color, border: `1px solid ${cfg.color}30` }}>
                      {cfg.label}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] text-slate-400 tabular-nums">{formatTimeShort(entry.timestamp)}</span>
                </div>
                <div className="mt-1 flex items-center gap-2 flex-wrap">
                  {entry.prevValue && (
                    <>
                      <span className="font-mono text-xs text-slate-400 line-through">{entry.prevValue}</span>
                      <ArrowRight size={10} className="text-slate-300" />
                    </>
                  )}
                  <span className="font-mono text-xs font-semibold" style={{ color: ac }}>{entry.value}</span>
                  {entry.confidence !== undefined && <ConfidencePill value={entry.confidence} />}
                </div>
                <p className="font-display text-[10px] text-slate-400 mt-0.5">
                  {entry.author} · {isHuman ? 'Manual review' : `Conf. ${entry.confidence}%`}
                </p>
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Event Marker Editor ──────────────────────────────────────────────────────

const EVT_COLORS = {
  drug:        { bg: '#EFF6FF', border: '#BFDBFE', text: '#3B82F6' },
  event:       { bg: '#F0FDF4', border: '#BBF7D0', text: '#16A34A' },
  observation: { bg: '#FFF7ED', border: '#FED7AA', text: '#EA580C' },
};

function EventMarkerEditor({ events, onAdd, onRemove }: {
  events: EventMarker[];
  onAdd: (m: Omit<EventMarker, 'id'>) => void;
  onRemove: (id: string) => void;
}) {
  const [customText, setCustomText] = useState('');

  const addCustom = () => {
    if (!customText.trim()) return;
    onAdd({ timestamp: Date.now(), label: customText.trim(), category: 'observation' });
    setCustomText('');
  };

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-slate-200 space-y-3 flex-shrink-0 bg-white">
        <p className="font-display text-[10px] text-slate-500 uppercase tracking-wider">Quick Event Markers</p>
        <div className="flex flex-wrap gap-2">
          {QUICK_EVENTS.map(({ label, icon: Icon, category }) => (
            <button key={label}
              onClick={() => onAdd({ timestamp: Date.now(), label, category })}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg font-display text-xs transition-all hover:scale-105 active:scale-95 border"
              style={{ ...EVT_COLORS[category], borderColor: EVT_COLORS[category].border }}>
              <Icon size={11} />
              {label.startsWith('Drug:') ? label.slice(6) : label}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <input type="text" value={customText} onChange={e => setCustomText(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addCustom()}
            aria-label="Custom observation text"
            placeholder="Add custom observation..."
            className="flex-1 h-8 px-3 rounded-lg border border-slate-200 bg-white font-display text-xs text-slate-700 placeholder-slate-400 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-all" />
          <Button variant="outline" size="sm" icon={<Plus size={12} />} onClick={addCustom}
            className="border-slate-200 text-slate-600">Add</Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <p className="font-display text-[10px] text-slate-500 uppercase tracking-wider mb-3">Recorded Events</p>
        <AnimatePresence>
          {events.length === 0 ? (
            <div className="text-center py-8 text-slate-400">
              <Clock size={22} className="mx-auto mb-2 opacity-30" />
              <p className="font-display text-xs">No events recorded</p>
            </div>
          ) : (
            [...events].sort((a, b) => a.timestamp - b.timestamp).map(evt => {
              const cols = EVT_COLORS[evt.category];
              return (
                <motion.div key={evt.id} layout initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 8, height: 0, marginBottom: 0 }}
                  transition={{ duration: 0.18 }}
                  className="flex items-center justify-between py-2 border-b border-slate-100 last:border-0">
                  <div className="flex items-center gap-2.5">
                    <span className="font-mono text-[10px] text-slate-400 tabular-nums">{formatTimeShort(evt.timestamp)}</span>
                    <span className="font-display text-xs px-2 py-0.5 rounded border"
                      style={{ background: cols.bg, borderColor: cols.border, color: cols.text }}>
                      {evt.label}
                    </span>
                  </div>
                  <button onClick={() => onRemove(evt.id)}
                    aria-label={`Remove event: ${evt.label}`}
                    className="p-1 rounded text-slate-300 hover:text-red-400 hover:bg-red-50 transition-colors">
                    <X size={11} aria-hidden="true" />
                  </button>
                </motion.div>
              );
            })
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

// ─── Sign Dialog ──────────────────────────────────────────────────────────────

function SignDialog({ open, onClose, onConfirm, session }: {
  open: boolean; onClose: () => void; onConfirm: () => void;
  session: { patient: { id: string; asa?: number }; procedure: string; anesthetist: string; startTime: number } | null;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="Sign & Lock Anaesthesia Record"
      description="This action is irreversible. Review the record carefully before proceeding."
      size="md" closeOnBackdrop={false}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="clinical" icon={<Lock size={13} />} onClick={onConfirm}>
            Sign & Lock Record
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
              By signing, you certify this anaesthesia record is accurate and complete.
              The record will be locked and become a binding legal medical document.
              All AI-flagged readings have been reviewed and corrected where necessary.
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
          <ShieldCheck size={14} style={{ color: '#30D158' }} />
          <p className="font-display text-xs" style={{ color: '#30D158' }}>
            All 4 flagged readings reviewed and resolved — chain of custody verified
          </p>
        </div>
      </div>
    </Dialog>
  );
}

// ─── PDF Progress Dialog ──────────────────────────────────────────────────────

function PdfDialog({ signState, pdfProgress, onClose }: {
  signState: SignState; pdfProgress: number; onClose: () => void;
}) {
  const label =
    pdfProgress < 25 ? 'Encrypting session data…' :
    pdfProgress < 55 ? 'Rendering anaesthesia charts…' :
    pdfProgress < 80 ? 'Applying digital signature…' : 'Finalising document…';

  return (
    <Dialog open={signState === 'signing' || signState === 'locked'} onClose={onClose}
      showClose={false} closeOnBackdrop={false} size="sm">
      <div className="py-4 flex flex-col items-center gap-4">
        <AnimatePresence mode="wait">
          {signState === 'signing' ? (
            <motion.div key="signing" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="w-full flex flex-col items-center gap-4">
              <CircularProgress value={pdfProgress} size={76} strokeWidth={5} color="#30D158"
                trackColor="rgba(24,43,66,0.8)"
                label={<span className="font-mono text-sm font-bold" style={{ color: '#30D158' }}>{Math.round(pdfProgress)}%</span>} />
              <div className="text-center">
                <p className="font-display font-semibold text-[#E8F1FF]">Generating PDF Record</p>
                <p className="font-display text-xs mt-1" style={{ color: '#7A90AA' }}>{label}</p>
              </div>
              <ProgressBar value={pdfProgress} color="#30D158" size="sm" trackColor="rgba(24,43,66,1)" className="w-full" />
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
                <p className="font-display font-semibold text-[#E8F1FF]">Record Signed & Locked</p>
                <p className="font-display text-xs mt-1" style={{ color: '#7A90AA' }}>
                  Anaesthesia PDF generated and sealed
                </p>
              </div>
              <div className="flex gap-2">
                <Button variant="success" size="sm" icon={<Download size={13} />}>Download PDF</Button>
                <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Dialog>
  );
}

// ─── Review Page ──────────────────────────────────────────────────────────────

export function ReviewPage() {
  const { activeSession, archivedSessions } = useSessionStore();
  const session = activeSession ?? archivedSessions[0] ?? null;

  const [flaggedItems, setFlaggedItems] = useState<FlaggedReading[]>(INITIAL_FLAGGED);
  const [auditEntries, setAuditEntries]   = useState<AuditEntry[]>(INITIAL_AUDIT);
  const [eventMarkers, setEventMarkers]   = useState<EventMarker[]>(INITIAL_EVENTS);
  const [selectedId, setSelectedId]       = useState<string>(INITIAL_FLAGGED[0].id);
  const [activeTab, setActiveTab]         = useState<TabId>('frame');
  const [signState, setSignState]         = useState<SignState>('idle');
  const [pdfProgress, setPdfProgress]     = useState(0);

  const selectedItem  = flaggedItems.find(i => i.id === selectedId) ?? flaggedItems[0];
  const selectedIndex = flaggedItems.findIndex(i => i.id === selectedId);
  const pendingItems  = flaggedItems.filter(i => i.status === 'pending');
  const allResolved   = pendingItems.length === 0;
  const resolvedCount = flaggedItems.filter(i => i.status !== 'pending').length;

  const signIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const signTimeoutRef  = useRef<ReturnType<typeof setTimeout>  | null>(null);

  useEffect(() => {
    return () => {
      if (signIntervalRef.current) clearInterval(signIntervalRef.current);
      if (signTimeoutRef.current)  clearTimeout(signTimeoutRef.current);
    };
  }, []);

  const handleCorrect = useCallback((id: string, correctedValue: string) => {
    setFlaggedItems(prev => {
      const item = prev.find(i => i.id === id)!;
      setAuditEntries(ae => [...ae, {
        id: `AUD-${Date.now()}`, timestamp: Date.now(), action: 'human_correct',
        vital: item.vital, value: correctedValue, prevValue: item.aiValue,
        author: session?.anesthetist ?? 'Anaesthetist', confidence: item.confidence,
      }]);
      const next = prev.find(i => i.status === 'pending' && i.id !== id);
      if (next) setSelectedId(next.id);
      return prev.map(i => i.id === id ? { ...i, status: 'corrected', correctedValue } : i);
    });
  }, [session?.anesthetist]);

  const handleDismiss = useCallback((id: string) => {
    setFlaggedItems(prev => {
      const item = prev.find(i => i.id === id)!;
      setAuditEntries(ae => [...ae, {
        id: `AUD-${Date.now()}`, timestamp: Date.now(), action: 'human_dismiss',
        vital: item.vital, value: item.aiValue,
        author: session?.anesthetist ?? 'Anaesthetist', confidence: item.confidence,
      }]);
      const next = prev.find(i => i.status === 'pending' && i.id !== id);
      if (next) setSelectedId(next.id);
      return prev.map(i => i.id === id ? { ...i, status: 'dismissed' } : i);
    });
  }, [session?.anesthetist]);

  const handleNavigate = useCallback((dir: 1 | -1) => {
    setFlaggedItems(prev => {
      const idx = Math.max(0, Math.min(prev.length - 1, prev.findIndex(i => i.id === selectedId) + dir));
      setSelectedId(prev[idx].id);
      return prev;
    });
  }, [selectedId]);

  const handleSignConfirm = useCallback(() => {
    setSignState('signing');
    setPdfProgress(0);
    let p = 0;
    signIntervalRef.current = setInterval(() => {
      p += Math.random() * 9 + 4;
      if (p >= 100) {
        setPdfProgress(100);
        clearInterval(signIntervalRef.current!);
        signIntervalRef.current = null;
        signTimeoutRef.current = setTimeout(() => setSignState('locked'), 350);
      } else {
        setPdfProgress(p);
      }
    }, 110);
  }, []);

  return (
    <motion.div className="h-full flex flex-col" style={{ background: '#F8FAFC' }}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.22 }}>

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <header className="flex items-center flex-shrink-0 bg-white border-b border-slate-200" style={{ height: 56 }}>
        {/* Patient info */}
        <div className="flex items-center gap-3 px-5 h-full border-r border-slate-200 flex-shrink-0" style={{ minWidth: 300 }}>
          <div className="w-8 h-8 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center flex-shrink-0">
            <FileText size={14} className="text-slate-500" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-slate-800">{session?.patient.id ?? 'No active session'}</span>
              {session?.patient.asa && (
                <span className="font-display text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-600">
                  ASA {session.patient.asa}
                </span>
              )}
            </div>
            <p className="font-display text-[10px] text-slate-500 truncate">
              {session?.procedure ?? 'Review mode'} · {session?.anesthetist ?? '—'}
            </p>
          </div>
        </div>

        {/* Review progress */}
        <div className="flex items-center gap-4 px-5 h-full border-r border-slate-200 flex-1">
          <div className="flex items-center gap-3 w-full max-w-sm">
            <CircularProgress
              value={(resolvedCount / flaggedItems.length) * 100}
              size={34} strokeWidth={3} color="#30D158" trackColor="#E2E8F0"
              label={<span className="font-mono text-[9px] font-bold text-slate-700">{resolvedCount}/{flaggedItems.length}</span>}
            />
            <div className="flex-1 min-w-0">
              <p className="font-display text-xs text-slate-700 font-medium">
                {allResolved ? 'All readings reviewed' : `${pendingItems.length} pending review`}
              </p>
              <ProgressBar value={(resolvedCount / flaggedItems.length) * 100}
                color="#30D158" size="xs" trackColor="#E2E8F0" className="mt-1" />
            </div>
          </div>
          <AnimatePresence>
            {allResolved && (
              <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-50 border border-emerald-200 flex-shrink-0">
                <CheckCircle2 size={12} className="text-emerald-600" />
                <span className="font-display text-[10px] font-semibold text-emerald-700">Ready to sign</span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Sign button */}
        <div className="flex items-center px-5 h-full flex-shrink-0">
          <Button variant={allResolved ? 'clinical' : 'secondary'} size="sm"
            icon={<Lock size={13} />}
            disabled={!allResolved || signState !== 'idle'}
            onClick={() => setSignState('confirming')}>
            Sign & Lock Record
          </Button>
        </div>
      </header>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden min-h-0">

        {/* ── Left sidebar ─────────────────────────────────────────────── */}
        <aside className="flex flex-col flex-shrink-0 bg-white border-r border-slate-200 overflow-hidden" style={{ width: 360 }}>
          {/* Queue header */}
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-200 flex-shrink-0">
            <div className="flex items-center gap-2">
              <AlertTriangle size={13} className="text-slate-400" />
              <span className="font-display text-xs font-semibold text-slate-700">Flagged Readings</span>
            </div>
            <AnimatePresence>
              {pendingItems.length > 0 && (
                <motion.span key={pendingItems.length} initial={{ scale: 1.4 }} animate={{ scale: 1 }}
                  className="font-mono text-[10px] px-1.5 py-0.5 rounded-full font-bold"
                  style={{ background: 'rgba(255,59,48,0.08)', color: '#FF3B30', border: '1px solid rgba(255,59,48,0.25)' }}>
                  {pendingItems.length}
                </motion.span>
              )}
            </AnimatePresence>
          </div>

          {/* Queue items */}
          <div className="overflow-y-auto flex-1 min-h-0">
            {flaggedItems.some(i => i.severity === 'critical') && (
              <>
                <div className="px-4 py-1.5 bg-red-50 border-b border-red-100">
                  <span className="font-display text-[9px] text-red-500 uppercase tracking-[0.18em] font-bold">Critical</span>
                </div>
                {flaggedItems.filter(i => i.severity === 'critical').map(item => (
                  <QueueCard key={item.id} item={item} selected={selectedId === item.id}
                    onSelect={() => { setSelectedId(item.id); setActiveTab('frame'); }} />
                ))}
              </>
            )}
            {flaggedItems.some(i => i.severity === 'warning') && (
              <>
                <div className="px-4 py-1.5 bg-amber-50 border-b border-amber-100 border-t border-slate-100">
                  <span className="font-display text-[9px] text-amber-600 uppercase tracking-[0.18em] font-bold">Warning</span>
                </div>
                {flaggedItems.filter(i => i.severity === 'warning').map(item => (
                  <QueueCard key={item.id} item={item} selected={selectedId === item.id}
                    onSelect={() => { setSelectedId(item.id); setActiveTab('frame'); }} />
                ))}
              </>
            )}
          </div>

          {/* Inspector */}
          {selectedItem && (
            <ReadingInspector key={selectedItem.id}
              item={selectedItem} allItems={flaggedItems}
              currentIndex={selectedIndex}
              onNavigate={handleNavigate}
              onCorrect={handleCorrect}
              onDismiss={handleDismiss}
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
                {activeTab === 'frame' && (
                  <FrameViewer selectedVital={selectedItem?.vital} flaggedItems={flaggedItems} />
                )}
                {activeTab === 'timeline' && (
                  <div className="h-full overflow-y-auto p-5">
                    <Timeline events={MOCK_TIMELINE} />
                  </div>
                )}
                {activeTab === 'audit' && <AuditTrail entries={auditEntries} />}
                {activeTab === 'events' && (
                  <EventMarkerEditor events={eventMarkers}
                    onAdd={m => setEventMarkers(p => [...p, { ...m, id: `EM-${Date.now()}` }])}
                    onRemove={id => setEventMarkers(p => p.filter(e => e.id !== id))} />
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
      />
      <PdfDialog
        signState={signState}
        pdfProgress={pdfProgress}
        onClose={() => setSignState('idle')}
      />
    </motion.div>
  );
}
