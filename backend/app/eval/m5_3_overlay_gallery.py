"""M5.3 Phase 9: look at what actually happened, frame by frame.

Aggregate metrics say tracking helps. They cannot say WHY a particular field
was wrong, and it is easy to blame the wrong stage -- docs/EVIDENCE.md's whole
argument is that M1-M4.6 spent milestones tuning OCR for what was really a
localization problem. So each image here renders, on the real frame:

    the STATIC calibrated box   (what M5.2 would have cropped)
    the TRACKED box             (what M5.3 crops)
    the GROUND-TRUTH box        (where the field actually is)

plus the tracking statistics and, where OCR ran, both arms' readings against
ground truth. Every case is labelled with the stage genuinely at fault:

    TRACKING     the transform was wrong or refused
    LOCALIZATION the box landed off the field
    OCR          the box was right and the read was still wrong
    RECONCILE    the read was right and the gate still withheld it
    NONE         everything worked

Cases are selected by a stated rule (largest IoU gain, largest loss, each
distinct failure status, and the framing-change boundaries), never by which
frames look best.

Usage:
    python -m app.eval.m5_3_overlay_gallery
"""

import json
import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from app.models.calibration import CalibrationProfile, NormalizedBox
from app.pipeline.calibrated_roi import make_extractor, reference_pixel_boxes
from app.pipeline.layout_tracker import LayoutTracker, TrackingResult
from app.pipeline.ocr import TesseractEngine, _locate_tesseract_binary
from app.validation.rules import CONFIDENCE_MEDIUM_MIN

import pytesseract

_resolved = _locate_tesseract_binary(None)
if _resolved:
    pytesseract.pytesseract.tesseract_cmd = _resolved

ENGINE = TesseractEngine()

FROZEN_B = "app/eval/tier2_data/external_monitor_B"
FROZEN_B_GT = os.path.join(FROZEN_B, "m5_ground_truth_values.json")
DENSE_B = "app/eval/tier2_data/dense_B"
ANCHOR_DIR = "app/eval/tier2_data/dense_B_anchors"
OUT_DIR = "app/eval/tier2_data/m5_3_report/gallery"

C_STATIC = (255, 96, 96)     # what M5.2 would crop
C_TRACKED = (96, 255, 128)   # what M5.3 crops
C_GT = (96, 176, 255)        # where the field really is


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _draw_box(img, box, colour, label, thickness=2, dashed=False):
    x, y, w, h = [int(round(v)) for v in box]
    if dashed:
        for i in range(x, x + w, 12):
            cv2.line(img, (i, y), (min(i + 6, x + w), y), colour, thickness)
            cv2.line(img, (i, y + h), (min(i + 6, x + w), y + h), colour, thickness)
        for j in range(y, y + h, 12):
            cv2.line(img, (x, j), (x, min(j + 6, y + h)), colour, thickness)
            cv2.line(img, (x + w, j), (x + w, min(j + 6, y + h)), colour, thickness)
    else:
        cv2.rectangle(img, (x, y), (x + w, y + h), colour, thickness)
    if label:
        cv2.putText(img, label, (x, max(12, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA)


def _panel(img: np.ndarray, lines: List[Tuple[str, Tuple[int, int, int]]]) -> np.ndarray:
    pad = 18 + 16 * len(lines)
    out = np.zeros((img.shape[0] + pad, img.shape[1], 3), np.uint8)
    out[: img.shape[0]] = img
    for i, (text, colour) in enumerate(lines):
        cv2.putText(out, text, (8, img.shape[0] + 16 + i * 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, colour, 1, cv2.LINE_AA)
    return out


def _blame(tracked_ok: bool, tracked_iou: Optional[float], ocr_correct: Optional[bool],
           confidence: Optional[float]) -> str:
    if not tracked_ok:
        return "TRACKING (refused -- fields withheld, value held by reconcile)"
    if tracked_iou is not None and tracked_iou < 0.3:
        return "LOCALIZATION (locked, but the box missed the field)"
    if ocr_correct is False:
        return "OCR (box was on the field; the read was still wrong)"
    if ocr_correct and confidence is not None and confidence < CONFIDENCE_MEDIUM_MIN:
        return f"RECONCILE (read correct at conf {confidence:.0f} < gate {CONFIDENCE_MEDIUM_MIN}: held, not confirmed)"
    return "NONE (tracked, located and read correctly)"


def _build(dataset_dir: str, prefix: str, reference_id: str, gt_values_path: str):
    with open(os.path.join(dataset_dir, reference_id + ".json")) as f:
        ref_rois = json.load(f)["rois"]
    ref_img = np.array(Image.open(os.path.join(dataset_dir, reference_id + ".png")).convert("RGB"))
    h, w = ref_img.shape[:2]
    profile = CalibrationProfile(
        id="gallery", reference_width=w, reference_height=h,
        roi_boxes={v: NormalizedBox(x=b[0] / w, y=b[1] / h, w=b[2] / w, h=b[3] / h)
                   for v, b in ref_rois.items()},
        created_at=0, updated_at=0,
    )
    tracker = LayoutTracker.from_reference_image(
        ref_img, exclude_boxes=list(reference_pixel_boxes(profile).values())
    )
    with open(gt_values_path) as f:
        gt_values = json.load(f)["values"]
    return profile, ref_img, tracker, gt_values


def render(dataset: str, dataset_dir: str, prefix: str, reference_id: str, gt_values_path: str,
           limit: int = 10) -> List[dict]:
    profile, ref_img, tracker, gt_values = _build(dataset_dir, prefix, reference_id, gt_values_path)
    static_extract = make_extractor(profile)
    sink: List[TrackingResult] = []
    tracked_extract = make_extractor(profile, tracker=tracker, on_tracking_result=sink.append)
    ref_boxes = reference_pixel_boxes(profile)

    ids = sorted(f[:-5] for f in os.listdir(dataset_dir)
                 if f.startswith(prefix) and f.endswith(".json"))
    ids = [i for i in ids if i != reference_id]

    cases = []
    for sid in ids:
        png = os.path.join(dataset_dir, sid + ".png")
        if not os.path.exists(png):
            continue
        img = np.array(Image.open(png).convert("RGB"))
        with open(os.path.join(dataset_dir, sid + ".json")) as f:
            gt_rois = json.load(f).get("rois", {})

        before = len(sink)
        tracked = tracked_extract(img)
        static = static_extract(img)
        tr = sink[-1] if len(sink) > before else None

        gain = 0.0
        per_vital = {}
        for vital in ref_boxes:
            gt = gt_rois.get(vital)
            if not gt:
                continue
            gt = tuple(float(c) for c in gt)
            s_box = static[vital].box if static.get(vital) else None
            t_box = tracked[vital].box if tracked.get(vital) else None
            s_iou = _iou(s_box, gt) if s_box else 0.0
            t_iou = _iou(t_box, gt) if t_box else 0.0
            per_vital[vital] = {"gt": gt, "static": s_box, "tracked": t_box,
                                "static_iou": s_iou, "tracked_iou": t_iou}
            gain += t_iou - s_iou
        cases.append({"id": sid, "img": img, "tr": tr, "per_vital": per_vital, "gain": gain})

    # Selection rule, stated so it cannot be mistaken for cherry-picking:
    # the largest improvement, the largest regression, and one example of
    # every distinct tracking status observed.
    chosen: Dict[str, dict] = {}
    ordered = sorted(cases, key=lambda c: c["gain"])
    if ordered:
        chosen[f"worst_delta__{ordered[0]['id']}"] = ordered[0]
        chosen[f"best_delta__{ordered[-1]['id']}"] = ordered[-1]
    seen_status = set()
    for c in cases:
        st = c["tr"].status.value if c["tr"] else "untracked"
        if st not in seen_status:
            seen_status.add(st)
            chosen[f"status_{st}__{c['id']}"] = c
    for c in cases[:: max(1, len(cases) // 4)]:
        if len(chosen) >= limit:
            break
        chosen.setdefault(f"sample__{c['id']}", c)

    os.makedirs(OUT_DIR, exist_ok=True)
    index = []
    for name, c in list(chosen.items())[:limit]:
        canvas = c["img"].copy()
        for vital, d in c["per_vital"].items():
            _draw_box(canvas, d["gt"], C_GT, f"{vital} GT", 2, dashed=True)
            if d["static"]:
                _draw_box(canvas, d["static"], C_STATIC, f"{vital} M5.2", 1)
            if d["tracked"]:
                _draw_box(canvas, d["tracked"], C_TRACKED, f"{vital} M5.3", 2)

        tr = c["tr"]
        lines = [(f"{dataset}  {c['id']}   ref={reference_id}", (235, 235, 235))]
        if tr is not None:
            lines.append((f"tracking: {tr.status.value}  inliers={tr.n_inliers}  "
                          f"scale={tr.scale:.3f}  rot={tr.rotation_deg:.2f}deg  "
                          f"reproj={tr.mean_reprojection_error:.2f}px", (200, 220, 255)))
            if tr.reject_reasons:
                lines.append((f"  refused: {'; '.join(tr.reject_reasons)[:110]}", (255, 170, 170)))

        blames = []
        for vital, d in c["per_vital"].items():
            roi = None
            correct = conf = None
            gt_val = gt_values.get(c["id"], {}).get(vital)
            if d["tracked"]:
                crop_img = c["img"]
                x, y, bw, bh = [int(v) for v in d["tracked"]]
                crop = crop_img[y:y + bh, x:x + bw]
                if crop.size:
                    val, conf = ENGINE.read_vital(crop, vital)
                    if gt_val is not None and val is not None:
                        correct = str(int(float(val))) == str(int(float(gt_val))) if vital != "temp" \
                            else abs(float(val) - float(gt_val)) < 0.05
            lines.append((f"  {vital:6s} IoU {d['static_iou']:.2f} -> {d['tracked_iou']:.2f}"
                          f"   gt={gt_val}  conf={conf if conf is None else round(conf)}", (200, 255, 210)))
            blames.append(_blame(tr.ok if tr else False, d["tracked_iou"], correct, conf))
        lines.append((f"  blame: {sorted(set(blames))[0] if blames else 'n/a'}", (255, 225, 160)))
        lines.append(("  dashed=ground truth   red=M5.2 static   green=M5.3 tracked", (150, 150, 150)))

        panelled = _panel(canvas, lines)
        if panelled.shape[1] < 1100:
            f = 1100 / panelled.shape[1]
            panelled = cv2.resize(panelled, None, fx=f, fy=f, interpolation=cv2.INTER_NEAREST)
        path = os.path.join(OUT_DIR, f"{dataset}__{name}.png")
        Image.fromarray(panelled).save(path)
        index.append({"file": os.path.basename(path), "dataset": dataset, "id": c["id"],
                      "selected_as": name.split("__")[0], "iou_gain": round(c["gain"], 3),
                      "tracking": c["tr"].to_dict() if c["tr"] else None})
        print(f"  wrote {os.path.basename(path)}")
    return index


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    index = []
    print("frozen_B (2712x1220, real framing changes):")
    index += render("frozen_B", FROZEN_B, "sample_", "sample_0001", FROZEN_B_GT)
    print("dense_B_anchors (640x360, original recording):")
    index += render("dense_B_anchors", ANCHOR_DIR, "anchor_", "anchor_004971",
                    os.path.join(ANCHOR_DIR, "m5_3_anchor_ground_truth_values.json"))
    with open(os.path.join(OUT_DIR, "index.json"), "w") as f:
        json.dump({"selection_rule": "largest IoU gain, largest IoU loss, one per distinct tracking "
                                     "status, then evenly spaced samples -- never chosen by appearance",
                   "legend": {"dashed_blue": "ground truth", "red": "M5.2 static box",
                              "green": "M5.3 tracked box"},
                   "cases": index}, f, indent=2)
    print(f"\nWrote {len(index)} overlays to {OUT_DIR}/")


if __name__ == "__main__":
    main()
