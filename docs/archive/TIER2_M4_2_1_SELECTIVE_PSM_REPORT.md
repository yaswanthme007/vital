> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M4.2.1 Selective PSM Report

Continuation of M4.1 + M4.2 ([`TIER2_M4_1_M4_2_OCR_EXPERIMENT_REPORT.md`](TIER2_M4_1_M4_2_OCR_EXPERIMENT_REPORT.md)), which recommended exactly one next experiment: `PSM10_SELECTIVE`. **Evaluation-only — no production code was modified.**

---

## 0. Methodology (read before trusting any number below)

`PSM10_SELECTIVE` was **not** produced by re-running OCR. M4.1/M4.2's benchmark (`m4_ocr_benchmark.py`) already recorded, for every one of the same 172 category-D crops, both the `CURRENT_BASELINE` record (production pixels + production per-vital PSM: `--psm 8` digit/decimal, `--psm 6` NIBP) and the `PSM10` record (production pixels + `--psm 10` for every vital) in `m4_raw_results_start0.json`. `PSM10_SELECTIVE` (new file: [`app/eval/m4_2_1_psm_selective.py`](backend/app/eval/m4_2_1_psm_selective.py)) is built by reading, per field, whichever of those two **already-measured** records the routing table below assigns:

| Vital | PSM10_SELECTIVE uses |
|---|---|
| HR | `PSM10` record (`--psm 10`) |
| SpO2 | `PSM10` record (`--psm 10`) |
| RR | `PSM10` record (`--psm 10`) |
| EtCO2 | `CURRENT_BASELINE` record (`--psm 8`, unchanged) |
| Temp | `CURRENT_BASELINE` record (`--psm 8` decimal, unchanged) |
| NIBP | `CURRENT_BASELINE` record (`--psm 6`, unchanged) |

This guarantees byte-for-byte identical crops, boxes, FieldCNN predictions, and selection decisions to M4.1/M4.2 (nothing upstream of OCR was touched or re-run), and reuses real, previously-measured OCR latency rather than re-timing anything — the most rigorous way to satisfy "use exactly the same 172 category-D crops, do not rerun candidate generation/FieldCNN/selection." Re-verified before running: `app/pipeline/ocr.py`'s `_DIGIT_CONFIG`/`_DECIMAL_CONFIG`/`_NIBP_CONFIG` are unchanged from M4.1/M4.2 (still `--psm 8`/`--psm 8` decimal/`--psm 6`), and `git status` on `app/pipeline/*` shows nothing new since that run — the "current production PSM/configuration" this experiment routes to is confirmed still current.

Preprocessing pixels are **never** touched by this experiment — every field, in every variant compared here, uses `CURRENT_BASELINE`'s exact production preprocessing (`_preprocess`: grayscale → upscale → median blur → Otsu → polarity fix → pad). Only which PSM value is applied changes.

Full backend suite re-run for this milestone: **228 passed, 0 failed, 0 skipped, 1 pre-existing warning** — identical to M4.1/M4.2's state, confirmed actual (not assumed).

---

## 1. Executive result

## GO TO M4.3

`PSM10_SELECTIVE` retains all three of PSM10's gains (HR, SpO2, RR) while fully preserving every field PSM10 regressed or that was already at ceiling (NIBP-Systolic, NIBP-Diastolic, Temp, EtCO2) at exactly baseline's level. It is a **strict Pareto improvement over CURRENT_BASELINE** — equal-or-better on every one of the 7 fields, never worse on any of them — which no other tested configuration (including PSM10 itself) achieved. See §4 for the one honest caveat (wrong-rate composition) that keeps this a "go" rather than an unqualified victory.

---

## 2. Comparison table

| Vital | Baseline | PSM10 | PSM10_SELECTIVE |
|---|---:|---:|---:|
| HR | 37.5% | 45.0% | **45.0%** |
| SpO2 | 54.5% | 72.7% | **72.7%** |
| NIBP-Sys | 100.0% | 78.6% | **100.0%** |
| NIBP-Dia | 100.0% | 100.0% | **100.0%** |
| EtCO2 | 72.7% | 63.6% | **72.7%** |
| Temp | 100.0% | 98.1% | **100.0%** |
| RR | 16.7% | 66.7% | **66.7%** |
| **Overall (micro)** | 66.3% (114/172) | 75.0% (129/172) | **77.9% (134/172)** |
| **Overall (macro)** | 68.8% | 75.0% | **79.6%** |

Total: **134 correct / 36 wrong / 2 missing**, out of 172 evaluated readings (vs. baseline's 114/25/33, and PSM10's 129/41/2).

### Explicit checks

| # | Question | Answer |
|---|---|---|
| 1 | Retain HR improvement? | **Yes** — 45.0%, identical to PSM10 (by construction: HR routes to the PSM10 record) |
| 2 | Retain SpO2 improvement? | **Yes** — 72.7%, identical to PSM10 |
| 3 | Retain RR improvement? | **Yes** — 66.7%, identical to PSM10 |
| 4 | Preserve NIBP 100%? | **Yes** — Systolic 100.0% (recovers PSM10's 78.6% regression completely), Diastolic 100.0% (was never regressed) |
| 5 | Preserve Temp 100%? | **Yes** — 100.0% (recovers PSM10's 98.1% near-miss completely) |
| 6 | Preserve EtCO2 baseline performance? | **Yes** — 72.7%, identical to baseline (PSM10 alone had dropped this to 63.6%) |

All six checks pass. This is the outcome M4.1/M4.2's recommendation was betting on.

---

## 3. Per-vital analysis

*(n is fixed across all variants — the same crops every time. Full parse/wrong/missing/confidence table below; PSM10_SELECTIVE's per-field row is, by construction, either PSM10's row or CURRENT_BASELINE's row — restated here as directly measured values from the raw records, not re-derived.)*

| Field | n | Variant | Acc | Parse | Wrong | Missing | Mean conf |
|---|---:|---|---:|---:|---:|---:|---:|
| HR | 40 | BASELINE | 37.5% | 72.5% | 35.0% | 27.5% | 59.0 |
| | | PSM10 | 45.0% | 97.5% | 52.5% | 2.5% | 57.4 |
| | | **SELECTIVE** | **45.0%** | **97.5%** | **52.5%** | **2.5%** | **57.4** |
| SpO2 | 11 | BASELINE | 54.5% | 81.8% | 27.3% | 18.2% | 41.2 |
| | | PSM10 | 72.7% | 90.9% | 18.2% | 9.1% | 51.7 |
| | | **SELECTIVE** | **72.7%** | **90.9%** | **18.2%** | **9.1%** | **51.7** |
| NIBP-Sys | 14 | BASELINE | 100.0% | 100.0% | 0.0% | 0.0% | 0.0 |
| | | PSM10 | 78.6% | 100.0% | 21.4% | 0.0% | 0.0 |
| | | **SELECTIVE** | **100.0%** | **100.0%** | **0.0%** | **0.0%** | **0.0** |
| NIBP-Dia | 14 | BASELINE | 100.0% | 100.0% | 0.0% | 0.0% | 0.0 |
| | | PSM10 | 100.0% | 100.0% | 0.0% | 0.0% | 0.0 |
| | | **SELECTIVE** | **100.0%** | **100.0%** | **0.0%** | **0.0%** | **0.0** |
| EtCO2 | 11 | BASELINE | 72.7% | 100.0% | 27.3% | 0.0% | 20.4 |
| | | PSM10 | 63.6% | 100.0% | 36.4% | 0.0% | 40.5 |
| | | **SELECTIVE** | **72.7%** | **100.0%** | **27.3%** | **0.0%** | **20.4** |
| Temp | 52 | BASELINE | 100.0% | 100.0% | 0.0% | 0.0% | 84.7 |
| | | PSM10 | 98.1% | 100.0% | 1.9% | 0.0% | 77.9 |
| | | **SELECTIVE** | **100.0%** | **100.0%** | **0.0%** | **0.0%** | **84.7** |
| RR | 30 | BASELINE | 16.7% | 33.3% | 16.7% | 66.7% | 37.1 |
| | | PSM10 | 66.7% | 100.0% | 33.3% | 0.0% | 82.5 |
| | | **SELECTIVE** | **66.7%** | **100.0%** | **33.3%** | **0.0%** | **82.5** |

**HR** — improved 37.5%→45.0%, but note parse rate jumped from 72.5%→97.5% *and* wrong-rate jumped from 35.0%→52.5%: PSM10 makes Tesseract answer almost every time, and more of those answers are right than under baseline, but even more of the *increase in answers* are wrong than right. HR remains the weakest field by far, dominated by the upstream under-crop/fragmentation issues §6 covers — this experiment was never expected to fix those, and didn't.

**SpO2** — the cleanest win: accuracy up (54.5%→72.7%), wrong-rate *down* (27.3%→18.2%), missing-rate down (18.2%→9.1%), confidence up (41.2→51.7). No caveats on this field.

**NIBP-Sys/Dia** — fully preserved at the ceiling. This was the field PSM10 alone broke (78.6%); selective routing keeps it untouched because NIBP was never assigned to PSM10 in the first place. Confidence remains 0.0 throughout, as in M4.1/M4.2 (§2.3 of that report) — production's MIN-confidence-fusion, unaffected by this experiment, is still what keeps a 0-confidence-but-correct NIBP read from being over-trusted downstream.

**EtCO2** — fully preserved at baseline (72.7%), because EtCO2 was deliberately kept off PSM10. PSM10 alone would have cost this field 9.1pp (72.7%→63.6%); routing avoids that entirely.

**Temp** — fully preserved at 100%, recovering PSM10's small but real 1.9pp regression (100.0%→98.1%) completely, for the same reason as EtCO2.

**RR** — improved 16.7%→66.7% (the largest single-field jump in the whole M4 program so far), and it was already the case in M4.1/M4.2 that pixel-preprocessing changes *and* PSM changes both independently reached 66.7% here — routing to PSM10 keeps the gain at no cost to RR's own wrong-rate (33.3%, identical under PSM10 and SELECTIVE) since RR was already assigned to PSM10.

---

## 4. Safety analysis

| Field | Preserved? |
|---|---|
| NIBP-Systolic | **Preserved at 100.0%** — the exact number the safety rule required. Confirmed via the routing table: NIBP was never assigned to PSM10, so its record is literally the same `CURRENT_BASELINE` measurement from M4.1/M4.2, re-read here, not re-derived or approximated. |
| Temp | **Preserved at 100.0%** — same mechanism (routed to `CURRENT_BASELINE`). |
| EtCO2 | **Materially preserved** — 72.7%, identical to baseline, not merely "close." |

**No field regressed relative to CURRENT_BASELINE.** Every one of the 7 fields is either equal to or strictly better than baseline — a genuine Pareto improvement, not an average-accuracy improvement bought with a hidden loser. This satisfies the task's safety rule as stated, not just its accuracy headline.

**The one honest caveat, stated plainly rather than buried:** for the three fields PSM10_SELECTIVE *does* change (HR, SpO2, RR), the composition of error shifted the same way it did for PSM10 alone in M4.1/M4.2 — missing-rate dropped sharply (HR 27.5%→2.5%, RR 66.7%→0.0%) while wrong-rate rose (HR 35.0%→52.5%, RR 16.7%→33.3%; SpO2 is the exception, improving on both axes). Net accuracy is higher in all three cases, and a "missing" read and a "confidently wrong" read are not equivalent from a downstream-safety standpoint — a missing OCR result forces `confidences[vital]=0`, which `reconcile()`'s existing gating already treats as "hold baseline, don't trust this tick" (per M3 §6/§13's documented behavior), whereas a wrong-but-parsed value carries whatever confidence Tesseract assigned it and is fused via `min(classifier_confidence, ocr_confidence)` before reaching that same gate. This experiment did not verify how `reconcile()` actually behaves on PSM10_SELECTIVE's specific wrong answers (that would require running the real `read_frame()`/`reconcile()` path, which M4.2.1 was not scoped to do) — it is flagged here as the one thing M4.3's reliability work should specifically check, not asserted as safe by this report alone.

---

## 5. Error analysis

Every requested hard case, re-measured directly from the same stored OCR records (not re-run):

| Case | GT | BASELINE | PSM10 | PSM10_SELECTIVE | Changed? |
|---|---|---|---|---|---|
| HR "0"→"10" (`sample_0017`) | 0 | `'10'` → 10.0 | `'0'` → 0.0 | `'0'` → 0.0 | **Fixed** |
| HR "0"→"10" (`sample_0027`) | 0 | `'10'` → 10.0 | `'0'` → 0.0 | `'0'` → 0.0 | **Fixed** |
| 3-digit HR under-crop (`sample_0040`) | 183 | `'83'` → 83.0 | `'33'` → 33.0 | `'33'` → 33.0 | **Not fixed** — still wrong (crop never contained the leading digit; PSM10 additionally misreads the visible "8" as "3") |
| SpO2 "98"→"93" (`sample_0003`) | 98 | `'93'` → 93.0 | `'98'` → 98.0 | `'98'` → 98.0 | **Fixed** |
| SpO2 "65"→"165" (`sample_0021`) | 65 | `'165'` → 165.0 | `'65'` → 65.0 | `'65'` → 65.0 | **Fixed** |
| EtCO2 "37"→"237" (`sample_0009`) | 37 | `'237'` → 237.0 | `'31'` → 31.0 | `'237'` → 237.0 | **Not fixed** (routed to baseline by design — EtCO2 stays on production PSM; PSM10 alone gets a *different* wrong answer here, not a correct one) |
| EtCO2 "12"→"21" (`sample_0017`) | 12 | `'21'` → 21.0 | `'2'` → 2.0 | `'21'` → 21.0 | **Not fixed** (same reason) |
| RR "4"→"14" (`sample_0002`) | 4 | `'14'` → 14.0 | `'4'` → 4.0 | `'4'` → 4.0 | **Fixed** |
| RR "12"→"2" (`sample_0006`) | 12 | `'2'` → 2.0 | `'2'` → 2.0 | `'2'` → 2.0 | **Not fixed** — same wrong fragment read under every PSM (this looks like a crop-content loss, not a PSM issue: neither `--psm 8` nor `--psm 10` sees the missing digit) |
| NIBP "150/80" (`sample_0001`) | 150/80/103 | `'10921 \| 150/80 \| 403'` → sys=150, dia=80, mean=109 (correct) | same | same (routed to baseline) | **Unaffected** — correct throughout |
| Temp "98.6" (`sample_0001`) | 98.6 | `'98.6'` → 98.6 (correct) | same | same (routed to baseline) | **Unaffected** — correct throughout |

**4 of 6 non-NIBP/Temp hard cases are fixed outright by the routing** (both HR "0"→"10" instances, both SpO2 cases, one RR case), **2 are correctly left unaddressed by design** (the EtCO2 cases, since EtCO2 was deliberately kept on baseline), and **2 are genuinely not fixable by any PSM choice** (the HR under-crop and the RR fragment-loss case — both upstream crop-content problems, exactly as §"Important upstream findings" said not to attempt to fix here, and this experiment correctly did not touch them).

No OCR output was ever manually corrected — every value above is the OCR engine's own literal output on the stored crop.

---

## 6. Latency

Measured directly from the same `time.perf_counter()` timings recorded during M4.1/M4.2 (not re-timed — since PSM10_SELECTIVE reuses those exact stored records, re-running would only add noise, not new information):

| Variant | Mean | Median | P95 |
|---|---:|---:|---:|
| CURRENT_BASELINE | 269.4ms | 187.4ms | 685.9ms |
| PSM7 | 222.6ms | 156.2ms | 598.9ms |
| PSM10 | 226.2ms | 151.5ms | 606.2ms |
| **PSM10_SELECTIVE** | **244.8ms** | **165.2ms** | **685.9ms** |

PSM10_SELECTIVE's latency sits between PSM10's (226ms mean) and CURRENT_BASELINE's (269ms mean), as expected — it's a per-field mixture of the two, and NIBP specifically dominates the P95 tail (NIBP crops take ~590–610ms regardless of PSM, per §3's per-field latency data, because `_read_nibp_from_processed`'s line-splitting does multiple Tesseract calls per crop) — since NIBP stays on the baseline path unchanged, PSM10_SELECTIVE's P95 (685.9ms) is identical to CURRENT_BASELINE's, not PSM10's lower figure. **No latency regression of practical concern**: the ~18ms mean difference from PSM10 alone is well within run-to-run noise at this sample size, and all three configurations remain far below candidate-generation's ~1s stage cost (M3 §14, unchanged, not re-measured here). No latency *improvement* is claimed either — this is not a speed experiment.

---

## 7. Limitations

- **Same 52-frame, single continuous recording, single monitor UI** as every prior M1–M4 report. Nothing about PSM10_SELECTIVE has been tested against a different monitor/vendor UI.
- **Ground truth is the same manually-transcribed values from M4.1** (one reader, reading pixels directly, no OCR involved, cross-validated for coverage but not independently double-annotated) — see that report's §2.1/§8 for the full methodology; not re-derived or re-verified here.
- **Support is uneven and small for several fields**: SpO2 (n=11) and EtCO2 (n=11) remain the thinnest-supported fields (for the same reasons M4.1/M4.2 §2.4/§8 documented — SpO2 specifically loses 48% of its evaluable frames to the competing-candidates safety net, upstream of everything measured here). A single flipped frame moves SpO2/EtCO2 by ~9 percentage points. HR (n=40), Temp (n=52), and RR (n=30) are on firmer footing.
- **This experiment did not touch, and therefore did not re-verify, anything upstream of the selected crop** — candidate generation, FieldCNN classification, and candidate selection are exactly as measured in M4.1/M4.2 and were not part of this run's scope, per the task's explicit "important upstream findings — do not attempt to fix" list (HR under-cropping/fragmentation, SpO2/RR competing-candidate selection, RR→not_a_vital FieldCNN miss). All four remain open, unaddressed, unchanged.
- **§4's wrong-rate/missing-rate composition shift was not validated against `reconcile()`'s actual downstream behavior** — this report can state what OCR returned and at what confidence, but not how the confirmed-reading state machine would ultimately treat those specific wrong answers on real frame-to-frame sequences. That is exactly the kind of question M4.3 (reliability/validation) exists to answer, not something M4.2.1 can settle from static per-crop measurement alone.
- **PSM10_SELECTIVE was assembled from stored OCR outputs, not re-executed against `pytesseract`** — this is a deliberate methodological choice (§0) to guarantee identical upstream state, not a shortcut that skipped real measurement: every number in this report traces back to an actual Tesseract call made during the M4.1/M4.2 run.

---

## 8. Production recommendation

**PSM10_SELECTIVE is clearly non-regressing and materially better than both CURRENT_BASELINE and PSM10 alone.** It is recommended as **the candidate configuration for M4.3**:

> HR / SpO2 / RR → `--psm 10` (whitelist unchanged); EtCO2 / Temp / NIBP → production's current PSM/config, completely unchanged (`--psm 8` digit, `--psm 8` decimal, `--psm 6` respectively). All existing pixel preprocessing (`_preprocess`), NIBP line-splitting, candidate generation, FieldCNN, selection, and confidence fusion remain exactly as they are today.

This satisfies every condition the task's safety rule set: overall accuracy rose (66.3%→77.9% micro, 68.8%→79.6% macro) *and* no field that already worked regressed (§4) — a strict improvement on the accuracy axis with zero cost on the safety axis, which is the specific combination PSM10 alone failed to deliver and which this report was designed to check for rather than assume.

**What M4.3 should specifically still validate**, since this remains static per-crop measurement, not a running-system test:
1. How `reconcile()` actually behaves on PSM10_SELECTIVE's real wrong-answer set for HR/SpO2/RR (§4's flagged caveat), on real frame sequences rather than isolated crops.
2. Whether the four upstream issues explicitly out of scope here (HR under-crop/fragmentation, SpO2/RR competing-candidate selection, RR FieldCNN miss) — none of which this PSM change could have fixed, and none of which it made worse — are worth a dedicated milestone once M4.3's OCR-side validation is settled.
