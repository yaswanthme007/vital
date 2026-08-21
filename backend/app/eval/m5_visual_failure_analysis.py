"""M5 Phase 9: visual failure analysis. Renders, for one representative
sample per failure category (A/B/C/D/E) plus one full-image "all candidates"
overlay, a debug image showing: original frame, ground-truth box, every
candidate box, the box production's real selection stage picked, its
predicted class/confidence, and (from the already-computed m5 report JSON)
the OCR text/parsed value/expected value/final reconcile outcome -- printed
as a text panel under the image so a human can see WHY the system failed
without re-running anything.

Read-only against m5_report/*.json (already written by
m5_second_monitor_generalization.py) and the external_monitor_B images.
Writes only under m5_report/failure_overlays/.

Usage:
    python -m app.eval.m5_visual_failure_analysis
"""
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.eval.tier2_candidates import adaptive_threshold_candidates_v2
from app.eval.tier2_common import VITALS, warp_box
from app.pipeline.detect import detect_screen
from app.pipeline.field_classifier import get_default_field_classifier

DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tier2_data", "external_monitor_B")
REPORT_DIR = os.path.join(DATASET_DIR, "m5_report")
OUT_DIR = os.path.join(REPORT_DIR, "failure_overlays")

COLOR_GT = (57, 255, 20)
COLOR_CANDIDATE = (140, 140, 140)
COLOR_SELECTED_RIGHT = (0, 200, 255)
COLOR_SELECTED_WRONG = (255, 60, 200)

# One illustrative (sample, vital, category) per failure type, chosen from
# m5_failure_taxonomy.json -- see that file for the full 70-row taxonomy.
EXAMPLES = [
    ("sample_0006", "temp", "A_candidate_generation_miss", "Temp: candidate generator never proposes a box near GT at all on this monitor."),
    ("sample_0001", "spo2", "B_classifier_misclassified", "SpO2: a candidate DOES overlap GT, but FieldCNN's top-1 label for it is not 'spo2'."),
    ("sample_0007", "hr", "C_wrong_crop_selected", "HR: a correctly-classified 'hr' candidate exists at GT's location, but production's selection stage (dedupe/competing-margin) picked a different box."),
    ("sample_0013", "hr", "D_ocr_wrong", "HR: the correct crop reached OCR, but Tesseract misread the digits (837/836 vs GT 86 -- classic multi-digit merge)."),
    ("sample_0016", "hr", "E_reconcile_rejected_correct_ocr", "HR: OCR read the CORRECT value (86), but reconcile()'s confidence gate held the stale baseline instead (fused confidence collapsed to 0 -- OCR confidence-extraction quirk, same mechanism M4.4 already root-caused for NIBP on Dataset A)."),
]


def render_example(sample_id: str, vital: str, category: str, note: str, gt_all: dict, taxonomy_by_id: dict) -> None:
    png_path = os.path.join(DATASET_DIR, sample_id + ".png")
    with open(os.path.join(DATASET_DIR, sample_id + ".json")) as f:
        label = json.load(f)

    img = np.array(Image.open(png_path).convert("RGB"))
    screen = detect_screen(img)
    work_img = screen.image

    gt_raw = label["rois"].get(vital)
    gt_box = None
    if gt_raw:
        gt_box = tuple(gt_raw)
        if screen.detected and screen.homography is not None:
            gt_box = warp_box(gt_box, screen.homography)

    candidates = adaptive_threshold_candidates_v2(work_img)
    classifier = get_default_field_classifier()

    h, w = work_img.shape[:2]
    crops, boxes = [], []
    for box in candidates:
        x, y, bw, bh = box
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
        if x1 <= x0 or y1 <= y0:
            continue
        crops.append(work_img[y0:y1, x0:x1])
        boxes.append((x0, y0, x1 - x0, y1 - y0))
    preds = classifier.classify(crops)

    from app.pipeline.tier2_roi import extract_rois_by_field_classifier

    selected = extract_rois_by_field_classifier(work_img, classifier=classifier)
    sel = selected.get(vital)

    canvas = Image.fromarray(work_img).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
        font_small = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = font_small = ImageFont.load_default()

    if gt_box is not None:
        x, y, bw, bh = gt_box
        draw.rectangle([x, y, x + bw, y + bh], outline=COLOR_GT, width=4)
        draw.text((x, max(0, y - 26)), f"GT:{vital}", fill=COLOR_GT, font=font)

    for box, pred in zip(boxes, preds):
        if pred.label == vital or pred.confidence > 0.3:
            x, y, bw, bh = box
            draw.rectangle([x, y, x + bw, y + bh], outline=COLOR_CANDIDATE, width=2)
            draw.text((x, max(0, y - 18)), f"{pred.label}:{pred.confidence*100:.0f}%", fill=COLOR_CANDIDATE, font=font_small)

    if sel is not None:
        x, y, bw, bh = sel.box
        from app.eval.tier2_common import iou as _iou

        correct = gt_box is not None and _iou(sel.box, gt_box) >= 0.3
        color = COLOR_SELECTED_RIGHT if correct else COLOR_SELECTED_WRONG
        draw.rectangle([x, y, x + bw, y + bh], outline=color, width=4)
        draw.text((x, y + bh + 4), f"SELECTED conf={sel.classifier_confidence:.0f}%", fill=color, font=font)

    # text panel
    panel_h = 170
    panel = Image.new("RGB", (canvas.width, panel_h), (18, 18, 18))
    pdraw = ImageDraw.Draw(panel)
    entry = taxonomy_by_id.get(sample_id, {}).get(vital, {})
    lines = [
        f"{sample_id} / {vital}  --  category: {category}",
        note,
        f"expected (GT) = {entry.get('gt')}    raw OCR parsed value = {entry.get('raw_ocr')}    OCR class = {entry.get('ocr_class')}",
        f"fused confidence = {entry.get('fused_confidence')}    reconcile reason = {entry.get('reason')}    final confirmed value = {entry.get('confirmed_value')}    confirmed_correct = {entry.get('confirmed_correct')}",
        "green=ground truth   gray=other candidates (label:conf%% shown)   cyan=selected box (correct)   magenta=selected box (wrong)",
    ]
    y = 6
    for line in lines:
        pdraw.text((8, y), line, fill=(255, 255, 0), font=font_small)
        y += 28

    combined = Image.new("RGB", (canvas.width, canvas.height + panel_h), (0, 0, 0))
    combined.paste(canvas, (0, 0))
    combined.paste(panel, (0, canvas.height))
    out_path = os.path.join(OUT_DIR, f"failure_{category}_{sample_id}_{vital}.png")
    combined.save(out_path)
    print(f"Wrote {out_path}")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(DATASET_DIR, "m5_ground_truth_values.json")) as f:
        gt_all = json.load(f)["values"]
    with open(os.path.join(REPORT_DIR, "m5_failure_taxonomy.json")) as f:
        taxonomy = json.load(f)

    field_to_group = {"hr": "hr", "spo2": "spo2", "etco2": "etco2", "temp": "temp", "rr": "rr"}
    taxonomy_by_id: dict = {}
    for rec in taxonomy:
        group = field_to_group.get(rec["field"])
        if group is None:
            continue
        taxonomy_by_id.setdefault(rec["id"], {})[group] = rec

    for sample_id, vital, category, note in EXAMPLES:
        render_example(sample_id, vital, category, note, gt_all, taxonomy_by_id)


if __name__ == "__main__":
    main()
