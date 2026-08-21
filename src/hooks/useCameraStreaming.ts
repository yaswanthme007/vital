import { useEffect, useRef } from 'react';
import { useCameraCapture } from '@/hooks/useCameraCapture';
import { useCameraStreamStore } from '@/store/cameraStreamStore';
import { useToast } from '@/store/toastStore';
import { api } from '@/lib/api';
import type { CameraSourceMode } from '@/hooks/useCameraCapture';

const CAPTURE_INTERVAL_MS = 1000; // ~1 FPS — matches CameraSource's own poll interval server-side

/**
 * The app's live-camera ingestion loop (owned by the always-mounted
 * CameraCaptureController -- see src/features/operation/
 * CameraCaptureController.tsx, M5.7): acquires its own MediaStream
 * (independent of whatever Calibration used — that stream was already
 * stopped when Calibration unmounted), then captures and POSTs a frame to
 * push-frame/{sessionId} on a fixed ~1s cadence for as long as camera mode
 * is enabled for this session -- CONTINUOUSLY across navigation between
 * the Active Operation workspace, Review and Archive, because the
 * controller that owns this hook lives at the app root, not inside any one
 * page. This is what makes "the camera remains active for the entire
 * active operation" true rather than aspirational.
 *
 * Deliberately does NOT touch vitalsStore, reconcile(), or the WebSocket —
 * this only feeds FrameQueue. CameraSource (server-side, inside the
 * `?source=camera` WS connection) is what turns pushed frames into
 * readings; see backend/app/sources/camera.py.
 */
export function useCameraStreaming(params: {
  sessionId: string | null;
  enabled: boolean;
  sourceMode: CameraSourceMode | null;
}) {
  const { sessionId, enabled, sourceMode } = params;
  const capture = useCameraCapture();
  const { videoRef, canvasRef, connect, stop, captureFrameBlob } = capture;
  const { setStatus, frameSent, reset, setMediaStream } = useCameraStreamStore();
  const { toast } = useToast();

  // Guards a single in-flight upload — if push-frame hasn't returned by the
  // next tick, that tick is skipped rather than queueing a second request.
  const uploadingRef = useRef(false);
  const failureStreakRef = useRef(0);

  useEffect(() => {
    if (!enabled || !sessionId || !sourceMode) {
      reset();
      return;
    }

    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | undefined;
    // Set the instant the track ends, so a tick already scheduled before
    // that (or one started by the connect().then() below racing against
    // onDisconnect) can't keep overwriting 'disconnected' with a misleading
    // 'upload_error' every second forever — see the ended-track bug this
    // guards: without it, captureFrameBlob() throws "Camera not connected"
    // on every tick after disconnect, which permanently masks the real
    // disconnected state behind an amber "Upload failing" badge instead of
    // the red "Camera unavailable" one the UI already has for this case.
    let disconnected = false;

    const onDisconnect = () => {
      if (cancelled) return;
      disconnected = true;
      if (intervalId) {
        clearInterval(intervalId);
        intervalId = undefined;
      }
      setStatus('disconnected');
      // The stream object itself is now dead (all tracks ended) but stays
      // referenced in the store otherwise, so CameraFeedPanel keeps
      // rendering it as a frozen (often solid black) <video> under a
      // confidently pulsing "LIVE CAMERA" badge instead of falling back to
      // its own "Camera unavailable" placeholder.
      setMediaStream(null);
      toast.error('Camera disconnected', { description: 'Live OCR paused. Reconnect or re-run Calibration to resume.' });
    };

    const tick = async () => {
      if (disconnected || uploadingRef.current) return; // previous upload still in flight — skip this tick, don't queue
      uploadingRef.current = true;
      setStatus('uploading');
      try {
        const blob = await captureFrameBlob();
        await api.pushFrame(sessionId, blob);
        failureStreakRef.current = 0;
        frameSent();
        setStatus('active');
      } catch (err) {
        failureStreakRef.current += 1;
        const message = err instanceof Error ? err.message : 'Frame upload failed';
        setStatus('upload_error', message);
        // Toast only on the first failure of a streak — a flaky tick every
        // ~1s must never spam the UI, but the user still needs to know the
        // backend is unreachable.
        if (failureStreakRef.current === 1) {
          toast.warning("Couldn't send camera frame", {
            description: 'Check that the VITAL backend is running.',
          });
        }
      } finally {
        uploadingRef.current = false;
      }
    };

    setStatus('connecting');
    connect(sourceMode, onDisconnect).then((ok) => {
      if (cancelled || disconnected) return; // ended before connect() even resolved — onDisconnect already handled it
      if (!ok) {
        setStatus('disconnected', capture.error ?? 'Camera access was denied.');
        toast.error('Camera access was denied', {
          description: 'Enable camera access to start live capture.',
        });
        return;
      }
      setStatus('active');
      // M5.7: publish the live MediaStream so any page that wants to SHOW
      // it (the Active Operation workspace) can attach it to its own
      // <video> without opening a second getUserMedia/getDisplayMedia
      // stream -- a MediaStream can back more than one <video> element at
      // once. videoRef.current.srcObject was just set by connect() above.
      setMediaStream((videoRef.current?.srcObject as MediaStream | null) ?? null);
      intervalId = setInterval(tick, CAPTURE_INTERVAL_MS);
    });

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      stop();
      reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, sessionId, sourceMode]);

  return { videoRef, canvasRef };
}
