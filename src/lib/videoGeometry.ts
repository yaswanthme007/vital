import { useEffect, useState } from 'react';
import type { NormalizedBox } from '@/types/calibration';

/**
 * Shared camera-frame ↔ screen geometry, used by BOTH Calibration's
 * RoiCanvas (drawing boxes) and Active Operation's CameraFeedPanel
 * (rendering the saved profile's boxes). Before this module existed, each
 * page carried its own copy of this math -- RoiCanvas's accounted for the
 * <video>'s object-contain letterboxing, CameraFeedPanel's didn't (it just
 * applied `box.x * 100%` against the whole container). Calibration and
 * Active Operation would then show the SAME normalized box in two different
 * physical places whenever the video's aspect ratio didn't exactly match its
 * container's -- which is the normal case, not an edge case, since the
 * container is a CSS panel and the video is a fixed camera resolution.
 *
 * Canonical coordinate system: a NormalizedBox is always [0,1] relative to
 * the VIDEO'S OWN FRAME (videoWidth × videoHeight), never the container. To
 * render it on screen you must first know where the video itself is drawn
 * inside its container -- `computeVideoDisplayRect` answers that for
 * `object-fit: contain` (the only mode either page uses: both CalibrationPage
 * and CameraFeedPanel apply `object-contain` to their <video> element), then
 * `normalizedBoxToRect` maps a box through that rect into container-relative
 * pixels. Same two functions, same result, in both places.
 */

export interface VideoDisplayRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Where an `object-fit: contain` video actually paints inside a
 * `containerWidth` × `containerHeight` box -- i.e. the letterboxed/
 * pillarboxed rect, in pixels relative to the container's own top-left.
 * Returns null when any dimension isn't known yet (video metadata not
 * loaded, or container not yet laid out).
 */
export function computeVideoDisplayRect(
  videoWidth: number,
  videoHeight: number,
  containerWidth: number,
  containerHeight: number,
): VideoDisplayRect | null {
  if (!videoWidth || !videoHeight || !containerWidth || !containerHeight) return null;

  const videoRatio = videoWidth / videoHeight;
  const containerRatio = containerWidth / containerHeight;
  let width: number, height: number;
  if (videoRatio > containerRatio) {
    // Video is relatively wider than the container -- full width, letterboxed top/bottom.
    width = containerWidth;
    height = containerWidth / videoRatio;
  } else {
    // Video is relatively taller than the container -- full height, pillarboxed left/right.
    height = containerHeight;
    width = containerHeight * videoRatio;
  }
  return { left: (containerWidth - width) / 2, top: (containerHeight - height) / 2, width, height };
}

/** Maps a canonical (video-frame-normalized) box through a display rect into
 * container-relative CSS pixels. Used identically by RoiCanvas (drawing) and
 * CameraFeedPanel (rendering the saved profile). */
export function normalizedBoxToRect(box: NormalizedBox, displayRect: VideoDisplayRect): VideoDisplayRect {
  return {
    left: displayRect.left + box.x * displayRect.width,
    top: displayRect.top + box.y * displayRect.height,
    width: box.w * displayRect.width,
    height: box.h * displayRect.height,
  };
}

/**
 * Tracks the live display rect of `videoRef`'s video as rendered inside
 * `containerRef`, recomputing on video metadata load, video resize (camera
 * resolution can change after `getUserMedia` settles), and container resize
 * (panel size differs between Calibration and Active Operation, and can
 * itself change e.g. on window resize). Returns null until both the video's
 * intrinsic size and the container's laid-out size are known.
 */
export function useVideoDisplayRect(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  containerRef: React.RefObject<HTMLElement | null>,
): VideoDisplayRect | null {
  const [rect, setRect] = useState<VideoDisplayRect | null>(null);

  useEffect(() => {
    const video = videoRef.current;
    const container = containerRef.current;
    if (!video || !container) return;

    const recompute = () => {
      setRect(
        computeVideoDisplayRect(video.videoWidth, video.videoHeight, container.clientWidth, container.clientHeight),
      );
    };

    recompute();
    video.addEventListener('loadedmetadata', recompute);
    video.addEventListener('resize', recompute);
    const ro = new ResizeObserver(recompute);
    ro.observe(container);
    return () => {
      video.removeEventListener('loadedmetadata', recompute);
      video.removeEventListener('resize', recompute);
      ro.disconnect();
    };
  }, [videoRef, containerRef]);

  return rect;
}
