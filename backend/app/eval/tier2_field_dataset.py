"""M2 Phase 1: builds the Tier-2 FIELD-CLASSIFIER dataset.

candidate crop (from app.eval.tier2_candidates.adaptive_threshold_candidates_v2,
the M1.1-hardened, recommended generator) -> {hr, spo2, nibp, etco2, temp, rr,
not_a_vital}

ISOLATED FROM PRODUCTION: reads the frozen 52-image/199-box external-monitor
annotation set (app/eval/tier2_data/external_monitor_video/sample_*.json/.png)
read-only, and the M1.1 v2 candidate generator (also read-only, untouched by
this file). Writes only under
app/eval/tier2_data/external_monitor_video/tier2_field_dataset/. Never
modifies a sample_XXXX.json annotation file, never touches app/pipeline/*.

Usage:
    python -m app.eval.tier2_field_dataset
    python -m app.eval.tier2_field_dataset --dataset app/eval/tier2_data/external_monitor_video
"""

import argparse
import json
import os
import random
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from app.eval.harness import load_dataset
from app.eval.tier2_candidates import adaptive_threshold_candidates_v2
from app.eval.tier2_common import VITALS, Box, iou, warp_box
from app.pipeline.detect import detect_screen

# ─── Label assignment ──────────────────────────────────────────────────────
# Same IoU>=0.3 convention established by TIER2_RECOGNITION_SPIKE.md sec
# 03/08 and used unchanged through M1/M1.1's own recall numbers -- a
# candidate at IoU>=0.3 against a ground-truth vital box is "found"; reused
# here, unmodified, as the positive/negative label-assignment boundary so
# this dataset's notion of "correct" matches the benchmark's own. There is
# deliberately no ignored/ambiguous middle band: every candidate gets
# assigned a label, per the M2 spec ("do not silently discard ambiguous
# candidates").
POS_IOU_THRESHOLD = 0.3
NOT_A_VITAL = "not_a_vital"
CLASSES = list(VITALS) + [NOT_A_VITAL]  # hr, spo2, nibp, etco2, temp, rr, not_a_vital

CROP_SIZE = 64  # fixed square CNN input side (bigger than digit_cnn's 28 --
# field crops carry multi-glyph + surrounding-context structure, not one glyph)

SPLIT_SEED = 0
CHUNK_SIZE = 2  # consecutive-sample-id grouping unit for the image-level
# train/val/test split -- see build_image_split() docstring for why this is
# stricter than "any per-image random split" on this particular dataset.

AUG_SEED = 0


# ─── Candidate labeling ─────────────────────────────────────────────────────


def _gt_boxes_for_sample(label: dict, screen) -> Dict[str, Box]:
    gt_rois = label.get("rois", {})
    out: Dict[str, Box] = {}
    for vital in VITALS:
        raw = gt_rois.get(vital)
        if not raw or raw[2] <= 0 or raw[3] <= 0:
            continue
        box: Box = tuple(raw)
        if screen.detected and screen.homography is not None:
            box = warp_box(box, screen.homography)
        out[vital] = box
    return out


def _assign_label(box: Box, gt_boxes: Dict[str, Box]) -> Tuple[str, float, Optional[str]]:
    """Deterministic candidate -> class assignment.

    A candidate overlapping multiple ground-truth vital boxes is assigned to
    the one with the HIGHEST IoU (argmax) -- ties (exact float equality,
    vanishingly rare but handled, not left to dict/hash order) broken by
    fixed VITALS tuple order (hr, spo2, nibp, etco2, temp, rr) so the
    assignment is 100% reproducible. Below POS_IOU_THRESHOLD the candidate is
    always assigned not_a_vital -- never dropped.
    """
    best_vital: Optional[str] = None
    best_iou_val = 0.0
    for vital in VITALS:  # fixed order = deterministic tie-break
        gt = gt_boxes.get(vital)
        if gt is None:
            continue
        v = iou(box, gt)
        if v > best_iou_val:
            best_iou_val = v
            best_vital = vital
    if best_vital is not None and best_iou_val >= POS_IOU_THRESHOLD:
        return best_vital, best_iou_val, best_vital
    return NOT_A_VITAL, best_iou_val, best_vital  # matched_vital kept even when
    # below threshold, for reporting ("near miss on X" vs "no overlap with anything")


def _negative_heuristic_category(box: Box, img_w: int, img_h: int, matched_vital: Optional[str], best_iou_val: float) -> str:
    """Heuristic-only content bucket for a not_a_vital candidate, used purely
    for the Phase-1 negative-source reporting table. This is NOT ground
    truth -- the dataset has no per-negative content label -- it's a
    position/shape heuristic over the box geometry, documented as such
    rather than presented as measured fact:
      - near_miss_<vital>: closest to a real vital box but under 0.3 IoU
        (candidate generator partially found it -- a hard, not random, negative)
      - header_band: sits in the top 12% of the frame (date/patient banner
        region in this dataset's layout, per TIER2_M1_1_HARDENING_REPORT.md sec 9)
      - toolbar_band: sits in the bottom 10% of the frame
      - large_panel: area > 8% of the frame (waveform-panel/banner-sized blob)
      - other: everything else (residual UI text, labels, scale numbers, etc.)
    """
    if matched_vital is not None and best_iou_val > 0.0:
        return f"near_miss_{matched_vital}"
    x, y, w, h = box
    cy = y + h / 2.0
    if cy < 0.12 * img_h:
        return "header_band"
    if cy > 0.90 * img_h:
        return "toolbar_band"
    if (w * h) > 0.08 * (img_w * img_h):
        return "large_panel"
    return "other"


def _letterbox_gray(crop: np.ndarray, size: int = CROP_SIZE) -> np.ndarray:
    """Aspect-preserving resize onto a fixed size x size black canvas,
    centered -- same strategy as app.pipeline.segment.normalize_cell, just a
    bigger canvas (field crops need to keep surrounding visual context, not
    just one glyph's ink)."""
    if crop.ndim == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    else:
        gray = crop
    h, w = gray.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((size, size), dtype=np.uint8)
    scale = min((size - 4) / h, (size - 4) / w)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size), dtype=np.uint8)
    y0 = (size - new_h) // 2
    x0 = (size - new_w) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def build_candidate_records(dataset_dir: str) -> List[dict]:
    """One record per M1.1-v2 candidate box, across all 52 images. Each
    record carries the RAW crop (uint8 grayscale, variable size still --
    letterboxing happens later, per split, so augmentation can run on the
    raw pixels first) plus its assigned label and bookkeeping fields."""
    samples = load_dataset(dataset_dir)
    if not samples:
        raise SystemExit(f"No sample_*.json/.png pairs found under {dataset_dir}")

    records: List[dict] = []
    for s in samples:
        img = np.array(Image.open(s["png_path"]).convert("RGB"))
        screen = detect_screen(img)
        work_img = screen.image
        gray_full = cv2.cvtColor(work_img, cv2.COLOR_RGB2GRAY)
        h, w = gray_full.shape[:2]

        gt_boxes = _gt_boxes_for_sample(s["label"], screen)
        candidates = adaptive_threshold_candidates_v2(work_img)

        for box in candidates:
            label, best_iou_val, matched_vital = _assign_label(box, gt_boxes)
            x, y, bw, bh = box
            x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
            x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
            if x1 <= x0 or y1 <= y0:
                continue
            crop = gray_full[y0:y1, x0:x1].copy()

            neg_category = None
            if label == NOT_A_VITAL:
                neg_category = _negative_heuristic_category(box, w, h, matched_vital, best_iou_val)

            records.append(
                {
                    "image_id": s["id"],
                    "box": [float(v) for v in box],
                    "label": label,
                    "best_iou": best_iou_val,
                    "matched_vital": matched_vital,
                    "neg_category": neg_category,
                    "crop": crop,  # raw grayscale, variable size
                }
            )
    return records


# ─── Image-level split ──────────────────────────────────────────────────────


def build_image_split(dataset_dir: str) -> Dict[str, List[str]]:
    """Splits the 52 SOURCE IMAGES (not crops) into train/val/test so no two
    crops from the same source image ever land in different splits.

    This dataset is 52 frames from ONE continuous recording with near-
    identical framing (per TIER2_M1_1_HARDENING_REPORT.md / the M1 report's
    own framing note) -- a plain per-image random split would still let a
    near-duplicate NEIGHBOURING frame land in a different split than its
    twin, which is temporal leakage in every practical sense even though it
    technically satisfies "no crop from the same image in two splits". To
    guard against that this groups images into CHUNK_SIZE=2 contiguous
    id-blocks first and splits at the CHUNK level, so adjacent near-duplicate
    frames are always kept together.

    Two of the six vitals (nibp, etco2) are annotated on only ~1/3 of the 52
    frames, concentrated in two temporal windows (see the per-image
    annotation coverage table in the M2 report) -- a naive chunk shuffle
    could easily starve val or test of both. So chunks are stratified by
    whether they contain the dataset's two globally rarest vitals before the
    seeded shuffle, and val/test are each guaranteed one chunk containing
    BOTH rare vitals before anything else is allocated.
    """
    samples = load_dataset(dataset_dir)
    ids = sorted(s["id"] for s in samples)
    label_by_id = {s["id"]: s["label"] for s in samples}

    vital_counts = {v: 0 for v in VITALS}
    for lbl in label_by_id.values():
        for v in lbl.get("rois", {}):
            if v in vital_counts:
                vital_counts[v] += 1
    rare_vitals = sorted(vital_counts, key=lambda v: (vital_counts[v], v))[:2]

    chunks: List[List[str]] = [ids[i : i + CHUNK_SIZE] for i in range(0, len(ids), CHUNK_SIZE)]

    def chunk_vitals(chunk: List[str]) -> set:
        s = set()
        for cid in chunk:
            s |= set(label_by_id[cid].get("rois", {}).keys())
        return s

    both_key = frozenset(rare_vitals)
    pool_both, pool_v0, pool_v1, pool_neither = [], [], [], []
    for c in chunks:
        cv = chunk_vitals(c)
        rare_present = frozenset(v for v in rare_vitals if v in cv)
        if rare_present == both_key:
            pool_both.append(c)
        elif rare_vitals[0] in rare_present:
            pool_v0.append(c)
        elif rare_vitals[1] in rare_present:
            pool_v1.append(c)
        else:
            pool_neither.append(c)

    rng = random.Random(SPLIT_SEED)
    for pool in (pool_both, pool_v0, pool_v1, pool_neither):
        rng.shuffle(pool)

    test_chunks: List[List[str]] = []
    val_chunks: List[List[str]] = []

    def _take(pool: List[List[str]]) -> Optional[List[str]]:
        return pool.pop() if pool else None

    for target in (test_chunks, val_chunks):
        c = _take(pool_both)
        if c is None:
            c0, c1 = _take(pool_v0), _take(pool_v1)
            c = (c0 or []) + (c1 or [])
        if c:
            target.append(c)

    n_target_val_test_images = max(1, round(0.15 * len(ids)))
    for target in (test_chunks, val_chunks):
        cur_n = sum(len(c) for c in target)
        while cur_n < n_target_val_test_images and (pool_neither or pool_v0 or pool_v1):
            c = _take(pool_neither) or _take(pool_v0) or _take(pool_v1)
            if c is None:
                break
            target.append(c)
            cur_n += len(c)

    remaining = pool_both + pool_v0 + pool_v1 + pool_neither
    train_chunks = remaining

    def _flatten(chunk_list: List[List[str]]) -> List[str]:
        return sorted(cid for c in chunk_list for cid in c)

    split = {
        "train": _flatten(train_chunks),
        "val": _flatten(val_chunks),
        "test": _flatten(test_chunks),
    }

    assigned = set(split["train"]) | set(split["val"]) | set(split["test"])
    assert assigned == set(ids), "split does not cover every image exactly once"
    assert not (set(split["train"]) & set(split["val"]))
    assert not (set(split["train"]) & set(split["test"]))
    assert not (set(split["val"]) & set(split["test"]))

    for name in ("val", "test"):
        present = {v for cid in split[name] for v in label_by_id[cid].get("rois", {})}
        missing = [v for v in rare_vitals if v not in present]
        if missing:
            raise SystemExit(
                f"Split guarantee violated: {name} split is missing rare vital(s) {missing}. "
                "Adjust CHUNK_SIZE or the stratification pools in build_image_split()."
            )

    split["_meta"] = {
        "rare_vitals": rare_vitals,
        "vital_counts_all_images": vital_counts,
        "chunk_size": CHUNK_SIZE,
        "seed": SPLIT_SEED,
    }
    return split


# ─── Augmentation (train only) ──────────────────────────────────────────────


def _augment_once(gray: np.ndarray, rng: random.Random) -> np.ndarray:
    """One realistic-camera-noise augmentation pass on a grayscale crop.
    Every transform here is calibrated to NOT change what a human would call
    the crop's semantic class (still the same field, still legible-ish) --
    no colour jitter (the model never sees colour at all, per the M2 spec's
    'do not use colour as the primary signal' -- this generator is grayscale
    from _letterbox_gray onward), no layout invention, no large rotation/
    perspective that would turn a real digit block into something a monitor
    never actually renders."""
    out = gray.astype(np.float32)

    # Brightness / contrast.
    brightness = rng.uniform(-18, 18)
    contrast = rng.uniform(0.85, 1.15)
    out = out * contrast + brightness

    # Mild blur.
    if rng.random() < 0.5:
        k = rng.choice([3, 3, 5])
        out = cv2.GaussianBlur(out, (k, k), 0)

    out = np.clip(out, 0, 255).astype(np.uint8)

    # Small scale + translation (simulate a slightly different candidate box).
    h, w = out.shape[:2]
    scale = rng.uniform(0.92, 1.08)
    tx = rng.uniform(-0.04, 0.04) * w
    ty = rng.uniform(-0.04, 0.04) * h
    angle = rng.uniform(-4, 4)
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, scale)
    matrix[0, 2] += tx
    matrix[1, 2] += ty
    out = cv2.warpAffine(out, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)

    # Mild perspective jitter.
    if rng.random() < 0.35:
        d = 0.03 * min(h, w)
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = np.float32(
            [
                [rng.uniform(-d, d), rng.uniform(-d, d)],
                [w + rng.uniform(-d, d), rng.uniform(-d, d)],
                [w + rng.uniform(-d, d), h + rng.uniform(-d, d)],
                [rng.uniform(-d, d), h + rng.uniform(-d, d)],
            ]
        )
        pm = cv2.getPerspectiveTransform(src, dst)
        out = cv2.warpPerspective(out, pm, (w, h), borderMode=cv2.BORDER_REPLICATE)

    # JPEG-compression noise.
    if rng.random() < 0.6:
        quality = rng.randint(35, 80)
        ok, enc = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            out = cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE)

    # Sensor noise.
    if rng.random() < 0.5:
        noise = np.random.default_rng(rng.randint(0, 2**31 - 1)).normal(0, rng.uniform(2, 8), out.shape)
        out = np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    return out


# ─── Dataset assembly (balancing + materialization) ─────────────────────────


def _class_counts(records: List[dict]) -> Dict[str, int]:
    counts = {c: 0 for c in CLASSES}
    for r in records:
        counts[r["label"]] += 1
    return counts


def assemble_dataset(records: List[dict], split: Dict[str, List[str]]) -> dict:
    by_split: Dict[str, List[dict]] = {"train": [], "val": [], "test": []}
    for r in records:
        for name in ("train", "val", "test"):
            if r["image_id"] in split[name]:
                by_split[name].append(r)
                break

    pre_balance_counts = {name: _class_counts(rows) for name, rows in by_split.items()}

    # ── Balancing (TRAIN split only -- val/test stay untouched, natural
    # distribution, evaluated exactly as candidate generation + reality
    # produced them) ──
    #
    # Strategy, chosen from the ACTUAL train-split distribution (see
    # pre_balance_counts in the written report -- not guessed):
    #   1. Bounded negative subsampling: not_a_vital training rows are
    #      randomly (seeded) capped at NEG_CAP_MULT x the largest positive
    #      class's count, rather than duplicated or left to swamp the loss
    #      at a 6-8:1 raw ratio.
    #   2. Controlled oversampling + augmentation for positive classes below
    #      a floor: every positive class is topped up to POS_FLOOR examples
    #      by drawing augmented (_augment_once) variants of its OWN real
    #      annotated crops, capped at AUG_CAP_MULT copies per original crop
    #      so no single real example is cloned into false confidence.
    #   3. Residual imbalance (after 1+2) is handled by inverse-frequency
    #      class weighting in the training loss (train_cnn.py's own
    #      existing idiom, reused unmodified in spirit) -- not by more
    #      resampling.
    # No synthetic/invented positives: every positive training row traces
    # back to a real annotated crop, augmented, never fabricated from
    # nothing.
    train_rows = by_split["train"]
    pos_rows = [r for r in train_rows if r["label"] != NOT_A_VITAL]
    neg_rows = [r for r in train_rows if r["label"] == NOT_A_VITAL]

    counts = pre_balance_counts["train"]
    max_pos_count = max((counts[v] for v in VITALS if counts[v] > 0), default=1)

    NEG_CAP_MULT = 4
    neg_cap = max(max_pos_count * NEG_CAP_MULT, 40)
    rng = random.Random(AUG_SEED)
    if len(neg_rows) > neg_cap:
        neg_rows_kept = rng.sample(neg_rows, neg_cap)
    else:
        neg_rows_kept = list(neg_rows)

    POS_FLOOR = max(30, int(round(max_pos_count * 0.5)))
    AUG_CAP_MULT = 6
    augmented_pos_rows: List[dict] = []
    per_class_pos = {v: [r for r in pos_rows if r["label"] == v] for v in VITALS}
    aug_summary = {}
    for v, rows in per_class_pos.items():
        n_real = len(rows)
        if n_real == 0:
            aug_summary[v] = {"real": 0, "augmented_added": 0, "reason": "no real positive candidates for this vital in TRAIN split"}
            continue
        need = max(0, POS_FLOOR - n_real)
        max_allowed = n_real * AUG_CAP_MULT
        need = min(need, max_allowed)
        added = 0
        i = 0
        while added < need:
            src = rows[i % n_real]
            aug_crop = _augment_once(src["crop"], rng)
            augmented_pos_rows.append({**src, "crop": aug_crop, "augmented": True})
            added += 1
            i += 1
        aug_summary[v] = {"real": n_real, "augmented_added": added}

    final_train_rows = neg_rows_kept + pos_rows + augmented_pos_rows
    rng.shuffle(final_train_rows)

    post_balance_counts = _class_counts(final_train_rows)

    return {
        "by_split_raw": by_split,  # val/test rows unmodified
        "train_final": final_train_rows,
        "pre_balance_counts": pre_balance_counts,
        "post_balance_train_counts": post_balance_counts,
        "neg_cap": neg_cap,
        "pos_floor": POS_FLOOR,
        "aug_summary": aug_summary,
    }


def _neg_category_breakdown(records: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in records:
        if r["label"] == NOT_A_VITAL:
            cat = r["neg_category"] or "other"
            out[cat] = out.get(cat, 0) + 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="app/eval/tier2_data/external_monitor_video")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    out_dir = args.out or os.path.join(args.dataset, "tier2_field_dataset")
    os.makedirs(out_dir, exist_ok=True)

    print("Building candidate crops from adaptive_threshold_candidates_v2 over all 52 images...")
    records = build_candidate_records(args.dataset)
    print(f"  {len(records)} candidate crops total")
    print(f"  class distribution (ALL candidates, pre-split): {_class_counts(records)}")

    print("Building image-level train/val/test split...")
    split = build_image_split(args.dataset)
    meta = split.pop("_meta")
    print(f"  rare vitals used for stratification: {meta['rare_vitals']}")
    print(f"  train images ({len(split['train'])}): {split['train']}")
    print(f"  val images   ({len(split['val'])}): {split['val']}")
    print(f"  test images  ({len(split['test'])}): {split['test']}")

    assembled = assemble_dataset(records, split)
    print()
    print("Class distribution BEFORE balancing, per split:")
    for name in ("train", "val", "test"):
        print(f"  {name:6s} {assembled['pre_balance_counts'][name]}")
    print(f"TRAIN class distribution AFTER balancing: {assembled['post_balance_train_counts']}")
    print(f"  neg_cap={assembled['neg_cap']}  pos_floor={assembled['pos_floor']}")
    print(f"  augmentation summary: {assembled['aug_summary']}")

    neg_breakdown = {
        name: _neg_category_breakdown(assembled["by_split_raw"][name]) for name in ("train", "val", "test")
    }
    print(f"Negative-source heuristic category breakdown: {neg_breakdown}")

    # ── Materialize fixed-size letterboxed arrays and write npz + meta ──
    label_to_idx = {c: i for i, c in enumerate(CLASSES)}

    def _to_arrays(rows: List[dict]):
        if not rows:
            return np.zeros((0, CROP_SIZE, CROP_SIZE), np.uint8), np.zeros((0,), np.int64), []
        X = np.stack([_letterbox_gray(r["crop"]) for r in rows]).astype(np.uint8)
        y = np.array([label_to_idx[r["label"]] for r in rows], dtype=np.int64)
        m = [
            {
                "image_id": r["image_id"],
                "box": r["box"],
                "label": r["label"],
                "best_iou": r["best_iou"],
                "matched_vital": r["matched_vital"],
                "neg_category": r.get("neg_category"),
                "augmented": bool(r.get("augmented", False)),
            }
            for r in rows
        ]
        return X, y, m

    X_train, y_train, meta_train = _to_arrays(assembled["train_final"])
    X_val, y_val, meta_val = _to_arrays(assembled["by_split_raw"]["val"])
    X_test, y_test, meta_test = _to_arrays(assembled["by_split_raw"]["test"])

    np.savez_compressed(
        os.path.join(out_dir, "field_crops.npz"),
        X_train=X_train,
        y_train=y_train,
        X_val=X_val,
        y_val=y_val,
        X_test=X_test,
        y_test=y_test,
    )

    report = {
        "classes": CLASSES,
        "pos_iou_threshold": POS_IOU_THRESHOLD,
        "crop_size": CROP_SIZE,
        "split_seed": SPLIT_SEED,
        "chunk_size": CHUNK_SIZE,
        "aug_seed": AUG_SEED,
        "image_split": {k: split[k] for k in ("train", "val", "test")},
        "split_meta": meta,
        "n_candidates_total": len(records),
        "class_distribution_all_candidates": _class_counts(records),
        "pre_balance_counts": assembled["pre_balance_counts"],
        "post_balance_train_counts": assembled["post_balance_train_counts"],
        "neg_cap": assembled["neg_cap"],
        "pos_floor": assembled["pos_floor"],
        "aug_summary": assembled["aug_summary"],
        "negative_category_breakdown": neg_breakdown,
        "counts": {"train": int(len(y_train)), "val": int(len(y_val)), "test": int(len(y_test))},
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump({"train": meta_train, "val": meta_val, "test": meta_test, "classes": CLASSES}, f)
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=2)

    print()
    print(f"Wrote {os.path.join(out_dir, 'field_crops.npz')}")
    print(f"Wrote {os.path.join(out_dir, 'meta.json')}")
    print(f"Wrote {os.path.join(out_dir, 'report.json')}")


if __name__ == "__main__":
    main()
