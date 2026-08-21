"""M4.4 after-fix measurement: re-runs EXACTLY M4.3's methodology
(app.eval.m4_3_reliability's run_variant/replay_reconcile,
app.eval.m4_3_analysis's field_summary/_category_d_mask -- imported and
reused, not reimplemented) against CURRENT_BASELINE only (PSM10_SELECTIVE
is explicitly deferred past M4.4, see TIER2_M4_4_RULES_LAYER_REPORT.md §2)
now that the M4.4 production fixes (RANGE_BOUNDS, temp unit normalization,
NIBP/EtCO2 OCR confidence) are live, so M4.3's "before" numbers can be
directly compared against a real "after" run using the identical dataset,
identical live-pipeline re-run approach, and identical aggregation code.

Writes ONLY under app/eval/tier2_data/external_monitor_video/m4_4_report/
-- M4.3's own artifacts under m4_3_report/ are never opened for writing.

Usage:
    python -m app.eval.m4_4_after_reliability
"""

import json
import os

from app.eval.harness import load_dataset
from app.eval.m4_3_analysis import FIELDS as ANALYSIS_FIELDS
from app.eval.m4_3_analysis import _category_d_mask, field_summary
from app.eval.m4_3_reliability import DATASET_DIR, GT_PATH, replay_reconcile, run_variant
from app.pipeline.ocr import TesseractEngine

OUT_DIR = os.path.join(DATASET_DIR, "m4_4_report")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(GT_PATH) as f:
        gt_all = json.load(f)["values"]

    samples = load_dataset(DATASET_DIR)
    print(f"Loaded {len(samples)} samples")

    engine = TesseractEngine()  # production, with M4.4's fixes now live
    print("\n=== Running CURRENT_BASELINE (post-M4.4) over all frames (live pipeline) ===")
    records = run_variant(samples, gt_all, "BASELINE_POST_M4.4", engine)

    with open(os.path.join(OUT_DIR, "m4_4_raw_records.json"), "w") as f:
        json.dump({"baseline_post_m4_4": records}, f, indent=2, default=str)
    print(f"\nWrote {os.path.join(OUT_DIR, 'm4_4_raw_records.json')}")

    timeline = replay_reconcile(records, interval_ms=1000)
    with open(os.path.join(OUT_DIR, "m4_4_timeline_baseline_interval1000.json"), "w") as f:
        json.dump(timeline, f, indent=2, default=str)
    print("Wrote timeline (interval=1000ms, matching M4.3's primary analysis)")

    d_mask = _category_d_mask()
    summary = {field: field_summary(timeline, field, d_mask) for field in ANALYSIS_FIELDS}
    with open(os.path.join(OUT_DIR, "m4_4_analysis_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Wrote {os.path.join(OUT_DIR, 'm4_4_analysis_summary.json')}")

    print("\n=== Post-M4.4 OCR accuracy vs confirmed accuracy ===")
    header = f"{'field':16s}{'n_scored':>10s}{'ocr_acc':>10s}{'confirmed_acc':>15s}{'confirmed_wrong':>17s}"
    print(header)
    for field, e in summary.items():
        def pct(x):
            return f"{x*100:.1f}%" if x is not None else "n/a"
        print(f"{field:16s}{e['n_scored']:>10d}{pct(e['ocr_accuracy']):>10s}{pct(e['confirmed_accuracy']):>15s}{e['confirmed_wrong']:>17d}")


if __name__ == "__main__":
    main()
