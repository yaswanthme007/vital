> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M4.1 + M4.2 OCR Experiment Report

Continuation of M1 ([`TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md`](TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md)), M1.1 ([`TIER2_M1_1_HARDENING_REPORT.md`](TIER2_M1_1_HARDENING_REPORT.md)), M2 ([`TIER2_M2_FIELD_CLASSIFIER_REPORT.md`](TIER2_M2_FIELD_CLASSIFIER_REPORT.md)), M3 ([`TIER2_M3_INTEGRATION_REPORT.md`](TIER2_M3_INTEGRATION_REPORT.md)). This run is **measurement and experimentation only** — M4.1 (current OCR baseline) and M4.2 (OCR preprocessing experiments), executed together as one isolated evaluation. **No production code was modified.** M4.3 (reliability/validation) and M4.4 (production changes) are explicitly out of scope and were not started.

---

## 0. What was actually run

Everything below is measured, not estimated. New, isolated files, all under `backend/app/eval/`, none imported by production:

| File | Purpose |
|---|---|
| [`app/eval/m4_ground_truth_extract.py`](backend/app/eval/m4_ground_truth_extract.py) | Crops every annotated ROI box at full resolution, packs into 9 labelled contact sheets for manual transcription |
| [`app/eval/tier2_data/external_monitor_video/m4_ocr_report/m4_ground_truth_values.json`](backend/app/eval/tier2_data/external_monitor_video/m4_ocr_report/m4_ground_truth_values.json) | The 199 manually-transcribed ground-truth values (see §2) |
| [`app/eval/m4_ocr_benchmark.py`](backend/app/eval/m4_ocr_benchmark.py) | Replays real, unmodified `detect_screen` → `adaptive_threshold_candidates_v2` → `FieldClassifierEngine` → `app.pipeline.tier2_roi._select_candidate_for_vital` (imported, not reimplemented), assigns the A–E failure category per (sample, vital), and — only for category D — runs 13 OCR configurations against the *identical* selected crop |
| [`app/eval/m4_aggregate_report.py`](backend/app/eval/m4_aggregate_report.py) | Turns the raw per-crop results into every table below |
| [`app/eval/m4_error_gallery.py`](backend/app/eval/m4_error_gallery.py) | Renders original/preprocessed/OCR-output comparison images for representative failures |
| `app/eval/tier2_data/external_monitor_video/m4_ocr_report/m4_raw_results_start0.json` | Full raw output: every (sample, vital, variant) record — box, FieldCNN label/confidence, raw OCR text, parsed value, confidence, latency |

Environment: project `.venv` (the system/Anaconda Python lacked `onnxruntime`/`cv2`/`fastapi`/`pytesseract` entirely — this was checked, not assumed). Tesseract resolved automatically via `ocr.py`'s own candidate-path list (`C:\Program Files\Tesseract-OCR\tesseract.exe`, found on this machine). Baseline suite confirmed **228 passed, 0 failed, 1 pre-existing warning** *before* any M4 work began, matching M3's claimed state exactly.

---

## 1. Executive verdict

- **Baseline OCR accuracy (current production preprocessing, PSM 8/6/decimal configs exactly as shipped): 66.3% exact-value accuracy** (114/172 evaluated readings correct, 25 wrong, 33 missing), over the 172 vital-value reads that actually reached OCR on the frozen 52-frame external-monitor dataset.
- **Best single global variant tested: PSM10 (baseline pixels, `--psm 10` instead of `--psm 8/6`) — 75.0% accuracy (129/172)**, with dramatically better parse rate (98.8% vs 80.8% — Tesseract almost never comes back empty) but a **higher wrong-rate than baseline** (23.8% vs 14.5%) and a **real regression on NIBP systolic** (100%→78.6%, the one field baseline already gets perfect). This is exactly the "improves X, destroys Y" pattern the task warned against calling a winner.
- **Preprocessing (pixel transforms) materially improves OCR only for RR** (16.7%→up to 66.7% on several variants) and is **actively harmful everywhere else**, most severely on Temp (100%→as low as 5.8%) because non-thresholded/non-padded pixels break Tesseract's decimal-point recognition on this font.
- **Preprocessing does NOT materially improve OCR overall** — of the 10 named pixel-preprocessing variants, none beats CURRENT_BASELINE's overall accuracy; the only large gains anywhere in the matrix come from changing **PSM mode**, not pixel treatment.
- **Recommendation: ITERATE M4.2, not GO TO M4.3.** See §9 for the exact next experiment (a per-vital-conditional PSM variant that was never itself tested as a controlled configuration).

---

## 2. M4.1 baseline

### 2.1 Ground truth: methodology (mandatory reading before trusting any number below)

The external-monitor annotations (`app/eval/tier2_data/external_monitor_video/sample_*.json`) contain **only bounding boxes** (`rois`) — confirmed by inspection (`grep -l values sample_*.json` → 0 matches) and by `manifest.json`'s own `annotation_status` note. There is no `values` field anywhere in this dataset, unlike the synthetic simulator dataset `app.eval.harness.evaluate_sample` was written for.

Per the task's explicit instruction ("determine the correct numeric ground-truth value from the source images... do not use OCR output as ground truth... use the least subjective method"): the least subjective method available is the **same method the boxes themselves were produced by** — `ANNOTATION_GUIDE.md` states "No boxes in this folder were auto-generated," i.e. a human looked at the full-resolution frame and drew the box. This run did the equivalent for values: `m4_ground_truth_extract.py` cropped all **199** annotated boxes (52 samples × up to 6 vitals, minus dashed/unannotated fields) at full resolution with a 14px margin, packed them into 9 contact sheets, and every value was **read directly by eye from the source pixels** — no OCR involved at any point — and cross-validated for 100% coverage against the annotation manifest (0 missing pairs).

Two things surfaced during this manual read that are worth flagging explicitly:
- **`hr=0` and `rr=0` are genuine on-screen values**, not "no reading." Samples 0017–0047 span an extended alarm-state sequence where this monitor literally displays `0` for HR and/or RR (matches `manifest.json`'s own `annotation_caveat` about "HR=0 during an EXTREME BRADY alarm"). These were counted as real, evaluable ground truth, not excluded.
- NIBP ground truth is stored as `"systolic/diastolic/mean"` and evaluated as separate `nibpSystolic`/`nibpDiastolic` fields per the task's explicit instruction not to collapse NIBP into one number.

### 2.2 Evaluated samples

199 (sample, vital) pairs had ground truth. Of those, **172 reached OCR** (category D — see §2.4); the rest never reached OCR at all (categories A/B/C) or had no ground truth (category E, not evaluated). **This is the actual "evaluated readings" figure the numbers below are computed over — not 199, and not 52×6=312.**

### 2.3 Per-vital baseline results (CURRENT_BASELINE = exact production preprocessing + configs, unmodified)

| Field | n (reached OCR) | Exact accuracy | Parse rate | Wrong rate | Missing rate | Mean OCR confidence |
|---|---:|---:|---:|---:|---:|---:|
| HR | 40 | 37.5% | 72.5% | 35.0% | 27.5% | 59.0 |
| SpO2 | 11 | 54.5% | 81.8% | 27.3% | 18.2% | 41.2 |
| NIBP-Systolic | 14 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0 |
| NIBP-Diastolic | 14 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0 |
| EtCO2 | 11 | 72.7% | 100.0% | 27.3% | 0.0% | 20.4 |
| Temp | 52 | 100.0% | 100.0% | 0.0% | 0.0% | 84.7 |
| RR | 30 | 16.7% | 33.3% | 16.7% | 66.7% | 37.1 |
| **Overall (micro)** | **172** | **66.3%** | **80.8%** | **14.5%** | **19.2%** | — |
| **Macro (unweighted mean of the 7 rows above)** | — | **68.8%** | — | — | — | — |

Total: **114 correct / 25 wrong / 33 unparseable**, out of 172 evaluated readings.

Do not read the HR/RR numbers as pure "Tesseract is bad at this font" — §6 shows both are dominated by an *upstream crop-completeness* problem, not a character-recognition problem.

**NIBP-Systolic/Diastolic are 100% exact-accuracy at 0.0 mean OCR confidence, every single time** — this exactly reproduces M3 §6's observation on `sample_0017` (systematically correct `150/80` reads scoring literal-zero per-token Tesseract confidence on this font/crop) across all 14 baseline NIBP reads, not just the one frame M3 happened to show. NIBP is simultaneously the most *accurate* field under baseline and the field whose confidence signal is least usable on its own — production's existing MIN-fusion (never trusting a strong signal to paper over a weak one) is doing real, necessary work here, confirmed at scale rather than as a single anecdote.

### 2.4 Failure-category breakdown (mandatory distinction — computed BEFORE any OCR preprocessing choice)

Using production's own imported `_select_candidate_for_vital` and `MIN_CLASSIFIER_CONFIDENCE`/`DEDUPE_IOU`/`COMPETING_MARGIN` thresholds, unmodified, against `POS_IOU_THRESHOLD=0.3` (same value `app.eval.tier2_field_dataset` and `ANNOTATION_GUIDE.md` use):

| Vital | A: candidate-gen miss | B: FieldCNN miss | C: selection failure | D: reaches OCR | E: no GT | Total w/ GT |
|---|---:|---:|---:|---:|---:|---:|
| HR | 3 | 0 | 0 | 40 | 9 | 43 |
| SpO2 | 3 | 0 | 13 | 11 | 25 | 27 |
| NIBP | 3 | 0 | 0 | 14† | 35 | 17 |
| EtCO2 | 6 | 0 | 0 | 11 | 35 | 17 |
| Temp | 0 | 0 | 0 | 52 | 0 | 52 |
| RR | 4 | 1 | 8 | 30 | 9 | 43 |

† NIBP counted per-box (one candidate covers both systolic/diastolic); the 14/14 split in §2.3 is the same 14 boxes evaluated on two sub-fields.

**This table is the real headline finding of M4.1, independent of any preprocessing question:**
- **SpO2 loses nearly half its evaluable frames (13/27, 48%) to category C — candidate-selection deliberately declining to choose between two competing candidates** (`_select_candidate_for_vital` reason = `competing_candidates` in all 13 cases, confirmed by inspection). This is very plausibly the monitor's separate `Pulse` field the annotation guide explicitly warns is "numerically close to HR but a different on-screen region" — i.e. Tier-2's safety net is very likely doing exactly what M3 designed it to do (refuse to guess between two plausible same-vital candidates) rather than malfunctioning. **This is upstream of OCR and out of M4's scope to fix**, but it means SpO2's OCR accuracy (§2.3) is measured on a smaller, and possibly biased ("easier"), subset than its true evaluable population.
- **RR loses 8/43 (19%) the same way**, plus 4 candidate-generation misses and 1 FieldCNN miss (this is the same `not_a_vital`-at-94.5%-confidence case M2 §14 already documented for `sample_0026`).
- **HR's 3 category-A misses at samples where GT is a 3-digit value (e.g. `sample_0038`, GT=181) reproduce M2 §14's known HR-fragmentation case exactly** — `"181"` splits into candidates none of which individually reach IoU≥0.3 against the full box. Confirmed still present, unaddressed (correctly — M3/M4 were told not to fix it), unchanged.
- **Temp has zero A/B/C failures across all 52 frames** — every Temp candidate that exists is correctly generated, classified, and selected, 100% of the time on this dataset. Its accuracy story (§2.3, §6) is *purely* an OCR-preprocessing story, unclouded by upstream error.

### 2.5 Confidence vs. correctness (baseline)

| OCR confidence bucket | n | Accuracy |
|---|---:|---:|
| [0, 20) | 78 | 52.6% |
| [20, 40) | 17 | 70.6% |
| [40, 60) | 9 | 55.6% |
| [60, 80) | 8 | 62.5% |
| [80, 101) | 60 | 85.0% |

Confidence is directionally useful (the top bucket is the most accurate) but far from cleanly calibrated — the [0,20) bucket, which includes every "missing" read (confidence forced to 0, always counted incorrect) *and* every genuinely-low-but-correct read (all 28 NIBP sys/dia reads sit at confidence 0.0, and all 28 are correct — see §2.3), still shows 52.6% accuracy, roughly the same as the [40,60) bucket. **Low OCR confidence alone should not be read as "probably wrong"** on this dataset without separating "OCR gave up" from "OCR answered but wasn't sure."

---

## 3. M4.2 experiment matrix

Every variant below ran against the exact same category-D crop, box, FieldCNN label, and selection decision (guaranteed by construction — preprocessing is the only thing that varies inside `ocr_variant()`; candidate generation/classification/selection run exactly once per sample, before any variant loop).

| # | Variant | Definition | Config (per vital) |
|---|---|---|---|
| 1 | `CURRENT_BASELINE` | Exact production `app.pipeline.ocr._preprocess` — grayscale → upscale to 120px height → median blur → Otsu threshold (majority-is-background polarity fix) → 20px white quiet-zone pad — unmodified, imported not reimplemented | `--psm 8` digit/decimal whitelist; `--psm 6` NIBP |
| 2 | `GRAYSCALE` | Grayscale only, no resize/threshold/pad | same as baseline |
| 3 | `UPSCALE_2X` | Grayscale + 2× cubic upscale | same |
| 4 | `UPSCALE_3X` | Grayscale + 3× cubic upscale | same |
| 5 | `CONTRAST` | Grayscale + min-max contrast normalization (`cv2.normalize`) | same |
| 6 | `OTSU` | Grayscale + Otsu threshold + majority-polarity fix (no blur, no pad) | same |
| 7 | `ADAPTIVE_THRESHOLD` | Grayscale + `cv2.adaptiveThreshold` (Gaussian, block ≈ crop-size/3) + polarity fix | same |
| 8 | `SHARPEN` | Grayscale + unsharp mask (Gaussian blur σ=2, weighted subtract) | same |
| 9 | `UPSCALE_2X_SHARPEN` | 2× upscale + sharpen | same |
| 10 | `UPSCALE_2X_ADAPTIVE` | 2× upscale + adaptive threshold | same |
| 11 | `PSM6` | **CURRENT_BASELINE pixels**, `--psm 6` (single uniform block) on every field including scalars | whitelist unchanged |
| 12 | `PSM7` | **CURRENT_BASELINE pixels**, `--psm 7` (single line) — production's own code comment says PSM 7 is known to misread a leading "7" as "1" on this font; re-tested here as a controlled variant, not assumed | whitelist unchanged |
| 13 | `PSM10` | **CURRENT_BASELINE pixels**, `--psm 10` (single character) | whitelist unchanged |

PSM variants 11–13 deliberately reuse `CURRENT_BASELINE`'s exact pixels so PSM's effect is isolated from preprocessing's effect — production already varies config by vital type (`_DIGIT_CONFIG`/`_DECIMAL_CONFIG`/`_NIBP_CONFIG`), so testing alternate PSM values is a controlled extension of an axis production already uses, not a new untested dimension.

No ground truth was used to choose or tune any variant. No value was ever hand-corrected. No per-frame/per-coordinate special-casing exists anywhere in `m4_ocr_benchmark.py`.

---

## 4. Results

### 4.1 Per-vital exact accuracy, every variant

| Variant | HR | SpO2 | NIBP-SYS | NIBP-DIA | EtCO2 | Temp | RR | Overall |
|---|---|---|---|---|---|---|---|---|
| CURRENT_BASELINE | 37.5% | 54.5% | **100.0%** | **100.0%** | **72.7%** | **100.0%** | 16.7% | 66.3% |
| GRAYSCALE | 27.5% | 45.5% | 100.0% | 100.0% | 45.5% | 13.5% | 66.7% | 44.2% |
| UPSCALE_2X | 32.5% | 45.5% | 71.4% | 71.4% | 45.5% | 13.5% | 56.7% | 39.0% |
| UPSCALE_3X | 30.0% | 45.5% | 71.4% | 78.6% | 45.5% | 13.5% | 66.7% | 40.7% |
| CONTRAST | 27.5% | 45.5% | 100.0% | 100.0% | 45.5% | 13.5% | 66.7% | 44.2% |
| OTSU | 27.5% | 54.5% | 92.9% | 100.0% | 36.4% | 13.5% | 40.0% | 39.0% |
| ADAPTIVE_THRESHOLD | 35.0% | 45.5% | 100.0% | 100.0% | 9.1% | 5.8% | 40.0% | 36.6% |
| SHARPEN | 32.5% | 45.5% | 78.6% | 78.6% | 45.5% | 13.5% | 66.7% | 41.9% |
| UPSCALE_2X_SHARPEN | 30.0% | 54.5% | 78.6% | 78.6% | 45.5% | 13.5% | 56.7% | 40.1% |
| UPSCALE_2X_ADAPTIVE | 35.0% | 54.5% | 100.0% | 100.0% | 9.1% | 5.8% | 43.3% | 37.8% |
| PSM6 | 17.5% | 36.4% | 100.0% | 100.0% | 0.0% | 90.4% | 6.7% | 51.2% |
| PSM7 | 42.5% | 45.5% | 71.4% | 85.7% | 0.0% | 90.4% | 66.7% | 64.5% |
| **PSM10** | **45.0%** | **72.7%** | 78.6% | 100.0% | 63.6% | 98.1% | **66.7%** | **75.0%** |

n per field (fixed across all variants — same crops every time): HR=40, SpO2=11, NIBP-SYS=14, NIBP-DIA=14, EtCO2=11, Temp=52, RR=30.

### 4.2 Overall accuracy / parse / wrong / missing / latency

| Variant | Exact Accuracy | Parse Rate | Wrong Rate | Missing Rate | Mean total latency |
|---|---:|---:|---:|---:|---:|
| CURRENT_BASELINE | 66.3% | 80.8% | 14.5% | 19.2% | 269ms |
| GRAYSCALE | 44.2% | 93.0% | 48.8% | 7.0% | 210ms |
| UPSCALE_2X | 39.0% | 86.6% | 47.7% | 13.4% | 229ms |
| UPSCALE_3X | 40.7% | 89.0% | 48.3% | 11.0% | 254ms |
| CONTRAST | 44.2% | 91.9% | 47.7% | 8.1% | 192ms |
| OTSU | 39.0% | 88.4% | 49.4% | 11.6% | 232ms |
| ADAPTIVE_THRESHOLD | 36.6% | 82.6% | 45.9% | 17.4% | 233ms |
| SHARPEN | 41.9% | 89.5% | 47.7% | 10.5% | 198ms |
| UPSCALE_2X_SHARPEN | 40.1% | 87.8% | 47.7% | 12.2% | 227ms |
| UPSCALE_2X_ADAPTIVE | 37.8% | 84.9% | 47.1% | 15.1% | 255ms |
| PSM6 | 51.2% | 75.6% | 24.4% | 24.4% | 226ms |
| PSM7 | 64.5% | 86.0% | 21.5% | 14.0% | 223ms |
| PSM10 | 75.0% | 98.8% | 23.8% | 1.2% | 226ms |

Note the pattern: every pixel-preprocessing variant (rows 2–10) *raises* parse rate over baseline (Tesseract almost always returns *something*) while roughly **tripling the wrong-rate** (from 14.5% to 45–49%) — these variants make Tesseract more talkative, not more correct. Only the PSM variants (11–13) improve exact accuracy net of that trade.

### 4.3 Vital-by-vital: baseline vs. best variant

| Vital | Baseline | Best variant | Best value | Improvement |
|---|---:|---|---:|---:|
| HR | 37.5% | PSM10 | 45.0% | +7.5pp |
| SpO2 | 54.5% | PSM10 | 72.7% | +18.2pp |
| NIBP-Systolic | **100.0%** | *(none beats it)* | 100.0% | 0pp — PSM10 **regresses this to 78.6%** |
| NIBP-Diastolic | **100.0%** | tied (several) | 100.0% | 0pp |
| EtCO2 | **72.7%** | *(none beats it)* | 72.7% | 0pp — every variant is worse; PSM10 drops to 63.6% |
| Temp | **100.0%** | *(none beats it)* | 100.0% | 0pp — every pixel-preprocessing variant collapses to 5.8–13.5%; PSM10 comes close (98.1%) but still regresses |
| RR | 16.7% | GRAYSCALE / UPSCALE_3X / CONTRAST / SHARPEN / PSM7 / PSM10 (tied) | 66.7% | **+50.0pp** |

Three of seven fields (NIBP-Sys, EtCO2, Temp) are **already at or near their ceiling under CURRENT_BASELINE and get worse under every tested alternative.** Two fields (HR, SpO2) improve meaningfully only under PSM10. One field (RR) improves dramatically under many different variants, suggesting its baseline failure has a specific, fixable cause (see §6) rather than being font-recognition-hard in general.

---

## 5. Best configuration

**No single tested variant is a clean winner**, and per the task's own instruction this is reported honestly rather than picking the header-accuracy leader:

- **PSM10** raises overall exact accuracy the most (66.3%→75.0%) and both HR and SpO2 meaningfully, and gets RR's benefit too — but it **regresses NIBP-Systolic from a perfect 100% to 78.6%**, and its overall wrong-rate (23.8%) is *higher* than baseline's (14.5%), because it trades "give up" (missing, which downstream confidence-gating already treats as a hold-baseline signal) for "guess and be wrong" far more often. A 100%→78.6% regression on a currently-perfect, clinically load-bearing field (blood pressure) is not something to accept solely because a different field improved.
- **None of the 10 pixel-preprocessing variants (2–10) is competitive overall** — all of them are below baseline's 66.3%, several dramatically so (36.6–44.2%), driven almost entirely by **Temp's catastrophic regression** (100%→as low as 5.8%) once the crop isn't cleanly binarized before Tesseract sees the decimal point.
- RR is the one field with a broad, variant-agnostic improvement (many different preprocessing AND PSM changes all reach 66.7%, well above baseline's 16.7%) — see §6 for why: baseline's failure here looks like a crop-noise problem that several different treatments happen to suppress, not a preprocessing-quality problem in the usual sense.

Per the task's explicit rule ("a configuration that improves HR but destroys NIBP should NOT be called the winner... if no configuration clearly dominates, say NO CLEAR WINNER"):

## **NO CLEAR WINNER among the 13 tested configurations.**

---

## 6. Error analysis

Representative, non-cherry-picked failures (rendered comparison images written to [`app/eval/tier2_data/external_monitor_video/m4_ocr_report/error_gallery/`](backend/app/eval/tier2_data/external_monitor_video/m4_ocr_report/error_gallery/), original crop / CURRENT_BASELINE / PSM10 / GRAYSCALE side by side):

- **HR "0"→"10" (`sample_0017_hr.png`, `sample_0027_hr.png`; alarm/unusual-value frame).** Ground truth is a genuine on-screen `0` (extreme-bradycardia alarm state — confirmed by direct visual inspection, not inferred). Both CURRENT_BASELINE and PSM10 read `"10"` — visually confirmed in the rendered comparison that Tesseract is splitting the single "0" glyph's outer stroke and inner counter into two separate detected characters on this font at this size. Reproducible across both affected frames. **Not fixed by any tested preprocessing variant** (GRAYSCALE reads it as empty instead — no better).
- **HR under-cropped leading digit (`sample_0040_hr.png`, and 8 other 3-digit-HR frames — see raw JSON).** Ground truth is 3 digits (e.g. `183`), but the *selected candidate box itself* — which passed the IoU≥0.3 threshold against the full GT box and was correctly classified `hr` — only spans the rightmost 2 digits. Every single preprocessing/PSM variant reads only `"83"`/`"79"`/`"78"` etc., because **the missing pixels were never in the crop that reached OCR.** This is a genuine, pipeline-visible finding: category D ("correct candidate reached OCR") is necessary but not sufficient for "the crop contains the full value" — IoU≥0.3 against a 3-digit box can still be satisfied by a box that only covers ~⅔ of it. **No OCR preprocessing change can fix this** — it is a candidate-generation/selection geometry issue, out of M4.2's scope by construction.
- **SpO2 digit confusion (`sample_0003_spo2.png`): GT=98, baseline reads "93"** — a classic 8/3 glyph confusion on this font, at a case where FieldCNN and selection were both correct. **SpO2 adjacent-digit bleed (`sample_0021_spo2.png`): GT=65, baseline reads "165"** — an extra leading "1" appears, most likely bleed from the dim alarm-limit-stack digits `ANNOTATION_GUIDE.md` describes as sitting directly above/below the real value.
- **EtCO2 leading-digit insertion (`sample_0009_etco2.png`): GT=37, baseline reads "237"**; **EtCO2 digit transposition (`sample_0017`): GT=12, baseline reads "21"**. Both look like real character-recognition errors, not crop or classification problems (FieldCNN/selection correct in both cases).
- **RR small-glyph crop noise (`sample_0002_rr.png`: GT=4, baseline reads "14"; `sample_0006_rr.png`: GT=12, baseline reads "2").** RR's digits are visually the smallest bold value on this monitor's layout. Baseline's median-blur + Otsu step appears to either merge a stray adjacent pixel into the glyph (`"4"`→`"14"`) or lose enough of the glyph that only a fragment survives (`"12"`→`"2"`) — consistent with why *dropping* the blur/threshold step (GRAYSCALE, CONTRAST, SHARPEN) recovers accuracy here specifically, while the same change is disastrous for Temp's decimal point.
- **NIBP two-line reads correctly (`sample_0001_nibp.png`, `sample_0009` is a category-A candidate-gen miss instead — not evaluable): both baseline and PSM10 correctly parse `150/80` out of a much noisier raw joined string** (`'10921 | 150/80 | 403'` for baseline) — production's line-splitting + regex-search logic (`_split_text_lines`, unmodified, imported) is doing real work here and is not something this preprocessing experiment touched.
- **Unusually large digits (3-digit HR, e.g. `179`–`183` across samples 0033–0050):** covered above — dominated by the under-crop issue, not digit-size per se.
- **Alarm/unusual-value frames:** samples 0017–0047 (HR/RR literal `0`) are the only unusual-value frames in this dataset (all 52 samples carry `"conditions": ["normal"]` per `manifest.json` — there is no separate alarm-condition tag, but the *values themselves* include a real alarm-state sequence, used above).

---

## 7. Latency

Measured directly (`time.perf_counter()` around each stage), 172 category-D crops × 13 variants = 2,236 individual OCR calls, single machine:

| Variant | Preprocess mean | Tesseract mean | Total mean | Total median | Total p95 |
|---|---:|---:|---:|---:|---:|
| CURRENT_BASELINE | 0.48ms | 268.9ms | 269ms | 187ms | 686ms |
| GRAYSCALE | 0.13ms | 209.8ms | 210ms | 185ms | 376ms |
| UPSCALE_2X | 0.56ms | 228.4ms | 229ms | 201ms | 408ms |
| UPSCALE_3X | 0.65ms | 253.3ms | 254ms | 237ms | 389ms |
| CONTRAST | 0.17ms | 191.7ms | 192ms | 183ms | 285ms |
| OTSU | 0.22ms | 231.9ms | 232ms | 174ms | 563ms |
| ADAPTIVE_THRESHOLD | 1.02ms | 231.2ms | 232ms | 176ms | 547ms |
| SHARPEN | 0.52ms | 197.1ms | 198ms | 186ms | 286ms |
| UPSCALE_2X_SHARPEN | 1.19ms | 224.4ms | 226ms | 211ms | 311ms |
| UPSCALE_2X_ADAPTIVE | 6.28ms | 242.7ms | 249ms | 189ms | 570ms |
| PSM6 | ~0ms (reused baseline pixels) | 226.2ms | 226ms | 161ms | 549ms |
| PSM7 | ~0ms | 222.6ms | 223ms | 156ms | 599ms |
| PSM10 | ~0ms | 226.2ms | 226ms | 152ms | 606ms |

- **Pixel preprocessing itself is never the bottleneck** — sub-millisecond to ~6ms even for the most expensive variant (`UPSCALE_2X_ADAPTIVE`'s adaptive-threshold call), confirming M3 §14's "OCR is the bottleneck either way" call still holds.
- **Tesseract subprocess/call overhead dominates every variant** (190–270ms mean), and does not vary by more than ~35% across the entire matrix — no variant here is a meaningful latency win or loss relative to any other. PSM10's small latency edge (226ms mean, lower median than baseline) is noise-level, not a claimed real effect at this sample size.
- Candidate-generation + FieldCNN latency (upstream of everything measured here) was **not** re-measured in M4 — it is unchanged from M3 §14 (candidate gen ~1.0s mean, FieldCNN ~26ms mean) since this experiment never touches that stage; re-stated here for context, not re-verified.

---

## 8. Known limitations

- **Ground truth is manually transcribed by a single reader reading pixels directly, not a second, independently-verified label source.** This is the same rigor level `ANNOTATION_GUIDE.md`'s own boxes were produced at ("No boxes in this folder were auto-generated"), and no OCR output was ever used to produce or influence a ground-truth value — but it is still one person's read, not a dual-annotated consensus. Values that are visually unambiguous (all of them in this dataset — large, high-contrast, clean digits with zero motion blur since this is a static-camera recording) carry low risk from this, but it should not be represented as independently audited.
- **This is still the same 52-frame, single continuous recording, single monitor UI** M1/M1.1/M2/M3 all flagged. Every number above is real and honestly measured on this data, but generalization to a different monitor/vendor UI remains completely untested.
- **Support is small and uneven per field**: SpO2 (n=11) and EtCO2 (n=11) have the smallest evaluable populations here — both because dashes/alarm-states removed many frames from the GT-available pool (category E) and because SpO2 specifically lost 13/27 to the competing-candidates safety net (§2.4). A single-frame swing changes SpO2's/EtCO2's percentage by ~9 points. NIBP-Sys/Dia (n=14) is similarly modest. **HR (n=40), Temp (n=52), and RR (n=30) are on firmer footing.** No field here reaches "n=1, 100%" — but several are close enough to double-digit n that a couple of flipped frames would meaningfully move the percentage; treat single-digit-pp differences between variants on SpO2/EtCO2/NIBP as within noise.
- **The SpO2/RR "competing_candidates" pattern (§2.4) was not investigated beyond confirming its cause is `_select_candidate_for_vital`'s designed safety margin, not a bug** — worth a closer look (is it really the Pulse-vs-SpO2 confusion the annotation guide warns about?) in a future milestone, but that is Tier-2 selection-logic work, not OCR preprocessing, and explicitly out of scope here.
- **The two known M2 candidate/classifier failure modes (HR fragmentation, RR→not_a_vital at 94.5%) both reproduced exactly as documented** and were, correctly, left unaddressed — M4 was not asked to fix them, and no threshold or logic was touched.
- **The newly-discovered "IoU≥0.3 but geometrically incomplete crop" pattern for multi-digit HR (§6)** is a real finding this run surfaced that neither M2 nor M3 called out explicitly (M2 discussed full fragmentation misses; this is a partial-crop case that still counts as category D). It deserves attention before any OCR-only fix is expected to help HR much further — no preprocessing change can recover pixels that were never in the crop.
- **Tesseract's own confidence is not a clean predictor of correctness here** (§2.5: baseline's lowest confidence bucket `[0,20)`, n=78, still shows 52.6% accuracy) — consistent with M3's own observation (`sample_0017` NIBP: correct value, confidence 0), now confirmed at scale across all 14 NIBP crops (28 sys/dia field reads) rather than one anecdote. Any future work using OCR confidence as a gating signal should not assume monotonic calibration on this dataset without re-checking it.

---

## 9. Production recommendation

## ITERATE M4.2

**Why not GO TO M4.3:** M4.3 is reliability/validation work built on top of whatever OCR configuration M4.2 lands on. Shipping PSM10 (the current best single-number result) into that work would mean validating against a configuration that is known, right now, to regress NIBP-Systolic from 100% to 78.6% — accepting a real safety regression on a field that already works, in exchange for gains on other fields, is exactly the failure mode §5's rule was written to prevent. Locking in "PSM10 everywhere" before checking a cheaper, more targeted alternative would be premature.

**What experiment is missing, precisely:** the current matrix conflates "which PSM value" with "applied uniformly to every vital." The per-vital results (§4.1) show the *optimal* choice is not uniform — HR/SpO2/RR prefer PSM10, EtCO2/Temp/NIBP prefer CURRENT_BASELINE's existing PSM 8/6 split. Production's `ocr.py` **already varies OCR config by vital type** (`_DIGIT_CONFIG` vs `_DECIMAL_CONFIG` vs `_NIBP_CONFIG`) — so the natural next controlled variant, not yet tested as its own row, is:

> **`PSM10_SELECTIVE`**: CURRENT_BASELINE pixel preprocessing (unchanged) + `--psm 10` for HR, SpO2, and RR only; keep `--psm 8` for EtCO2, `--psm 8`/decimal for Temp, and `--psm 6` for NIBP exactly as production does today.

This is a config-selection change (which existing, already-controlled-variant PSM value applies to which vital), not a new preprocessing idea and not a per-frame/per-value hardcoded correction — squarely inside what M4.2 was scoped to test and did not yet cover. If `PSM10_SELECTIVE` recovers HR/SpO2/RR's gains while holding NIBP/EtCO2/Temp at baseline's current (near-)ceiling, *that* would be a legitimate, non-regressing candidate for M4.3. If it does not — if e.g. EtCO2's own PSM10 regression turns out to share a root cause with HR/SpO2's improvement — that itself is a useful, cheap thing to learn before M4.3 starts.

Two structural issues surfaced here are **not** OCR-preprocessing questions and should be scoped as their own follow-ups, not folded into M4.2:
1. The multi-digit-HR under-crop pattern (§6) — a candidate-generation/selection geometry question.
2. The SpO2/RR competing-candidates rate (§2.4) — a Tier-2 selection-logic question, possibly the documented Pulse-vs-SpO2 confusion.

---

*No production code was modified. No commits or tags were made. Verified: `git status` shows the only new files this session are `backend/app/eval/m4_*.py` and new content under `backend/app/eval/tier2_data/external_monitor_video/m4_ocr_report/`; the pre-existing modified/untracked files (`app/api/pipeline.py`, `app/pipeline/read_frame.py`, `app/pipeline/types.py`, `app/ws/vitals.py`, the M1–M3 eval/pipeline files, `models/*.onnx`, `src/*`) all predate this session per their mtimes and were only read, never written, here. `models/field_classifier.onnx` (mtime unchanged, Aug 18 20:12) and `models/digit_cnn.onnx` (mtime unchanged, Aug 13 21:36) were never touched. All 52 annotation JSONs unchanged (spot-checked mtimes predate this session's start). Full backend test suite re-run after all M4 work: **228 passed, 0 failed, 0 skipped, 1 pre-existing warning** — identical to the pre-M4 baseline.*
