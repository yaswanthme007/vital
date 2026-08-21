"""M4.1 + M4.2: current-OCR baseline + OCR-preprocessing-variant experiment.

ISOLATED FROM PRODUCTION: imports app.pipeline.detect, app.pipeline.
tier2_roi, app.pipeline.field_classifier, app.pipeline.ocr READ-ONLY (never
modified) to reproduce the exact Tier-2 candidate-generation -> FieldCNN ->
candidate-selection decision production would make, then experiments ONLY
with what happens to the one selected crop once it reaches OCR. Writes only
under app/eval/tier2_data/external_monitor_video/m4_ocr_report/.

Per-sample, per-vital pipeline (mirrors app.pipeline.tier2_roi.
extract_rois_by_field_classifier and app.eval.tier2_field_pipeline_eval.
evaluate_image, which this intentionally reuses the same logic/thresholds
from -- not a reimplementation with different behaviour):

  1. detect_screen(img)                              [production, unchanged]
  2. adaptive_threshold_candidates_v2(screen.image)    [production, unchanged]
  3. FieldClassifierEngine.classify(all crops)         [production, unchanged]
  4. app.pipeline.tier2_roi._select_candidate_for_vital [production, unchanged]
     -- IMPORTED, not reimplemented, so the selection decision is guaranteed
     identical to what read_frame(ROI_ENGINE=tier2) would actually pick.

Failure taxonomy (mandatory per the M4 task) is assigned per (sample,
vital) BEFORE any OCR preprocessing choice is considered:

  E  ground_truth_unavailable    -- no GT value exists for this vital/sample
  A  candidate_generation_miss   -- GT exists, but no candidate box reaches
                                     IoU >= POS_IOU_THRESHOLD (0.3) against it
  B  fieldcnn_miss                -- a candidate at good IoU exists, but no
                                     candidate was classified as this vital
                                     at >= MIN_CLASSIFIER_CONFIDENCE
  C  candidate_selection_failure -- a correctly-classified, GT-matching
                                     candidate exists, but
                                     _select_candidate_for_vital picked a
                                     different one (or resolved to None,
                                     e.g. competing_candidates)
  D  reaches_ocr                 -- the correct candidate was selected;
                                     THIS is what M4.2's preprocessing
                                     variants are measured against.

Only category D crops are run through the preprocessing matrix -- that is
the entire point of the controlled-experiment requirement: every variant
sees the exact same crop, box, FieldCNN prediction and selection decision;
only pixel preprocessing before Tesseract changes.

Usage:
    python -m app.eval.m4_ocr_benchmark [--limit N] [--out DIR]
"""

import argparse
import json
import os
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from app.eval.harness import load_dataset
from app.eval.tier2_candidates import adaptive_threshold_candidates_v2
from app.eval.tier2_common import Box, iou, warp_box
from app.pipeline.detect import detect_screen
from app.pipeline.field_classifier import NOT_A_VITAL, FieldClassifierEngine, FieldPrediction, get_default_field_classifier
from app.pipeline.ocr import (
    NibpValue,
    _DECIMAL_CONFIG,
    _DIGIT_CONFIG,
    _NIBP_CONFIG,
    _extract_tokens,
    _joined_text_and_confidence,
    _preprocess as production_preprocess,
    _split_text_lines,
    _locate_tesseract_binary,
)
from app.pipeline.tier2_roi import (
    DEDUPE_IOU,
    MIN_CLASSIFIER_CONFIDENCE,
    COMPETING_MARGIN,
    _clip_box,
    _select_candidate_for_vital,
)

import pytesseract
from pytesseract import Output

DATASET_DIR = "app/eval/tier2_data/external_monitor_video"
OUT_DIR = os.path.join(DATASET_DIR, "m4_ocr_report")
GT_VALUES_PATH = os.path.join(OUT_DIR, "m4_ground_truth_values.json")
POS_IOU_THRESHOLD = 0.3  # identical to app.eval.tier2_field_dataset.POS_IOU_THRESHOLD

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

resolved = _locate_tesseract_binary(None)
if resolved:
    pytesseract.pytesseract.tesseract_cmd = resolved


# ─── Preprocessing variants (Part C) ────────────────────────────────────
# Each variant is (name, fn(crop_rgb) -> grayscale/binary np.ndarray ready
# for pytesseract). ONLY this function differs between variants -- the
# candidate crop fed in, and the Tesseract *config string* used afterward
# per vital type, are identical across all variants (except the PSM-only
# variants below, which deliberately vary ONLY the config string on top of
# the CURRENT_BASELINE pixels, to isolate PSM's effect from preprocessing).


def _to_gray(crop: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop


def _normalize_polarity_to_black_on_white(binary: np.ndarray) -> np.ndarray:
    """Same generic majority-is-background heuristic app.pipeline.ocr's own
    _preprocess() uses (never GT-informed) -- glyph strokes are always a
    minority of a binarized crop's area."""
    white = int(np.count_nonzero(binary == 255))
    black = binary.size - white
    if black > white:
        binary = cv2.bitwise_not(binary)
    return binary


def v_current_baseline(crop: np.ndarray) -> Optional[np.ndarray]:
    return production_preprocess(crop)  # byte-for-byte production function, unmodified


def v_grayscale(crop: np.ndarray) -> Optional[np.ndarray]:
    return _to_gray(crop)


def v_upscale_2x(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    h, w = gray.shape[:2]
    return cv2.resize(gray, (max(1, w * 2), max(1, h * 2)), interpolation=cv2.INTER_CUBIC)


def v_upscale_3x(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    h, w = gray.shape[:2]
    return cv2.resize(gray, (max(1, w * 3), max(1, h * 3)), interpolation=cv2.INTER_CUBIC)


def v_contrast(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    return cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)


def v_otsu(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return _normalize_polarity_to_black_on_white(binary)


def v_adaptive_threshold(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    h, w = gray.shape[:2]
    block = max(3, (min(h, w) // 3) | 1)  # odd, scales with crop size
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, -3)
    return _normalize_polarity_to_black_on_white(binary)


def v_sharpen(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.0)
    return cv2.addWeighted(gray, 1.5, blurred, -0.5, 0)


def v_upscale_2x_sharpen(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    h, w = gray.shape[:2]
    up = cv2.resize(gray, (max(1, w * 2), max(1, h * 2)), interpolation=cv2.INTER_CUBIC)
    blurred = cv2.GaussianBlur(up, (0, 0), sigmaX=2.0)
    return cv2.addWeighted(up, 1.5, blurred, -0.5, 0)


def v_upscale_2x_adaptive(crop: np.ndarray) -> Optional[np.ndarray]:
    gray = _to_gray(crop)
    h, w = gray.shape[:2]
    up = cv2.resize(gray, (max(1, w * 2), max(1, h * 2)), interpolation=cv2.INTER_CUBIC)
    block = max(3, (min(up.shape[:2]) // 3) | 1)
    binary = cv2.adaptiveThreshold(up, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, -3)
    return _normalize_polarity_to_black_on_white(binary)


PREPROCESS_VARIANTS: List[Tuple[str, callable]] = [
    ("CURRENT_BASELINE", v_current_baseline),
    ("GRAYSCALE", v_grayscale),
    ("UPSCALE_2X", v_upscale_2x),
    ("UPSCALE_3X", v_upscale_3x),
    ("CONTRAST", v_contrast),
    ("OTSU", v_otsu),
    ("ADAPTIVE_THRESHOLD", v_adaptive_threshold),
    ("SHARPEN", v_sharpen),
    ("UPSCALE_2X_SHARPEN", v_upscale_2x_sharpen),
    ("UPSCALE_2X_ADAPTIVE", v_upscale_2x_adaptive),
]

# PSM-only variants: CURRENT_BASELINE pixels (production _preprocess,
# unmodified), only the Tesseract config string's --psm value changes, to
# isolate PSM's effect from preprocessing. Production already uses PSM 8
# for scalar digits (chosen per ocr.py's own comment: PSM 7 systematically
# misreads a leading "7" as "1" on this font) and PSM 6 for NIBP -- those
# ARE "CURRENT_BASELINE" in the main matrix above, so this section only
# adds the modes production does NOT already use, to see whether they do
# better or reproduce the same known failure.
def _psm_config(psm: int, whitelist: str) -> str:
    return f"--psm {psm} -c tessedit_char_whitelist={whitelist}"


PSM_VARIANTS: List[Tuple[str, int]] = [
    ("PSM6", 6),
    ("PSM7", 7),
    ("PSM10", 10),
]


# ─── OCR execution over a preprocessed image (mirrors TesseractEngine's own
# parsing exactly -- imported helpers, not reimplemented) ──────────────────


def _run_ocr(image: np.ndarray, config: str) -> dict:
    try:
        return pytesseract.image_to_data(image, config=config, output_type=Output.DICT)
    except pytesseract.TesseractError:
        return {"text": [], "conf": []}


def _read_scalar_from_processed(processed: np.ndarray, config: str, decimal: bool) -> Tuple[Optional[float], float, str, float]:
    """Returns (value, confidence, raw_text, tesseract_latency_seconds)."""
    t0 = time.perf_counter()
    data = _run_ocr(processed, config)
    latency = time.perf_counter() - t0
    tokens = _extract_tokens(data)
    text, confidence = _joined_text_and_confidence(tokens)
    if not text:
        return None, 0.0, text, latency
    import re

    pattern = r"\d+\.\d+" if decimal else r"\d+"
    match = re.search(pattern, text)
    if not match:
        match = re.search(r"\d+", text)
        if not match:
            return None, confidence, text, latency
    try:
        value = float(match.group())
    except ValueError:
        return None, confidence, text, latency
    return value, confidence, text, latency


def _read_nibp_from_processed(processed: np.ndarray, config: str) -> Tuple[NibpValue, float, str, float]:
    import re

    t0 = time.perf_counter()
    lines = _split_text_lines(processed)
    did_split = len(lines) >= 2
    if not lines:
        lines = [processed]

    line_reads = []
    for line_img in lines:
        data = _run_ocr(line_img, config)
        tokens = _extract_tokens(data)
        text, confidence = _joined_text_and_confidence(tokens, sep=" ")
        if text:
            line_reads.append((text, confidence, line_img.shape[0]))
    latency = time.perf_counter() - t0

    systolic = diastolic = mean = None
    confidences: List[float] = []
    raw_texts = [t for t, _c, _h in line_reads]

    sys_dia_line = next((t for t in line_reads if "/" in t[0]), None)
    if sys_dia_line is None and not did_split and line_reads:
        sys_dia_line = line_reads[0]

    if sys_dia_line:
        match = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", sys_dia_line[0])
        if match:
            systolic = float(match.group(1))
            diastolic = float(match.group(2))
            confidences.append(sys_dia_line[1])

    remaining = [t for t in line_reads if t is not sys_dia_line]
    for text, confidence, _h in remaining:
        match = re.search(r"\d{2,3}", text)
        if match:
            mean = float(match.group())
            confidences.append(confidence)
            break

    value = NibpValue(systolic=systolic, diastolic=diastolic, mean=mean)
    overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return value, overall_confidence, " | ".join(raw_texts), latency


def ocr_variant(crop: np.ndarray, vital: str, preprocess_fn, config_override: Optional[str] = None):
    """Runs ONE preprocessing variant end to end on ONE crop, returns a dict
    record with everything the M4 spec's per-record schema asks for. This is
    the single seam where preprocessing/config choice enters -- everything
    upstream (which crop, which box) is fixed before this is ever called."""
    t0 = time.perf_counter()
    processed = preprocess_fn(crop)
    preprocess_latency = time.perf_counter() - t0

    if processed is None:
        return {
            "raw_text": "",
            "value": None,
            "confidence": 0.0,
            "preprocess_latency_s": preprocess_latency,
            "tesseract_latency_s": 0.0,
        }

    if vital == "nibp":
        config = config_override or _NIBP_CONFIG
        value, confidence, raw_text, tess_latency = _read_nibp_from_processed(processed, config)
    elif vital == "temp":
        config = config_override or _DECIMAL_CONFIG
        value, confidence, raw_text, tess_latency = _read_scalar_from_processed(processed, config, decimal=True)
    else:
        config = config_override or _DIGIT_CONFIG
        value, confidence, raw_text, tess_latency = _read_scalar_from_processed(processed, config, decimal=False)

    return {
        "raw_text": raw_text,
        "value": value.__dict__ if isinstance(value, NibpValue) else value,
        "confidence": confidence,
        "config": config,
        "preprocess_latency_s": preprocess_latency,
        "tesseract_latency_s": tess_latency,
    }


# ─── Pipeline replay: candidate gen -> FieldCNN -> selection (Part A / harness) ──


def _gt_box_for_vital(label: dict, vital: str, screen) -> Optional[Box]:
    raw = label.get("rois", {}).get(vital)
    if not raw or raw[2] <= 0 or raw[3] <= 0:
        return None
    box: Box = tuple(raw)
    if screen.detected and screen.homography is not None:
        box = warp_box(box, screen.homography)
    return box


def process_sample(png_path: str, label: dict, classifier: FieldClassifierEngine, gt_values: dict) -> dict:
    img = np.array(Image.open(png_path).convert("RGB"))
    screen = detect_screen(img)
    work_img = screen.image
    h, w = work_img.shape[:2]

    raw_candidates = adaptive_threshold_candidates_v2(work_img)
    boxes: List[Tuple[int, int, int, int]] = []
    crops: List[np.ndarray] = []
    for box in raw_candidates:
        clipped = _clip_box(box, w, h)
        if clipped is None:
            continue
        x, y, bw, bh = clipped
        crops.append(work_img[y : y + bh, x : x + bw])
        boxes.append(clipped)

    predictions = classifier.classify(crops)

    per_vital_candidates: Dict[str, List[Tuple[Tuple[int, int, int, int], FieldPrediction]]] = {v: [] for v in VITALS}
    all_records = []
    for box, pred in zip(boxes, predictions):
        all_records.append({"box": box, "pred_label": pred.label, "pred_confidence": pred.confidence})
        if pred.label == NOT_A_VITAL or pred.label not in per_vital_candidates:
            continue
        if pred.confidence < MIN_CLASSIFIER_CONFIDENCE:
            continue
        per_vital_candidates[pred.label].append((box, pred))

    per_vital_out = {}
    for vital in VITALS:
        gt_box = _gt_box_for_vital(label, vital, screen)
        vital_gt = gt_values.get(vital)

        if gt_box is None or vital_gt is None:
            per_vital_out[vital] = {"category": "E", "reason": "ground_truth_unavailable"}
            continue

        # Category A check: did ANY raw candidate (regardless of predicted
        # label) reach IoU >= POS_IOU_THRESHOLD against this GT box?
        best_iou_any = 0.0
        for box, _pred in zip(boxes, predictions):
            v = iou(tuple(map(float, box)), tuple(map(float, gt_box)))
            if v > best_iou_any:
                best_iou_any = v
        if best_iou_any < POS_IOU_THRESHOLD:
            per_vital_out[vital] = {"category": "A", "reason": "candidate_generation_miss", "best_iou_any_candidate": best_iou_any}
            continue

        # Category B check: among candidates that overlap GT well, was ANY
        # of them correctly classified as this vital at/above threshold?
        correct_gt_matches = [
            (box, pred)
            for box, pred in per_vital_candidates[vital]
            if iou(tuple(map(float, box)), tuple(map(float, gt_box))) >= POS_IOU_THRESHOLD
        ]
        if not correct_gt_matches:
            per_vital_out[vital] = {"category": "B", "reason": "fieldcnn_miss", "best_iou_any_candidate": best_iou_any}
            continue

        # Category C check: production's OWN selection function -- was the
        # correct (GT-matching) candidate actually the one selected?
        box, pred, select_reason = _select_candidate_for_vital(per_vital_candidates[vital])
        if box is None or pred is None:
            per_vital_out[vital] = {"category": "C", "reason": select_reason or "unresolved"}
            continue
        selected_iou = iou(tuple(map(float, box)), tuple(map(float, gt_box)))
        if selected_iou < POS_IOU_THRESHOLD:
            per_vital_out[vital] = {"category": "C", "reason": "wrong_candidate_selected", "selected_iou": selected_iou}
            continue

        # Category D: correct candidate reached OCR. Run every preprocessing
        # variant on this EXACT crop.
        x, y, bw, bh = box
        crop = work_img[y : y + bh, x : x + bw].copy()
        variant_results = {}
        for name, fn in PREPROCESS_VARIANTS:
            variant_results[name] = ocr_variant(crop, vital, fn)
        # PSM-only variants: CURRENT_BASELINE pixels, alternate PSM configs.
        baseline_processed = v_current_baseline(crop)
        if baseline_processed is not None:
            for name, psm in PSM_VARIANTS:
                if vital == "nibp":
                    cfg = _psm_config(psm, "0123456789/")
                    t0 = time.perf_counter()
                    value, confidence, raw_text, tess_latency = _read_nibp_from_processed(baseline_processed, cfg)
                    pre_lat = 0.0
                elif vital == "temp":
                    cfg = _psm_config(psm, "0123456789.")
                    value, confidence, raw_text, tess_latency = _read_scalar_from_processed(baseline_processed, cfg, decimal=True)
                    pre_lat = 0.0
                else:
                    cfg = _psm_config(psm, "0123456789")
                    value, confidence, raw_text, tess_latency = _read_scalar_from_processed(baseline_processed, cfg, decimal=False)
                    pre_lat = 0.0
                variant_results[name] = {
                    "raw_text": raw_text,
                    "value": value.__dict__ if isinstance(value, NibpValue) else value,
                    "confidence": confidence,
                    "config": cfg,
                    "preprocess_latency_s": pre_lat,
                    "tesseract_latency_s": tess_latency,
                }

        per_vital_out[vital] = {
            "category": "D",
            "reason": "reaches_ocr",
            "box": list(box),
            "fieldcnn_label": pred.label,
            "fieldcnn_confidence": pred.confidence,
            "selected_iou": selected_iou,
            "ground_truth": vital_gt,
            "variants": variant_results,
        }

    return {"id": label.get("id"), "screen_detected": screen.detected, "per_vital": per_vital_out}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=DATASET_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=OUT_DIR)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    with open(GT_VALUES_PATH) as f:
        gt_all = json.load(f)["values"]

    samples = load_dataset(args.dataset)
    samples = samples[args.start :]
    if args.limit:
        samples = samples[: args.limit]

    classifier = get_default_field_classifier()

    results = []
    started = time.time()
    for i, s in enumerate(samples):
        sample_gt = gt_all.get(s["id"], {})
        r = process_sample(s["png_path"], s["label"], classifier, sample_gt)
        results.append(r)
        print(f"[{i+1}/{len(samples)}] {s['id']}: " + ", ".join(f"{v}={r['per_vital'][v]['category']}" for v in VITALS))

    elapsed = time.time() - started
    out_path = os.path.join(args.out, f"m4_raw_results_start{args.start}.json")

    def _default(o):
        if isinstance(o, (tuple, list)):
            return list(o)
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        return str(o)

    with open(out_path, "w") as f:
        json.dump({"results": results, "elapsed_seconds": elapsed}, f, indent=2, default=_default)
    print(f"\nWrote {out_path} ({len(results)} samples, {elapsed:.1f}s)")


if __name__ == "__main__":
    main()
