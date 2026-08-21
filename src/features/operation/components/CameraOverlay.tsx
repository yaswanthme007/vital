import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Camera, Wifi, WifiOff, Loader2, Crosshair, AlertTriangle } from 'lucide-react';
import { useSessionStore } from '@/store/sessionStore';
import { useCameraStreamStore } from '@/store/cameraStreamStore';
import { useVitalsStore } from '@/store/vitalsStore';

// Real camera/AI-vision status for the Live Monitor — replaces what used to
// be a fully mocked confidence readout (random walk + random "detecting"
// vital) with the actual state of the capture loop (useCameraStreaming) and
// the actual per-vital confidence the backend returned for this session.
// Outside camera mode there is no OCR happening, so this shows a plain
// "Synthetic Feed" badge rather than fabricating AI-vision activity.

function useTick(ms: number) {
  const [, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((n) => n + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
}

function relativeSeconds(since: number | null): string | null {
  if (since == null) return null;
  const s = Math.max(0, Math.round((Date.now() - since) / 1000));
  return s < 1 ? 'just now' : `${s}s ago`;
}

export function CameraOverlay() {
  const cameraMode = useSessionStore((s) => s.cameraMode);
  const status = useCameraStreamStore((s) => s.status);
  const lastFrameSentAt = useCameraStreamStore((s) => s.lastFrameSentAt);
  const lastError = useCameraStreamStore((s) => s.lastError);
  const confidence = useVitalsStore((s) => s.currentConfidence);
  const tracking = useVitalsStore((s) => s.tracking);
  const [expanded, setExpanded] = useState(false);
  useTick(1000); // keeps "Xs ago" fresh without re-deriving from a WS event

  const values = confidence ? Object.values(confidence) : [];
  const meanConf = values.length ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : null;
  const barColor = meanConf == null ? '#94A3B8' : meanConf >= 90 ? '#16A34A' : meanConf >= 78 ? '#D97706' : '#DC2626';

  const statusMeta: Record<string, { label: string; dot: string; icon: React.ReactNode }> = {
    connecting:    { label: 'Connecting…',        dot: '#D97706', icon: <Loader2 size={9} className="animate-spin text-slate-400" /> },
    active:        { label: 'Camera Active',      dot: '#16A34A', icon: <Wifi size={9} className="text-slate-400" /> },
    uploading:     { label: 'Processing frame…',  dot: '#16A34A', icon: <Loader2 size={9} className="animate-spin text-slate-400" /> },
    upload_error:  { label: "Upload failing",     dot: '#D97706', icon: <WifiOff size={9} className="text-amber-500" /> },
    disconnected:  { label: 'Camera unavailable', dot: '#DC2626', icon: <WifiOff size={9} className="text-red-500" /> },
    idle:          { label: 'Waiting for camera', dot: '#94A3B8', icon: <Wifi size={9} className="text-slate-400" /> },
  };
  const meta = statusMeta[status] ?? statusMeta.idle;

  // M5.3 layout-tracking lock. Three genuinely different states, and
  // collapsing them would mislead: null = the backend is not tracking at all
  // (no calibration reference frame, or LAYOUT_TRACKING=off), locked = the
  // regions are being re-anchored to the monitor every frame, and unlocked =
  // tracking is running but has REFUSED this frame, so every vital is being
  // withheld and the displayed values are held from the last confirmed
  // reading. That last state is the one an operator must act on, so it is
  // shown as a warning with the tracker's own reason.
  const lockMeta = !tracking
    ? null
    : tracking.locked
      ? { label: 'Layout locked', color: '#16A34A', icon: <Crosshair size={9} className="text-emerald-600" /> }
      : { label: 'Monitor lost — recalibrate', color: '#DC2626',
          icon: <AlertTriangle size={9} className="text-red-500" /> };

  return (
    <motion.div
      className="absolute bottom-4 right-4 z-20 cursor-pointer"
      onClick={() => setExpanded((e) => !e)}
      whileHover={{ scale: 1.02 }}
    >
      <div
        className="rounded-xl overflow-hidden"
        style={{
          background: '#FFFFFF',
          border: '1px solid #E2E8F0',
          backdropFilter: 'blur(12px)',
          boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
          minWidth: 170,
        }}
      >
        {/* Header row */}
        <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: '1px solid #E2E8F0' }}>
          <Camera size={11} className="text-clinical-primary flex-shrink-0" />
          <span className="font-display text-[10px] font-bold uppercase tracking-widest text-slate-500 flex-1">
            {cameraMode ? 'AI Vision' : 'Synthetic Feed'}
          </span>
          <motion.div
            className="w-1.5 h-1.5 rounded-full flex-shrink-0"
            style={{ backgroundColor: cameraMode ? meta.dot : '#94A3B8' }}
            animate={{ opacity: [1, 0.2, 1] }}
            transition={{ repeat: Infinity, duration: 1.4 }}
          />
        </div>

        {!cameraMode ? (
          <div className="px-3 py-2">
            <span className="font-display text-[9px] text-slate-400">
              Synthetic vitals — no camera/OCR active this session
            </span>
          </div>
        ) : (
          <>
            {/* Confidence — real per-vital mean from the backend's WS envelope */}
            <div className="px-3 py-2">
              <div className="flex items-center justify-between mb-1.5">
                <span className="font-display text-[9px] uppercase tracking-[0.18em] text-slate-400">Confidence</span>
                <span className="font-mono text-[10px] font-bold" style={{ color: barColor }}>
                  {meanConf != null ? `${meanConf}%` : '—'}
                </span>
              </div>
              <div className="h-1 rounded-full overflow-hidden" style={{ backgroundColor: '#F1F5F9' }}>
                <motion.div
                  className="h-full rounded-full"
                  style={{ backgroundColor: barColor }}
                  animate={{ width: `${meanConf ?? 0}%` }}
                  transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                />
              </div>
            </div>

            {/* Real status: connecting / active / processing / unavailable */}
            <AnimatePresence mode="wait">
              <motion.div
                key={status}
                className="px-3 pb-2 flex items-center gap-1.5"
                initial={{ opacity: 0, y: 3 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                {meta.icon}
                <span className="font-display text-[9px] text-slate-400">{meta.label}</span>
              </motion.div>
            </AnimatePresence>

            {lockMeta && (
              <div className="px-3 pb-2 flex items-center gap-1.5">
                {lockMeta.icon}
                <span className="font-display text-[9px] font-medium" style={{ color: lockMeta.color }}>
                  {lockMeta.label}
                </span>
              </div>
            )}

            {expanded && (
              <div className="px-3 pb-2.5 border-t border-slate-100 pt-2 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-display text-[9px] text-slate-400">Last frame sent</span>
                  <span className="font-mono text-[9px] text-slate-600">{relativeSeconds(lastFrameSentAt) ?? '—'}</span>
                </div>
                {tracking && (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="font-display text-[9px] text-slate-400">Tracking inliers</span>
                      <span className="font-mono text-[9px] text-slate-600">{tracking.inliers ?? '—'}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="font-display text-[9px] text-slate-400">Tracking status</span>
                      <span className="font-mono text-[9px] text-slate-600">{tracking.status}</span>
                    </div>
                    {!tracking.locked && tracking.reasons && tracking.reasons.length > 0 && (
                      <div className="font-display text-[9px] text-red-600 leading-snug">
                        {tracking.reasons[0]} — values are held, not confirmed.
                      </div>
                    )}
                  </>
                )}
                {lastError && (
                  <div className="font-display text-[9px] text-amber-600 leading-snug">{lastError}</div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </motion.div>
  );
}
