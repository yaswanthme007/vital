from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# Axis-aligned pixel box: (x, y, w, h). Matches the simulator's ground-truth
# ROI contract (simulator/render/*, simulator/randomize/augment.py) so results
# from this pipeline can be compared/overlaid directly against it.
Box = Tuple[int, int, int, int]


@dataclass
class VitalRoiResult:
    """One vital's colour-detected region, ready for OCR in S5."""

    box: Box
    crop: np.ndarray
    source_colour: Tuple[int, int, int]


@dataclass
class ScreenDetectionResult:
    """Output of detect_screen(). `image` is always usable downstream: either
    the perspective-rectified screen, or — when no confident quad was found —
    the original image unchanged (see `detected`)."""

    image: np.ndarray
    detected: bool
    homography: Optional[np.ndarray] = None
    corners: Optional[np.ndarray] = None
