"""S11's real-camera/OCR source, anticipated by VitalsSource's docstring
(app/sources/base.py) since S6. Drains app.sources.frame_queue -- filled by
POST /api/pipeline/push-frame/{channel} -- and runs each new frame through
the SAME app.pipeline.read_frame.read_frame() the "pipeline" ReplaySource
mode and the eval harness use, unmodified.

Design choice: browser-push, not server-side cv2.VideoCapture(index).
    This backend runs inside the Docker container DEMO.md has the team
    deploy. Docker Desktop on Windows/macOS (the two platforms a
    conference laptop is likeliest to be running) does not pass a USB/
    built-in webcam through to a container's device tree -- only native
    Linux Docker does that cleanly. A cv2.VideoCapture(index) inside the
    container would therefore work in local dev on Linux and then silently
    fail to find a camera on stage, which is the single worst failure mode
    for a live demo: something that worked in every rehearsal and breaks
    only in front of the audience.

    The browser already has camera access (Calibration's own connect-camera
    step proves getUserMedia works in this app), and a browser tab isn't
    sandboxed behind the container's device isolation at all -- so pushing
    JPEG frames from the browser to a small REST endpoint sidesteps the
    passthrough problem entirely, works identically in dev and on stage,
    and reuses UX the team already built. The tradeoff is an extra HTTP
    hop and JPEG re-encode per frame versus a raw in-process camera read;
    at anaesthesia-monitor refresh rates (~1 Hz, not 30fps video) that cost
    is immaterial.

CameraSource.stream() itself does NOT call reconcile(), persist, or touch
alerts -- exactly like ReplaySource, it only yields Frame objects. All of
that happens once, in app.ws.vitals.send_loop, for every source uniformly.
"""

import asyncio
import io
import time
from typing import AsyncIterator, Optional

import numpy as np
from PIL import Image, UnidentifiedImageError

from app.pipeline.ocr import OcrEngine
from app.pipeline.read_frame import RoiExtractor, read_frame
from app.sources.base import Frame, VitalsSource
from app.sources.frame_queue import get_latest_frame


def _provenance_for_confidence(mean_confidence: float) -> str:
    if mean_confidence >= 80:
        return "ai_high"
    if mean_confidence >= 50:
        return "ai_medium"
    return "ai_low"


class CameraSource(VitalsSource):
    def __init__(
        self,
        channel: str,
        interval: float = 1.0,
        engine: Optional[OcrEngine] = None,
        roi_extractor: Optional[RoiExtractor] = None,
        skip_screen_detection: bool = False,
    ):
        """roi_extractor (M5.2): optional override passed straight through
        to read_frame(). None (the default) keeps M5.1's exact behaviour --
        read_frame() falls back to its own ROI_ENGINE-selected default
        (Tier-1 colour ROI unless overridden). app.ws.vitals binds this to
        the caller's active CalibrationProfile, if one exists, before
        constructing a CameraSource -- see
        app.ws.vitals._camera_roi_extractor.

        skip_screen_detection (M5.7.1): passed straight through to
        read_frame() too. True whenever `roi_extractor` is a calibrated
        (operator-drawn-box) extractor -- see read_frame()'s own docstring
        for why detect_screen() must not run ahead of one. False (default)
        for the uncalibrated/default-ROI_ENGINE path, unchanged."""
        self.channel = channel
        self.interval = interval
        self.engine = engine
        self.roi_extractor = roi_extractor
        self.skip_screen_detection = skip_screen_detection

    async def stream(self) -> AsyncIterator[Frame]:
        last_seq = -1
        while True:
            item = get_latest_frame(self.channel)

            if item is not None and item.seq != last_seq:
                last_seq = item.seq
                # read_frame() is synchronous CPU-bound work (OpenCV +
                # a Tesseract subprocess per vital) that measures 0.7-1.5s
                # per frame in practice -- run it off the event loop via
                # to_thread(), not inline, or one camera tick would freeze
                # every other concurrent WS connection (other sessions'
                # synthetic streams, ping/pong, everything) for that whole
                # 0.7-1.5s. ReplaySource's "pipeline" mode has this same gap;
                # worth the identical fix there.
                frame = await asyncio.to_thread(self._read, item.data, last_seq)
                if frame is not None:
                    yield frame

            await asyncio.sleep(self.interval)

    def _read(self, data: bytes, seq: int) -> Optional[Frame]:
        """Runs the real pipeline on one pushed frame. Returns None (skip
        this tick, never crash the stream) on a corrupt/undecodable frame --
        a dropped webcam frame is routine, not exceptional."""
        try:
            img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
        except (UnidentifiedImageError, OSError):
            return None

        # M5.4.1: crop_integrity is a mutable dict read_frame() fills in as
        # it goes (same in-place-output pattern reconcile()'s temporal_state
        # already uses) -- costs one extra, already-computed OCR text
        # comparison per vital, no extra Tesseract invocation. Only the live
        # camera path opts in; ReplaySource/synthetic sources never call
        # read_frame() with this at all, so Frame.crop_suspicious defaults
        # to {} for them.
        crop_integrity: dict = {}
        reading, confidences = read_frame(
            img,
            engine=self.engine,
            roi_extractor=self.roi_extractor,
            crop_integrity=crop_integrity,
            skip_screen_detection=self.skip_screen_detection,
        )
        reading["timestamp"] = int(time.time() * 1000)

        mean_confidence = sum(confidences.values()) / len(confidences) if confidences else 0.0
        return Frame(
            reading=reading,
            per_vital_confidence=confidences,
            provenance=_provenance_for_confidence(mean_confidence),
            source_frame_id=f"{self.channel}-{seq}",
            crop_suspicious=crop_integrity,
        )
