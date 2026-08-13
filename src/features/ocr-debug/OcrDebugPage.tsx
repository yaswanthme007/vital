import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Camera, Cpu, Eye, CheckCircle, AlertTriangle,
  Play, SkipForward, RotateCcw, ChevronRight,
  Zap, Grid3x3, Box, Type, Shield, Activity,
} from 'lucide-react';
import { Button } from '@/design-system/components/Button';
import { Badge } from '@/design-system/components/Badge';
import { ProgressBar } from '@/design-system/components/Progress';
import { cn } from '@/lib/utils';

// ─── Pipeline stage definitions ───────────────────────────────────────────────

type StageId =
  | 'capture'
  | 'preprocess'
  | 'detect'
  | 'warp'
  | 'extract'
  | 'ocr'
  | 'validate'
  | 'output';

interface Stage {
  id: StageId;
  label: string;
  sublabel: string;
  icon: React.ElementType;
  color: string;
  latency: number; // ms
  details: Record<string, string>;
  overlayMode: 'raw' | 'gray' | 'detect' | 'warp' | 'roi' | 'ocr' | 'validate' | 'final';
}

const STAGES: Stage[] = [
  {
    id: 'capture',
    label: 'Camera Capture',
    sublabel: 'Raw video frame acquisition',
    icon: Camera,
    color: '#32ADE6',
    latency: 33,
    overlayMode: 'raw',
    details: {
      'Resolution': '1920 × 1080',
      'Frame rate': '30 fps',
      'Exposure': '3.2 ms',
      'ISO': '200',
      'White balance': 'Auto (5500K)',
      'Frame #': '1,247',
    },
  },
  {
    id: 'preprocess',
    label: 'Preprocessing',
    sublabel: 'Noise reduction & contrast enhancement',
    icon: Grid3x3,
    color: '#BF5AF2',
    latency: 8,
    overlayMode: 'gray',
    details: {
      'Operation': 'CLAHE + Denoise',
      'Clip limit': '2.0',
      'Tile grid': '8 × 8',
      'SNR improvement': '+4.2 dB',
      'Sharpness': '94 / 100',
      'Contrast ratio': '1:820',
    },
  },
  {
    id: 'detect',
    label: 'Monitor Detection',
    sublabel: 'Boundary & edge localization',
    icon: Box,
    color: '#FF9500',
    latency: 12,
    overlayMode: 'detect',
    details: {
      'Method': 'Canny + Hough',
      'Corners found': '4 / 4',
      'Confidence': '97.4%',
      'Area': '162,432 px²',
      'Aspect ratio': '16 : 9.1',
      'Skew': '2.3°',
    },
  },
  {
    id: 'warp',
    label: 'Perspective Warp',
    sublabel: 'Homography & rectification',
    icon: RotateCcw,
    color: '#00D4FF',
    latency: 5,
    overlayMode: 'warp',
    details: {
      'Method': 'Homography (RANSAC)',
      'Inliers': '48 / 48',
      'Reprojection err': '0.42 px',
      'Output size': '800 × 480',
      'Scale factor': '1.18×',
      'Keypoints': '48',
    },
  },
  {
    id: 'extract',
    label: 'Region Extraction',
    sublabel: 'Vital sign ROI isolation',
    icon: Target2,
    color: '#FFD600',
    latency: 4,
    overlayMode: 'roi',
    details: {
      'Regions found': '4',
      'HR region':    '(348, 52) 168×65',
      'SpO₂ region':  '(348, 120) 168×65',
      'EtCO₂ region': '(348, 188) 168×65',
      'NIBP region':  '(348, 256) 168×65',
      'Overlap check': 'Pass',
    },
  },
  {
    id: 'ocr',
    label: 'OCR Processing',
    sublabel: 'Text recognition per region',
    icon: Type,
    color: '#00FF88',
    latency: 22,
    overlayMode: 'ocr',
    details: {
      'Engine': 'Tesseract 5.3 (LSTM)',
      'HR detected': '"74" — 98.1%',
      'SpO₂ detected': '"98" — 97.4%',
      'EtCO₂ detected': '"37" — 94.2%',
      'NIBP detected': '"122/78" — 91.8%',
      'Total chars': '12',
    },
  },
  {
    id: 'validate',
    label: 'Validation',
    sublabel: 'Physiological plausibility checks',
    icon: Shield,
    color: '#30D158',
    latency: 3,
    overlayMode: 'validate',
    details: {
      'HR range check': 'PASS (74 ∈ [30, 250])',
      'SpO₂ range':     'PASS (98 ∈ [50, 100])',
      'EtCO₂ range':    'PASS (37 ∈ [15, 80])',
      'NIBP range':     'PASS (122/78)',
      'Delta check':    'PASS (Δ < 20%)',
      'Flags':          'None',
    },
  },
  {
    id: 'output',
    label: 'Final Output',
    sublabel: 'Validated readings dispatched',
    icon: Activity,
    color: '#00FF88',
    latency: 1,
    overlayMode: 'final',
    details: {
      'HR':    '74 bpm',
      'SpO₂':  '98 %',
      'EtCO₂': '37 mmHg',
      'NIBP':  '122/78 mmHg',
      'Confidence': '95.4% avg.',
      'Total latency': '88 ms',
    },
  },
];

// placeholder icon (lucide doesn't have "target" with inner circle)
function Target2({ size = 18, ...props }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </svg>
  );
}

// ─── Monitor SVG frame with per-stage overlays ────────────────────────────────

const VITAL_REGIONS = [
  { key: 'hr',    label: 'HR',    color: '#00FF88', value: '74',     unit: 'bpm',  x: 348, y: 52,  w: 168, h: 65, conf: 98.1 },
  { key: 'spo2',  label: 'SpO₂',  color: '#00D4FF', value: '98',     unit: '%',    x: 348, y: 120, w: 168, h: 65, conf: 97.4 },
  { key: 'etco2', label: 'EtCO₂', color: '#FFD600', value: '37',     unit: 'mmHg', x: 348, y: 188, w: 168, h: 65, conf: 94.2 },
  { key: 'nibp',  label: 'NIBP',  color: '#FF4757', value: '122/78', unit: 'mmHg', x: 348, y: 256, w: 168, h: 65, conf: 91.8 },
];

function FrameCanvas({ stage, roiIndex }: { stage: Stage; roiIndex: number }) {
  const mode = stage.overlayMode;

  return (
    <svg viewBox="0 0 640 360" className="w-full h-full" style={{ background: '#04080F' }}>
      <defs>
        <filter id="dbGlow">
          <feGaussianBlur stdDeviation="3" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="dbGray">
          <feColorMatrix type="saturate" values="0" />
          <feComponentTransfer>
            <feFuncR type="linear" slope="1.4" intercept="-0.2" />
            <feFuncG type="linear" slope="1.4" intercept="-0.2" />
            <feFuncB type="linear" slope="1.4" intercept="-0.2" />
          </feComponentTransfer>
        </filter>
      </defs>

      {/* Base: dark background */}
      <rect x="0" y="0" width="640" height="360" fill="#060C18" />

      {/* Monitor body */}
      <g filter={mode === 'gray' ? 'url(#dbGray)' : undefined}>
        <polygon points="95,44 549,36 562,316 82,324" fill="#0A1520" stroke="#182B42" strokeWidth="1.5" />
        <polygon points="108,56 537,49 550,304 96,312" fill="#070D1A" />
      </g>

      <clipPath id="dbClip">
        <polygon points="108,56 537,49 550,304 96,312" />
      </clipPath>
      <g clipPath="url(#dbClip)" filter={mode === 'gray' ? 'url(#dbGray)' : undefined}>
        {/* divider */}
        <line x1="340" y1="52" x2="340" y2="308" stroke="#182B42" strokeWidth="1" />

        {/* Waveforms */}
        <path d="M108 110 L140 110 L150 90 L158 130 L166 110 L200 110 L210 90 L218 130 L226 110 L260 110 L270 90 L278 130 L286 110 L320 110"
          fill="none" stroke="#00FF88" strokeWidth="1.5" opacity="0.6" filter="url(#dbGlow)" />
        <path d="M108 190 Q130 160 152 190 Q174 220 196 190 Q218 160 240 190 Q262 220 284 190 Q306 160 328 190"
          fill="none" stroke="#00D4FF" strokeWidth="1.5" opacity="0.5" filter="url(#dbGlow)" />
        <path d="M108 270 L130 270 L132 248 L160 248 L162 270 L190 270 L192 248 L220 248 L222 270 L250 270 L252 248 L280 248 L282 270 L310 270"
          fill="none" stroke="#FFD600" strokeWidth="1.5" opacity="0.4" />

        {/* Vital value boxes */}
        {VITAL_REGIONS.map((v) => (
          <g key={v.key}>
            <rect x={v.x} y={v.y} width={v.w} height={v.h} fill="#0C1625" rx="2" />
            <rect x={v.x} y={v.y} width="3" height={v.h} fill={v.color} opacity="0.8" />
            <text x={v.x + 10} y={v.y + 18} fill={v.color} fontSize="10" fontFamily="'Share Tech Mono', monospace" opacity="0.7">{v.label}</text>
            <text x={v.x + 10} y={v.y + 48} fill={v.color} fontSize="26" fontFamily="'Share Tech Mono', monospace" filter="url(#dbGlow)">{v.value}</text>
            <text x={v.x + v.w - 8} y={v.y + v.h - 10} fill={v.color} fontSize="9" fontFamily="'Share Tech Mono', monospace" textAnchor="end" opacity="0.5">{v.unit}</text>
          </g>
        ))}
      </g>

      {/* ── Stage-specific overlays ── */}

      {/* DETECT: boundary quadrilateral */}
      {(mode === 'detect') && (
        <g filter="url(#dbGlow)">
          <polygon points="95,44 549,36 562,316 82,324" fill="none" stroke="#FF9500" strokeWidth="2" strokeDasharray="10 5" />
          {[{ x: 95, y: 44 }, { x: 549, y: 36 }, { x: 562, y: 316 }, { x: 82, y: 324 }].map((pt, i) => (
            <g key={i}>
              <circle cx={pt.x} cy={pt.y} r="8" fill="none" stroke="#FF9500" strokeWidth="1.5" />
              <circle cx={pt.x} cy={pt.y} r="3" fill="#FF9500" />
            </g>
          ))}
          <rect x="108" y="52" width="140" height="18" rx="3" fill="rgba(255,149,0,0.15)" />
          <text x="116" y="65" fill="#FF9500" fontSize="10" fontFamily="'Share Tech Mono', monospace">BOUNDARY: 97.4%</text>
        </g>
      )}

      {/* WARP: corrected rectangle */}
      {(mode === 'warp') && (
        <g filter="url(#dbGlow)">
          <rect x="108" y="52" width="429" height="252" fill="none" stroke="#00D4FF" strokeWidth="1.5" />
          {[{ x: 108, y: 52 }, { x: 537, y: 52 }, { x: 537, y: 304 }, { x: 108, y: 304 }].map((pt, i) => (
            <g key={i}>
              <circle cx={pt.x} cy={pt.y} r="6" fill="none" stroke="#00D4FF" strokeWidth="1.5" />
              <circle cx={pt.x} cy={pt.y} r="2.5" fill="#00D4FF" />
            </g>
          ))}
          <rect x="108" y="52" width="165" height="18" rx="3" fill="rgba(0,212,255,0.15)" />
          <text x="116" y="65" fill="#00D4FF" fontSize="10" fontFamily="'Share Tech Mono', monospace">RECTIFIED: 0.42px err</text>
        </g>
      )}

      {/* ROI: colored vital boxes */}
      {(mode === 'roi' || mode === 'ocr' || mode === 'validate' || mode === 'final') &&
        VITAL_REGIONS.map((v, i) => {
          const visible = mode === 'roi' ? i <= roiIndex : true;
          if (!visible) return null;
          const confColor =
            v.conf >= 95 ? '#00FF88' : v.conf >= 85 ? '#FFD600' : '#FF4757';
          return (
            <g key={v.key} filter="url(#dbGlow)">
              <rect x={v.x - 3} y={v.y - 3} width={v.w + 6} height={v.h + 6}
                fill="none" stroke={v.color} strokeWidth="1.5" strokeDasharray={mode === 'roi' ? '6 3' : 'none'} rx="3" />
              {/* corner markers */}
              {[[v.x - 3, v.y - 3], [v.x + v.w + 3, v.y - 3], [v.x + v.w + 3, v.y + v.h + 3], [v.x - 3, v.y + v.h + 3]].map(([cx, cy], ci) => (
                <rect key={ci} x={(cx as number) - 3} y={(cy as number) - 3} width="6" height="6" fill={v.color} rx="1" />
              ))}
              {/* confidence tag for OCR + validate + final */}
              {(mode === 'ocr' || mode === 'validate' || mode === 'final') && (
                <>
                  <rect x={v.x - 3} y={v.y - 17} width={50} height={14} rx="3" fill={v.color} opacity="0.9" />
                  <text x={v.x + 1} y={v.y - 7} fill="#000" fontSize="9" fontFamily="'Share Tech Mono', monospace" fontWeight="bold">
                    {v.conf}%
                  </text>
                </>
              )}
              {/* validate: check or cross */}
              {(mode === 'validate' || mode === 'final') && (
                <text x={v.x + v.w - 4} y={v.y + 14} fill="#00FF88" fontSize="12" textAnchor="end" fontFamily="'Share Tech Mono', monospace">
                  ✓
                </text>
              )}
            </g>
          );
        })}

      {/* Preprocess: scan lines */}
      {mode === 'gray' && (
        <g opacity="0.15">
          {[...Array(20)].map((_, i) => (
            <line key={i} x1="0" y1={i * 18} x2="640" y2={i * 18} stroke="#32ADE6" strokeWidth="0.5" />
          ))}
          <rect x="0" y="0" width="640" height="360" fill="#32ADE6" opacity="0.04" />
        </g>
      )}

      {/* LIVE indicator */}
      <g>
        <rect x="14" y="14" width="42" height="18" rx="4" fill="#FF3B30" opacity="0.9" />
        <text x="35" y="26.5" textAnchor="middle" fill="white" fontSize="9" fontFamily="'Share Tech Mono', monospace" fontWeight="bold">LIVE</text>
        <circle cx="20" cy="23" r="3" fill="white" opacity="0.9">
          <animate attributeName="opacity" values="1;0.3;1" dur="1.2s" repeatCount="indefinite" />
        </circle>
      </g>

      {/* Stage label overlay */}
      <g>
        <rect x="0" y="330" width="640" height="30" fill="rgba(4,8,15,0.85)" />
        <text x="16" y="350" fill={stage.color} fontSize="11" fontFamily="'Share Tech Mono', monospace" fontWeight="bold">
          [{STAGES.findIndex(s => s.id === stage.id) + 1}/8] {stage.label.toUpperCase()}
        </text>
        <text x="624" y="350" fill="#3D5570" fontSize="10" fontFamily="'Share Tech Mono', monospace" textAnchor="end">
          {stage.latency}ms
        </text>
      </g>
    </svg>
  );
}

// ─── Timing bar ───────────────────────────────────────────────────────────────

function TimingBar({ activeIndex }: { activeIndex: number }) {
  const total = STAGES.reduce((s, st) => s + st.latency, 0);
  let cursor = 0;
  return (
    <div className="flex items-center h-6 gap-px overflow-hidden rounded-lg">
      {STAGES.map((st, i) => {
        const pct = (st.latency / total) * 100;
        const x = cursor;
        cursor += pct;
        return (
          <div
            key={st.id}
            className="h-full flex items-center justify-center transition-opacity duration-200"
            style={{
              width: `${pct}%`,
              backgroundColor: i <= activeIndex ? st.color : '#182B42',
              opacity: i === activeIndex ? 1 : i < activeIndex ? 0.6 : 0.25,
            }}
            title={`${st.label}: ${st.latency}ms`}
          />
        );
      })}
    </div>
  );
}

// ─── Pipeline list ────────────────────────────────────────────────────────────

function PipelineList({
  activeIndex,
  expandedIndex,
  onSelect,
}: {
  activeIndex: number;
  expandedIndex: number;
  onSelect: (i: number) => void;
}) {
  return (
    <div className="space-y-1">
      {STAGES.map((stage, i) => {
        const Icon = stage.icon;
        const done = i < activeIndex;
        const active = i === activeIndex;
        const future = i > activeIndex;
        const expanded = i === expandedIndex;
        return (
          <div key={stage.id}>
            <button
              onClick={() => onSelect(i)}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border text-left transition-all duration-150',
                active && 'border-transparent bg-[rgba(50,173,230,0.08)] shadow-sm',
                done && !active && 'border-transparent hover:bg-white/5',
                future && 'border-transparent opacity-35 cursor-default',
                expanded && !active && !future && 'bg-white/5'
              )}
            >
              {/* Status icon */}
              <div
                className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 border transition-all"
                style={
                  active
                    ? { background: `${stage.color}20`, borderColor: stage.color, boxShadow: `0 0 8px ${stage.color}40` }
                    : done
                    ? { background: 'rgba(0,255,136,0.08)', borderColor: 'rgba(0,255,136,0.3)' }
                    : { background: 'transparent', borderColor: '#182B42' }
                }
              >
                {done ? (
                  <CheckCircle size={13} className="text-[#00FF88]" />
                ) : (
                  <Icon size={13} style={{ color: active ? stage.color : '#3D5570' }} />
                )}
              </div>

              <div className="flex-1 min-w-0">
                <div className={cn(
                  'font-display text-vital-xs font-medium leading-tight',
                  active ? 'text-[#E8F1FF]' : done ? 'text-[#7A90AA]' : 'text-[#3D5570]'
                )}>
                  {stage.label}
                </div>
                <div className="font-display text-[10px] text-[#3D5570] truncate mt-0.5">{stage.sublabel}</div>
              </div>

              <div className="flex items-center gap-1.5 flex-shrink-0">
                {done && (
                  <span className="font-mono text-[10px] text-[#3D5570]">{stage.latency}ms</span>
                )}
                {active && (
                  <span className="font-mono text-[10px]" style={{ color: stage.color }}>{stage.latency}ms</span>
                )}
                <ChevronRight
                  size={12}
                  className={cn(
                    'text-[#3D5570] transition-transform',
                    expanded && 'rotate-90'
                  )}
                />
              </div>
            </button>

            {/* Expanded details */}
            <AnimatePresence>
              {expanded && !future && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.18, ease: [0, 0, 0.2, 1] }}
                  className="overflow-hidden"
                >
                  <div className="mx-3 mb-1 rounded-xl border border-monitor-border bg-monitor-bg px-4 py-3 space-y-0">
                    {Object.entries(stage.details).map(([k, v]) => (
                      <div key={k} className="flex items-center justify-between py-1.5 border-b border-monitor-border last:border-0">
                        <span className="font-display text-[10px] text-[#3D5570]">{k}</span>
                        <span className="font-mono text-[10px] text-[#7A90AA]">{v}</span>
                      </div>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Connector */}
            {i < STAGES.length - 1 && (
              <div className="flex justify-center h-3">
                <div
                  className="w-px transition-colors duration-300"
                  style={{ background: done ? 'rgba(0,255,136,0.25)' : '#182B42' }}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export function OcrDebugPage() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [expandedIndex, setExpandedIndex] = useState(0);
  const [autoPlay, setAutoPlay] = useState(false);
  const [roiIndex, setRoiIndex] = useState(-1);
  const autoRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const activeStage = STAGES[activeIndex];
  const totalLatency = STAGES.slice(0, activeIndex + 1).reduce((s, st) => s + st.latency, 0);

  const advance = () => {
    setActiveIndex((i) => {
      const next = Math.min(i + 1, STAGES.length - 1);
      setExpandedIndex(next);
      return next;
    });
  };

  const reset = () => {
    setActiveIndex(0);
    setExpandedIndex(0);
    setRoiIndex(-1);
    setAutoPlay(false);
    clearTimeout(autoRef.current);
  };

  // ROI animation when entering extract stage
  useEffect(() => {
    if (activeStage.id === 'extract') {
      setRoiIndex(-1);
      VITAL_REGIONS.forEach((_, i) => {
        setTimeout(() => setRoiIndex(i), 300 + i * 400);
      });
    }
  }, [activeStage.id]);

  // Auto-play
  useEffect(() => {
    if (!autoPlay) return;
    const delay = activeStage.latency * 80 + 600;
    autoRef.current = setTimeout(() => {
      if (activeIndex < STAGES.length - 1) advance();
      else setAutoPlay(false);
    }, delay);
    return () => clearTimeout(autoRef.current);
  }, [autoPlay, activeIndex]);

  const handleSelect = (i: number) => {
    if (i > activeIndex) return;
    setExpandedIndex(expandedIndex === i ? -1 : i);
  };

  return (
    <motion.div
      className="h-full bg-monitor-bg flex flex-col"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      {/* Header */}
      <header className="h-12 flex items-center justify-between px-5 border-b border-monitor-border bg-monitor-surface flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-[rgba(50,173,230,0.1)] border border-[rgba(50,173,230,0.2)] flex items-center justify-center">
            <Cpu size={14} className="text-[#32ADE6]" />
          </div>
          <h1 className="font-display font-semibold text-[#E8F1FF]">OCR Pipeline Inspector</h1>
          <div className="h-4 w-px bg-monitor-border" />
          <span className="font-display text-vital-xs text-[#3D5570]">
            Stage {activeIndex + 1} of {STAGES.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={activeIndex === STAGES.length - 1 ? 'success' : 'info'}>
            {activeIndex === STAGES.length - 1 ? 'Complete' : 'Processing'}
          </Badge>
          <span className="font-mono text-vital-xs text-[#3D5570]">{totalLatency}ms total</span>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* ── Left: frame viewer ── */}
        <div className="flex-1 flex flex-col overflow-hidden min-h-0">
          {/* Canvas */}
          <div className="flex-1 bg-monitor-bg min-h-0">
            <AnimatePresence mode="wait">
              <motion.div
                key={activeIndex}
                className="w-full h-full"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              >
                <FrameCanvas stage={activeStage} roiIndex={roiIndex} />
              </motion.div>
            </AnimatePresence>
          </div>

          {/* Timing bar */}
          <div className="flex-shrink-0 bg-monitor-surface border-t border-monitor-border px-5 py-3 space-y-2">
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-display text-[10px] text-[#3D5570] uppercase tracking-wider">Pipeline Timing</span>
              <span className="font-mono text-[10px] text-[#7A90AA]">
                {STAGES.reduce((s, st) => s + st.latency, 0)}ms total · ~{Math.round(1000 / STAGES.reduce((s, st) => s + st.latency, 0))} fps
              </span>
            </div>
            <TimingBar activeIndex={activeIndex} />
            <div className="flex items-center justify-between">
              {STAGES.map((st, i) => (
                <div
                  key={st.id}
                  className="font-display text-[9px] transition-colors"
                  style={{ color: i <= activeIndex ? st.color : '#1E3A56' }}
                  title={st.label}
                >
                  {st.latency}
                </div>
              ))}
            </div>
          </div>

          {/* Controls */}
          <div className="flex-shrink-0 bg-monitor-surface border-t border-monitor-border px-5 py-3 flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              icon={<RotateCcw size={14} />}
              onClick={reset}
            >
              Reset
            </Button>

            <Button
              variant={autoPlay ? 'danger' : 'ghost'}
              size="sm"
              icon={<Play size={14} />}
              onClick={() => setAutoPlay(v => !v)}
              disabled={activeIndex === STAGES.length - 1}
            >
              {autoPlay ? 'Stop' : 'Auto-play'}
            </Button>

            <div className="flex-1">
              <ProgressBar
                value={Math.round((activeIndex / (STAGES.length - 1)) * 100)}
                size="sm"
                color="#32ADE6"
                animated={autoPlay}
              />
            </div>

            <Button
              variant="primary"
              size="sm"
              icon={<SkipForward size={14} />}
              onClick={advance}
              disabled={activeIndex === STAGES.length - 1}
            >
              {activeIndex === STAGES.length - 1 ? 'Complete' : 'Next Stage'}
            </Button>
          </div>
        </div>

        {/* ── Right: pipeline list ── */}
        <div className="w-80 border-l border-monitor-border bg-monitor-surface flex flex-col flex-shrink-0">
          {/* Stage detail header */}
          <div
            className="flex-shrink-0 px-4 py-4 border-b border-monitor-border"
            style={{ borderLeftColor: activeStage.color, borderLeftWidth: 3 }}
          >
            <div className="flex items-center gap-2 mb-1">
              <activeStage.icon size={14} style={{ color: activeStage.color }} />
              <span className="font-display text-vital-sm font-semibold text-[#E8F1FF]">{activeStage.label}</span>
              <span className="ml-auto font-mono text-[10px]" style={{ color: activeStage.color }}>{activeStage.latency}ms</span>
            </div>
            <p className="font-display text-[10px] text-[#3D5570]">{activeStage.sublabel}</p>
          </div>

          {/* Pipeline stages */}
          <div className="flex-1 overflow-y-auto p-3">
            <PipelineList
              activeIndex={activeIndex}
              expandedIndex={expandedIndex}
              onSelect={handleSelect}
            />
          </div>

          {/* Summary footer */}
          {activeIndex === STAGES.length - 1 && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex-shrink-0 px-4 py-4 border-t border-monitor-border"
            >
              <div className="flex items-center gap-2 mb-3">
                <CheckCircle size={14} className="text-[#00FF88]" />
                <span className="font-display text-vital-xs font-semibold text-[#00FF88]">Pipeline complete</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: 'Total latency', value: `${STAGES.reduce((s, st) => s + st.latency, 0)}ms` },
                  { label: 'Throughput', value: '~11 fps' },
                  { label: 'Avg confidence', value: '95.4%' },
                  { label: 'Flags', value: 'None' },
                ].map(({ label, value }) => (
                  <div key={label} className="rounded-xl border border-monitor-border bg-monitor-bg p-2">
                    <div className="font-display text-[10px] text-[#3D5570]">{label}</div>
                    <div className="font-display text-vital-xs font-semibold text-[#E8F1FF] mt-0.5">{value}</div>
                  </div>
                ))}
              </div>
            </motion.div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
