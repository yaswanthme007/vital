import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Camera, CheckCircle, Target, Sliders, Wifi,
  ChevronLeft, ChevronRight, Cpu, Trash2, AlertTriangle, Monitor, Activity,
} from 'lucide-react';
import { Button } from '@/design-system/components/Button';
import { Badge } from '@/design-system/components/Badge';
import { ProgressBar } from '@/design-system/components/Progress';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { useCameraCapture } from '@/hooks/useCameraCapture';
import { useSessionStore } from '@/store/sessionStore';
import { RoiCanvas, VITAL_COLORS, VITAL_LABELS, cropVideoFrame } from './RoiCanvas';
import { allDrawnFieldsConfirmed as computeAllDrawnFieldsConfirmed, buildFieldMeta, getFieldVerifyState } from './fieldVerifyState';
import type { FieldVerifyTone } from './fieldVerifyState';
import type { CalibrationFieldMeta, CalibrationProfile, CalibrationVerifyResult, NormalizedBox, VitalKey } from '@/types/calibration';
import { VITAL_KEYS } from '@/types/calibration';

// ─── Steps ──────────────────────────────────────────────────────────────────
//
// M5.2: replaces the pre-M5.2 6-step wizard (Camera / Detect / Perspective /
// Regions / Verify / Complete), whose Detect/Perspective/Regions steps were
// animated SVG mockups with hardcoded coordinates -- see
// docs/M5_2_REAL_CALIBRATION_REPORT.md sec 3 for why "detect the screen
// automatically" and "correct perspective automatically" were dropped
// rather than implemented for real: detect_screen() fires 0/52 and 0/17 on
// real photographed monitors (ARCHITECTURE.md), so animating a step around
// it would still be a mock wearing real-looking chrome. What ships instead
// is the operator drawing the six regions directly, which ROADMAP.md /
// EVIDENCE.md's own "calibrated box, no tracking" counterfactual already
// validated as the mechanism that actually works.

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// M5.7.2: how many independently-captured frames Verify collects per run,
// and how far apart to space the captures. Spacing matters, not just count
// -- captureFrameBlob() draws whatever the <video> element is currently
// decoding, and back-to-back calls with no delay risk sampling the exact
// same still-buffered frame twice (especially over a shared-tab/screen
// source, which can refresh far slower than a real webcam). 200ms is
// comfortably slower than a typical 15-30fps camera's own frame period, so
// each capture is a genuinely separate read of the live setup -- see
// backend/app/pipeline/burst_verify.py's module docstring for why several
// independent captures, not one, is the whole point.
const BURST_FRAME_COUNT = 5;
const BURST_FRAME_INTERVAL_MS = 200;

type StepId = 'connect' | 'regions' | 'verify' | 'complete';

const STEPS: { id: StepId; label: string; icon: React.ElementType; desc: string }[] = [
  { id: 'connect', label: 'Camera',  icon: Camera,  desc: 'Connect capture camera' },
  { id: 'regions', label: 'Regions', icon: Target,  desc: 'Draw the six vital fields' },
  { id: 'verify',  label: 'Verify',  icon: Sliders, desc: 'Confirm each field reads correctly' },
  { id: 'complete', label: 'Complete', icon: CheckCircle, desc: 'Save profile' },
];

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

// ─── Main component ─────────────────────────────────────────────────────────

export function CalibrationPage() {
  const [currentStep, setCurrentStep] = useState<StepId>('connect');
  const {
    videoRef, canvasRef, active: cameraActive, error: cameraError, info: cameraInfo,
    sourceMode, connect, stop: stopCamera, captureFrameBlob,
  } = useCameraCapture();
  const setCameraMode = useSessionStore((s) => s.setCameraMode);
  const activeSession = useSessionStore((s) => s.activeSession);
  const navigate = useNavigate();

  const [boxes, setBoxes] = useState<Partial<Record<VitalKey, NormalizedBox>>>({});
  const [activeVital, setActiveVital] = useState<VitalKey | null>('hr');
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  // M5.7.1: calibration GEOMETRY is reusable across cases (a CalibrationProfile
  // deliberately outlives any one session -- same camera/monitor rig across a
  // shift), but VERIFICATION stays mandatory every case -- see this file's
  // handleSave, which always re-runs live OCR and requires every field
  // re-confirmed before (re)activating a profile. Pre-filling from whatever
  // profile is currently active just saves the operator re-drawing boxes that
  // haven't moved; it never skips Verify/Save.
  const [reusedFromProfile, setReusedFromProfile] = useState<CalibrationProfile | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.getActiveCalibrationProfile().then((profile) => {
      if (cancelled || !profile) return;
      setReusedFromProfile(profile);
      setBoxes(profile.roiBoxes);
    }).catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [verifying, setVerifying] = useState(false);
  // M5.7.2: staged status for the multi-frame burst -- null when idle.
  // 'capturing' advances frameIndex as each of BURST_FRAME_COUNT frames is
  // grabbed; 'analyzing' covers the single request to the backend that
  // aggregates them (see runVerify below).
  const [burstProgress, setBurstProgress] = useState<
    { phase: 'capturing' | 'analyzing'; frameIndex: number } | null
  >(null);
  const [verifyResult, setVerifyResult] = useState<CalibrationVerifyResult | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [fieldMeta, setFieldMeta] = useState<Partial<Record<VitalKey, CalibrationFieldMeta>>>({});

  // M5.3: the exact frame Verify ran against. Held so it can be uploaded as
  // the layout-tracking reference at Save -- tracking must anchor to the
  // frame the operator actually confirmed each reading on, not some later
  // frame the camera has since drifted from.
  const [verifiedFrame, setVerifiedFrame] = useState<Blob | null>(null);
  const [trackingEnabled, setTrackingEnabled] = useState(false);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedProfile, setSavedProfile] = useState<CalibrationProfile | null>(null);

  const clearAndRecalibrate = () => {
    setBoxes({});
    setFieldMeta({});
    setVerifyResult(null);
    setVerifiedFrame(null);
    setReusedFromProfile(null);
  };

  const stepIndex = STEPS.findIndex((s) => s.id === currentStep);
  const drawnVitals = useMemo(() => VITAL_KEYS.filter((v) => boxes[v]), [boxes]);

  // Live crop preview for whichever vital is currently selected -- Phase 2's
  // "must show the live crop as the box is drawn" requirement.
  useEffect(() => {
    let cancelled = false;
    if (!activeVital || !boxes[activeVital] || !videoRef.current || currentStep !== 'regions') {
      setPreviewUrl(null);
      return;
    }
    const box = boxes[activeVital]!;
    cropVideoFrame(videoRef.current, box).then((url) => {
      if (!cancelled) setPreviewUrl(url);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVital, boxes, currentStep]);

  const connectSource = async (mode: 'camera' | 'screen') => {
    if (cameraActive) {
      stopCamera();
      return;
    }
    await connect(mode);
  };

  const handleBoxChange = (vital: VitalKey, box: NormalizedBox | null) => {
    setBoxes((prev) => {
      const next = { ...prev };
      if (box) next[vital] = box;
      else delete next[vital];
      return next;
    });
    // Any geometry edit invalidates that field's prior verification -- a
    // moved/resized box hasn't actually been re-checked (Phase 7).
    setFieldMeta((prev) => {
      if (!prev[vital]) return prev;
      const next = { ...prev };
      delete next[vital];
      return next;
    });
    setVerifyResult(null);
    // The reference frame is only valid for the geometry it was verified
    // with; a moved box means it must be recaptured (M5.3).
    setVerifiedFrame(null);
  };

  // M5.7.2: captures a short BURST of independent frames and asks the
  // backend to only report a field as verified once its value is
  // consistently reproduced across them -- see backend/app/pipeline/
  // burst_verify.py for the full design. Replaces the old single-frame
  // "capture once, trust it" runVerify; the button/step names stay the
  // same (this is what "Run Verification" does now), so the rest of the
  // wizard's flow -- and every E2E script that clicks that same button --
  // is unaffected.
  const runVerify = async () => {
    if (drawnVitals.length === 0 || !videoRef.current) return;
    setVerifying(true);
    setVerifyError(null);
    setBurstProgress({ phase: 'capturing', frameIndex: 0 });
    try {
      const frames: Blob[] = [];
      for (let i = 0; i < BURST_FRAME_COUNT; i++) {
        frames.push(await captureFrameBlob());
        setBurstProgress({ phase: 'capturing', frameIndex: i + 1 });
        if (i < BURST_FRAME_COUNT - 1) await sleep(BURST_FRAME_INTERVAL_MS);
      }
      setBurstProgress({ phase: 'analyzing', frameIndex: BURST_FRAME_COUNT });
      const result = await api.verifyCalibrationBurst(
        frames,
        videoRef.current.videoWidth,
        videoRef.current.videoHeight,
        boxes,
      );
      setVerifyResult(result);
      // Any frame in the burst anchors tracking equally well (all several
      // moments apart of the identical static setup) -- the last one is
      // simplest to reason about ("closest to the moment Save happens").
      setVerifiedFrame(frames[frames.length - 1]);
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : 'Verification failed');
    } finally {
      setVerifying(false);
      setBurstProgress(null);
    }
  };

  // M5.7.3: builds the operator-confirmed CalibrationFieldMeta via
  // fieldVerifyState.buildFieldMeta -- `verified: true` is set purely from
  // this click (the operator explicitly confirming what they see against
  // the monitor), never from OCR's own confidence/stability tone. See
  // buildFieldMeta's docstring and backend/app/models/calibration.py's
  // CalibrationFieldMeta docstring for the exact semantics this preserves.
  const confirmField = (vital: VitalKey, confirmed: boolean) => {
    if (!verifyResult) return;
    setFieldMeta((prev) => ({ ...prev, [vital]: buildFieldMeta(vital, verifyResult, confirmed) }));
  };

  const allDrawnFieldsConfirmed = computeAllDrawnFieldsConfirmed(drawnVitals, fieldMeta);

  const canProceed = () => {
    if (currentStep === 'connect') return cameraActive;
    if (currentStep === 'regions') return drawnVitals.length > 0;
    if (currentStep === 'verify') return allDrawnFieldsConfirmed;
    return true;
  };

  const handleSave = async () => {
    if (!videoRef.current) return;
    setSaving(true);
    setSaveError(null);
    try {
      const profile = await api.saveCalibrationProfile({
        referenceWidth: videoRef.current.videoWidth,
        referenceHeight: videoRef.current.videoHeight,
        roiBoxes: boxes,
        fieldMeta,
      });
      setSavedProfile(profile);

      // M5.3: attach the Verify frame so the live path can re-anchor these
      // boxes per frame. Deliberately NOT fatal -- the profile is already
      // saved and fully usable without it, just untracked (M5.2 behaviour),
      // so a failure here downgrades tracking rather than losing the
      // calibration. The operator is told which of the two they got.
      let tracking = false;
      if (verifiedFrame) {
        try {
          await api.attachCalibrationReferenceFrame(verifiedFrame);
          tracking = true;
        } catch (err) {
          setSaveError(
            err instanceof Error
              ? `Calibration saved, but layout tracking could not be enabled: ${err.message}`
              : 'Calibration saved, but layout tracking could not be enabled',
          );
        }
      }
      setTrackingEnabled(tracking);
      setCameraMode(true, sourceMode ?? 'camera');
      setCurrentStep('complete');
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Could not save the calibration profile');
    } finally {
      setSaving(false);
    }
  };

  // M5.7.1: "After calibration Save succeeds, automatically route to
  // /operation" -- there is no separate camera-start step for the operator
  // to discover, so once there's an active case to enter, this page's job
  // is done and Active Operation should open on its own. A short beat lets
  // the confirmation screen register before the hand-off; the button in
  // ContentComplete remains as an immediate-continue affordance for anyone
  // who doesn't want to wait for it. When there is no active session (a
  // pre-case camera setup, or a standalone recalibration), there is nothing
  // to auto-enter -- the operator picks "Start a Case with This Profile" to
  // go create one.
  useEffect(() => {
    if (currentStep !== 'complete' || !activeSession) return;
    const timer = setTimeout(() => navigate('/operation'), 1400);
    return () => clearTimeout(timer);
  }, [currentStep, activeSession, navigate]);

  const goNext = () => {
    if (currentStep === 'verify') {
      void handleSave();
      return;
    }
    if (stepIndex < STEPS.length - 1) setCurrentStep(STEPS[stepIndex + 1].id);
  };
  const goPrev = () => {
    if (stepIndex > 0) setCurrentStep(STEPS[stepIndex - 1].id);
  };

  const isLastContent = currentStep === 'verify';

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
          <span className="font-display text-vital-xs text-[#3D5570]">Step {Math.min(stepIndex + 1, STEPS.length)} of {STEPS.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={cameraActive ? 'success' : 'neutral'} pulse={cameraActive}>
            {cameraActive ? 'Camera Active' : 'No Camera'}
          </Badge>
          {currentStep === 'complete' && <Badge variant="success">Profile Saved</Badge>}
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
                  <div
                    className={cn(
                      'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 border transition-all',
                      active && 'bg-[#32ADE6] border-[#32ADE6] text-white shadow-[0_0_12px_rgba(50,173,230,0.5)]',
                      done && 'bg-[rgba(0,255,136,0.1)] border-[rgba(0,255,136,0.4)] text-[#00FF88]',
                      future && 'bg-transparent border-monitor-border text-[#3D5570]'
                    )}
                  >
                    {done ? <CheckCircle size={14} /> : <Icon size={14} />}
                  </div>
                  <div className="min-w-0">
                    <div className={cn('font-display text-vital-xs font-medium truncate', active ? 'text-[#E8F1FF]' : done ? 'text-[#7A90AA]' : 'text-[#3D5570]')}>
                      {step.label}
                    </div>
                    <div className="font-display text-[10px] text-[#3D5570] truncate mt-0.5">{step.desc}</div>
                  </div>
                </button>
                {i < STEPS.length - 1 && (
                  <div className="absolute left-[35px] top-[54px] w-0.5 h-3">
                    <div className={cn('w-full h-full transition-colors', done ? 'bg-[rgba(0,255,136,0.3)]' : 'bg-monitor-border')} />
                  </div>
                )}
              </div>
            );
          })}
        </aside>

        {/* ── Content area ── */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Camera preview + drawing surface */}
          {currentStep !== 'complete' && (
            <div className="relative flex-shrink-0 bg-monitor-bg border-b border-monitor-border overflow-hidden" style={{ height: 320 }}>
              <video
                ref={videoRef}
                muted
                playsInline
                className={cn('absolute inset-0 w-full h-full object-contain bg-black', cameraActive ? 'block' : 'hidden')}
              />
              <canvas ref={canvasRef} className="hidden" />
              {cameraActive && (currentStep === 'regions' || currentStep === 'verify') && (
                <RoiCanvas videoRef={videoRef} boxes={boxes} activeVital={currentStep === 'regions' ? activeVital : null} onBoxChange={handleBoxChange} disabled={currentStep !== 'regions'} />
              )}
              {cameraActive && (
                <div className="absolute top-3 left-3 flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[#FF3B30]/90">
                  <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                  <span className="font-display text-[9px] font-bold text-white tracking-wider">LIVE</span>
                </div>
              )}
              {!cameraActive && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
                  <Monitor size={32} className="text-[#1E3A56]" />
                  <span className="font-display text-vital-xs text-[#3D5570]">No signal — connect a camera to begin</span>
                </div>
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
                {currentStep === 'connect' && (
                  <ContentConnect cameraActive={cameraActive} cameraError={cameraError} cameraInfo={cameraInfo} sourceMode={sourceMode} onConnect={connectSource} />
                )}
                {currentStep === 'regions' && (
                  <ContentRegions
                    boxes={boxes}
                    activeVital={activeVital}
                    onSelect={setActiveVital}
                    onClear={(v) => handleBoxChange(v, null)}
                    previewUrl={previewUrl}
                    reusedFromProfile={reusedFromProfile}
                    onClearAll={clearAndRecalibrate}
                  />
                )}
                {currentStep === 'verify' && (
                  <ContentVerify
                    drawnVitals={drawnVitals}
                    verifying={verifying}
                    burstProgress={burstProgress}
                    result={verifyResult}
                    error={verifyError}
                    fieldMeta={fieldMeta}
                    onRun={runVerify}
                    onConfirm={confirmField}
                    saving={saving}
                    saveError={saveError}
                  />
                )}
                {currentStep === 'complete' && (
                  <ContentComplete
                    profile={savedProfile}
                    trackingEnabled={trackingEnabled}
                    // M5.7.1: with an active session, Active Operation opens
                    // automatically (see the useEffect above) -- this button
                    // is just "don't make me wait" for that same navigation.
                    // M5.7's original fix still applies with no active
                    // session: fall back to /start rather than a second case.
                    autoAdvancing={Boolean(activeSession)}
                    onStartCase={() => navigate(activeSession ? '/operation' : '/start')}
                  />
                )}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </div>

      {/* ── Footer navigation ── */}
      {currentStep !== 'complete' && (
        <footer className="flex items-center justify-between px-6 py-3 border-t border-monitor-border bg-monitor-surface flex-shrink-0">
          <Button variant="ghost" size="sm" icon={<ChevronLeft size={15} />} onClick={goPrev} disabled={stepIndex === 0}>
            Back
          </Button>

          <div className="flex items-center gap-1.5">
            {STEPS.slice(0, -1).map((_, i) => (
              <div
                key={i}
                className={cn('h-1.5 rounded-full transition-all duration-300', i === stepIndex ? 'w-6 bg-[#32ADE6]' : i < stepIndex ? 'w-1.5 bg-[rgba(0,255,136,0.5)]' : 'w-1.5 bg-monitor-border')}
              />
            ))}
          </div>

          <Button
            variant="primary"
            size="sm"
            onClick={goNext}
            disabled={!canProceed() || saving}
            iconRight={<ChevronRight size={15} />}
          >
            {isLastContent ? (saving ? 'Saving…' : 'Save Profile') : 'Continue'}
          </Button>
        </footer>
      )}
    </motion.div>
  );
}

// ─── Step content panels ────────────────────────────────────────────────────

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
        <Button variant="danger" onClick={() => onConnect(sourceMode ?? 'camera')} className="w-full justify-center">
          Disconnect
        </Button>
      ) : (
        <div className="flex flex-col gap-2.5">
          <Button variant="clinical" icon={<Wifi size={15} />} onClick={() => onConnect('camera')} className="w-full justify-center">
            Connect Camera
          </Button>
          <Button variant="ghost" icon={<Monitor size={15} />} onClick={() => onConnect('screen')} className="w-full justify-center">
            Share a Tab/Screen Instead
          </Button>
          <p className="font-display text-[10px] text-[#3D5570] text-center px-2">
            Testing without a second device? Open Live Monitor in another tab, then share <em>that tab</em> here — no camera or lighting needed, it reads the tab's pixels directly.
          </p>
        </div>
      )}
      {cameraError && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(255,71,87,0.08)] border border-[rgba(255,71,87,0.25)]">
          <div>
            <div className="font-display text-vital-xs font-medium text-[#FF4757]">Camera access failed</div>
            <div className="font-display text-[10px] text-[#3D5570] mt-0.5">{cameraError}</div>
          </div>
        </motion.div>
      )}
      {cameraActive && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(0,255,136,0.06)] border border-[rgba(0,255,136,0.2)]">
          <CheckCircle size={15} className="text-[#00FF88] flex-shrink-0" />
          <div>
            <div className="font-display text-vital-xs font-medium text-[#00FF88]">Camera connected</div>
            <div className="font-display text-[10px] text-[#3D5570] mt-0.5">Signal detected · Proceed to draw the vital regions</div>
          </div>
        </motion.div>
      )}
    </div>
  );
}

function ContentRegions({ boxes, activeVital, onSelect, onClear, previewUrl, reusedFromProfile, onClearAll }: {
  boxes: Partial<Record<VitalKey, NormalizedBox>>;
  activeVital: VitalKey | null;
  onSelect: (v: VitalKey) => void;
  onClear: (v: VitalKey) => void;
  previewUrl: string | null;
  reusedFromProfile: CalibrationProfile | null;
  onClearAll: () => void;
}) {
  const drawnCount = VITAL_KEYS.filter((v) => boxes[v]).length;
  return (
    <div className="space-y-4 max-w-lg">
      <StepTitle icon={Target} title="Draw the Vital Regions" desc="Select a vital below, then drag a box around its full display slot in the video above" />
      {reusedFromProfile && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex items-start gap-2.5 px-4 py-3 rounded-xl bg-[rgba(0,255,136,0.06)] border border-[rgba(0,255,136,0.2)]">
          <CheckCircle size={14} className="text-[#00FF88] flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <div className="font-display text-vital-xs font-medium text-[#00FF88]">Existing calibration found</div>
            <p className="font-display text-[10px] text-[#7A90AA] mt-0.5">
              Regions below are pre-filled from the active calibration profile. Adjust any box if the camera or
              monitor has moved, then Verify and Save as usual — or start over completely.
            </p>
            <button
              onClick={onClearAll}
              className="font-display text-[10px] font-medium text-[#32ADE6] hover:text-[#E8F1FF] mt-1.5 transition-colors"
            >
              Clear all &amp; recalibrate from scratch
            </button>
          </div>
        </motion.div>
      )}
      <StepCard>
        <div className="space-y-1.5">
          {VITAL_KEYS.map((vital) => {
            const drawn = Boolean(boxes[vital]);
            const isActive = vital === activeVital;
            return (
              <button
                key={vital}
                onClick={() => onSelect(vital)}
                className={cn(
                  'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border text-left transition-all',
                  isActive ? 'bg-white/5' : 'hover:bg-white/[0.03]'
                )}
                style={{ borderColor: isActive ? VITAL_COLORS[vital] : 'rgba(24,43,66,1)' }}
              >
                <div className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: VITAL_COLORS[vital] }} />
                <span className="font-display text-vital-xs text-[#E8F1FF] flex-1">{VITAL_LABELS[vital]}</span>
                {drawn ? (
                  <>
                    <span className="font-display text-[10px] text-[#00FF88] uppercase tracking-wider">Drawn</span>
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(e) => { e.stopPropagation(); onClear(vital); }}
                      className="w-6 h-6 rounded-md flex items-center justify-center text-[#7A90AA] hover:text-[#FF4757] hover:bg-[rgba(255,71,87,0.1)] transition-colors"
                    >
                      <Trash2 size={12} />
                    </span>
                  </>
                ) : (
                  <span className="font-display text-[10px] text-[#3D5570] uppercase tracking-wider">Not drawn</span>
                )}
              </button>
            );
          })}
        </div>
      </StepCard>

      {activeVital && (
        <StepCard>
          <div className="flex items-center gap-3">
            <div className="w-24 h-16 rounded-lg overflow-hidden bg-black border border-monitor-border flex items-center justify-center flex-shrink-0">
              {previewUrl ? (
                <img src={previewUrl} alt={`${VITAL_LABELS[activeVital]} crop preview`} className="max-w-full max-h-full" />
              ) : (
                <span className="font-display text-[9px] text-[#3D5570] text-center px-1">No box drawn yet</span>
              )}
            </div>
            <div className="flex-1">
              <div className="font-display text-vital-xs font-medium text-[#E8F1FF]">Live crop preview — {VITAL_LABELS[activeVital]}</div>
              <p className="font-display text-[10px] text-[#3D5570] mt-1">
                Draw around the field's full display slot — not just the digits currently shown. A box too tight to
                "74" will clip a later "104".
              </p>
            </div>
          </div>
        </StepCard>
      )}

      <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(50,173,230,0.06)] border border-[rgba(50,173,230,0.2)]">
        <span className="font-display text-vital-xs text-[#32ADE6]">
          {drawnCount} of {VITAL_KEYS.length} regions drawn — a monitor that doesn't display a field (e.g. no NIBP reading) can be left blank.
        </span>
      </div>
    </div>
  );
}

function ContentVerify({
  drawnVitals, verifying, burstProgress, result, error, fieldMeta, onRun, onConfirm, saving, saveError,
}: {
  drawnVitals: VitalKey[];
  verifying: boolean;
  burstProgress: { phase: 'capturing' | 'analyzing'; frameIndex: number } | null;
  result: CalibrationVerifyResult | null;
  error: string | null;
  fieldMeta: Partial<Record<VitalKey, CalibrationFieldMeta>>;
  onRun: () => void;
  onConfirm: (vital: VitalKey, confirmed: boolean) => void;
  saving: boolean;
  saveError: string | null;
}) {
  return (
    <div className="space-y-4 max-w-lg">
      <StepTitle icon={Sliders} title="Verify Each Field" desc="Captures several live frames, then lets you visually confirm each detected value against the monitor" />
      <StepCard>
        {!result ? (
          <div className="flex flex-col items-center py-6 gap-4">
            {verifying ? (
              <div className="w-full max-w-xs space-y-3">
                <div className="flex justify-center">
                  <Sliders size={28} className="text-[#32ADE6] animate-pulse" />
                </div>
                <ProgressBar
                  value={burstProgress?.phase === 'capturing' ? (burstProgress.frameIndex / BURST_FRAME_COUNT) * 100 : undefined}
                  label={burstProgress?.phase === 'analyzing' ? 'Analyzing live frames…' : 'Capturing live frames…'}
                  showValue={burstProgress?.phase === 'capturing'}
                />
                <p className="font-display text-[10px] text-[#3D5570] text-center">
                  {burstProgress?.phase === 'analyzing'
                    ? `Comparing ${BURST_FRAME_COUNT} captures against every drawn field…`
                    : `Frame ${Math.min(burstProgress?.frameIndex ?? 0, BURST_FRAME_COUNT)} of ${BURST_FRAME_COUNT}`}
                </p>
              </div>
            ) : (
              <>
                <Target size={28} className="text-[#3D5570]" />
                <p className="font-display text-vital-sm text-[#7A90AA] text-center">
                  {`Ready to verify ${drawnVitals.length} region${drawnVitals.length === 1 ? '' : 's'}`}
                </p>
                <Button variant="clinical" onClick={onRun} icon={<Sliders size={15} />} disabled={drawnVitals.length === 0}>
                  Run Verification
                </Button>
              </>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {drawnVitals.map((vital) => {
              // M5.7.3: OCR confidence/stability (`state.tone`) and operator
              // confirmation (`confirmed`, from fieldMeta -- set ONLY by an
              // explicit click) are deliberately two separate variables, not
              // one. A low-confidence or unstable candidate never becomes
              // "confirmed" on its own -- see fieldVerifyState.ts's module
              // docstring for the full A-vs-B distinction this preserves.
              const state = getFieldVerifyState(vital, result);
              const confidence = Math.round(result.confidence[vital] ?? 0);
              const confirmed = fieldMeta[vital]?.verified ?? false;
              const diag = result.diagnostics?.[vital];
              const toneColor: Record<FieldVerifyTone, string> = {
                stable: '#00FF88',
                recovered: '#00FF88',
                unstable: '#FF9F0A',
                suspicious: '#FF4757',
                none: '#FF9F0A',
              };
              return (
                <div key={vital} className="py-2 border-b border-monitor-border last:border-0">
                  <div className="flex items-center gap-3">
                    <div className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: VITAL_COLORS[vital] }} />
                    <span className="font-display text-vital-xs text-[#E8F1FF] w-16">{VITAL_LABELS[vital]}</span>
                    <span className="font-mono text-vital-xs text-[#E8F1FF] w-16">{state.displayValue ?? '—'}</span>
                    <span className="font-mono text-[10px] text-[#7A90AA] w-14">
                      {state.displayValue ? `${confidence}% conf` : ''}
                    </span>
                    <button
                      onClick={() => onConfirm(vital, !confirmed)}
                      disabled={!state.confirmable}
                      className={cn(
                        'ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-lg border font-display text-[10px] font-medium transition-colors',
                        confirmed
                          ? 'bg-[rgba(0,255,136,0.1)] border-[rgba(0,255,136,0.4)] text-[#00FF88]'
                          : state.tone === 'suspicious'
                            ? 'border-[rgba(255,71,87,0.4)] text-[#FF4757] hover:bg-[rgba(255,71,87,0.08)]'
                            : state.tone === 'unstable'
                              ? 'border-[rgba(255,159,10,0.4)] text-[#FF9F0A] hover:bg-[rgba(255,159,10,0.08)]'
                              : 'border-monitor-border text-[#7A90AA] hover:bg-white/5',
                        !state.confirmable && 'opacity-40 cursor-not-allowed'
                      )}
                    >
                      <CheckCircle size={11} />
                      {confirmed ? 'Confirmed' : state.confirmable ? `Confirm ${state.displayValue}` : 'Confirm'}
                    </button>
                  </div>
                  {/* M5.7.3: five states rendered here, not a binary
                      stable/disabled -- STABLE and MOSTLY STABLE/recovered
                      (green, OCR itself trusts this), UNSTABLE (amber, a
                      plausible candidate that just didn't reach OCR's own
                      stability bar -- confirmable, the operator's eyes
                      settle it), SUSPICIOUS (residual/crop-integrity
                      warning -- still confirmable, but flagged harder so
                      the operator looks twice), and NONE (no candidate at
                      all -- the only state that disables Confirm). See
                      fieldVerifyState.ts for the full rationale. */}
                  {state.message && (
                    <p className="font-display text-[10px] mt-1 pl-5" style={{ color: toneColor[state.tone] }}>
                      {state.message}
                    </p>
                  )}
                  {/* Legacy single-frame fallback (no burst metadata at
                      all) -- explain from diagnostics instead, same as
                      before M5.7.3. Nothing in the shipped UI reaches this
                      today (only the burst endpoint is called), kept for
                      the untouched /verify response shape. */}
                  {!state.message && state.displayValue === null && (
                    <p className="font-display text-[10px] text-[#FF9F0A] mt-1 pl-5">
                      {diag === undefined
                        ? 'Could not read this field — try redrawing the box around the digits.'
                        : diag === null
                          ? 'This box produced no image to read — it may be off-frame or the video hasn’t loaded a frame yet.'
                          : diag.rawText
                            ? `OCR saw "${diag.rawText}" but couldn't parse a value from it — try widening or redrawing the box.`
                            : 'OCR found no readable text in this crop — try redrawing the box tighter around the digits.'}
                    </p>
                  )}
                </div>
              );
            })}
            <Button variant="ghost" size="sm" onClick={onRun} disabled={verifying} className="mt-2">
              {verifying ? 'Re-running…' : 'Re-run Verification'}
            </Button>
          </div>
        )}
      </StepCard>
      {error && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(255,71,87,0.08)] border border-[rgba(255,71,87,0.25)]">
          <span className="font-display text-vital-xs text-[#FF4757]">{error}</span>
        </motion.div>
      )}
      {result && (
        <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(50,173,230,0.06)] border border-[rgba(50,173,230,0.2)]">
          <AlertTriangle size={14} className="text-[#32ADE6] flex-shrink-0" />
          <span className="font-display text-[10px] text-[#7A90AA]">
            Save is disabled until every drawn field above is confirmed — an unconfirmed field is a field the pipeline
            could be reading wrong without anyone having checked.
          </span>
        </div>
      )}
      {saving && (
        <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(50,173,230,0.06)] border border-[rgba(50,173,230,0.2)]">
          <span className="font-display text-vital-xs text-[#32ADE6]">Saving calibration profile…</span>
        </div>
      )}
      {saveError && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-[rgba(255,71,87,0.08)] border border-[rgba(255,71,87,0.25)]">
          <span className="font-display text-vital-xs text-[#FF4757]">{saveError}</span>
        </motion.div>
      )}
    </div>
  );
}

function ContentComplete({ profile, trackingEnabled, autoAdvancing, onStartCase }: {
  profile: CalibrationProfile | null;
  trackingEnabled: boolean;
  autoAdvancing: boolean;
  onStartCase: () => void;
}) {
  const fieldCount = profile ? Object.keys(profile.roiBoxes).length : 0;
  const confidences = profile
    ? Object.values(profile.fieldMeta)
        .map((m) => m?.verifiedConfidence)
        .filter((c): c is number => c != null)
    : [];
  const avgConfidence = confidences.length ? confidences.reduce((a, b) => a + b, 0) / confidences.length : null;

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
      <motion.h2 initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="font-display text-2xl font-semibold text-[#E8F1FF] mb-2">
        Calibration Complete
      </motion.h2>
      {/* M5.6: the previous copy promised "until you recalibrate", which is
          not true across a page reload — the profile survives (it is in the
          database) but this browser's camera mode does not (it is in memory),
          so a refreshed tab silently streams synthetic vitals against a live
          profile. Say what actually holds, and give the operator an in-app
          route onward so the reload path is not the natural one to take. */}
      <motion.p initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="font-display text-vital-sm text-[#7A90AA] text-center max-w-md mb-6">
        {autoAdvancing
          ? 'This calibration profile is saved and active. Active Operation is opening automatically — the camera will start on its own.'
          : 'This calibration profile is saved and active. Start your case from here and Live Monitor will map these regions onto every incoming camera frame.'}
      </motion.p>
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.35 }} className="mb-8">
        <Button variant="clinical" onClick={onStartCase} icon={<Activity size={15} />}>
          {autoAdvancing ? (
            <motion.span
              className="inline-flex items-center gap-2"
              animate={{ opacity: [1, 0.6, 1] }}
              transition={{ repeat: Infinity, duration: 1.1 }}
            >
              Entering Active Operation…
            </motion.span>
          ) : (
            'Start a Case with This Profile'
          )}
        </Button>
        <p className="font-display text-[10px] text-[#3D5570] text-center mt-2 max-w-xs">
          {autoAdvancing
            ? "Don't want to wait? Click above to continue immediately."
            : 'Reloading the browser keeps the profile but resets this tab to synthetic vitals — recalibrate or start the case from here instead.'}
        </p>
      </motion.div>
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }} className="w-full max-w-sm rounded-2xl border border-monitor-border bg-monitor-surface p-5 space-y-0">
        <InfoRow label="Profile ID" value={profile?.id ?? '—'} color="#32ADE6" />
        <InfoRow label="Reference resolution" value={profile ? `${profile.referenceWidth} × ${profile.referenceHeight}` : '—'} />
        <InfoRow label="Regions calibrated" value={`${fieldCount} vital${fieldCount === 1 ? '' : 's'}`} />
        <InfoRow label="Avg. verified confidence" value={avgConfidence != null ? `${avgConfidence.toFixed(0)}%` : '—'} color="#00FF88" />
        {/* M5.3: reported truthfully either way. Tracking is a real
            capability difference (the regions follow the monitor if the
            camera is nudged), so an operator must never be left assuming it
            is on when the reference frame failed to attach. */}
        <InfoRow
          label="Layout tracking"
          value={trackingEnabled ? 'Enabled — regions follow the monitor' : 'Off — fixed regions'}
          color={trackingEnabled ? '#00FF88' : '#D97706'}
        />
        <InfoRow label="Status" value="Active" color="#00FF88" />
      </motion.div>
    </div>
  );
}
