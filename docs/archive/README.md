# Archived documentation — superseded

Everything in this directory is a **historical record**, not guidance. It documents
what was measured and believed during the Tier-2 effort (M1 → M5). The architecture
those milestones built has been **retired from the runtime decision path**, and
several of their conclusions were shown to be wrong when the pipeline was re-run
stage-by-stage against both datasets.

**Read instead:** [`../ARCHITECTURE.md`](../ARCHITECTURE.md) ·
[`../ROADMAP.md`](../ROADMAP.md) · [`../EVIDENCE.md`](../EVIDENCE.md)

These files are kept because they are the audit trail for the datasets, the
annotations, and the measurements that the current decision rests on. Deleting them
would destroy evidence the new plan cites. They are **not** in git history — they
were never committed — so this directory is the only copy.

---

## What each document got wrong

| Document | Still valid | Superseded / wrong |
|---|---|---|
| `TIER2_RECOGNITION_SPIKE.md` | The pipeline audit and the `read_frame.py` swap-seam analysis | Recommends the candidate-generation + FieldCNN hybrid that is now retired. Claims `detect_screen()` is "already Tier-2-ready as-is" — it has **never** fired on real data (0/52 on Dataset A, 0/17 on Dataset B). Its "100% recall" POC ran on a synthetic 960×560 proxy frame, never a real photo. |
| `TIER2_M1_BENCHMARK_REPORT.md` | Benchmark method and IoU≥0.3 convention | Benchmarks a candidate generator that is leaving the runtime path. |
| `TIER2_M1_1_HARDENING_REPORT.md` | The `_strip_line_artifacts` root-cause analysis is sound and well-evidenced | Its conclusion — that the generator can be made to generalise by parameter sweeps — does not hold. There is **no cross-monitor operating point**: Dataset A's optimum and Dataset B's optimum are mutually exclusive. |
| `TIER2_M1_ANNOTATION_VALIDATION.md` | ✅ Factually valid — Dataset A annotation provenance | — |
| `TIER2_M1_EXTERNAL_VIDEO_INGEST.md` | ✅ Factually valid — Dataset A provenance | — |
| `TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md` | Measurement method | Same generator-generalisation assumption as above. |
| `TIER2_M2_FIELD_CLASSIFIER_REPORT.md` | The training pipeline, and its own §296 caveat ("not yet evidence of generalization") — which turned out to be exactly right | The headline **92.6% / 98.3%** figures are dominated by the negative class: the test set is **208 of 234 `not_a_vital`**, with per-class support of **1 crop (etco2)** and **2 (spo2)**. Given *perfect* crops the model scores **64.8% on Dataset A** and **4.3% on Dataset B**. |
| `TIER2_M3_INTEGRATION_REPORT.md` | The `ROI_ENGINE` env-var seam — reused by the new architecture | Integrates the Tier-2 ROI stage that is now retired from the decision path. |
| `TIER2_M4_1_M4_2_OCR_EXPERIMENT_REPORT.md` | OCR experiment method | Tunes OCR to compensate for mislocated crops. With correct crops Dataset A reads **98.4%**, so the deficit these milestones chased was never an OCR deficit. |
| `TIER2_M4_2_1_SELECTIVE_PSM_REPORT.md` | The per-field PSM routing idea | Its promoted config (`--psm 10` + `tessedit_char_whitelist` for SpO2/RR) **destroys the confidence signal**: on Dataset B it reads SpO2 82% correctly at confidence exactly **0**. Reversed by M5.1. |
| `TIER2_M4_4_RULES_LAYER_REPORT.md` | ✅ Its whitelist root-cause analysis is **correct and is the direct basis for M5.1** | Its §61 decision to apply the fix only to NIBP/EtCO2 and defer HR/SpO2/RR is what strands Monitor B at 0% confirmable. |
| `M4_6_PRODUCTION_PROMOTION_REPORT.md` | The promotion process and safety checks | Promotes `ROI_ENGINE=tier2` as the production path. That path is being replaced by `ROI_ENGINE=calibrated`. |
| `TIER2_M5_SECOND_MONITOR_GENERALIZATION_REPORT.md` | ✅ Its headline numbers **reproduce exactly** (candidate recall 32.9%, micro OCR 5.7%, screen detection 0/17), and its NO-GO verdict is correct | Two causal attributions are wrong: (1) it presents `detect_screen()`'s 17/17 failure as a Monitor-B finding and candidate root cause — it fails **0/52 on Dataset A** too; (2) its §19 recommendations (investigate `detect_screen` first, then fine-tune the FieldCNN) both target dead ends. |
| `PROJECT_STATUS_SUMMARY.md` | Feature/status inventory up to M4.6 | Describes the pre-M5 architecture as current. |

## Dataset caveats these documents do not state

Carried forward into [`../EVIDENCE.md`](../EVIDENCE.md):

- **Dataset A's Temp is `98.6` °F, identical in all 52 frames.** Every "Temp 100%"
  claim is one static number read 52 times.
- **Dataset B's Temp ground-truth box is clipped** — it crops `3.7` out of `23.7` —
  and `23.7` is outside `RANGE_BOUNDS["temp"] = (30, 44)` regardless (bench demo,
  probe unattached). Temp on Dataset B is **no-data**, not failure.
