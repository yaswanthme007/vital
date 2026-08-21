import { useSessionStore } from '@/store/sessionStore';
import { useCameraStreaming } from '@/hooks/useCameraStreaming';

/**
 * M5.7. THE non-negotiable invariant this file exists to satisfy: the
 * camera remains active for the entire active operation, with no operator
 * action required to keep it running -- including navigating to Review or
 * Archive mid-case, which used to silently stop frame capture because the
 * old SurgeryPage owned this loop itself and unmounted it on every route
 * change (see docs/VITAL_LIVE_CAMERA_TRACKING_ARCHITECTURE_ANALYSIS.md).
 *
 * Mounted exactly ONCE, at the app root (src/App.tsx), as a SIBLING of
 * <Routes> rather than inside any one page -- so it is never torn down by
 * client-side navigation, only by the session itself ending (cameraMode
 * flips false) or the tab closing. The camera starts automatically the
 * moment a case with an active calibration profile begins (see
 * useSessionStore.setCameraMode, called at the end of Calibration's Save
 * step) -- there is no separate "start camera" action inside the
 * operation itself.
 *
 * Renders nothing visible: this is the OFF-SCREEN capture rig
 * (getUserMedia/getDisplayMedia + the canvas that snapshots frames to
 * JPEG), not a preview. Any page that wants to actually SHOW the feed
 * (the Active Operation workspace) reads the live MediaStream this
 * publishes to useCameraStreamStore and attaches it to its own visible
 * <video> -- see OperationPage's useSharedCameraPreview.
 */
export function CameraCaptureController() {
  const { activeSession, cameraMode, cameraSourceMode } = useSessionStore();

  // Same source-of-truth condition useVitalsSimulation uses for whether the
  // WS talks to CameraSource: this loop must only push frames when the WS
  // is actually consuming source=camera.
  const enabled = Boolean(
    activeSession && activeSession.status === 'active' && cameraMode
  );

  const { videoRef, canvasRef } = useCameraStreaming({
    sessionId: activeSession?.id ?? null,
    enabled,
    sourceMode: cameraSourceMode,
  });

  if (!enabled) return null;

  return (
    <div aria-hidden className="fixed w-px h-px overflow-hidden pointer-events-none" style={{ clip: 'rect(0,0,0,0)' }}>
      <video ref={videoRef} muted playsInline />
      <canvas ref={canvasRef} />
    </div>
  );
}
