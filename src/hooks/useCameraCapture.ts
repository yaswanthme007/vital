import { useRef, useState, useCallback, useEffect } from 'react';

export type CameraSourceMode = 'camera' | 'screen';

export interface CameraInfo {
  label: string;
  width: number;
  height: number;
}

/**
 * Shared MediaStream + frame-capture plumbing, extracted from
 * CalibrationPage's original inline connect/capture logic so Calibration and
 * the live-camera streaming loop (SurgeryPage) don't each carry their own
 * copy of the same getUserMedia/getDisplayMedia/canvas-capture code.
 *
 * Owns: acquiring a stream, attaching it to a <video>, capturing the current
 * video frame to a JPEG Blob via an offscreen <canvas>, and tearing the
 * stream down cleanly. Does NOT own any push/upload loop — that's
 * useCameraStreaming's job.
 */
export function useCameraCapture() {
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<CameraInfo | null>(null);
  const [sourceMode, setSourceMode] = useState<CameraSourceMode | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setActive(false);
    setInfo(null);
    setSourceMode(null);
  }, []);

  const connect = useCallback(async (mode: CameraSourceMode, onDisconnect?: () => void) => {
    setError(null);
    try {
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
      setInfo({
        label: track?.label || (mode === 'camera' ? 'Camera' : 'Shared tab'),
        width: settings?.width ?? 0,
        height: settings?.height ?? 0,
      });
      setSourceMode(mode);
      setActive(true);

      // Screen share can be stopped from the browser's own "Stop sharing"
      // bar, not just our Disconnect button — that only ends the track, it
      // doesn't fire our state, so without this the caller would keep
      // believing it's connected to a stream that's already dead.
      track?.addEventListener('ended', () => {
        stop();
        onDisconnect?.();
      });
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not access camera';
      setError(message);
      return false;
    }
  }, [stop]);

  const captureFrameBlob = useCallback(async (): Promise<Blob> => {
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
    return new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not capture frame'))), 'image/jpeg', 0.85)
    );
  }, []);

  // Release the stream on unmount, not just on explicit disconnect.
  useEffect(() => () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
  }, []);

  return { videoRef, canvasRef, active, error, info, sourceMode, connect, stop, captureFrameBlob, setError };
}
