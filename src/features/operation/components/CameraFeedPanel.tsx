import { useEffect, useRef, useState } from 'react';
import { Camera, Crosshair, AlertTriangle, Video } from 'lucide-react';
import { useCameraStreamStore } from '@/store/cameraStreamStore';
import { useVitalsStore } from '@/store/vitalsStore';
import { useSessionStore } from '@/store/sessionStore';
import { api } from '@/lib/api';
import { VITAL_COLORS, VITAL_LABELS } from '@/features/calibration/RoiCanvas';
import { normalizedBoxToRect, useVideoDisplayRect } from '@/lib/videoGeometry';
import { VITAL_KEYS } from '@/types/calibration';
import type { CalibrationProfile } from '@/types/calibration';
import { CameraOverlay } from './CameraOverlay';

/**
 * M5.7. THE visible camera feed for the Active Operation workspace --
 * replaces SurgeryPage's old off-screen-clipped <video> (see
 * src/features/surgery/SurgeryPage.tsx's previous "capture rig, not a
 * preview" comment). The camera is the PRIMARY sensor of this product; an
 * operator watching a case should be able to see what the pipeline sees.
 *
 * Attaches the SAME MediaStream the always-mounted CameraCaptureController
 * (app root) is capturing frames from -- via useCameraStreamStore's
 * mediaStream, not a second getUserMedia call -- so this panel can mount
 * and unmount freely (e.g. this workspace isn't even open) without ever
 * affecting whether frames keep being captured and pushed.
 *
 * Draws the calibrated ROI boxes as a static overlay (the profile's own
 * normalized roiBoxes, fetched once from the active profile) plus the
 * current layout-tracking lock state. This is a VISUAL aid for the
 * operator, not a claim about the exact per-frame tracked geometry the
 * server-side pipeline used for that tick's OCR -- see
 * docs/VITAL_LIVE_CAMERA_TRACKING_ARCHITECTURE_ANALYSIS.md for why exposing
 * the tracker's own per-frame transformed boxes was deliberately scoped out
 * of this pass.
 *
 * Every roiBox is normalized to the VIDEO'S OWN FRAME (see
 * src/lib/videoGeometry.ts), not this panel's container -- so it's mapped
 * through the same useVideoDisplayRect/normalizedBoxToRect Calibration's
 * RoiCanvas uses to draw the boxes in the first place. Positioning boxes as
 * a flat `box.x * 100%` of the container (the previous implementation) is
 * only correct when the video happens to exactly fill its container; with
 * `object-contain` letterboxing -- the normal case whenever this panel's
 * aspect ratio doesn't match the camera's -- that put every box visibly off
 * the monitor digits it was calibrated against.
 */
export function CameraFeedPanel() {
  const mediaStream = useCameraStreamStore((s) => s.mediaStream);
  const status = useCameraStreamStore((s) => s.status);
  const cameraMode = useSessionStore((s) => s.cameraMode);
  const tracking = useVitalsStore((s) => s.tracking);
  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const displayRect = useVideoDisplayRect(videoRef, containerRef);
  const [profile, setProfile] = useState<CalibrationProfile | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getActiveCalibrationProfile().then((p) => { if (!cancelled) setProfile(p); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = mediaStream;
    if (mediaStream) video.play().catch(() => {});
  }, [mediaStream]);

  const lockMeta = !tracking
    ? null
    : tracking.locked
      ? { label: 'Layout locked — following the monitor', color: '#16A34A', icon: <Crosshair size={12} /> }
      : { label: 'Monitor lost — every field withheld until it re-locks', color: '#DC2626', icon: <AlertTriangle size={12} /> };

  return (
    <div ref={containerRef} className="relative flex-1 min-w-0 bg-slate-950 rounded-2xl overflow-hidden border border-slate-800">
      {mediaStream ? (
        <>
          <video ref={videoRef} muted playsInline className="absolute inset-0 w-full h-full object-contain" />
          {/* Calibrated ROI overlay -- normalized boxes from the active profile,
              mapped through the video's actual letterboxed display rect (same
              geometry Calibration's RoiCanvas uses) so a box lands on the same
              physical monitor pixels here as it did there. */}
          {profile && displayRect && (
            <div className="absolute inset-0 pointer-events-none">
              {VITAL_KEYS.filter((v) => profile.roiBoxes[v]).map((v) => {
                const box = profile.roiBoxes[v]!;
                const r = normalizedBoxToRect(box, displayRect);
                return (
                  <div
                    key={v}
                    className="absolute border-2 rounded-sm"
                    style={{
                      left: r.left, top: r.top, width: r.width, height: r.height,
                      borderColor: VITAL_COLORS[v],
                      opacity: lockMeta === null || lockMeta.color === '#16A34A' ? 0.9 : 0.35,
                    }}
                  >
                    <span
                      className="absolute -top-5 left-0 px-1.5 py-0.5 rounded text-[9px] font-display font-bold text-white"
                      style={{ background: VITAL_COLORS[v] }}
                    >
                      {VITAL_LABELS[v]}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </>
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-slate-500">
          <Video size={28} />
          <span className="font-display text-xs">
            {cameraMode ? `Camera ${status === 'connecting' ? 'connecting…' : 'unavailable'}` : 'Synthetic feed — no camera calibrated for this case'}
          </span>
        </div>
      )}

      {/* Status chip */}
      <div className="absolute top-3 left-3 flex items-center gap-2">
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-black/60 backdrop-blur-sm">
          <Camera size={11} className="text-white" />
          <span className="font-display text-[10px] font-bold text-white uppercase tracking-wider">
            {cameraMode ? 'Live Camera' : 'Synthetic'}
          </span>
          {mediaStream && (
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" aria-hidden />
          )}
        </div>
        {lockMeta && (
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-black/60 backdrop-blur-sm" style={{ color: lockMeta.color }}>
            {lockMeta.icon}
            <span className="font-display text-[10px] font-semibold">{lockMeta.label}</span>
          </div>
        )}
      </div>

      {/* Detailed status (confidence breakdown, upload health, expand for
          more) -- the existing, already-built widget; this panel's own
          top-left chip stays a one-glance summary, not a duplicate. */}
      <CameraOverlay />
    </div>
  );
}
