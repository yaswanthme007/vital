import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { OperationHeader } from './components/OperationHeader';
import { CameraFeedPanel } from './components/CameraFeedPanel';
import { LedgerPanel } from './components/LedgerPanel';
import { VitalsGrid } from './components/VitalsGrid';
import { AlertsFooter } from './components/AlertsFooter';
import { useSessionStore } from '@/store/sessionStore';
import { useToast } from '@/store/toastStore';

/**
 * M5.7. The Active Operation workspace -- what used to be SurgeryPage
 * ("Live Monitor"). Renamed and restructured around the product's actual
 * shape: the camera feed is the primary, VISIBLE panel (not an off-screen
 * capture rig), the observation ledger sits beside it as the live
 * chronological record of what the camera has genuinely confirmed, and the
 * vitals strip along the bottom shows confirmed/held state per field
 * rather than a value with no provenance.
 *
 * Deliberately does NOT own the camera capture loop or the vitals
 * WebSocket connection any more -- both moved to app-root-mounted owners
 * (CameraCaptureController and useVitalsSimulation, wired in src/App.tsx)
 * specifically so navigating away to Review or Archive mid-case does not
 * stop the camera. This page can mount and unmount freely; observation
 * never pauses because it did.
 *
 * Also drops the old WaveformsPanel: a synthetic ECG/pleth/capnography
 * animation derived from current.hr, representing no camera-observed data
 * at all -- exactly the kind of "looks like a real monitor, isn't reading
 * one" surface this milestone exists to remove. See
 * docs/VITAL_LIVE_CAMERA_TRACKING_ARCHITECTURE_ANALYSIS.md.
 *
 * M5.7.1: the operator must never reach this page before calibration has
 * completed for the active session -- `cameraMode` (flipped true only by
 * CalibrationPage.handleSave, see sessionStore) is the existing signal for
 * "this case has an activated calibration profile"; it just wasn't being
 * checked here before. Reaching /operation directly (URL edit, stale link,
 * refresh mid-calibration) with a session but no calibration now bounces to
 * /calibration instead of silently rendering on the synthetic fallback feed.
 */
export function OperationPage() {
  const activeSession = useSessionStore((s) => s.activeSession);
  const cameraMode = useSessionStore((s) => s.cameraMode);
  const navigate = useNavigate();
  const { toast } = useToast();
  const warnedRef = useRef(false);

  useEffect(() => {
    if (!activeSession) {
      navigate('/start', { replace: true });
      return;
    }
    if (!cameraMode) {
      if (!warnedRef.current) {
        warnedRef.current = true;
        toast.info('Camera calibration required', {
          description: 'Complete camera calibration for this case before monitoring can begin.',
        });
      }
      navigate('/calibration', { replace: true });
    }
  }, [activeSession, cameraMode, navigate, toast]);

  if (!activeSession || !cameraMode) return null;

  return (
    <motion.div
      className="relative flex flex-col h-screen w-screen overflow-hidden"
      style={{ background: '#F8FAFC' }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.22 }}
    >
      <OperationHeader />

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Camera feed + ledger -- takes remaining space */}
        <div className="flex flex-1 min-w-0 gap-3 p-3 overflow-hidden">
          <CameraFeedPanel />
          <LedgerPanel />
        </div>
        {/* Vitals panel — fixed width, readable at 2 m */}
        <div className="flex-shrink-0 overflow-hidden border-l border-slate-200" style={{ width: 320 }}>
          <VitalsGrid />
        </div>
      </div>

      <AlertsFooter />
    </motion.div>
  );
}
