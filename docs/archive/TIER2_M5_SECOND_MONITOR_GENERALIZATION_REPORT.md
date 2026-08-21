> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M5 — Second-Monitor Zero-Shot Generalization Report

Does the exact, unmodified M4.6 production Tier-2 pipeline — candidate generation, FieldCNN, candidate selection, production OCR, `reconcile()` — generalize to a second anaesthesia monitor it has never seen: a GE CARESCAPE B650, in a small (17-frame) external real-world video, with **zero** retraining, fine-tuning, or threshold changes?

---

## 1. Executive Summary

## Verdict: **NO-GO**

The current M4.6 Tier-2 system does **not** generalize zero-shot to this second monitor. Across the 17-frame GE CARESCAPE B650 recording, **micro OCR accuracy is 5.7% (4/70 scored fields) and micro confirmed accuracy is 8.6% (6/70)** — and even that 8.6% is not genuine: every one of those 6 "confirmed correct" instances is the pre-session `DEFAULT_BASELINE` placeholder coincidentally matching this recording's real value, not a value the pipeline actually read (raw OCR was `None` in all 6 cases — see §11). Excluding baseline coincidences, **true confirmed accuracy is 0/70 across all five scored vitals.**

The failure is not concentrated at one stage — it is distributed and stage-specific, and the failure taxonomy (§13) shows exactly where:

- **Temp, EtCO2, RR: 100% candidate-generation miss.** The classical candidate generator (`adaptive_threshold_candidates_v2`, unmodified) never proposes a single box near these fields' ground-truth location, on any of the 36 scored frames across these three vitals. Nothing downstream can ever see them.
- **SpO2: 0% end-to-end.** Candidate generation partially finds it (41.2% recall) but FieldCNN never once classifies a correctly-located SpO2 candidate as `spo2` (0/7) — it calls them `nibp` or `not_a_vital` instead. SpO2 never once reaches OCR.
- **HR: the one vital that gets furthest — and still 0% confirmed-correct.** Candidate generation (94.1%) and classification (93.75% given found) both work reasonably well. But production's candidate-*selection* stage (dedupe + competing-margin logic) then picks the wrong box in 8/17 frames, OCR misreads the right crop in 3/17, and — the most important finding of this milestone — in the 4/17 frames where OCR reads the crop **correctly**, `reconcile()`'s confidence gate rejects it anyway, because OCR's own reported confidence collapses to ~0 on this monitor's font/rendering (max observed fused confidence across all 17 HR frames: **3 out of 100**). HR's confirmed accuracy is 0/17, not because the system can't read it, but because it can't tell that it read it right.

This is a genuine, evidence-backed generalization failure, not a fixable rounding error. It is reported here exactly as measured, on a 17-frame recording of one previously-unseen monitor — not extrapolated to "Tier-2 doesn't work," and not hidden behind the small sample size.

## What was actually tested / not tested

**Tested:** the exact M4.6 production code path (`detect_screen` → `adaptive_threshold_candidates_v2` → `FieldClassifierEngine`/`field_classifier.onnx` → `extract_rois_by_field_classifier`'s real selection logic → `TesseractEngine` with M4.6's selective-PSM routing → real `reconcile()`), called directly and through the same `app.eval.m4_3_reliability.run_variant`/`replay_reconcile` harness M4.3/M4.5/M4.6 used, over 17 hand-annotated, manually-transcribed frames of a GE CARESCAPE B650.

**Not tested:** any other GE model or firmware version, any other manufacturer, clinical accuracy, session/demo-mode isolation, or whether a *retrained* Tier-2 (new candidate generator tuning, a FieldCNN fine-tuned on this monitor's frames, or adjusted confidence thresholds) would do better — that is deliberately out of scope for M5 (see §5, Zero-Shot Rule) and is the natural next question, not answered here.

---

## 2. Dataset Provenance

- **Source:** `C:\Users\Admin\Desktop\Sample\Sample imgs 2` — 17 JPEG screenshots (`Screenshot_2026-08-19-14-19-33-448_com.google.android.youtube.jpg` … `..._14-20-31-411_...jpg`), timestamped 14:19:33–14:20:31, all `com.google.android.youtube` filenames.
- **What these actually are:** phone screenshots taken while watching a YouTube video of someone bench-testing/demonstrating a physical GE CARESCAPE B650 monitor (visible hands adjusting an SpO2 probe and a temperature probe, cables, a workbench). This is **not** a photo we took of a physical device ourselves.
- **Provenance label used throughout this report and its artifacts:** `external_real_world_monitor_video`, `physical_monitor_capture: false` — per explicit instruction, matching the same distinction `external_monitor_video`'s (Dataset A) own `ANNOTATION_GUIDE.md` already established for that dataset.
- **Monitor identification:** GE branding is visible on the device bezel; on-screen layout (HR/Pleth/SpO2 numeric column, NIBP/Temp/CO2 bottom row, `awRR`-style CO2/RR combo block) is consistent with a GE CARESCAPE-family monitor. This was **not** independently verified against a GE spec sheet — identification is from the on-screen branding and layout alone, stated as such, not as a certified fact.
- **Confirmed visually different from Dataset A**: different manufacturer chrome/bezel; different color key — Dataset A renders each vital in its own distinct hue (HR green, SpO2 yellow, EtCO2/RR white, per `external_monitor_video/ANNOTATION_GUIDE.md`), while this monitor renders **both HR and SpO2 in the same yellow**, with Temp in cyan/white and NIBP in red — a genuinely different color convention, not just a different palette; different font; different panel layout (NIBP bottom-left instead of Dataset A's arrangement); a **camera that pans/zooms across the recording** (Dataset A used one fixed framing for all 52 frames — this recording uses at least 3 distinct framings across 17 frames, handheld); and background clutter (cables, boxes, a hand) visible around the monitor in every frame — Dataset A's frames are tightly cropped to just the monitor+bezel.

---

## 3. Dataset Statistics

| # | Filename (source) | Size | Format | Corrupt? |
|---|---|---|---|---|
| 1–17 | `Screenshot_2026-08-19-14-19-33-448_...jpg` … `..._14-20-31-411_...jpg` | 674 KB – 1.07 MB | JPEG, RGB | No |

- **Exact count:** 17 files, all readable, all `2712×1220` RGB JPEG (identical resolution to Dataset A — both are phone-screenshots of full-screen YouTube playback on the same device/app).
- **Duplicates:** none — MD5 hash of every file is unique (checked directly, not assumed).
- **Near-duplicate / framing check:** frames 1–9 share one wide handheld framing (minor jitter only); frames 10–12 share a second, more zoomed-in framing; frames 13–17 share a third framing that itself drifts noticeably frame-to-frame (confirmed by direct pixel-coordinate inspection during annotation, §5) — this is a continuously-adjusted handheld shot, not a fixed tripod like Dataset A.
- **Conversion:** originals preserved byte-for-byte unmodified under `backend/app/eval/tier2_data/external_monitor_B/originals_jpg/sample_NNNN.jpg`; a lossless PNG copy (`sample_NNNN.png`) was made for the annotation/eval pipeline, matching the `sample_XXXX.png` convention every M1–M4 script already expects (`app.eval.harness.load_dataset`). No image content was cropped, rotated, recolored, or otherwise altered in this conversion.
- **No frames were duplicated, synthesized, or fabricated to inflate the 17-image count**, per explicit instruction.

---

## 4. Annotation Validation

Ground-truth boxes were drawn by hand (no auto-generation, no OCR) after visually reading pixel-coordinate-gridded crops of every frame (see `backend/app/eval/tier2_data/external_monitor_B/_zoom_inspect/*.png` for the full inspection trail — dozens of zoomed, grid-labeled crops per frame, kept as the annotation's audit trail).

Validation performed programmatically over all 17 `sample_XXXX.json` files:

| Check | Result |
|---|---|
| Image/JSON 1:1 pairing | ✅ 17 PNGs ↔ 17 JSONs, no orphans either direction |
| JSON parses | ✅ all 17 |
| Allowed labels only (`hr,spo2,nibp,etco2,temp,rr`) | ✅ no stray labels |
| Coordinate bounds (inside image, positive w/h) | ✅ 0 violations across 70 boxes |
| No accidentally-empty annotations | ✅ every sample has ≥3 boxes |
| Missing-value representation | ✅ `nibp` omitted from all 17 (never present — see §6); other vitals omitted per-frame when the monitor itself showed dashes/blank/apnea-text instead of a number |
| Aspect-ratio outliers (flag <0.3 or >4.0) | ✅ none flagged |
| **Visual overlay sanity check, every image (not a sample)** | ✅ all 17 rendered with color-coded boxes and inspected directly; **two were wrong on first pass** (sample_0016's HR/Temp/CO2 boxes, sample_0017's Temp box landed off-target after a camera-framing shift) — caught by this exact check, re-grid-cropped, corrected, and re-verified visually before proceeding. This is the process working as intended, not skipped. |

70 total boxes across 17 images: hr=17, spo2=17, temp=17, etco2=12, rr=7, nibp=0.

Full validation script output and the overlay images themselves are under `backend/app/eval/tier2_data/external_monitor_B/validation_overlays/`.

---

## 5. Ground-Truth Methodology

Per the M5 brief's explicit instruction, **no OCR was used to produce ground truth**. Every numeric value in `m5_ground_truth_values.json` was manually transcribed by a human visually reading the boxed digits off zoomed, pixel-grid-coordinate-overlaid crops of the original 2712×1220 frame (tool: `_zoom_inspect/gridcrop.py`, a throwaway annotation aid, not part of the eval pipeline) — the same "human reads a full-resolution crop and types the number" method M4.1's `m4_ground_truth_extract.py` established for Dataset A.

**Vital → this monitor's own on-screen label** (documented mapping, not assumed):

| VITAL's key | This monitor's label | Notes |
|---|---|---|
| `hr` | `HR` (top-right numeric box) | ECG shows **"Leads off" in all 17 frames** — no ECG lead is attached in this recording. The number shown in the `HR` slot is therefore pulse-rate-derived (from the Pleth/SpO2 waveform, labeled `Pleth` on-screen), not ECG-derived — the monitor itself displays it in the `HR` slot regardless, so it was boxed as `hr`. Flagged here, not hidden. |
| `spo2` | `SpO2 %` | Directly below HR. |
| `nibp` | `NIBP mmHg` "SYS/DIA" block | **Never populated in this recording** — always dashes (`- - - / - - -`) with a `(---)` mean and `Manual` mode label, on every one of the 17 frames. Omitted from every sample. This is a genuine recording limitation (the demo never triggers an NIBP measurement), not an annotation gap — see §16. |
| `etco2` | `ET` row of the CO2 `mmHg` block | This block sits directly below an **unrelated** `O2/N2O/AA` anesthetic-agent block that *also* has its own `ET`/`FI` rows (gas concentrations, not CO2) — confirmed by direct inspection not to be the same field; only the lower, `mmHg`-headed block's `ET` value was boxed. |
| `temp` | `T1` (label `Temp °C`, sub-label `Tblood`) | This device's own blood/peripheral-temperature probe channel. `T2` shows `No sensor detected` throughout and was never used. |
| `rr` | `RR` column of the same CO2 block as `etco2` | Frequently shows `APN` (an apnea-annunciation text string, not a number) or is blank in the first ~9 frames of the recording — omitted whenever the displayed slot was not a plain number, never guessed. |

Cross-checked programmatically: every annotated box has a corresponding GT value and vice versa (§4). Values genuinely unavailable on-screen (dashes, `APN` text, blank) are omitted, never interpolated or guessed — documented per-sample in each `sample_XXXX.json`'s `notes` field (e.g. "RR slot shows 'APN'", "RR slot is blank, verified by dedicated close-up crop — omitted as genuinely unreadable, not guessed").

---

## 6. Exact M4.6 Configuration Used

**Zero changes to any production artifact.** Verified both by direct inspection and by `git status` diffing (§17):

- `app.pipeline.detect.detect_screen` — unmodified.
- `app.pipeline.tier2_roi.extract_rois_by_field_classifier` — unmodified; called **directly**, not reimplemented, including its real `MIN_CLASSIFIER_CONFIDENCE=0.5`, `DEDUPE_IOU=0.5`, `COMPETING_MARGIN=0.15` thresholds.
- `app.eval.tier2_candidates.adaptive_threshold_candidates_v2` — the exact M1.1-hardened generator production imports — unmodified.
- `models/field_classifier.onnx` + `field_classifier.labels.json` + `field_classifier.preprocess.json` — loaded via the real `app.pipeline.field_classifier.get_default_field_classifier()` singleton, exact production ONNX artifact, **not retrained, not fine-tuned**.
- `app.pipeline.ocr.TesseractEngine` — the real, M4.6-promoted class, unmodified: SpO2/RR → `--psm 10`, HR/NIBP/EtCO2/Temp unchanged, exactly as `M4_6_PRODUCTION_PROMOTION_REPORT.md` documents. No eval-only OCR subclass used anywhere in this milestone.
- `app.validation.reconcile.reconcile` / `app.validation.rules` — unmodified; called through `app.eval.m4_3_reliability.replay_reconcile`, the same real-reconcile harness M4.3/M4.5/M4.6 used.
- `ROI_ENGINE=tier2` (Tier-2 path, not the Tier-1 colour fallback) — set exactly as `m4_3_reliability.py` already does on import (`os.environ.setdefault`), not overridden.

No threshold, no candidate-generation parameter, no OCR config, no reconcile rule, no PSM routing was adjusted for this milestone, before or during the run — confirmed by `git status` showing zero diff on any file under `backend/app/pipeline/`, `backend/app/validation/`, `backend/app/sources/`, `backend/app/ws/`, or `src/` (§17).

New code written for M5 lives entirely under `backend/app/eval/`: `m5_second_monitor_generalization.py` (the benchmark harness — reuses `harness.load_dataset`, `tier2_candidates`, `field_classifier`, `tier2_roi`, and `m4_3_reliability.run_variant`/`replay_reconcile` verbatim, adding only instrumentation to attribute failures to a stage) and `m5_visual_failure_analysis.py` (renders the Phase-9 overlays from the already-computed report JSON).

---

## 7. Candidate-Generation Results (Stage A)

`adaptive_threshold_candidates_v2`, unmodified, run against `detect_screen()`'s output for each frame. **`detect_screen()` itself failed to find a screen quadrilateral on all 17/17 frames** (`screen_detected=False` throughout) — candidate generation therefore ran on the raw, un-rectified, full 2712×1220 frame (background clutter and all) in every case, not a cropped/rectified screen region. This is itself a finding, not a bug in this script: `detect_screen()`'s own documented fallback ("no confident quad found → return the image unchanged, `detected=False`, never guess") behaved exactly as production's `read_frame()` would behave live — see §16.

| Vital | GT-present frames | Candidate recall (IoU≥0.3) |
|---|---:|---:|
| HR | 17 | **94.1%** (16/17) |
| SpO2 | 17 | **41.2%** (7/17) |
| NIBP | 0 | n/a (never present) |
| EtCO2 | 12 | **0.0%** (0/12) |
| Temp | 17 | **0.0%** (0/17) |
| RR | 7 | **0.0%** (0/7) |

Overall candidate recall (micro, across the 70 GT-present vital-frames): **32.9%** (23/70) — vs. Dataset A's M2 report: **96.3%**.

---

## 8. FieldCNN Results (Stage B)

`field_classifier.onnx`, the real production artifact, classifying every candidate box (not just matched ones — full confusion matrix computed over all 120 candidates generated across the 17 frames):

```
true\pred        hr      spo2      nibp     etco2      temp        rr  not_a_vital
hr                15         0         0         0         0         0         1
spo2               0         0         2         0         0         0         5
nibp                0         0         0         0         0         0         0
etco2               0         0         0         0         0         0         0
temp                0         0         0         0         0         0         0
rr                  0         0         0         0         0         0         0
not_a_vital        11         0         0         0         0         0        86
```

- **HR:** of 16 candidates that genuinely overlap GT (IoU≥0.3), FieldCNN classifies 15 correctly (**93.75%**). Notably, **11 genuinely non-vital regions get misclassified as `hr`** — decoy detections (UI chrome, ECG-lead-off banner, a `1 mV` calibration mark) — this is the direct cause of Stage C's selection failures (§9).
- **SpO2:** of the 7 candidates that overlap GT, FieldCNN classifies **0** as `spo2` — 2 are called `nibp`, 5 are called `not_a_vital`. SpO2's true positive rate at the classifier stage is 0.0%.
- **EtCO2 / Temp / RR:** no true-positive candidates ever reach the classifier (Stage A already missed them), so classifier accuracy for these three is undefined (n/a), not zero — an important distinction the taxonomy (§13) makes explicit.
- **False-positive rejection rate** (recall of the `not_a_vital` class over all 97 genuinely-non-vital candidates): **88.7%** (86/97) — noticeably below Dataset A's M2-report **98.6%**. 11 false alarms on `hr` out of 120 total candidates in just 17 frames.

---

## 9. Candidate-Selection Results (Stage C)

The real `extract_rois_by_field_classifier()` selection logic (dedupe by IoU>0.5, then resolve competing candidates within a 0.15-confidence margin to "unresolved"), called directly:

| Vital | Correctly-classified candidate existed | Production selected the *correct* box |
|---|---:|---:|
| HR | 15/17 frames | **41.2%** (7/17) |
| SpO2 | 0/17 frames | 0.0% (no correctly-classified candidate ever existed to select) |
| EtCO2 / Temp / RR | 0 frames | 0.0% (nothing ever reaches this stage) |

HR is the one vital where this stage's own failure is visible in isolation: a correctly-labeled `hr` candidate at the true location exists in 15/17 frames, yet production's selection only picks it 7 times. In the other 8, a decoy `hr`-classified region (§8) sits within `COMPETING_MARGIN=0.15` of the true one's confidence, and `_select_candidate_for_vital()` — by design, per its own docstring's "genuine competing evidence... resolve to no candidate rather than pick one arbitrarily" — resolves HR to **unresolved** for that frame rather than guessing wrong. That design choice is doing exactly what it was built to do; it just fires far more often on this monitor's decoy-heavy frames than it ever did on Dataset A.

---

## 10. OCR Results (Stage D)

Real, M4.6-promoted `TesseractEngine`, fed whatever crop the real pipeline actually selected (not a hand-picked correct crop):

| Field | n scored | OCR correct | OCR wrong | OCR missing (parse failure) | OCR accuracy |
|---|---:|---:|---:|---:|---:|
| HR | 17 | 4 | 7 | 6 | **23.5%** |
| SpO2 | 17 | 0 | 0 | 17 | 0.0% |
| EtCO2 | 12 | 0 | 0 | 12 | 0.0% |
| Temp | 17 | 0 | 0 | 17 | 0.0% |
| RR | 7 | 0 | 0 | 7 | 0.0% |

**HR's 7 wrong reads are almost entirely a downstream consequence of Stage C selecting the wrong crop** — OCR faithfully reads whatever it's handed (a decoy region), producing garbage like `137`, `8`, `836` (see §14's visual example: `86` misread as `836` even on a *correctly*-selected crop, via Tesseract's own multi-glyph merge behavior, a mechanism already well-documented in this codebase from Dataset A work). SpO2/EtCO2/Temp/RR's 100% "OCR missing" isn't an OCR failure at all — OCR is never invoked with a usable crop, because no crop ever reaches it (Stages A–C).

---

## 11. Reconciliation Results (Stage E) — the most important single finding

Real `reconcile()`, real `DEFAULT_BASELINE` seed, real confidence tiers (`ai_low <70`, `ai_medium 70–89`, `ai_high ≥90`):

| Field | Confirmed correct | Confirmed wrong | Confirmed accuracy | Reject reasons among wrong-OCR frames |
|---|---:|---:|---:|---|
| HR | 0 | 17 | **0.0%** | `low_confidence`×5, `jump_rejected`×1, `implausible_range`×1 |
| SpO2 | 5 | 12 | 29.4%* | n/a (raw OCR always `None`) |
| EtCO2 | 0 | 12 | 0.0% | n/a |
| Temp | 0 | 17 | 0.0% | n/a |
| RR | 1 | 6 | 14.3%* | n/a |

**\* Both non-zero numbers are baseline coincidences, not real reads.** `reconcile()`'s `initial_confirmed_state()` seeds SpO2 at 98 and RR at 14 (`DEFAULT_BASELINE`, `app/validation/reconcile.py`). Since raw OCR for SpO2 and RR is `None` on literally every one of the 24 scored frames between them (§10), the "confirmed" value shown is always the held baseline, never a value the pipeline read. This recording's real SpO2 (98–100) and one frame's real RR (14) happen to be close enough to those hardcoded placeholders to register as "correct" by coincidence. **Genuine confirmed accuracy, once baseline coincidences are excluded, is 0/70 (0.0%) across every scored vital in this recording.**

**HR is the single most important result in this report.** In the 4/17 frames where OCR reads the correct crop *and* reads the correct value (`ocr_class == "correct"`), `reconcile()` still rejects all 4 — every one for `reason == "low_confidence"`. The measured fused confidence for HR across all 17 frames tops out at **3 out of 100** (mean 0.41/100). This traces to a specific, previously-documented mechanism: Tesseract's own reported per-token confidence collapsing toward 0 on a correctly-recognized crop — the exact phenomenon `TIER2_M4_4_RULES_LAYER_REPORT.md`/`ocr.py` already root-caused for Dataset A's NIBP field (§14 shows a direct example: HR read `86` correctly, classifier confidence 59%, but OCR's own confidence was 0, so `min(classifier, ocr)` fusion produces 0). On this monitor, that same known OCR-confidence-extraction quirk now also affects HR — meaning even a technically-correct read can never clear `reconcile()`'s gate.

---

## 12. End-to-End Results

| Metric | Value |
|---|---:|
| Total scored fields (across 5 present vitals × frames each) | 70 |
| Micro OCR accuracy | **5.7%** (4/70) |
| Micro confirmed accuracy (raw, including baseline coincidences) | **8.6%** (6/70) |
| Micro confirmed accuracy (genuine reads only, excluding baseline coincidences) | **0.0%** (0/70) |
| Confirmed accuracy, per scored vital | HR 0%, SpO2 0%\*, EtCO2 0%, Temp 0%, RR 0%\* |
| NIBP | never present in this recording — not scored (§6, §16) |

\* SpO2/RR's nominal non-zero numbers in §11 are baseline coincidences, not genuine reads — excluded here per the honesty requirement in the milestone brief.

---

## 13. Per-Vital Breakdown

| Vital | Cand. recall | Classifier acc. (given found) | Selection acc. | OCR acc. | Confirmed acc. (genuine) | Dominant failure mode |
|---|---:|---:|---:|---:|---:|---|
| **HR** | 94.1% | 93.75% | 41.2% | 23.5% | 0.0% | Selection (C, 8/17) and confidence-collapse-at-reconcile (E, 4/17) roughly tied as the two biggest contributors |
| **SpO2** | 41.2% | 0.0% | 0.0% | 0.0% | 0.0%\* | Classifier never once labels a correctly-located candidate `spo2` (B) |
| **NIBP** | n/a | n/a | n/a | n/a | n/a | Never present in this recording — not a pipeline failure, a dataset limitation (§16) |
| **EtCO2** | 0.0% | n/a | n/a | 0.0% | 0.0% | 100% candidate-generation miss (A) |
| **Temp** | 0.0% | n/a | n/a | 0.0% | 0.0% | 100% candidate-generation miss (A) |
| **RR** | 0.0% | n/a | n/a | 0.0% | 0.0%\* | 100% candidate-generation miss (A) |

\* baseline-coincidence excluded, see §11.

---

## 14. Failure Taxonomy

Every scored (frame, vital) pair was classified into exactly one category — A through E, or `correct` — per the M5 brief's explicit instruction not to stop at "OCR accuracy":

| Category | Meaning | HR | SpO2 | Temp | EtCO2 | RR |
|---|---|---:|---:|---:|---:|---:|
| **A** — candidate generator never produced a usable box near GT | 1 | 10 | 17 | 12 | 7 |
| **B** — a candidate existed, but FieldCNN classified it as something else | 1 | 7 | — | — | — |
| **C** — correct class existed among candidates, but the wrong (or no) crop was selected | 8 | — | — | — | — |
| **D** — correct crop reached OCR, but OCR misread it | 3 | — | — | — | — |
| **E** — OCR read the correct value, but `reconcile()` produced a wrong/rejected final state | 4 | — | — | — | — |
| **correct** (no failure at any stage) | 0 | 0 | 0 | 0 | 0 |

(Column totals: HR 17, SpO2 17, Temp 17, EtCO2 12, RR 7 — matches each vital's scored-frame count exactly.)

**Category C's own two sub-mechanisms**, both counted together as "C" but worth distinguishing in prose: (i) production picks a different, wrong candidate outright, or (ii) production's competing-candidates margin logic (§9) resolves to *no* selection at all because a decoy sits within 0.15 confidence of the true detection. Both were observed among HR's 8 C-category frames.

**Full 70-row taxonomy:** `backend/app/eval/tier2_data/external_monitor_B/m5_report/m5_failure_taxonomy.json`.

---

## 15. Visual Failure Analysis

Five representative debug overlays were rendered (`backend/app/eval/tier2_data/external_monitor_B/m5_report/failure_overlays/`, contact sheet at `_contact_sheet.png`), each showing the original frame, the ground-truth box (green), every candidate FieldCNN scored above 30% or agreeing with the true label (gray, labeled `class:confidence%`), the box production's real selection stage picked (cyan if correct / magenta if wrong), and a text panel with OCR text, parsed value, expected value, and the final reconcile outcome:

1. **`failure_A_..._sample_0006_temp.png`** — Temp's GT box sits cleanly around `23.5`; **zero** candidate boxes appear anywhere near it. The generator's shape/size heuristics (tuned against Dataset A's much larger, bolder digits) never propose anything for this monitor's smaller, differently-styled Temp digits.
2. **`failure_B_..._sample_0001_spo2.png`** — GT box around `100`; no candidate is drawn there at the ≥30%-confidence display threshold, consistent with the classifier having assigned it to `nibp`/`not_a_vital` with the true class never in the running.
3. **`failure_C_..._sample_0007_hr.png`** — GT box around `84`; two other regions near the top of the frame (a calibration mark, the "Leads off" banner) are confidently mislabeled `hr:97%`/`hr:99%` by FieldCNN, triggering the competing-candidates margin and leaving HR unresolved (`raw_ocr=None`) despite the true region existing right where it should.
4. **`failure_D_..._sample_0013_hr.png`** — the **correct** crop is selected (cyan box, 97% confidence, tightly around `86`), but Tesseract reads it as `836` — a multi-digit merge artifact, the same OCR failure mode this codebase has already documented on Dataset A.
5. **`failure_E_..._sample_0016_hr.png`** — the correct crop is selected and OCR reads it **correctly** as `86`, but `fused_confidence` collapses to 0 (OCR's own reported confidence is 0 despite the correct read) and `reconcile()` holds the stale baseline `75` instead — the single clearest illustration of §11's central finding.

---

## 16. Dataset A vs. Monitor B Comparison

**No cherry-picking** — every number below is the same metric, computed the same way, on both datasets.

| Metric | Dataset A (M4.6, 52 frames) | Monitor B (M5, 17 frames) | Change |
|---|---:|---:|---:|
| Candidate recall (overall) | 96.3% | 32.9% | **−63.4 pp** |
| FieldCNN end-to-end classification accuracy (overall) | 92.6% | 21.4% | **−71.2 pp** |
| False-positive rejection rate | 98.6% | 88.7% | −9.9 pp |
| Micro OCR accuracy (5 scored vitals, comparable set)† | 61.6%†† | 5.7% | **−55.9 pp** |
| Micro confirmed accuracy (same set) | 55.6%†† | 0.0% (genuine) | **−55.6 pp** |

† Dataset A's per-field n differs by vital (17–52); "micro" here is the same field-level pooling method used for Monitor B in §12, applied to Dataset A's `m4_6_analysis_summary.json` numbers (hr, spo2, nibpSystolic, nibpDiastolic, etco2, temp[unit-corrected], rr; nibpMean excluded, matching M4's own convention of excluding it from accuracy tables).
†† Computed directly (not from memory) from `m4_6_analysis_summary.json`'s stored per-field `n_scored`/`ocr_accuracy`/`confirmed_accuracy`, pooled to 216 total scored fields: 133/216 OCR-correct = 61.6%; 120/216 confirmed-correct = 55.6%, using temp's unit-corrected 100% confirmed accuracy (per `M4_6_PRODUCTION_PROMOTION_REPORT.md` §3, not the raw pre-unit-correction 0% figure stored in the raw JSON, which would understate Dataset A unfairly).

### Per-vital comparison

| Vital | Dataset A OCR acc. | Monitor B OCR acc. | Dataset A confirmed acc. | Monitor B confirmed acc. |
|---|---:|---:|---:|---:|
| HR | 34.9% | 23.5% | 11.6% | **0.0%** |
| SpO2 | 29.6% | 0.0% | 33.3% | **0.0%** (genuine) |
| NIBP | 82.4% (sys/dia) | n/a — never present | 58.8–100% | n/a |
| EtCO2 | 58.8% | 0.0% | 0.0%‡ | 0.0% |
| Temp | 100.0% | 0.0% | 100.0% (unit-corrected)‡ | 0.0% |
| RR | 46.5% | 0.0% | 62.8% | **0.0%** (genuine) |

‡ Dataset A's EtCO2/Temp confirmed accuracy is itself a known, previously-reported limitation (EtCO2: never reaches confirmed state, low confidence ceiling; Temp: 100% once unit-corrected). Included for a complete comparison, not to imply Dataset A was flawless.

### Attribution — five distinguishable causes, not one blob

1. **Different monitor layout / rendering** (primary, structural): this monitor's Temp/EtCO2/RR digits are smaller, differently colored, and positioned in a denser bottom info bar than Dataset A's — the candidate generator's shape heuristics (kernel sizes, min/max area fractions) were never tuned against this layout and miss these fields entirely (§7).
2. **OCR-specific failure**: HR's multi-digit merge (`86`→`836`, §14) and the pervasive confidence-collapse (§11) are OCR/Tesseract-layer issues, distinct from localization — and partially a *recurrence* of a mechanism already known from Dataset A's NIBP field, now hitting a different vital on a different monitor.
3. **Missing parameters in the recording**: NIBP is absent from the whole recording (dashes/`Manual` throughout) — a property of this specific demo video, not a pipeline failure. Correctly excluded from every accuracy denominator rather than counted as a miss.
4. **Annotation/data limitations**: the handheld, drifting camera framing (3+ distinct positions across 17 frames, vs. Dataset A's one fixed framing) made annotation itself harder and is plausibly part of why `detect_screen()` never locks onto a clean quad here (§7) — a data-collection-quality factor, not purely a model limitation.
5. **Genuine Tier-2 generalization failure**: FieldCNN's near-total inability to recognize `spo2` on this monitor's rendering (§8) and the candidate generator's complete blindness to Temp/EtCO2/RR (§7) are the clearest examples of the model/heuristics themselves not transferring — no amount of re-running or re-cropping would fix these without retraining or re-tuning, which is explicitly out of scope for this milestone.

---

## 17. Production-Safety Verification

```
$ git status --porcelain   (before M5 work started)
```
75 lines — the pre-existing M1–M4.6 working tree (modified `backend/app/pipeline/ocr.py`, `read_frame.py`, `types.py`, `backend/app/validation/*`, `backend/app/ws/vitals.py`, `src/*`, plus untracked `backend/app/pipeline/field_classifier.py`, `tier2_roi.py`, `backend/app/sources/camera.py`, `frame_queue.py`, and the M1–M4 `backend/app/eval/*` scripts/reports — saved verbatim to `/tmp/m5_git_status_before.txt`).

```
$ git status --porcelain   (after all M5 work completed)
```
Identical, plus exactly one new line: `?? backend/app/eval/m5_visual_failure_analysis.py` (the other new M5 file, `m5_second_monitor_generalization.py`, is also new/untracked, alongside the new `backend/app/eval/tier2_data/external_monitor_B/` directory — all under the allowed `backend/app/eval/` tree).

**Diffed directly, not asserted:** `diff` between the before/after `git status --porcelain` snapshots for `backend/app/pipeline/`, `backend/app/validation/`, `backend/app/sources/`, `backend/app/ws/`, and `src/` shows **zero** changes. No production file was modified, added, or removed by this milestone.

Backend test suite:

```
$ .venv/Scripts/python.exe -m pytest tests/ simulator/tests/ -q   (before)
284 passed, 1 warning in 112.78s

$ .venv/Scripts/python.exe -m pytest tests/ simulator/tests/ -q   (after)
284 passed, 1 warning in 94.73s
```

Identical pass count, identical single pre-existing `StarletteDeprecationWarning`, before and after — matches the M4.6 baseline exactly. No investigation needed; nothing changed.

```
$ npx tsc --noEmit
(clean — no output, no errors)

$ npx vite build
✓ 2026 modules transformed. ... ✓ built in 12.57s
```

Frontend clean, same pre-existing bundle-size warning as every prior milestone. No frontend file was touched.

All new M5 files live under `backend/app/eval/` (the script + `tier2_data/external_monitor_B/`) or at the repo root (this report) — nothing under `backend/app/pipeline/*`, `backend/app/validation/*`, `backend/app/sources/*`, `backend/app/ws/*`, or `frontend/src/*` (this repo's `src/`) was modified, per the milestone's explicit constraint.

---

## 18. Limitations

Stated plainly, per this milestone's own instruction not to let the small sample size hide the result — and not to overclaim beyond it either:

- **17 frames, one recording, one monitor, one (drifting) camera framing set.** This is a small external validation set, smaller than Dataset A's already-limited 52 frames, from a single continuous video. It shows this specific recording of this specific device generalizes poorly zero-shot — it does not, by itself, prove GE CARESCAPE B650s in general fail, nor that every other real monitor would fail this badly (nor that all would fail this way — see §16's five-cause breakdown, several of which are monitor/recording-specific, not universal).
- **NIBP was never scored** — the recording never populates it. This report cannot say anything about NIBP generalization one way or the other; it is a gap in this dataset, not a measured 0%.
- **This is not a clinical validation, and does not establish anything about clinical deployment safety** on any monitor, including Dataset A's own already-limited validation.
- **`detect_screen()`'s 17/17 failure to find a quad** is itself only measured on this recording's loose, cluttered framing — whether it would succeed on a tighter, cleaner shot of the same monitor is untested here and is a natural, cheap next check before concluding anything about screen-detection generalization specifically.
- **The Category-C selection-margin behavior** (§9) is a design choice (`COMPETING_MARGIN=0.15`, itself an "unvalidated, conservative default" per its own code comment, carried over unchanged from M3) doing exactly what it was built to do; this report measures how often it fires here, not whether 0.15 is the right value — that is a retuning question, explicitly out of scope for a zero-shot milestone.
- **HR's confidence-collapse mechanism (§11)** is described here by analogy to Dataset A's already-documented NIBP case, because the pattern (correct OCR text, near-zero reported confidence) matches; it was not independently re-derived from Tesseract's internals in this milestone the way M4.4 originally did for NIBP — a reasonable inference from strong existing precedent, not a fresh root-cause investigation.
- **Performance/latency was not measured in this milestone** — M5's brief scope was accuracy/generalization, not timing; no claim is made either way.

---

## 19. Recommended Next Milestone

Given a clean, well-attributed NO-GO with a per-stage failure breakdown already in hand, the highest-value next step is **not** a blind retrain. In priority order:

1. **Investigate `detect_screen()`'s 100% miss rate on this recording specifically** (§7, §18) — cheap (no model changes), and if a tighter/cleaner camera framing alone recovers screen detection, that changes how much of §7–§9's cascade is really "Tier-2 doesn't generalize" vs. "this recording's camera work defeats an upstream, unrelated stage." Worth ruling in or out before spending effort on retraining.
2. **A small, targeted FieldCNN fine-tune or few-shot adaptation pass using a handful of this monitor's own frames** (not full retraining from scratch) — given HR's candidate generation and base classification already work reasonably (94%/94%), the class-confusion pattern (§8: SpO2→nibp/not_a_vital) looks like exactly the kind of gap a small labeled sample from the new monitor could close, more so than the candidate-generation blindness for Temp/EtCO2/RR.
3. **Revisit the candidate generator's size/shape heuristics** against this monitor's smaller Temp/EtCO2/RR digit rendering (§7) — this is the single largest recall gap (0% on three vitals) and the M1.1 hardening history (`TIER2_M1_1_HARDENING_REPORT.md`) already shows this codebase can measurably improve candidate generation through targeted, evidence-based parameter sweeps rather than a full redesign.
4. **A larger second-monitor holdout** (more than 17 frames, ideally a second, independent GE B650 recording or a different device entirely) before drawing any conclusion about whether fixes from #1–#3 generalize beyond this one recording — the same "don't over-trust a small sample" caution this report itself follows.

Do not attempt any of #1–#3 as part of a "fix M5" pass — per the milestone's own explicit instruction, this report's job was to measure what M4.6 can do on a second monitor, not to make it better. That decision belongs to the next milestone.

---

## Files Changed / Added

**Production files modified:** none.

**New evaluation code** (`backend/app/eval/`):
```
m5_second_monitor_generalization.py
m5_visual_failure_analysis.py
```

**New dataset + report artifacts** (`backend/app/eval/tier2_data/external_monitor_B/`):
```
sample_0001.png … sample_0017.png            (converted from source JPEGs, unmodified content)
originals_jpg/sample_0001.jpg … _0017.jpg    (byte-for-byte preserved originals)
_ingest_manifest_raw.json                    (Phase 1 ingestion manifest: id/source filename/dims/format per sample)
sample_0001.json … sample_0017.json          (hand-drawn annotations, ANNOTATION_GUIDE.md shape)
m5_ground_truth_values.json                  (manually transcribed values + provenance + vital-label mapping)
contact_sheet.png
validation_overlays/overlay_sample_0001.png … _0017.png
_zoom_inspect/                               (full annotation inspection trail: grid-crops, build script)
m5_report/
  m5_raw_records.json
  m5_timeline_interval1000.json
  m5_analysis_summary.json
  m5_failure_taxonomy.json
  m5_classifier_confusion.json
  failure_overlays/failure_A_..._sample_0006_temp.png
  failure_overlays/failure_B_..._sample_0001_spo2.png
  failure_overlays/failure_C_..._sample_0007_hr.png
  failure_overlays/failure_D_..._sample_0013_hr.png
  failure_overlays/failure_E_..._sample_0016_hr.png
  failure_overlays/_contact_sheet.png
TIER2_M5_SECOND_MONITOR_GENERALIZATION_REPORT.md   (this report, repo root — matching prior milestones' convention)
```

No previous milestone report or artifact (M1–M4.6) was modified or deleted.

No commits, no tags — all git operations left for the user, per instruction.
