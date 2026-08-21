"""M4 error-analysis visual gallery: for a hand-picked set of representative
category-D cases (chosen for being illustrative of a known failure pattern,
not cherry-picked for a good OUTCOME), renders original crop / baseline
preprocessed / best-variant preprocessed side by side with GT + OCR text
overlaid. Read-only against the frozen dataset; writes only under
m4_ocr_report/error_gallery/.
"""
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.eval.harness import load_dataset
from app.pipeline.detect import detect_screen
import app.eval.m4_ocr_benchmark as bench

OUT_DIR = "app/eval/tier2_data/external_monitor_video/m4_ocr_report/error_gallery"

CASES = [
    ("sample_0017", "hr", "extreme-brady alarm: GT literally '0', baseline OCR misreads as '10'"),
    ("sample_0021", "spo2", "adjacent alarm-limit digit bleeds in: GT=65 baseline misreads '165'"),
    ("sample_0003", "spo2", "classic 8/3 glyph confusion: GT=98 baseline misreads '93'"),
    ("sample_0009", "etco2", "leading-digit insertion: GT=37 baseline misreads '237'"),
    ("sample_0002", "rr", "small-glyph crop noise: GT=4 baseline misreads '14'"),
    ("sample_0006", "rr", "small-glyph crop noise: GT=12 baseline misreads '2'"),
    ("sample_0040", "hr", "3-digit HR box under-crops leading digit: GT=183, all variants read '*83' at best"),
    ("sample_0009", "nibp", "correctly-read two-line NIBP (baseline 100% on this field)"),
    ("sample_0001", "nibp", "correctly-read two-line NIBP, low-contrast red-on-black"),
]

VARIANTS_TO_SHOW = ["CURRENT_BASELINE", "PSM10", "GRAYSCALE"]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    samples = {s["id"]: s for s in load_dataset("app/eval/tier2_data/external_monitor_video")}
    raw = json.load(open("app/eval/tier2_data/external_monitor_video/m4_ocr_report/m4_raw_results_start0.json"))
    by_id = {r["id"]: r for r in raw["results"]}

    try:
        font = ImageFont.truetype("arial.ttf", 18)
        font_small = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = font_small = ImageFont.load_default()

    for sample_id, vital, note in CASES:
        r = by_id[sample_id]
        pv = r["per_vital"][vital]
        if pv["category"] != "D":
            print(f"skip {sample_id}/{vital}: category={pv['category']} (not D, no crop recorded)")
            continue
        s = samples[sample_id]
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        screen = detect_screen(img)
        x, y, bw, bh = pv["box"]
        crop = screen.image[y : y + bh, x : x + bw]

        cell_w, cell_h, label_h = 260, 220, 90
        n_cells = 1 + len(VARIANTS_TO_SHOW)
        canvas = Image.new("RGB", (cell_w * n_cells, cell_h + label_h), (25, 25, 25))
        draw = ImageDraw.Draw(canvas)

        def paste(idx, pil_img, title, subtitle=""):
            cx = idx * cell_w
            scale = min((cell_w - 10) / pil_img.width, (cell_h - 10) / pil_img.height)
            new_size = (max(1, int(pil_img.width * scale)), max(1, int(pil_img.height * scale)))
            resized = pil_img.resize(new_size, Image.LANCZOS)
            px = cx + (cell_w - new_size[0]) // 2
            py = label_h + (cell_h - new_size[1]) // 2
            canvas.paste(resized, (px, py))
            draw.rectangle([cx, 0, cx + cell_w - 1, label_h + cell_h - 1], outline=(90, 90, 90), width=1)
            draw.text((cx + 4, 4), title, fill=(255, 255, 0), font=font)
            draw.text((cx + 4, 26), subtitle, fill=(0, 220, 255), font=font_small)

        paste(0, Image.fromarray(crop), "ORIGINAL CROP", f"{sample_id}/{vital}")
        gt = pv["ground_truth"]
        for i, vname in enumerate(VARIANTS_TO_SHOW, start=1):
            vrec = pv["variants"][vname]
            processed = bench.PREPROCESS_VARIANTS[[n for n, _ in bench.PREPROCESS_VARIANTS].index(vname)][1](crop) if vname != "PSM10" else bench.v_current_baseline(crop)
            if processed is None:
                processed_img = Image.new("L", (10, 10), 0)
            else:
                processed_img = Image.fromarray(processed).convert("RGB") if processed.ndim == 2 else Image.fromarray(processed)
            val = vrec.get("value")
            val_str = json.dumps(val) if isinstance(val, dict) else str(val)
            paste(i, processed_img, vname, f"gt={gt}  ocr='{vrec.get('raw_text','')}'→{val_str}")

        # footer note
        footer = Image.new("RGB", (canvas.width, 30), (15, 15, 15))
        fdraw = ImageDraw.Draw(footer)
        fdraw.text((6, 6), note, fill=(255, 255, 255), font=font_small)
        combined = Image.new("RGB", (canvas.width, canvas.height + 30), (15, 15, 15))
        combined.paste(canvas, (0, 0))
        combined.paste(footer, (0, canvas.height))

        out_path = os.path.join(OUT_DIR, f"{sample_id}_{vital}.png")
        combined.save(out_path)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
