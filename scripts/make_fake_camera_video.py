"""M5.6 Phase 7: build Y4M videos for Chrome's fake video capture device.

Chrome can replace a physical webcam with the contents of a file:

    --use-fake-device-for-media-stream --use-file-for-fake-video-capture=X.y4m

getUserMedia() then returns a real MediaStream carrying those frames, which
means the ENTIRE browser path under test is genuine -- real permission flow,
real <video> element, real canvas capture, real JPEG encode, real
push-frame upload. The only thing simulated is the sensor.

This is NOT a claim of physical-camera validation, and the M5.6 report says
so explicitly. It is the strongest browser-level evidence obtainable without
a human holding a camera in front of a monitor.

Y4M rather than MJPEG deliberately: uncompressed I420 is the format Chrome's
FileVideoCaptureDevice has supported longest, so it is the least likely to
fail on stage or in a different Chrome build. The cost is file size, which is
why the static videos hold only a couple of frames -- Chrome loops the file,
and a looping single frame is exactly what "a camera pointed at a monitor
that is not moving" looks like.

Usage (from the repo root):
    backend/.venv/Scripts/python.exe scripts/make_fake_camera_video.py OUTDIR
"""

import os
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

DENSE_B = os.path.join("backend", "app", "eval", "tier2_data", "dense_B")


def _rgb_to_i420(img: np.ndarray) -> bytes:
    """RGB -> planar I420 (Y plane, then half-resolution U and V)."""
    import cv2

    yuv = cv2.cvtColor(img, cv2.COLOR_RGB2YUV_I420)
    return yuv.tobytes()


def write_y4m(frames, out_path: str, fps: int = 10) -> str:
    """frames: iterable of HxWx3 uint8 RGB arrays, all the same size."""
    frames = list(frames)
    if not frames:
        raise ValueError("no frames")
    h, w = frames[0].shape[:2]
    if w % 2 or h % 2:
        raise ValueError(f"Y4M needs even dimensions, got {w}x{h}")

    with open(out_path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420\n".encode("ascii"))
        for frame in frames:
            if frame.shape[:2] != (h, w):
                raise ValueError("all frames must share one size")
            f.write(b"FRAME\n")
            f.write(_rgb_to_i420(np.ascontiguousarray(frame)))
    return out_path


def _render(reading: dict, path: str):
    from simulator.render.monitor_layout import render_monitor

    meta = render_monitor(reading, path, layout="grid")
    return np.array(Image.open(path).convert("RGB")), meta


# The calibration UI instructs the operator to draw "the field's full display
# slot -- not just the digits currently shown", because a box drawn tight to
# a 2-digit value truncates a later 3-digit one (measured: "145" -> "14").
# The simulator's ROI metadata is the DIGITS' bounding box, so the two fields
# that change digit count during the demo are widened here to model an
# operator who followed the instruction.
SLOT_WIDEN = {"hr": 1.5, "spo2": 1.5}


def _normalized_rois(meta: dict, w: int, h: int) -> dict:
    out = {}
    for vital, (bx, by, bw, bh) in meta["rois"].items():
        bw = bw * SLOT_WIDEN.get(vital, 1.0)
        out[vital] = {"x": bx / w, "y": by / h, "w": bw / w, "h": bh / h}
    return out


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)

    normal = {"hr": 74, "spo2": 98, "nibpSystolic": 120, "nibpDiastolic": 80, "nibpMean": 93,
              "etco2": 38, "temp": 36.8, "rr": 14, "timestamp": int(time.time() * 1000)}
    # Two deteriorations, testing two different things (matching
    # app/eval/tier2_data/m5_6_report/m5_6_e2e_script.py exactly):
    #   spo2 98 -> 88 clears the <=90 CRITICAL threshold in app.alerts.rules
    #     AND reads at confidence 82-94, comfortably over the 70 confirmation
    #     gate. This is the alert the demo is expected to actually show.
    #   hr 74 -> 145 changes DIGIT COUNT, which is the truncation case the
    #     display-slot widening above exists to survive.
    critical = dict(normal, spo2=88, hr=145)

    made = []

    # 1. A steady monitor. Two identical frames is enough: Chrome loops, so
    #    the stream is indistinguishable from a camera held still.
    img, meta = _render(normal, os.path.join(out_dir, "_monitor_normal.png"))
    h, w = img.shape[:2]
    made.append(write_y4m([img, img], os.path.join(out_dir, "monitor_normal.y4m")))

    # 2. The same monitor showing a critical SpO2 (and a 3-digit HR).
    img_c, _ = _render(critical, os.path.join(out_dir, "_monitor_critical.png"))
    made.append(write_y4m([img_c, img_c], os.path.join(out_dir, "monitor_critical.y4m")))

    # The browser driver needs to know WHERE to drag each box. These are the
    # ground-truth field positions in the video it is about to be shown --
    # the equivalent of an operator who can see the monitor.
    import json

    with open(os.path.join(out_dir, "rois.json"), "w") as f:
        json.dump({"width": w, "height": h, "normal": normal, "critical": critical,
                   "rois": _normalized_rois(meta, w, h)}, f, indent=2)
    made.append(os.path.join(out_dir, "rois.json"))

    # 3. The same monitor, held still long enough to calibrate against, then
    #    deliberately MOVED -- the "nudge the camera and watch the boxes
    #    follow" demonstration, in the browser rather than in a harness.
    #
    #    The declared frame rate is what makes this usable: at 2 fps the
    #    120-frame steady head is a full minute of stillness, comfortably
    #    longer than a calibration run, and Chrome loops the file so the
    #    steady window always comes back around.
    import cv2

    steady = [img] * 120
    motion = []
    for i in range(100):
        t = (i + 1) / 100.0
        h_i, w_i = img.shape[:2]
        M = cv2.getRotationMatrix2D((w_i / 2.0, h_i / 2.0), 6.0 * t, 1.0 + 0.10 * t)
        M[0, 2] += 45.0 * t
        M[1, 2] += 30.0 * t
        motion.append(cv2.warpAffine(img, M, (w_i, h_i), flags=cv2.INTER_LINEAR,
                                     borderValue=(8, 10, 14)))
    made.append(write_y4m(steady + motion, os.path.join(out_dir, "monitor_nudge.y4m"), fps=2))

    # 4. REAL footage with REAL camera motion -- the dense Dataset B split
    #    (one continuous 54s handheld phone recording of a GE CARESCAPE
    #    B650). This is the arm that exercises layout tracking in the
    #    browser rather than in a harness.
    if os.path.isdir(DENSE_B):
        names = sorted(n for n in os.listdir(DENSE_B) if n.endswith(".png"))
        frames = [np.array(Image.open(os.path.join(DENSE_B, n)).convert("RGB")) for n in names]
        made.append(write_y4m(frames, os.path.join(out_dir, "monitor_real_motion.y4m"), fps=5))
    else:
        print(f"  (skipped real-motion video: {DENSE_B} not found)")

    for path in made:
        print(f"  {os.path.basename(path):28s} {os.path.getsize(path) / 1e6:8.1f} MB")


if __name__ == "__main__":
    main()
