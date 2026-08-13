import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Camera, CheckCircle, Monitor, Target, Sliders, Wifi,
  ChevronLeft, ChevronRight, Cpu, RotateCcw,
} from 'lucide-react';
import { Button } from '@/design-system/components/Button';
import { Badge } from '@/design-system/components/Badge';
import { ProgressBar } from '@/design-system/components/Progress';
import { cn } from '@/lib/utils';
import { api, type PipelineReadFrameResult } from '@/lib/api';

// ─── Types & config ─────────────────────────────────────────────────────────────

type StepId = 'connect' | 'detect' | 'perspective' | 'roi' | 'verify' | 'complete';

const STEPS: { id: StepId; label: string; icon: React.ElementType; desc: string }[] = [
  { id: 'connect',     label: 'Camera',         icon: Camera,       desc: 'Connect capture camera' },
  { id: 'detect',      label: 'Detect Monitor', icon: Monitor,      desc: 'Locate monitor boundary' },
  { id: 'perspective', label: 'Perspective',    icon: RotateCcw,    desc: 'Correct camera angle' },
  { id: 'roi',         label: 'Regions',        icon: Target,       desc: 'Map vital sign areas' },
  { id: 'verify',      label: 'Verify',         icon: Sliders,      desc: 'Test OCR accuracy' },
  { id: 'complete',    label: 'Complete',        icon: CheckCircle,  desc: 'Save profile' },
];

const VITAL_REGIONS = [
  { key: 'hr',    label: 'HR',    color: '#00FF88', value: '74',     unit: 'bpm',  x: 348, y: 52,  w: 168, h: 65, conf: 98 },
  { key: 'spo2',  label: 'SpO₂',  color: '#00D4FF', value: '98',     unit: '%',    x: 348, y: 120, w: 168, h: 65, conf: 97 },
  { key: 'etco2', label: 'EtCO₂', color: '#FFD600', value: '37',     unit: 'mmHg', x: 348, y: 188, w: 168, h: 65, conf: 94 },
  { key: 'nibp',  label: 'NIBP',  color: '#FF4757', value: '122/78', unit: 'mmHg', x: 348, y: 256, w: 168, h: 65, conf: 91 },
];

// ─── Camera preview SVG ──────────────────────────────────────────────────────────

function CameraPreview({ step, cameraActive, roiVisible, verifyProgress, verifyDone }: {
  step: StepId;
  cameraActive: boolean;
  roiVisible: boolean[];
  verifyProgress: number;
  verifyDone: boolean;
}) {
  return (
    <svg
      viewBox="0 0 640 360"
      className="w-full h-full"
      style={{ background: '#04080F' }}
    >
      <defs>
        {/* scan line gradient */}
        <linearGradient id="scanGrad" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#32ADE6" stopOpacity="0" />
          <stop offset="50%" stopColor="#32ADE6" stopOpacity="0.8" />
          <stop offset="100%" stopColor="#32ADE6" stopOpacity="0" />
        </linearGradient>
        {/* glow filter */}
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="softGlow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      {/* ── Ambient background noise (always) ── */}
      {[...Array(18)].map((_, i) => (
        <rect
          key={i}
          x={Math.sin(i * 37) * 300 + 320}
          y={Math.cos(i * 53) * 160 + 180}
          width={Math.abs(Math.sin(i * 17)) * 3 + 1}
          height="1"
          fill="#182B42"
          opacity="0.4"
        />
      ))}

      {/* ── NO SIGNAL state ── */}
      {!cameraActive && step === 'connect' && (
        <>
          <text x="320" y="155" textAnchor="middle" fill="#182B42" fontSize="11" fontFamily="monospace" letterSpacing="4">
            ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ● ●
          </text>
          <Camera x={282} y={158} width={76} height={76} stroke="#1E3A56" strokeWidth={1} fill="none" />
          <text x="320" y="260" textAnchor="middle" fill="#1E3A56" fontSize="13" fontFamily="'Share Tech Mono', monospace" letterSpacing="3">
            NO SIGNAL
          </text>
        </>
      )}

      {/* ── LIVE FEED: Monitor shape ── */}
      {(cameraActive || step !== 'connect') && (
        <>
          {/* Room background suggestion */}
          <rect x="0" y="0" width="640" height="360" fill="#060C18" />
          {/* Monitor body (slightly perspective-warped trapezoid) */}
          <polygon
            points="95,44 549,36 562,316 82,324"
            fill="#0A1520"
            stroke="#182B42"
            strokeWidth="1.5"
          />
          {/* Monitor bezel highlight */}
          <polygon
            points="95,44 549,36 562,316 82,324"
            fill="none"
            stroke="#1E3A56"
            strokeWidth="0.5"
          />
          {/* Monitor screen area */}
          <polygon
            points="108,56 537,49 550,304 96,312"
            fill="#070D1A"
          />

          {/* ── Screen content: waveform area ── */}
          <clipPath id="screenClip">
            <polygon points="108,56 537,49 550,304 96,312" />
          </clipPath>
          <g clipPath="url(#screenClip)">
            {/* Divider between waveforms and vitals */}
            <line x1="340" y1="52" x2="340" y2="308" stroke="#182B42" strokeWidth="1" />

            {/* ECG waveform (left panel) */}
            <g opacity="0.7">
              <path
                d="M108 110 L140 110 L150 90 L158 130 L166 110 L200 110 L210 90 L218 130 L226 110 L260 110 L270 90 L278 130 L286 110 L320 110"
                fill="none"
                stroke="#00FF88"
                strokeWidth="1.5"
                filter="url(#glow)"
              />
              <text x="115" y="72" fill="#00FF88" opacity="0.5" fontSize="9" fontFamily="'Share Tech Mono', monospace">ECG</text>
            </g>

            {/* SpO2 pleth waveform */}
            <g opacity="0.6">
              <path
                d="M108 190 Q130 160 152 190 Q174 220 196 190 Q218 160 240 190 Q262 220 284 190 Q306 160 328 190"
                fill="none"
                stroke="#00D4FF"
                strokeWidth="1.5"
                filter="url(#glow)"
              />
              <text x="115" y="152" fill="#00D4FF" opacity="0.5" fontSize="9" fontFamily="'Share Tech Mono', monospace">SpO₂</text>
            </g>

            {/* Capnography */}
            <g opacity="0.5">
              <path
                d="M108 270 L130 270 L132 248 L160 248 L162 270 L190 270 L192 248 L220 248 L222 270 L250 270 L252 248 L280 248 L282 270 L310 270 L312 248 L330 248"
                fill="none"
                stroke="#FFD600"
                strokeWidth="1.5"
              />
              <text x="115" y="237" fill="#FFD600" opacity="0.5" fontSize="9" fontFamily="'Share Tech Mono', monospace">EtCO₂</text>
            </g>

            {/* ── Vital value boxes (right panel) ── */}
            {VITAL_REGIONS.map((v, i) => (
              <g key={v.key}>
                {/* Box bg */}
                <rect x={v.x} y={v.y} width={v.w} height={v.h} fill="#0C1625" rx="2" />
                {/* Left color stripe */}
                <rect x={v.x} y={v.y} width="3" height={v.h} fill={v.color} opacity="0.8" />
                {/* Label */}
                <text x={v.x + 10} y={v.y + 18} fill={v.color} fontSize="10" fontFamily="'Share Tech Mono', monospace" opacity="0.7">
                  {v.label}
                </text>
                {/* Value */}
                <text x={v.x + 10} y={v.y + 48} fill={v.color} fontSize="26" fontFamily="'Share Tech Mono', monospace" filter="url(#glow)">
                  {v.value}
                </text>
                {/* Unit */}
                <text x={v.x + v.w - 8} y={v.y + v.h - 10} fill={v.color} fontSize="9" fontFamily="'Share Tech Mono', monospace" textAnchor="end" opacity="0.5">
                  {v.unit}
                </text>
                {/* ROI bounding box overlay */}
                {(step === 'roi' || step === 'verify' || step === 'complete') && roiVisible[i] && (
                  <g filter="url(#glow)">
                    <rect
                      x={v.x - 2} y={v.y - 2} width={v.w + 4} height={v.h + 4}
                      fill="none"
                      stroke={v.color}
                      strokeWidth="1.5"
                      strokeDasharray={step === 'roi' ? '6 3' : 'none'}
                      rx="3"
                    />
                    {/* Corner markers */}
                    {[
                      [v.x - 2, v.y - 2], [v.x + v.w + 2, v.y - 2],
                      [v.x + v.w + 2, v.y + v.h + 2], [v.x - 2, v.y + v.h + 2],
                    ].map(([cx, cy], ci) => (
                      <rect key={ci} x={(cx as number) - 3} y={(cy as number) - 3} width="6" height="6" fill={v.color} opacity="0.9" />
                    ))}
                  </g>
                )}
                {/* Verify: confidence overlay */}
                {step === 'verify' && roiVisible[i] && (
                  <g>
                    <rect x={v.x + 2} y={v.y + v.h - 14} width={v.w - 4} height="10" fill="rgba(0,0,0,0.6)" rx="2" />
                    <rect
                      x={v.x + 3} y={v.y + v.h - 13}
                      width={Math.min((v.w - 6) * verifyProgress * (v.conf / 100), v.w - 6)}
                      height="8"
                      fill={v.color}
                      rx="2"
                      opacity="0.8"
                    />
                    {verifyDone && (
                      <text x={v.x + v.w / 2} y={v.y + v.h - 6} fill="#FFFFFF" fontSize="8" textAnchor="middle" fontFamily="'Share Tech Mono', monospace">
                        {v.conf}%
                      </text>
                    )}
                  </g>
                )}
              </g>
            ))}
          </g>

          {/* ── Detect step: animated quadrilateral ── */}
          {step === 'detect' && (
            <AnimatedQuadrilateral />
          )}

          {/* ── Perspective step: corner handles ── */}
          {step === 'perspective' && (
            <PerspectiveHandles />
          )}

          {/* ── Active LIVE badge ── */}
          {step !== 'connect' && (
            <g>
              <rect x="14" y="14" width="42" height="18" rx="4" fill="#FF3B30" opacity="0.9" />
              <text x="35" y="26.5" textAnchor="middle" fill="white" fontSize="9" fontFamily="'Share Tech Mono', monospace" fontWeight="bold">
                LIVE
              </text>
              <circle cx="20" cy="23" r="3" fill="white" opacity="0.9">
                <animate attributeName="opacity" values="1;0.3;1" dur="1.2s" repeatCount="indefinite" />
              </circle>
            </g>
          )}

          {/* ── Camera info overlay ── */}
          <g opacity="0.4">
            <text x="16" y="345" fill="#32ADE6" fontSize="9" fontFamily="'Share Tech Mono', monospace">
              1920×1080 · 30fps · USB-C
            </text>
            <text x="624" y="345" fill="#32ADE6" fontSize="9" fontFamily="'Share Tech Mono', monospace" textAnchor="end">
              VITAL CAM v2
            </text>
          </g>
        </>
      )}

      {/* ── Verify step: scan line ── */}
      {step === 'verify' && verifyProgress < 1 && (
        <rect
          x="0"
          y={verifyProgress * 360 - 20}
          width="640"
          height="40"
          fill="url(#scanGrad)"
          opacity="0.5"
        />
      )}
    </svg>
  );
}

// ── Detect step animation ────────────────────────────────────────────────────────
function AnimatedQuadrilateral() {
  return (
    <g filter="url(#glow)">
      {/* Animated dashed border */}
      <motion.polygon
        points="95,44 549,36 562,316 82,324"
        fill="none"
        stroke="#32ADE6"
        strokeWidth="2"
        strokeDasharray="12 6"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: 1.5, ease: 'easeInOut' }}
      />
      {/* Corner dots */}
      {[{ x: 95, y: 44 }, { x: 549, y: 36 }, { x: 562, y: 316 }, { x: 82, y: 324 }].map((pt, i) => (
        <motion.circle
          key={i}
          cx={pt.x} cy={pt.y} r="6"
          fill="#32ADE6"
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: 0.3 + i * 0.25, duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
        >
          <animate attributeName="r" values="6;9;6" dur="2s" repeatCount="indefinite" begin={`${i * 0.5}s`} />
        </motion.circle>
      ))}
      {/* Detection label */}
      <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.2 }}>
        <rect x="108" y="52" width="130" height="20" rx="3" fill="rgba(50,173,230,0.15)" />
        <text x="116" y="66" fill="#32ADE6" fontSize="10" fontFamily="'Share Tech Mono', monospace">
          MONITOR DETECTED
        </text>
      </motion.g>
    </g>
  );
}

// ── Perspective step: draggable handles simulation ───────────────────────────────
function PerspectiveHandles() {
  const corners = [
    { x: 95,  y: 44,  label: 'TL' },
    { x: 549, y: 36,  label: 'TR' },
    { x: 562, y: 316, label: 'BR' },
    { x: 82,  y: 324, label: 'BL' },
  ];
  // Corrected rectangle target positions
  const targets = [
    { x: 96,  y: 44  },
    { x: 548, y: 44  },
    { x: 548, y: 316 },
    { x: 96,  y: 316 },
  ];

  return (
    <g>
      {/* Original boundary */}
      <polygon
        points="95,44 549,36 562,316 82,324"
        fill="none"
        stroke="#FF9500"
        strokeWidth="1"
        strokeDasharray="8 4"
        opacity="0.4"
      />
      {/* Corrected rectangle */}
      <motion.rect
        x="96" y="44" width="452" height="272"
        fill="none"
        stroke="#00FF88"
        strokeWidth="1.5"
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.6 }}
        transition={{ delay: 0.8, duration: 0.4 }}
      />
      {/* Corner handles */}
      {corners.map((c, i) => (
        <motion.g key={i}
          initial={{ x: 0, y: 0 }}
          animate={{ x: targets[i].x - c.x, y: targets[i].y - c.y }}
          transition={{ delay: 0.5 + i * 0.1, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        >
          <circle cx={c.x} cy={c.y} r="10" fill="rgba(50,173,230,0.15)" stroke="#32ADE6" strokeWidth="1.5" />
          <circle cx={c.x} cy={c.y} r="4" fill="#32ADE6" />
          {/* Cross hair */}
          <line x1={c.x - 14} y1={c.y} x2={c.x + 14} y2={c.y} stroke="#32ADE6" strokeWidth="0.5" opacity="0.5" />
          <line x1={c.x} y1={c.y - 14} x2={c.x} y2={c.y + 14} stroke="#32ADE6" strokeWidth="0.5" opacity="0.5" />
        </motion.g>
      ))}
      {/* Correction label */}
      <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.4 }}>
        <rect x="108" y="52" width="155" height="20" rx="3" fill="rgba(0,255,136,0.1)" />
        <text x="116" y="66" fill="#00FF88" fontSize="10" fontFamily="'Share Tech Mono', monospace">
          PERSPECTIVE CORRECTED
        </text>
      </motion.g>
    </g>
  );
}

// ─── Step content components ─────────────────────────────────────────────────────

function StepCard({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-2xl border border-monitor-border bg-monitor-surface p-5', className)}>
      {children}
    </div>
  );
}

function StepTitle({ icon: Icon, title, desc }: { icon: React.ElementType; title: string; desc: string }) {
  return (
    <div className="flex items-start gap-3 mb-5">
      <div className="w-9 h-9 rounded-xl bg-[rgba(50,173,230,0.1)] border border-[rgba(50,173,230,0.2)] flex items-center justify-center flex-shrink-0">
        <Icon size={18} className="text-[#32ADE6]" />
      </div>
      <div>
        <h2 className="font-display font-semibold text-[#E8F1FF] text-lg leading-tight">{title}</h2>
        <p className="font-display text-vital-xs text-[#7A90AA] mt-0.5">{desc}</p>
      </div>
    </div>
  );
}

function InfoRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-monitor-border last:border-0">
      <span className="font-display text-vital-xs text-[#7A90AA]">{label}</span>
      <span className="font-display text-vital-xs font-medium" style={{ color: color ?? '#E8F1FF' }}>{value}</span>
    </div>
  );
}

function InstructionItem({ n, text }: { n: number; text: string }) {
  return (
    <li className="flex items-start gap-2.5 font-display text-vital-sm text-[#7A90AA]">
      <span className="w-5 h-5 rounded-full bg-[rgba(50,173,230,0.1)] border border-[rgba(50,173,230,0.3)] text-[#32ADE6] flex-shrink-0 flex items-center justify-center text-[10px] font-bold mt-0.5">
        {n}
      </span>
      {text}
    </li>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────────

export function CalibrationPage() {
  const [currentStep, setCurrentStep] = useState<StepId>('connect');
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [cameraInfo, setCameraInfo] = useState<{ label: string; width: number; height: number } | null>(null);
  const [sourceMode, setSourceMode] = useState<'camera' | 'screen' | null>(null);
  const [roiVisible, setRoiVisible] = useState<boolean[]>([false, false, false, false]);
  const [verifyProgress, setVerifyProgress] = useState(0);
  const [verifyDone, setVerifyDone] = useState(false);
  const [verifyResult, setVerifyResult] = useState<PipelineReadFrameResult | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [detectProgress, setDetectProgress] = useState(0);
  const [perspectiveProgress, setPerspectiveProgress] = useState(0);
  const verifyRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setCameraActive(false);
    setCameraInfo(null);
    setSourceMode(null);
  };

  const connectSource = async (mode: 'camera' | 'screen') => {
    if (cameraActive) {
      stopCamera();
      return;
    }
    setCameraError(null);
    try {
      // Camera: photographs a real screen (phone/monitor), so it inherits
      // lighting/lens/JPEG artefacts on top of whatever colour drift the
      // source screen already has.
      // Screen/tab share: captures another tab's actual rendered pixels
      // directly — no camera, no lighting, no re-encoding — so pointing
      // this at VITAL's own Live Monitor tab is the cleanest possible
      // verification of the colour-ROI pipeline, and needs no second device.
      const stream = mode === 'camera'
        ? await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } } })
        : await navigator.mediaDevices.getDisplayMedia({ video: { displaySurface: 'browser' } as MediaTrackConstraints });

      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      const track = stream.getVideoTracks()[0];
      const settings = track?.getSettings();
      setCameraInfo({
        label: track?.label || (mode === 'camera' ? 'Camera' : 'Shared tab'),
        width: settings?.width ?? 0,
        height: settings?.height ?? 0,
      });
      setSourceMode(mode);
      setCameraActive(true);

      // Screen share can be stopped from the browser's own "Stop sharing"
      // bar, not just our Disconnect button — that only ends the track, it
      // doesn't fire our state, so without this the UI would keep claiming
      // to be connected to a stream that's already dead.
      track?.addEventListener('ended', stopCamera);
    } catch (err) {
      setCameraError(err instanceof Error ? err.message : 'Could not access camera');
    }
  };

  // Release the camera when leaving the page, not just on explicit disconnect
  useEffect(() => () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
  }, []);

  const stepIndex = STEPS.findIndex((s) => s.id === currentStep);
  const isLastContent = stepIndex === STEPS.length - 2; // 'verify' step

  // Auto-run detect progress when entering detect step
  useEffect(() => {
    if (currentStep === 'detect') {
      setDetectProgress(0);
      const iv = setInterval(() => {
        setDetectProgress((p) => {
          if (p >= 1) { clearInterval(iv); return 1; }
          return p + 0.015;
        });
      }, 50);
      return () => clearInterval(iv);
    }
  }, [currentStep]);

  // Auto-run perspective progress
  useEffect(() => {
    if (currentStep === 'perspective') {
      setPerspectiveProgress(0);
      const iv = setInterval(() => {
        setPerspectiveProgress((p) => {
          if (p >= 1) { clearInterval(iv); return 1; }
          return p + 0.012;
        });
      }, 40);
      return () => clearInterval(iv);
    }
  }, [currentStep]);

  // Animate ROI boxes appearing one by one
  useEffect(() => {
    if (currentStep === 'roi') {
      setRoiVisible([false, false, false, false]);
      VITAL_REGIONS.forEach((_, i) => {
        setTimeout(() => {
          setRoiVisible((prev) => {
            const next = [...prev];
            next[i] = true;
            return next;
          });
        }, 400 + i * 500);
      });
    }
  }, [currentStep]);

  const runVerify = async () => {
    setVerifyProgress(0);
    setVerifyDone(false);
    setVerifyError(null);
    setVerifyResult(null);
    setRoiVisible([true, true, true, true]);

    // Real OCR round-trip has no progress events of its own — this timer just
    // keeps the bar moving while we wait, capped short of 100% until the
    // response actually lands.
    let p = 0;
    verifyRef.current = setInterval(() => {
      p += Math.random() * 0.02 + 0.01;
      setVerifyProgress(Math.min(p, 0.9));
    }, 80);

    try {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas || !streamRef.current) {
        throw new Error('Camera not connected');
      }
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Could not capture frame');
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob>((resolve, reject) =>
        canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not capture frame'))), 'image/jpeg', 0.92)
      );
      const result = await api.readFrame(blob);
      setVerifyResult(result);
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : 'Verification failed');
    } finally {
      clearInterval(verifyRef.current);
      setVerifyProgress(1);
      setVerifyDone(true);
    }
  };

  const goNext = () => {
    if (stepIndex < STEPS.length - 1) setCurrentStep(STEPS[stepIndex + 1].id);
  };
  const goPrev = () => {
    if (stepIndex > 0) {
      if (currentStep === 'verify') {
        setVerifyProgress(0);
        setVerifyDone(false);
      }
      setCurrentStep(STEPS[stepIndex - 1].id);
    }
  };

  const canProceed = () => {
    if (currentStep === 'connect') return cameraActive;
    if (currentStep === 'detect') return detectProgress >= 1;
    if (currentStep === 'perspective') return perspectiveProgress >= 1;
    if (currentStep === 'roi') return roiVisible.every(Boolean);
    if (currentStep === 'verify') return verifyDone;
    return true;
  };

  return (
    <motion.div
      className="h-full bg-monitor-bg flex flex-col"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      {/* Header */}
      <header className="flex items-center justify-between h-12 px-6 border-b border-monitor-border bg-monitor-surface flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-[rgba(50,173,230,0.1)] border border-[rgba(50,173,230,0.2)] flex items-center justify-center">
            <Cpu size={14} className="text-[#32ADE6]" />
          </div>
          <h1 className="font-display font-semibold text-[#E8F1FF]">Camera Calibration</h1>
          <div className="h-4 w-px bg-monitor-border" />
          <span className="font-display text-vital-xs text-[#3D5570]">Step {Math.min(stepIndex + 1, 5)} of 5</span>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={cameraActive ? 'success' : 'neutral'} pulse={cameraActive}>
            {cameraActive ? 'Camera Active' : 'No Camera'}
          </Badge>
          {currentStep === 'complete' && (
            <Badge variant="success">Profile Saved</Badge>
          )}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* ── Step sidebar ── */}
        <aside className="w-52 border-r border-monitor-border bg-monitor-surface flex flex-col py-4 flex-shrink-0">
          <div className="px-4 mb-3">
            <span className="font-display text-[10px] text-[#3D5570] uppercase tracking-widest">Setup Steps</span>
          </div>
          {STEPS.map((step, i) => {
            const Icon = step.icon;
            const done = i < stepIndex;
            const active = i === stepIndex;
            const future = i > stepIndex;
            return (
              <div key={step.id} className="relative">
                <button
                  onClick={() => done && setCurrentStep(step.id)}
                  disabled={future}
                  className={cn(
                    'w-full flex items-center gap-3 px-4 py-3 text-left transition-all duration-150',
                    active && 'bg-[rgba(50,173,230,0.08)]',
                    done && 'hover:bg-white/5 cursor-pointer',
                    future && 'cursor-default opacity-40'
                  )}
                >
                  <motion.div
                    className={cn(
                      'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 border transition-all',
                      active  && 'bg-[#32ADE6] border-[#32ADE6] text-white shadow-[0_0_12px_rgba(50,173,230,0.5)]',
                      done    && 'bg-[rgba(0,255,136,0.1)] border-[rgba(0,255,136,0.4)] text-[#00FF88]',
                      future  && 'bg-transparent border-monitor-border text-[#3D5570]'
                    )}
                    animate={active ? { scale: [1, 1.05, 1] } : {}}
                    transition={{ duration: 2, repeat: Infinity }}
                  >
                    {done ? <CheckCircle size={14} /> : <Icon size={14} />}
                  </motion.div>
                  <div className="min-w-0">
                    <div className={cn(
                      'font-display text-vital-xs font-medium truncate',
                      active ? 'text-[#E8F1FF]' : done ? 'text-[#7A90AA]' : 'text-[#3D5570]'
                    )}>
                      {step.label}
                    </div>
                    <div className="font-display text-[10px] text-[#3D5570] truncate mt-0.5">{step.desc}</div>
                  </div>
                </button>
                {/* Connector line */}
                {i < STEPS.length - 1 && (
                  <div className="absolute left-[35px] top-[54px] w-0.5 h-3">
                    <div className={cn(
                      'w-full h-full transition-colors',
                      done || (active && i < stepIndex) ? 'bg-[rgba(0,255,136,0.3)]' : 'bg-monitor-border'
                    )} />
                  </div>
                )}
              </div>
            );
          })}

          {/* Progress summary */}
          {currentStep !== 'connect' && currentStep !== 'complete' && (
            <div className="mt-auto px-4 pb-2">
              <div className="rounded-xl border border-monitor-border bg-monitor-bg p-3">
                <div className="font-display text-[10px] text-[#3D5570] uppercase tracking-wider mb-2">Overall</div>
                <ProgressBar
                  value={Math.round((stepIndex / 5) * 100)}
                  size="sm"
                  color="#32ADE6"
                  showValue
                />
              </div>
            </div>
          )}
        </aside>

        {/* ── Content area ── */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Camera preview */}
          {currentStep !== 'complete' && (
            <div className="relative flex-shrink-0 bg-monitor-bg border-b border-monitor-border overflow-hidden" style={{ height: 260 }}>
              {/* Real camera feed — shown full-bleed once connected, for every step */}
              <video
                ref={videoRef}
                muted
                playsInline
                className={cn('absolute inset-0 w-full h-full object-cover', cameraActive ? 'block' : 'hidden')}
              />
              <canvas ref={canvasRef} className="hidden" />
              {cameraActive && (
                <div className="absolute top-3 left-3 flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[#FF3B30]/90">
                  <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                  <span className="font-display text-[9px] font-bold text-white tracking-wider">LIVE</span>
                </div>
              )}
              {!cameraActive && (
                <CameraPreview
                  step={currentStep}
                  cameraActive={cameraActive}
                  roiVisible={roiVisible}
                  verifyProgress={verifyProgress}
                  verifyDone={verifyDone}
                />
              )}
            </div>
          )}

          {/* Step content */}
          <div className="flex-1 overflow-y-auto p-6">
            <AnimatePresence mode="wait">
              <motion.div
                key={currentStep}
                initial={{ opacity: 0, x: 18 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -18 }}
                transition={{ duration: 0.2, ease: [0, 0, 0.2, 1] }}
              >
                {currentStep === 'connect'     && <ContentConnect cameraActive={cameraActive} cameraError={cameraError} cameraInfo={cameraInfo} sourceMode={sourceMode} onConnect={connectSource} />}
                {currentStep === 'detect'      && <ContentDetect progress={detectProgress} />}
                {currentStep === 'perspective' && <ContentPerspective progress={perspectiveProgress} />}
                {currentStep === 'roi'         && <ContentROI roiVisible={roiVisible} />}
                {currentStep === 'verify'      && <ContentVerify running={!verifyDone && verifyProgress > 0} done={verifyDone} onStart={runVerify} result={verifyResult} error={verifyError} />}
                {currentStep === 'complete'    && <ContentComplete />}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </div>

      {/* ── Footer navigation ── */}
      {currentStep !== 'complete' && (
        <footer className="flex items-center justify-between px-6 py-3 border-t border-monitor-border bg-monitor-surface flex-shrink-0">
          <Button
            variant="ghost"
            size="sm"
            icon={<ChevronLeft size={15} />}
            onClick={goPrev}
            disabled={stepIndex === 0}
          >
            Back
          </Button>

          <div className="flex items-center gap-1.5">
            {STEPS.slice(0, -1).map((_, i) => (
              <div
                key={i}
                className={cn(
                  'h-1.5 rounded-full transition-all duration-300',
                  i === stepIndex ? 'w-6 bg-[#32ADE6]' : i < stepIndex ? 'w-1.5 bg-[rgba(0,255,136,0.5)]' : 'w-1.5 bg-monitor-border'
                )}
              />
            ))}
          </div>

          <Button
            variant="primary"
            size="sm"
            onClick={isLastContent ? (verifyDone ? goNext : runVerify) : goNext}
            disabled={!canProceed() && !(isLastContent && !verifyDone)}
            iconRight={<ChevronRight size={15} />}
          >
            {isLastContent && !verifyDone ? 'Run Test' : isLastContent ? 'Save Profile' : 'Continue'}
          </Button>
        </footer>
      )}
    </motion.div>
  );
}

// ─── Step content panels ──────────────────────────────────────────────────────────

function ContentConnect({ cameraActive, cameraError, cameraInfo, sourceMode, onConnect }: {
  cameraActive: boolean;
  cameraError: string | null;
  cameraInfo: { label: string; width: number; height: number } | null;
  sourceMode: 'camera' | 'screen' | null;
  onConnect: (mode: 'camera' | 'screen') => void;
}) {
  return (
    <div className="space-y-4 max-w-lg">
      <StepTitle icon={Camera} title="Camera Connection" desc="Grant access to your device's camera, or share a tab/screen directly" />
      <StepCard>
        <div className="space-y-3">
          <InfoRow label="Source" value={cameraActive ? (sourceMode === 'screen' ? 'Shared tab/screen' : 'Camera') : 'Not detected'} color={cameraActive ? '#00FF88' : undefined} />
          <InfoRow label="Device" value={cameraActive ? (cameraInfo?.label || 'Camera') : 'Not detected'} color={cameraActive ? '#00FF88' : undefined} />
          <InfoRow label="Resolution" value={cameraActive && cameraInfo ? `${cameraInfo.width} × ${cameraInfo.height}` : '—'} />
        </div>
      </StepCard>
      {cameraActive ? (
        <Button
          variant="danger"
          onClick={() => onConnect(sourceMode ?? 'camera')}
          className="w-full justify-center"
        >
          Disconnect
        </Button>
      ) : (
        <div className="flex flex-col gap-2.5">
          <Button
            variant="clinical"
            icon={<Wifi size={15} />}
            onClick={() => onConnect('camera')}
            className="w-full justify-center"
          >
            Connect Camera
          </Button>
          <Button
            variant="ghost"
            icon={<Monitor size={15} />}
            onClick={() => onConnect('screen')}
            className="w-full justify-center"
          >
            Share a Tab/Screen Instead
          </Button>
          <p className="font-display text-[10px] text-[#3D5570] text-center px-2">
            Testing without a second device? Open Live Monitor in another tab, then share <em>that tab</em> here — no camera or lighting needed, it reads the tab's pixels directly.
          </p>
        </div>
      )}
      {cameraError && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(255,71,87,0.08)] border border-[rgba(255,71,87,0.25)]"
        >
          <div>
            <div className="font-display text-vital-xs font-medium text-[#FF4757]">Camera access failed</div>
            <div className="font-display text-[10px] text-[#3D5570] mt-0.5">{cameraError}</div>
          </div>
        </motion.div>
      )}
      {cameraActive && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(0,255,136,0.06)] border border-[rgba(0,255,136,0.2)]"
        >
          <CheckCircle size={15} className="text-[#00FF88] flex-shrink-0" />
          <div>
            <div className="font-display text-vital-xs font-medium text-[#00FF88]">Camera connected</div>
            <div className="font-display text-[10px] text-[#3D5570] mt-0.5">Signal detected · Proceed to monitor detection</div>
          </div>
        </motion.div>
      )}
    </div>
  );
}

function ContentDetect({ progress }: { progress: number }) {
  return (
    <div className="space-y-4 max-w-lg">
      <StepTitle icon={Monitor} title="Monitor Detection" desc="Automatically locating the anaesthesia monitor in the camera frame" />
      <StepCard>
        <div className="space-y-3">
          <div className="flex items-center justify-between mb-2">
            <span className="font-display text-vital-xs text-[#7A90AA]">Detection Progress</span>
            <span className="font-mono text-vital-xs text-[#32ADE6]">{Math.round(progress * 100)}%</span>
          </div>
          <ProgressBar value={Math.round(progress * 100)} size="md" color="#32ADE6" animated />
        </div>
        <div className="mt-4 space-y-0">
          {[
            { label: 'Edge detection', done: progress > 0.2 },
            { label: 'Corner extraction', done: progress > 0.5 },
            { label: 'Boundary fitting', done: progress > 0.75 },
            { label: 'Monitor confirmed', done: progress >= 1 },
          ].map((item) => (
            <div key={item.label} className="flex items-center gap-2.5 py-2 border-b border-monitor-border last:border-0">
              <motion.div
                animate={item.done ? { scale: [1, 1.2, 1] } : {}}
                className={cn(
                  'w-4 h-4 rounded-full border flex items-center justify-center flex-shrink-0 transition-all',
                  item.done ? 'bg-[rgba(0,255,136,0.15)] border-[rgba(0,255,136,0.5)]' : 'border-monitor-border'
                )}
              >
                {item.done && <div className="w-1.5 h-1.5 rounded-full bg-[#00FF88]" />}
              </motion.div>
              <span className={cn('font-display text-vital-xs', item.done ? 'text-[#E8F1FF]' : 'text-[#3D5570]')}>
                {item.label}
              </span>
              {item.done && (
                <span className="ml-auto font-mono text-[10px] text-[#00FF88]">✓</span>
              )}
            </div>
          ))}
        </div>
      </StepCard>
      {progress >= 1 && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(0,255,136,0.06)] border border-[rgba(0,255,136,0.2)]"
        >
          <CheckCircle size={15} className="text-[#00FF88] flex-shrink-0" />
          <span className="font-display text-vital-xs text-[#00FF88]">Monitor boundary detected — 4 corners identified</span>
        </motion.div>
      )}
    </div>
  );
}

function ContentPerspective({ progress }: { progress: number }) {
  const correctionDeg = progress * 5.2; // simulate 5.2° correction
  return (
    <div className="space-y-4 max-w-lg">
      <StepTitle icon={RotateCcw} title="Perspective Correction" desc="Compensating for camera angle with homography transform" />
      <StepCard>
        <div className="space-y-3">
          <InfoRow label="Horizontal skew" value={`${(correctionDeg * 0.7).toFixed(1)}°`} color="#32ADE6" />
          <InfoRow label="Vertical tilt" value={`${(correctionDeg * 0.45).toFixed(1)}°`} color="#32ADE6" />
          <InfoRow label="Keypoints matched" value={progress >= 1 ? '48 / 48' : `${Math.round(progress * 48)} / 48`} />
          <InfoRow label="Reprojection error" value={progress >= 1 ? '0.42 px' : '…'} color={progress >= 1 ? '#00FF88' : undefined} />
        </div>
        <div className="mt-3">
          <ProgressBar value={Math.round(progress * 100)} size="md" color="#32ADE6" animated />
        </div>
      </StepCard>
      {progress >= 1 && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(0,255,136,0.06)] border border-[rgba(0,255,136,0.2)]"
        >
          <CheckCircle size={15} className="text-[#00FF88] flex-shrink-0" />
          <span className="font-display text-vital-xs text-[#00FF88]">Perspective corrected — homography matrix computed</span>
        </motion.div>
      )}
    </div>
  );
}

function ContentROI({ roiVisible }: { roiVisible: boolean[] }) {
  const allDone = roiVisible.every(Boolean);
  return (
    <div className="space-y-4 max-w-lg">
      <StepTitle icon={Target} title="Region of Interest" desc="Mapping vital sign display areas within the corrected frame" />
      <StepCard>
        <div className="space-y-0">
          {VITAL_REGIONS.map((v, i) => (
            <motion.div
              key={v.key}
              className="flex items-center gap-3 py-2.5 border-b border-monitor-border last:border-0"
              initial={{ opacity: 0.3 }}
              animate={{ opacity: roiVisible[i] ? 1 : 0.3 }}
            >
              <div
                className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                style={{ backgroundColor: roiVisible[i] ? v.color : '#182B42' }}
              />
              <span className="font-display text-vital-xs text-[#E8F1FF] flex-1">{v.label}</span>
              {roiVisible[i] ? (
                <motion.div
                  className="flex items-center gap-2"
                  initial={{ opacity: 0, x: 8 }}
                  animate={{ opacity: 1, x: 0 }}
                >
                  <span className="font-mono text-vital-xs" style={{ color: v.color }}>{v.conf}% conf.</span>
                  <CheckCircle size={13} className="text-[#00FF88]" />
                </motion.div>
              ) : (
                <div className="w-4 h-4 rounded-full border border-monitor-border" />
              )}
            </motion.div>
          ))}
        </div>
      </StepCard>
      {allDone && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(0,255,136,0.06)] border border-[rgba(0,255,136,0.2)]"
        >
          <CheckCircle size={15} className="text-[#00FF88] flex-shrink-0" />
          <span className="font-display text-vital-xs text-[#00FF88]">All 4 vital regions mapped — ready for verification</span>
        </motion.div>
      )}
    </div>
  );
}

function ContentVerify({ running, done, onStart, result, error }: {
  running: boolean;
  done: boolean;
  onStart: () => void;
  result: PipelineReadFrameResult | null;
  error: string | null;
}) {
  const rows = VITAL_REGIONS.map((v) => {
    let display: string | null = null;
    let conf = 0;
    if (result) {
      if (v.key === 'nibp') {
        const { nibpSystolic, nibpDiastolic } = result.reading;
        display = nibpSystolic != null && nibpDiastolic != null ? `${nibpSystolic}/${nibpDiastolic}` : null;
        conf = result.confidence.nibp ?? 0;
      } else {
        const raw = result.reading[v.key as 'hr' | 'spo2' | 'etco2' | 'temp' | 'rr'];
        display = raw != null ? String(raw) : null;
        conf = result.confidence[v.key] ?? 0;
      }
    }
    return { ...v, display, conf: Math.round(conf) };
  });
  const readCount = rows.filter((r) => r.display !== null).length;

  return (
    <div className="space-y-4 max-w-lg">
      <StepTitle icon={Sliders} title="Verification Test" desc="Capturing a live frame and running it through the real OCR pipeline" />
      <StepCard>
        {done && result ? (
          <div className="space-y-0">
            {rows.map((v) => (
              <div key={v.key} className="flex items-center gap-3 py-2.5 border-b border-monitor-border last:border-0">
                <div className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: v.color }} />
                <span className="font-display text-vital-xs text-[#E8F1FF] w-16">{v.label}</span>
                <span className="font-mono text-vital-xs text-[#E8F1FF]">
                  {v.display ?? '—'} {v.display ? v.unit : ''}
                </span>
                <div className="flex-1 mx-2 h-1.5 bg-monitor-bg rounded-full overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${v.conf}%`, backgroundColor: v.color }} />
                </div>
                <span className="font-mono text-vital-xs" style={{ color: v.color }}>{v.conf}%</span>
                {v.display ? (
                  <CheckCircle size={13} className="text-[#00FF88]" />
                ) : (
                  <span className="font-display text-[9px] text-[#3D5570] uppercase">no read</span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center py-6 gap-4">
            <Target size={28} className="text-[#3D5570]" />
            <p className="font-display text-vital-sm text-[#7A90AA] text-center">
              {running
                ? 'Capturing frame and running OCR…'
                : "Point the camera at a screen showing VITAL's colour-coded vitals, then run the test"}
            </p>
            {!running && (
              <Button variant="clinical" onClick={onStart} icon={<Sliders size={15} />}>
                Run Verification
              </Button>
            )}
          </div>
        )}
      </StepCard>
      {error && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(255,71,87,0.08)] border border-[rgba(255,71,87,0.25)]"
        >
          <span className="font-display text-vital-xs text-[#FF4757]">{error}</span>
        </motion.div>
      )}
      {done && result && readCount > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(0,255,136,0.06)] border border-[rgba(0,255,136,0.2)]"
        >
          <CheckCircle size={15} className="text-[#00FF88] flex-shrink-0" />
          <span className="font-display text-vital-xs text-[#00FF88]">{readCount} of 4 vitals read from the live camera</span>
        </motion.div>
      )}
      {done && result && readCount === 0 && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(255,149,0,0.08)] border border-[rgba(255,149,0,0.25)]"
        >
          <span className="font-display text-vital-xs text-[#FF9500]">
            No vitals detected in this frame — point the camera at a screen displaying VITAL's colour-coded readout and try again.
          </span>
        </motion.div>
      )}
    </div>
  );
}

function ContentComplete() {
  return (
    <div className="flex flex-col items-center justify-center h-full py-16 px-8">
      <motion.div
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="w-20 h-20 rounded-full bg-[rgba(0,255,136,0.12)] border-2 border-[rgba(0,255,136,0.4)] flex items-center justify-center mb-6 shadow-[0_0_40px_rgba(0,255,136,0.2)]"
      >
        <CheckCircle size={36} className="text-[#00FF88]" />
      </motion.div>
      <motion.h2
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
        className="font-display text-2xl font-semibold text-[#E8F1FF] mb-2"
      >
        Calibration Complete
      </motion.h2>
      <motion.p
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="font-display text-vital-sm text-[#7A90AA] text-center max-w-md mb-8"
      >
        Camera profile saved. VITAL will now automatically detect and digitize all vital signs from your anaesthesia monitor in real time.
      </motion.p>
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="w-full max-w-sm rounded-2xl border border-monitor-border bg-monitor-surface p-5 space-y-0"
      >
        <InfoRow label="Profile ID" value="CAL-2024-001" color="#32ADE6" />
        <InfoRow label="Camera" value="VITAL CAM v2" />
        <InfoRow label="Resolution" value="1920 × 1080" />
        <InfoRow label="Avg. Confidence" value="95.0%" color="#00FF88" />
        <InfoRow label="Regions mapped" value="4 vitals" />
        <InfoRow label="Status" value="Active" color="#00FF88" />
      </motion.div>
    </div>
  );
}
