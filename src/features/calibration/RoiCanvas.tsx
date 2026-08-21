import { useCallback, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { normalizedBoxToRect, useVideoDisplayRect } from '@/lib/videoGeometry';
import type { NormalizedBox, VitalKey } from '@/types/calibration';

// Mirrors backend/app/pipeline/roi.py's VITAL_COLORS exactly, so a box drawn
// here reads the same colour the rest of the app (VitalCard, the simulator's
// own render) already associates with that vital.
export const VITAL_COLORS: Record<VitalKey, string> = {
  hr: '#00FF88',
  spo2: '#00D4FF',
  nibp: '#FF4757',
  etco2: '#FFD600',
  temp: '#FF9500',
  rr: '#BF5AF2',
};

export const VITAL_LABELS: Record<VitalKey, string> = {
  hr: 'HR',
  spo2: 'SpO₂',
  nibp: 'NIBP',
  etco2: 'EtCO₂',
  temp: 'Temp',
  rr: 'RR',
};

// Video display rect (accounting for object-contain letterboxing) is now
// computed by the shared src/lib/videoGeometry.ts, so this page and
// CameraFeedPanel's Active Operation overlay use the exact same geometry --
// see that module's docstring for why a per-page copy of this math was the
// root cause of Calibration/Active Operation ROI misalignment. Every mouse
// coordinate the operator draws is converted through the video's actual
// displayed rect, not the container's — otherwise a normalized box wouldn't
// correspond to the same pixels captureFrameBlob() (which snapshots the
// video at its native videoWidth/videoHeight) later crops.

const MIN_DRAG_PX = 6;

export function RoiCanvas({
  videoRef,
  boxes,
  activeVital,
  onBoxChange,
  disabled,
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  boxes: Partial<Record<VitalKey, NormalizedBox>>;
  activeVital: VitalKey | null;
  onBoxChange: (vital: VitalKey, box: NormalizedBox | null) => void;
  disabled?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const displayRect = useVideoDisplayRect(videoRef, containerRef);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const draggingRef = useRef(false);

  const toNormalized = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } | null => {
      const container = containerRef.current;
      if (!container || !displayRect) return null;
      const bounds = container.getBoundingClientRect();
      const localX = clientX - bounds.left - displayRect.left;
      const localY = clientY - bounds.top - displayRect.top;
      return {
        x: Math.min(1, Math.max(0, localX / displayRect.width)),
        y: Math.min(1, Math.max(0, localY / displayRect.height)),
      };
    },
    [displayRect],
  );

  const handlePointerDown = (e: React.PointerEvent) => {
    if (disabled || !activeVital) return;
    const p = toNormalized(e.clientX, e.clientY);
    if (!p) return;
    draggingRef.current = true;
    (e.target as Element).setPointerCapture(e.pointerId);
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    const p = toNormalized(e.clientX, e.clientY);
    if (!p) return;
    setDrag((prev) => (prev ? { ...prev, x1: p.x, y1: p.y } : prev));
  };

  const finishDrag = () => {
    if (!draggingRef.current || !drag || !activeVital || !displayRect) {
      draggingRef.current = false;
      setDrag(null);
      return;
    }
    draggingRef.current = false;
    const x = Math.min(drag.x0, drag.x1);
    const y = Math.min(drag.y0, drag.y1);
    const w = Math.abs(drag.x1 - drag.x0);
    const h = Math.abs(drag.y1 - drag.y0);
    setDrag(null);
    // A near-zero drag (a click, not a drawn box) doesn't create/replace a
    // box -- avoids a mis-click silently wiping out a good existing box.
    if (w * displayRect.width < MIN_DRAG_PX || h * displayRect.height < MIN_DRAG_PX) return;
    onBoxChange(activeVital, { x, y, w, h });
  };

  const renderBox = (vital: VitalKey, box: NormalizedBox, isActive: boolean) => {
    if (!displayRect) return null;
    const color = VITAL_COLORS[vital];
    const r = normalizedBoxToRect(box, displayRect);
    const style: React.CSSProperties = {
      position: 'absolute',
      left: r.left,
      top: r.top,
      width: r.width,
      height: r.height,
      border: `2px solid ${color}`,
      background: `${color}1A`,
      borderRadius: 3,
      pointerEvents: 'none',
    };
    return (
      <div key={vital} style={style}>
        <span
          className="absolute -top-5 left-0 px-1.5 py-0.5 rounded font-display text-[9px] font-bold uppercase tracking-wider whitespace-nowrap"
          style={{ background: color, color: '#04080F' }}
        >
          {VITAL_LABELS[vital]}
        </span>
        {isActive && (
          <div
            className="absolute inset-0 rounded-[3px]"
            style={{ boxShadow: `0 0 0 2px ${color}, 0 0 12px ${color}88` }}
          />
        )}
      </div>
    );
  };

  return (
    <div ref={containerRef} className="absolute inset-0">
      {/* Drawing surface -- sits exactly over the video's letterboxed display
          rect, not the whole container, so drag coordinates line up with the
          captured frame's actual pixels. */}
      <div
        className={cn('absolute', !disabled && activeVital && 'cursor-crosshair')}
        style={
          displayRect
            ? { left: displayRect.left, top: displayRect.top, width: displayRect.width, height: displayRect.height }
            : { inset: 0 }
        }
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
      />

      {(Object.entries(boxes) as [VitalKey, NormalizedBox][]).map(([vital, box]) => renderBox(vital, box, vital === activeVital))}

      {drag && displayRect && activeVital && (
        <motion.div
          initial={false}
          style={{
            position: 'absolute',
            left: displayRect.left + Math.min(drag.x0, drag.x1) * displayRect.width,
            top: displayRect.top + Math.min(drag.y0, drag.y1) * displayRect.height,
            width: Math.abs(drag.x1 - drag.x0) * displayRect.width,
            height: Math.abs(drag.y1 - drag.y0) * displayRect.height,
            border: `2px dashed ${VITAL_COLORS[activeVital]}`,
            background: `${VITAL_COLORS[activeVital]}22`,
            borderRadius: 3,
            pointerEvents: 'none',
          }}
        />
      )}

      {!displayRect && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-display text-vital-xs text-[#3D5570]">Waiting for video…</span>
        </div>
      )}
    </div>
  );
}

/** Crops the CURRENT video frame to `box` (normalized) and returns a JPEG
 * blob -- used for the live per-field preview thumbnail while drawing, so
 * the operator sees the actual crop content, not just a rectangle (Phase 2's
 * "must show the live crop as the box is drawn" requirement). */
export async function cropVideoFrame(video: HTMLVideoElement, box: NormalizedBox): Promise<string | null> {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) return null;
  const canvas = document.createElement('canvas');
  const sx = Math.round(box.x * vw);
  const sy = Math.round(box.y * vh);
  const sw = Math.max(1, Math.round(box.w * vw));
  const sh = Math.max(1, Math.round(box.h * vh));
  canvas.width = sw;
  canvas.height = sh;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.drawImage(video, sx, sy, sw, sh, 0, 0, sw, sh);
  return canvas.toDataURL('image/png');
}
