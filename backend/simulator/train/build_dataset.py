"""Task 1 of S11: builds a labeled DIGIT-CELL dataset for training the CNN.

Renders frames across all 3 layouts and the full augmentation range (clean
through heavy), crops each vital by its ground-truth ROI box -- WITH "ROI
jitter augmentation" sampled from simulator/train/roi_jitter_stats.json
(Task 0's measurement of how far app.pipeline.roi's real boxes deviate from
ground truth) so training crops cover the same loose/shifted framing roi.py
actually produces at inference, not a tight ground-truth box the model will
never see in production. Cells are then segmented via the SAME
app.pipeline.segment.segment_digit_cells() the inference engine uses, and
labeled against the known rendered string.

A segmented line is only kept when its cell COUNT matches the expected
string length exactly; on any mismatch (a glyph merged or spuriously split
under augmentation) the whole line is discarded, never mislabeled -- a
skipped sample costs nothing (there's no shortage of synthetic frames), a
wrong label would quietly poison training.

Usage:
    python -m simulator.train.build_dataset --count 1500 --seed 0
"""

import argparse
import json
import os
import random
import shutil
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.pipeline.segment import CELL_SIZE, segment_digit_cells  # noqa: E402
from simulator.generate import generate_sample  # noqa: E402
from simulator.render.monitor_layout import LAYOUTS  # noqa: E402
from simulator.train.labels import BLANK_LABEL, LABEL_CLASSES  # noqa: E402

VITALS = ("hr", "spo2", "nibp", "etco2", "temp", "rr")

DEFAULT_JITTER_STATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roi_jitter_stats.json")
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
DEFAULT_FRAME_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "build_dataset_frames"
)

# Biased toward degraded frames -- per the task, "the degraded frames are
# the whole point" -- while keeping a real clean slice too, so the model
# (and later eval) has a genuine clean baseline to compare against.
AUGMENT_MIX = [("none", 0.15), ("light", 0.30), ("heavy", 0.30), ("random", 0.25)]

BLANK_FRACTION = 0.06  # share of the final dataset synthesized as the 'blank' negative class


def _expected_lines(vital: str, values: dict) -> List[str]:
    if vital == "nibp":
        return [f"{round(values['nibpSystolic'])}/{round(values['nibpDiastolic'])}", f"{round(values['nibpMean'])}"]
    if vital == "temp":
        return [f"{values['temp']:.1f}"]
    return [f"{round(values[vital])}"]


def _load_jitter_stats(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _sample_jitter(stats: dict, rng: random.Random) -> Tuple[float, float, float, float]:
    def u(key: str) -> float:
        s = stats[key]
        lo, hi = s["p05"], s["p95"]
        if lo > hi:
            lo, hi = hi, lo
        return rng.uniform(lo, hi)

    return u("left_frac"), u("top_frac"), u("right_frac"), u("bottom_frac")


def _apply_jitter(
    box: Tuple[int, int, int, int], jitter: Tuple[float, float, float, float], img_w: int, img_h: int
) -> Tuple[int, int, int, int]:
    x, y, w, h = box
    left, top, right, bottom = jitter
    nx0 = x + left * w
    ny0 = y + top * h
    nx1 = x + w + right * w
    ny1 = y + h + bottom * h
    nx0 = max(0.0, min(nx0, img_w - 1))
    ny0 = max(0.0, min(ny0, img_h - 1))
    nx1 = max(nx0 + 1.0, min(nx1, img_w))
    ny1 = max(ny0 + 1.0, min(ny1, img_h))
    return int(round(nx0)), int(round(ny0)), int(round(nx1 - nx0)), int(round(ny1 - ny0))


def _synthesize_blank_cells(rng: random.Random, count: int) -> List[np.ndarray]:
    """Negative examples for the 'blank' class: segmentation occasionally
    produces a sliver that isn't really a glyph (an over-split fragment, a
    speck of noise/JPEG artifact surviving the ink threshold). Without any
    labeled examples of that, the CNN has no way to say "not a digit" and
    is forced to guess a wrong one instead. Synthesized directly (mostly-
    empty canvases with a little noise or a thin partial stroke) rather
    than mined from real over-segmentation -- simpler, and guarantees an
    exactly-correct negative label."""
    cells = []
    for _ in range(count):
        canvas = np.zeros((CELL_SIZE, CELL_SIZE), dtype=np.uint8)
        variant = rng.random()
        if variant < 0.4:
            n_pixels = rng.randint(1, 6)
            for _ in range(n_pixels):
                py, px = rng.randrange(CELL_SIZE), rng.randrange(CELL_SIZE)
                canvas[py, px] = rng.randint(120, 255)
        elif variant < 0.7:
            x0 = rng.randrange(CELL_SIZE - 4)
            w = rng.randint(1, 3)
            y0 = rng.randrange(CELL_SIZE // 3)
            h = rng.randint(CELL_SIZE // 3, CELL_SIZE - y0 - 1)
            canvas[y0 : y0 + h, x0 : x0 + w] = rng.randint(150, 255)
        # else: pure black -- true empty space.
        cells.append(canvas)
    return cells


def build_frame(
    sample_id: str,
    frame_dir: str,
    layout: str,
    augment: str,
    seed: int,
    jitter_stats: dict,
    rng: random.Random,
) -> List[dict]:
    """Renders one frame and returns a list of {"cell", "label", "vital",
    "augment_level", "is_augmented"} dicts -- one per successfully segmented
    (count-matched) glyph."""
    result = generate_sample(sample_id, frame_dir, layout=layout, augment=augment, seed=seed)
    label = result["record"]
    img = np.array(Image.open(result["png_path"]).convert("RGB"))
    h_img, w_img = img.shape[:2]
    is_augmented = len(label["augmentations"]) > 0

    rows = []
    for vital in VITALS:
        roi = label["rois"].get(vital)
        if not roi or roi[2] <= 0 or roi[3] <= 0:
            continue

        jitter = _sample_jitter(jitter_stats, rng)
        jx, jy, jw, jh = _apply_jitter(tuple(roi), jitter, w_img, h_img)
        if jw <= 0 or jh <= 0:
            continue
        crop = img[jy : jy + jh, jx : jx + jw]

        seg_lines = segment_digit_cells(crop, vital)
        exp_lines = _expected_lines(vital, label["values"])

        for i, expected in enumerate(exp_lines):
            if i >= len(seg_lines) or len(seg_lines[i]) != len(expected):
                continue  # count mismatch -- discard, never mislabel
            for cell, char in zip(seg_lines[i], expected):
                rows.append(
                    {
                        "cell": cell,
                        "label": char,
                        "vital": vital,
                        "augment_level": augment,
                        "is_augmented": is_augmented,
                    }
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=1500, help="Number of frames to render (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=0, help="Random seed (default: %(default)s)")
    parser.add_argument("--jitter-stats", default=DEFAULT_JITTER_STATS)
    parser.add_argument("--frame-dir", default=DEFAULT_FRAME_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--keep-frames", action="store_true", help="Don't delete rendered PNG/JSON frames after extracting cells"
    )
    args = parser.parse_args()

    if not os.path.isfile(args.jitter_stats):
        raise SystemExit(f"Jitter stats not found at {args.jitter_stats} -- run measure_roi_jitter.py first.")
    jitter_stats = _load_jitter_stats(args.jitter_stats)

    rng = random.Random(args.seed)
    layouts = sorted(LAYOUTS)

    counts_per_level: Dict[str, int] = {}
    remaining = args.count
    for level, frac in AUGMENT_MIX[:-1]:
        n = int(round(args.count * frac))
        counts_per_level[level] = n
        remaining -= n
    counts_per_level[AUGMENT_MIX[-1][0]] = remaining

    print(f"Rendering {args.count} frames: {counts_per_level}")

    all_rows: List[dict] = []
    frame_i = 0
    started = time.time()
    for level, n in counts_per_level.items():
        for _ in range(n):
            layout = rng.choice(layouts)
            frame_seed = rng.randint(0, 2**32 - 1)
            sample_id = f"cellframe_{frame_i:05d}"
            rows = build_frame(sample_id, args.frame_dir, layout, level, frame_seed, jitter_stats, rng)
            all_rows.extend(rows)
            frame_i += 1
            if frame_i % 200 == 0:
                print(f"  {frame_i}/{args.count} frames -> {len(all_rows)} cells so far ({time.time() - started:.0f}s)")

    print(f"Rendered {frame_i} frames -> {len(all_rows)} real cells in {time.time() - started:.0f}s")

    n_blank = int(len(all_rows) * BLANK_FRACTION / (1 - BLANK_FRACTION))
    for cell in _synthesize_blank_cells(rng, n_blank):
        all_rows.append(
            {"cell": cell, "label": BLANK_LABEL, "vital": None, "augment_level": "synthetic", "is_augmented": True}
        )
    print(f"Added {n_blank} synthesized 'blank' cells -> {len(all_rows)} total")

    rng.shuffle(all_rows)

    # Stratified train/val/test split: split each augment-level bucket
    # 80/10/10 independently, then recombine -- guarantees the test set
    # gets its proportional share of heavily-augmented cells rather than
    # whatever a single global split happens to leave it with.
    buckets: Dict[str, List[dict]] = {}
    for row in all_rows:
        buckets.setdefault(row["augment_level"], []).append(row)

    train, val, test = [], [], []
    for rows in buckets.values():
        n = len(rows)
        n_val = max(1, int(n * 0.1)) if n >= 10 else 0
        n_test = max(1, int(n * 0.1)) if n >= 10 else 0
        test.extend(rows[:n_test])
        val.extend(rows[n_test : n_test + n_val])
        train.extend(rows[n_test + n_val :])

    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)

    label_to_idx = {c: i for i, c in enumerate(LABEL_CLASSES)}

    def _to_arrays(rows: List[dict]):
        if rows:
            X = np.stack([r["cell"] for r in rows]).astype(np.uint8)
        else:
            X = np.zeros((0, CELL_SIZE, CELL_SIZE), np.uint8)
        y = np.array([label_to_idx[r["label"]] for r in rows], dtype=np.int64)
        meta = [{"vital": r["vital"], "augment_level": r["augment_level"], "is_augmented": r["is_augmented"]} for r in rows]
        return X, y, meta

    X_train, y_train, meta_train = _to_arrays(train)
    X_val, y_val, meta_val = _to_arrays(val)
    X_test, y_test, meta_test = _to_arrays(test)

    os.makedirs(args.out_dir, exist_ok=True)
    np.savez_compressed(
        os.path.join(args.out_dir, "cells.npz"),
        X_train=X_train,
        y_train=y_train,
        X_val=X_val,
        y_val=y_val,
        X_test=X_test,
        y_test=y_test,
    )
    with open(os.path.join(args.out_dir, "meta.json"), "w") as f:
        json.dump({"train": meta_train, "val": meta_val, "test": meta_test, "label_classes": LABEL_CLASSES}, f)

    def _class_counts(rows: List[dict]) -> dict:
        counts: Dict[str, int] = {}
        for r in rows:
            counts[r["label"]] = counts.get(r["label"], 0) + 1
        return counts

    heavy_test = sum(1 for r in test if r["augment_level"] in ("heavy", "random"))
    print()
    print(f"train={len(train)}  val={len(val)}  test={len(test)}  (heavily-augmented test cells: {heavy_test})")
    print(f"class balance (train): {_class_counts(train)}")

    if not args.keep_frames:
        shutil.rmtree(args.frame_dir, ignore_errors=True)
        print(f"Removed scratch frame dir {args.frame_dir}")

    print(f"Wrote {os.path.join(args.out_dir, 'cells.npz')}")
    print(f"Wrote {os.path.join(args.out_dir, 'meta.json')}")


if __name__ == "__main__":
    main()
