"""M4.1 ground-truth-value extraction helper.

The frozen external_monitor_video annotations (M1) contain ONLY bounding
boxes (`rois`), never the numeric value a box contains -- unlike the
synthetic simulator dataset, whose labels carry a `values` dict the
simulator itself wrote at render time. There is no manually-verified value
metadata anywhere in this repo for this dataset (checked: no
`sample_*.json` here has a `values` key, `manifest.json` confirms
`annotation_status` never included values, only boxes).

Per the M4 task's explicit instruction ("determine the correct numeric
ground-truth value from the source images... DO NOT use OCR output itself
as ground truth... If it does not [exist], inspect the existing dataset
tooling and determine the least subjective way to establish the values"):
the least subjective method available is the SAME method the boxes
themselves were produced by (ANNOTATION_GUIDE.md: "No boxes in this folder
were auto-generated" -- a human looked at the frame and drew a box) --
i.e. a human visually reads the boxed digits directly from the
full-resolution source image and transcribes them.

This script does the mechanical, non-subjective part of that: for every
annotated ROI box in every sample, crop it (with a small margin) from the
original 2712x1220 .png at full resolution, and pack the crops into
labelled contact-sheet grids that are large enough to read the digits by
eye. It does NOT invent or infer any value itself -- it only prepares
images for manual transcription. The actual transcription is recorded
separately in `m4_ground_truth_values.json`, written by hand after
visually reading each contact sheet.

Read-only against the frozen dataset: never writes into
external_monitor_video/ itself except a new, separate m4_ocr_report/
subdirectory (mirrors tier2_field_pipeline_eval.py's own
tier2_m2_report/ convention).

Usage:
    python -m app.eval.m4_ground_truth_extract
"""

import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.eval.harness import load_dataset

DATASET_DIR = "app/eval/tier2_data/external_monitor_video"
OUT_DIR = os.path.join(DATASET_DIR, "m4_ocr_report")
CROP_DIR = os.path.join(OUT_DIR, "gt_crops")
SHEET_DIR = os.path.join(OUT_DIR, "contact_sheets")

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")
MARGIN = 14  # px, so digits aren't shaved at the box edge

# grid geometry for the contact sheets
COLS = 4
CELL_W = 420
CELL_H = 300
LABEL_H = 28


def _crop_with_margin(img: np.ndarray, box, margin: int):
    h, w = img.shape[:2]
    x, y, bw, bh = box
    x0 = max(0, int(x) - margin)
    y0 = max(0, int(y) - margin)
    x1 = min(w, int(x + bw) + margin)
    y1 = min(h, int(y + bh) + margin)
    return img[y0:y1, x0:x1]


def main() -> None:
    os.makedirs(CROP_DIR, exist_ok=True)
    os.makedirs(SHEET_DIR, exist_ok=True)

    samples = load_dataset(DATASET_DIR)
    entries = []  # (sample_id, vital, crop_path, box)

    for s in samples:
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        rois = s["label"].get("rois", {})
        for vital in VITALS:
            box = rois.get(vital)
            if not box or box[2] <= 0 or box[3] <= 0:
                continue
            crop = _crop_with_margin(img, box, MARGIN)
            if crop.size == 0:
                continue
            fname = f"{s['id']}_{vital}.png"
            fpath = os.path.join(CROP_DIR, fname)
            Image.fromarray(crop).save(fpath)
            entries.append({"id": s["id"], "vital": vital, "crop_path": fpath, "box": list(box)})

    manifest_path = os.path.join(OUT_DIR, "m4_gt_crop_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(entries, f, indent=2)
    print(f"Wrote {len(entries)} GT crops to {CROP_DIR}")
    print(f"Wrote manifest: {manifest_path}")

    # ---- contact sheets, grouped so each sheet fits comfortably on screen ----
    per_sheet = 24
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()

    n_sheets = (len(entries) + per_sheet - 1) // per_sheet
    for sheet_idx in range(n_sheets):
        chunk = entries[sheet_idx * per_sheet : (sheet_idx + 1) * per_sheet]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * CELL_W, rows * (CELL_H + LABEL_H)), (30, 30, 30))
        draw = ImageDraw.Draw(sheet)
        for i, e in enumerate(chunk):
            r, c = divmod(i, COLS)
            cell_x, cell_y = c * CELL_W, r * (CELL_H + LABEL_H)
            crop_img = Image.open(e["crop_path"])
            # fit crop into the cell, preserving aspect, no upscaling beyond 2x
            scale = min(CELL_W / crop_img.width, CELL_H / crop_img.height, 2.0)
            new_size = (max(1, int(crop_img.width * scale)), max(1, int(crop_img.height * scale)))
            resized = crop_img.resize(new_size, Image.LANCZOS)
            paste_x = cell_x + (CELL_W - new_size[0]) // 2
            paste_y = cell_y + LABEL_H + (CELL_H - new_size[1]) // 2
            sheet.paste(resized, (paste_x, paste_y))
            draw.rectangle([cell_x, cell_y, cell_x + CELL_W - 1, cell_y + LABEL_H + CELL_H - 1], outline=(90, 90, 90), width=1)
            draw.text((cell_x + 4, cell_y + 2), f"{e['id']} / {e['vital']}", fill=(255, 255, 0), font=font)
        sheet_path = os.path.join(SHEET_DIR, f"sheet_{sheet_idx:02d}.png")
        sheet.save(sheet_path)
        print(f"Wrote {sheet_path} ({len(chunk)} crops)")

    print(f"\nTotal: {len(entries)} GT boxes across {len(samples)} samples, {n_sheets} contact sheets.")


if __name__ == "__main__":
    main()
