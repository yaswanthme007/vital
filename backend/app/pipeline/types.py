from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# Axis-aligned pixel box: (x, y, w, h). Matches the simulator's ground-truth
# ROI contract (simulator/render/*, simulator/randomize/augment.py) so results
# from this pipeline can be compared/overlaid directly against it.
Box = Tuple[int, int, int, int]


@dataclass
class VitalRoiResult:
    """One vital's located region, ready for OCR in S5.

    Originally colour-only (S4/S5); extended for M3 Tier-2 (see
    app.pipeline.tier2_roi) rather than given a parallel type, per
    TIER2_RECOGNITION_SPIKE.md sec 10 ("Same VitalRoiResult; source_colour
    becomes optional"). The two new fields default so every existing
    construction site (app.pipeline.roi.extract_rois_by_colour) is
    unaffected and produces engine="tier1_colour" for free.
    """

    box: Box
    crop: np.ndarray
    source_colour: Optional[Tuple[int, int, int]] = None
    engine: str = "tier1_colour"  # or "tier2_fieldcnn" -- which stage located this region
    classifier_confidence: Optional[float] = None  # 0-100, FieldCNN's own
    # confidence for this crop's predicted vital class. None for tier1_colour
    # (there is no classifier in that path). Tier-2 sets this so read_frame()
    # can fuse it with OCR confidence via MIN, never average (see
    # TIER2_RECOGNITION_SPIKE.md sec 07).


@dataclass
class ScreenDetectionResult:
    """Output of detect_screen(). `image` is always usable downstream: either
    the perspective-rectified screen, or — when no confident quad was found —
    the original image unchanged (see `detected`)."""

    image: np.ndarray
    detected: bool
    homography: Optional[np.ndarray] = None
    corners: Optional[np.ndarray] = None
