"""M1 POC: two colour-agnostic candidate-region generators, per
TIER2_RECOGNITION_SPIKE.md section 03/04.

Both are isolated proof-of-concepts for app.eval.tier2_benchmark to score --
NEITHER is wired into app.pipeline.read_frame(). Production ROI extraction
is still exclusively app.pipeline.roi.extract_rois_by_colour(); nothing here
changes that.

Both operate purely on luminance/edges (never colour) and return a flat,
unlabelled list of candidate boxes -- candidate generation doesn't know
"which vital", only "this looks like it might be a numeric readout". That
per-box vital assignment is the (not-yet-built) Tier-2 field classifier's
job, per the spike's hybrid architecture (section 04/06).
"""

from typing import List, Tuple

import cv2
import numpy as np

from app.eval.tier2_common import Box, iou

# Minimum glyph-stroke size in pixels -- drops antialiasing/JPEG noise specks,
# mirrors app.pipeline.roi's MIN_AREA_PIXELS in spirit (that one is colour-
# mask pixel count; here it's the box's raw width/height).
_MIN_BOX_W = 4
_MIN_BOX_H = 6


def _dedupe_boxes(boxes: List[Box], iou_thresh: float = 0.7) -> List[Box]:
    """Merge near-duplicate boxes (e.g. found by both threshold polarities)
    by keeping the larger box whenever two overlap heavily."""
    ordered = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    kept: List[Box] = []
    for b in ordered:
        if not any(iou(b, k) > iou_thresh for k in kept):
            kept.append(b)
    return kept


def _components_to_boxes(
    raw_mask: np.ndarray,
    dilated_mask: np.ndarray,
    min_count: int,
    min_fill_density: float,
    max_w: float,
    max_h: float,
) -> List[Box]:
    """Connected-components labeling on the dilated mask (so nearby glyph
    strokes/digits merge into one group), box measured from the RAW
    (non-dilated) mask so it stays tight -- identical strategy to
    app.pipeline.roi.extract_rois_by_colour, just without the colour gate."""
    num_labels, labels = cv2.connectedComponents(dilated_mask)
    raw_bool = raw_mask > 0

    boxes: List[Box] = []
    for label in range(1, num_labels):
        group = raw_bool & (labels == label)
        count = int(np.count_nonzero(group))
        if count < min_count:
            continue
        ys, xs = np.where(group)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if bw < _MIN_BOX_W or bh < _MIN_BOX_H:
            continue
        if bw > max_w or bh > max_h:
            continue
        density = count / (bw * bh)
        if density < min_fill_density:
            continue
        boxes.append((float(x0), float(y0), float(bw), float(bh)))
    return boxes


def adaptive_threshold_candidates(
    img: np.ndarray,
    block_size: int = 25,
    c: int = -10,
    min_fill_density: float = 0.08,
    min_area_frac: float = 0.00006,
    max_w_frac: float = 0.6,
    max_h_frac: float = 0.4,
) -> List[Box]:
    """Adaptive-threshold + connected-components candidate generator.

    Colour-agnostic: works on luminance only. A real device's text/background
    polarity (bright-on-dark vs dark-on-bright) isn't known in advance, so
    both are thresholded and merged rather than assuming one.
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    total_area = h * w
    min_count = max(20, int(total_area * min_area_frac))

    # Same size-relative dilation kernel roi.py uses, for the same reason:
    # merge a multi-digit number's separate glyphs into one component
    # without also swallowing unrelated neighbouring text.
    kernel_size = max(3, int(min(h, w) * 0.015))
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    bs = block_size if block_size % 2 == 1 else block_size + 1

    boxes: List[Box] = []
    for thresh_type in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
        mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, thresh_type, bs, c)
        dilated = cv2.dilate(mask, kernel, iterations=1)
        boxes.extend(
            _components_to_boxes(mask, dilated, min_count, min_fill_density, w * max_w_frac, h * max_h_frac)
        )
    return _dedupe_boxes(boxes)


def canny_contour_candidates(
    img: np.ndarray,
    canny_lo: int = 50,
    canny_hi: int = 150,
    min_fill_density: float = 0.06,
    min_area_frac: float = 0.00006,
    max_w_frac: float = 0.6,
    max_h_frac: float = 0.4,
) -> List[Box]:
    """Canny edges + dilate + contour candidate generator. Same edge-detection
    family app.pipeline.detect.detect_screen() already uses one layer up, per
    the spike's observation that this family is already proven on this
    codebase's content."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, canny_lo, canny_hi)

    kernel_size = max(3, int(min(h, w) * 0.015))
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=2)

    total_area = h * w
    min_count = max(20, int(total_area * min_area_frac))
    max_w, max_h = w * max_w_frac, h * max_h_frac

    # RETR_LIST, not RETR_EXTERNAL: a rectified crop can retain a thin sliver
    # of bezel/frame right at its own border (detect_screen locked onto the
    # room-vs-bezel edge rather than the bezel-vs-screen edge, or the camera
    # framing simply includes a hair of bezel). That sliver's own edge forms
    # one big contour enclosing everything else -- RETR_EXTERNAL would then
    # return ONLY that outer frame and silently drop every legitimate
    # interior glyph contour nested inside it. RETR_LIST returns every
    # contour regardless of nesting, matching adaptive_threshold_candidates'
    # connectedComponents call (which has no such "outermost only" limit).
    contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes: List[Box] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < _MIN_BOX_W or bh < _MIN_BOX_H or bw > max_w or bh > max_h:
            continue
        area = bw * bh
        if area < min_count:
            continue
        # Canny only marks outlines, not fills -- density here is "share of
        # the dilated-edge mask inside this box", which still separates
        # dense glyph clusters from one stray edge segment.
        crop = dilated[y : y + bh, x : x + bw]
        density = cv2.countNonZero(crop) / area
        if density < min_fill_density:
            continue
        boxes.append((float(x), float(y), float(bw), float(bh)))
    return _dedupe_boxes(boxes)


GENERATORS = {
    "adaptive_threshold": adaptive_threshold_candidates,
    "canny_contour": canny_contour_candidates,
}


# ─── M1.1 hardening: v2 generators ─────────────────────────────────────────
#
# v1 above is left completely unchanged (byte-for-byte) so the original M1
# results stay reproducible by calling the same functions. Everything below
# is additive. Root causes were established by inspecting actual candidate
# boxes/masks on the real external-video dataset (see
# TIER2_M1_1_HARDENING_REPORT.md), not guessed:
#
#   1. etCO2 (and other) misses: a thin, long gridline/baseline in the raw
#      threshold mask (measured: 1286x5px on one frame) survives thresholding
#      and, after dilation, bridges two otherwise-unrelated UI regions into
#      one oversized blob that then fails the max-size filter -- losing
#      BOTH regions, not just shrinking the box. Fix: strip line-shaped raw
#      components (extreme aspect ratio, spanning a large fraction of the
#      frame in one dimension) before dilation ever runs.
#   2. NIBP: candidates land on the right area (best IoU ~0.22-0.26, not
#      near-zero) but under-cover its two stacked lines (sys/dia, mean).
#      First hypothesis was "two separate boxes that need a merge pass" --
#      built `_merge_vertically_stacked` for exactly that (still defined
#      below, kept for the record). Measured result: fix 1 (line-stripping)
#      ALONE already gets NIBP to 82.4% recall (0.0%->82.4%, full dataset)
#      with no merge pass at all -- the real fix was removing the
#      line-artifact bridge, not adding a merge step. Adding
#      `_merge_vertically_stacked` on top was then tested and made things
#      WORSE (see its docstring) -- it is built but deliberately NOT called
#      by either v2 generator.
#   3. HR/SpO2 (large bold digits): near-miss IoUs (~0.17-0.30) consistent
#      with a multi-digit number fragmenting into 2+ components -- measured
#      directly (sample_0036's "183" split into 3 separate ~90px-wide
#      components with 18-38px gaps between them) against a merge-dilation
#      kernel whose real per-side reach is only ~(kernel_size-1)/2 ~= 8.5px,
#      short of both gaps. Fix: widen the merge-dilation kernel
#      HORIZONTALLY only (anisotropic, height unchanged) -- swept width
#      multipliers 1.0-3.0 against the real dataset (not guessed): 1.5x is
#      the sweet spot (HR 58.1%->93.0%, SpO2 85.2%->88.9% recall on the
#      full 52-image set); anything above ~2.0x starts bridging into
#      unrelated neighbouring content and recall collapses again. An
#      earlier per-component "widen large blobs by up to 80px" attempt was
#      tried first and made every vital WORSE (verified, not assumed) by
#      bridging HR's giant digit into the ECG waveform -- replaced by this
#      much smaller, uniform, swept value instead of chasing that approach
#      further.
#   4. False positives: line-stripping (fix 1) already removes some. A
#      final aspect-ratio cap on the finished candidate list catches
#      leftover banner/toolbar/waveform-panel-shaped survivors that weren't
#      pure thin lines -- every real vital box measured on this dataset has
#      an aspect ratio between ~0.5 and ~2.4 (see the annotation validation
#      report); the cap here (5:1) is deliberately looser than that
#      measured range so it only removes clearly non-glyph shapes.


def _strip_line_artifacts(mask: np.ndarray, max_aspect: float = 8.0, min_span_frac: float = 0.2) -> np.ndarray:
    """Zero out raw (pre-dilation) connected components that are thin AND
    long relative to the frame -- gridlines, baselines, separators. A glyph
    cluster is never simultaneously this thin and this long; a UI line
    always is. General shape-based filter, not tuned to any fixed
    coordinates -- reruns per-frame against that frame's own mask."""
    h, w = mask.shape[:2]
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    cleaned = mask.copy()
    for lbl in range(1, num_labels):
        x, y, cw, ch, _area = stats[lbl]
        is_h_line = cw > max_aspect * max(ch, 1) and cw > min_span_frac * w
        is_v_line = ch > max_aspect * max(cw, 1) and ch > min_span_frac * h
        if is_h_line or is_v_line:
            cleaned[labels == lbl] = 0
    return cleaned


def _merge_vertically_stacked(boxes: List[Box], max_gap_ratio: float = 0.9, min_x_overlap_ratio: float = 0.3) -> List[Box]:
    """Merge candidate boxes that sit directly above/below each other with a
    small vertical gap and overlapping horizontal extent -- the general
    "these text lines belong to one multi-line block" heuristic, built to
    fix NIBP's two-line split (sys/dia + mean).

    NOT CALLED BY EITHER v2 GENERATOR -- kept only for the record. Measured
    on the full 52-image real-video dataset: line-stripping alone already
    solves NIBP (0.0%->82.4%) without this pass. Adding this pass on top,
    swept across max_gap_ratio in {0.9, 0.5, 0.3, 0.15}, made OVERALL
    recall WORSE at every single setting tested (e.g. adaptive-threshold:
    90.5% with no merge pass vs. 50.8%/57.3%/58.8%/78.9% at those four gap
    ratios) by incorrectly bridging already-correct boxes (Temp measured
    directly: a clean 0.66 IoU candidate collapsed to 0.03 after merging
    with an unrelated nearby component) -- worse than doing nothing, at
    every tested parameter, on every generator it was tried on (Canny was
    hit even harder). A geometric heuristic that sounds reasonable and
    helped its target case in isolated spot-checks still needs a full-
    dataset sweep before being trusted; this function is the artifact of
    that check failing, not passing."""
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ax, ay, aw, ah = boxes[i]
                bx, by, bw, bh = boxes[j]
                ox0, ox1 = max(ax, bx), min(ax + aw, bx + bw)
                x_overlap = max(0.0, ox1 - ox0)
                min_w = min(aw, bw)
                if min_w <= 0 or x_overlap / min_w < min_x_overlap_ratio:
                    continue
                if ay + ah <= by:
                    gap, ref_h = by - (ay + ah), min(ah, bh)
                elif by + bh <= ay:
                    gap, ref_h = ay - (by + bh), min(ah, bh)
                else:
                    continue  # already vertically overlapping -- _dedupe_boxes handles that
                if gap <= max_gap_ratio * ref_h:
                    nx0, ny0 = min(ax, bx), min(ay, by)
                    nx1, ny1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
                    boxes[i] = (nx0, ny0, nx1 - nx0, ny1 - ny0)
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    return boxes


def _filter_extreme_aspect(boxes: List[Box], max_aspect: float = 5.0) -> List[Box]:
    """Drop finished candidates shaped nothing like a numeric readout --
    every ground-truth vital box measured on the real-video dataset has
    width/height between ~0.5 and ~2.4; 5:1 stays well clear of that
    measured range so it only removes banner/toolbar/waveform-panel shapes,
    never a legitimate (even multi-line-merged) value block."""
    kept = []
    for (x, y, w, h) in boxes:
        if h <= 0 or w <= 0:
            continue
        aspect = w / h
        if aspect > max_aspect or aspect < (1.0 / max_aspect):
            continue
        kept.append((x, y, w, h))
    return kept


def adaptive_threshold_candidates_v2(
    img: np.ndarray,
    block_size: int = 25,
    c: int = -10,
    min_fill_density: float = 0.08,
    min_area_frac: float = 0.00006,
    max_w_frac: float = 0.6,
    max_h_frac: float = 0.4,
    kernel_width_mult: float = 1.5,
) -> List[Box]:
    """Hardened adaptive-threshold generator -- see module-level comment
    block above for the fixes applied (line-stripping, anisotropic wider
    merge kernel, aspect filter) and the measured evidence behind each.
    `_merge_vertically_stacked` was tried and explicitly REJECTED here --
    see that function's docstring for why."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    total_area = h * w
    min_count = max(20, int(total_area * min_area_frac))

    kernel_size = max(3, int(min(h, w) * 0.015))
    kernel_w = max(kernel_size, int(kernel_size * kernel_width_mult))
    kernel = np.ones((kernel_size, kernel_w), np.uint8)  # (height, width) -- wider than tall on purpose
    bs = block_size if block_size % 2 == 1 else block_size + 1

    boxes: List[Box] = []
    for thresh_type in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
        mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, thresh_type, bs, c)
        mask = _strip_line_artifacts(mask)
        dilated = cv2.dilate(mask, kernel, iterations=1)
        boxes.extend(
            _components_to_boxes(mask, dilated, min_count, min_fill_density, w * max_w_frac, h * max_h_frac)
        )
    boxes = _dedupe_boxes(boxes)
    boxes = _filter_extreme_aspect(boxes)
    return boxes


def canny_contour_candidates_v2(
    img: np.ndarray,
    canny_lo: int = 50,
    canny_hi: int = 150,
    min_fill_density: float = 0.06,
    min_area_frac: float = 0.00006,
    max_w_frac: float = 0.6,
    max_h_frac: float = 0.4,
    kernel_width_mult: float = 1.0,
) -> List[Box]:
    """Hardened Canny+contour generator -- line-stripping only (measured:
    42.7%->43.7% overall recall, no regression on any vital). The adaptive
    generator's kernel-widening fix does NOT transfer here and is
    deliberately NOT applied (default kernel_width_mult=1.0, i.e.
    unchanged): swept 1.0/1.2/1.5 against the full real-video dataset and
    ANY widening collapsed hr/spo2/rr recall to ~0% (measured, not
    guessed) -- Canny already dilates with iterations=2 (double the
    adaptive generator's iterations=1), so its effective merge reach is
    already much larger; widening on top of that over-merges into
    unrelated neighbouring content instead of just closing digit-kerning
    gaps. Same lesson as `_merge_vertically_stacked` below: a fix that
    measurably helps one generator can measurably hurt the other -- always
    verify both, not just the one the fix was designed for."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, canny_lo, canny_hi)
    edges = _strip_line_artifacts(edges, max_aspect=8.0, min_span_frac=0.2)

    kernel_size = max(3, int(min(h, w) * 0.015))
    kernel_w = max(kernel_size, int(kernel_size * kernel_width_mult))
    kernel = np.ones((kernel_size, kernel_w), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=2)

    total_area = h * w
    min_count = max(20, int(total_area * min_area_frac))
    max_w, max_h = w * max_w_frac, h * max_h_frac

    contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes: List[Box] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < _MIN_BOX_W or bh < _MIN_BOX_H or bw > max_w or bh > max_h:
            continue
        area = bw * bh
        if area < min_count:
            continue
        crop = dilated[y : y + bh, x : x + bw]
        density = cv2.countNonZero(crop) / area
        if density < min_fill_density:
            continue
        boxes.append((float(x), float(y), float(bw), float(bh)))
    boxes = _dedupe_boxes(boxes)
    boxes = _filter_extreme_aspect(boxes)
    return boxes


GENERATORS_V2 = {
    "adaptive_threshold": adaptive_threshold_candidates_v2,
    "canny_contour": canny_contour_candidates_v2,
}
