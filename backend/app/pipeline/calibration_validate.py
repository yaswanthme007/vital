"""M5.2 Phase 7: deterministic calibration-quality checks, run server-side
(app.api.calibration.save_profile) before a profile is allowed to persist.
Pure geometry -- no OCR here. OCR sanity is a separate signal, the Verify
step (app.api.calibration.verify_candidate + CalibrationFieldMeta.verified),
folded into validate_profile() below only as "was this field actually
verified", not re-run. Two different failure modes need two different
checks: a box can be geometrically fine but never watched OCR correctly
(caught by the verified check here), or watched OCR correctly on ONE frame
by coincidence while still being a badly-placed box that will fail on the
next frame's slightly different digit count (caught by the geometry checks
here) -- see docs/M5_2_REAL_CALIBRATION_REPORT.md sec 8 for the "too tight"
/ "too generous" cases EVIDENCE.md sec 6.1 measured, which is exactly what
MIN_BOX_FRACTION/MAX_BOX_FRACTION below are set to catch.
"""

from typing import List

from app.models.calibration import CalibrationProfile, NormalizedBox

# A box smaller than this fraction of the frame's area is almost certainly
# a mis-click or a box drawn around a single momentary digit (EVIDENCE.md
# sec 6.1's "too tight" case: a box drawn around a single-digit '0' caps
# that field at 28% forever once the reading goes to 3 digits) rather than
# a deliberately-sized field slot.
MIN_BOX_FRACTION = 0.0008
MIN_DIMENSION_FRACTION = 0.01  # each side must span at least 1% of its axis
# A box this large is pulling in far more than one field -- EVIDENCE.md sec
# 6.1's "too generous" case measured accuracy DROPPING 71.5% -> 37.6% at 50%
# padding, by pulling in neighbouring text; 35% is a conservative ceiling
# well below that measured regression point.
MAX_BOX_FRACTION = 0.35
# Width:height (or its inverse) beyond this looks like a waveform trace or
# an entire panel row, not a single numeric value.
MAX_ASPECT_RATIO = 15.0
# Two fields' boxes overlapping more than this fraction of the smaller box
# are very likely the same physical region drawn twice, or a box that
# strayed onto its neighbour.
MAX_OVERLAP_FRACTION = 0.3
# Phase 7 "required-field completeness": a profile with zero drawn boxes is
# useless. Not fixed at 6 -- a monitor can genuinely omit a vital (Dataset B
# never displays NIBP at all, EVIDENCE.md sec 9), so the UI's 6-box target
# is UX guidance (Phase 2), not a hard server-side requirement.
REQUIRED_MIN_FIELDS = 1


def _box_errors(vital: str, box: NormalizedBox) -> List[str]:
    errors: List[str] = []
    if box.w <= 0 or box.h <= 0:
        return [f"{vital}: box has zero or negative size"]
    if box.x < -1e-6 or box.y < -1e-6:
        errors.append(f"{vital}: box origin is outside the frame")
    if box.x + box.w > 1.0 + 1e-6 or box.y + box.h > 1.0 + 1e-6:
        errors.append(f"{vital}: box extends outside the frame")

    area = box.w * box.h
    if area < MIN_BOX_FRACTION:
        errors.append(f"{vital}: box is too small ({area * 100:.2f}% of frame) — draw the field's full display slot")
    if area > MAX_BOX_FRACTION:
        errors.append(f"{vital}: box is too large ({area * 100:.0f}% of frame) — it likely includes neighbouring fields")
    if box.w < MIN_DIMENSION_FRACTION or box.h < MIN_DIMENSION_FRACTION:
        errors.append(f"{vital}: box is too thin")

    aspect = box.w / box.h if box.h else float("inf")
    if aspect > MAX_ASPECT_RATIO or aspect < 1 / MAX_ASPECT_RATIO:
        errors.append(f"{vital}: box aspect ratio ({aspect:.1f}:1) looks like a trace or full row, not a single field")
    return errors


def _overlap_fraction(a: NormalizedBox, b: NormalizedBox) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    iw, ih = max(0.0, x1 - x0), max(0.0, y1 - y0)
    inter = iw * ih
    smaller = min(a.w * a.h, b.w * b.h)
    return inter / smaller if smaller > 0 else 0.0


def validate_profile(profile: CalibrationProfile) -> List[str]:
    """Returns a list of human-readable errors; empty == valid. Never
    raises -- app.api.calibration decides whether to reject (422) based on
    the result. Order is stable (geometry, then overlap, then
    verification) so a caller displaying only the first error still shows
    the most actionable one first."""
    errors: List[str] = []

    if profile.reference_width <= 0 or profile.reference_height <= 0:
        errors.append("Invalid reference frame dimensions")

    if len(profile.roi_boxes) < REQUIRED_MIN_FIELDS:
        errors.append("At least one vital region must be drawn")

    for vital, box in profile.roi_boxes.items():
        errors.extend(_box_errors(vital, box))

    items = list(profile.roi_boxes.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (v1, b1), (v2, b2) = items[i], items[j]
            overlap = _overlap_fraction(b1, b2)
            if overlap > MAX_OVERLAP_FRACTION:
                errors.append(f"{v1} and {v2} boxes overlap {overlap * 100:.0f}% — they likely need separating")

    unverified = sorted(
        vital
        for vital in profile.roi_boxes
        if not profile.field_meta.get(vital, None) or not profile.field_meta[vital].verified
    )
    if unverified:
        errors.append(f"Unverified field(s): {', '.join(unverified)} — run Verify and confirm each value before saving")

    return errors
