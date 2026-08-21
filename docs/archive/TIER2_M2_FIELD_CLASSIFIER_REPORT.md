> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M2 Field Classifier Report

Continuation of M1 ([`TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md`](TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md)) and M1.1 ([`TIER2_M1_1_HARDENING_REPORT.md`](TIER2_M1_1_HARDENING_REPORT.md)). Field-classifier build + isolated evaluation only — no production integration, same frozen 52-image/199-box external-monitor annotation set (`backend/app/eval/tier2_data/external_monitor_video/`, unmodified — verified again at the end).

```
candidate crop (adaptive_threshold_candidates_v2, M1.1's recommended generator)
      ↓
FieldCNN  (new — NOT the digit CNN)
      ↓
HR / SpO2 / NIBP / EtCO2 / Temp / RR / NOT_A_VITAL
```

New code, all under `backend/app/eval/`, nothing in `app/pipeline/*` touched:

| File | Purpose |
|---|---|
| `tier2_field_dataset.py` | Phase 1 — candidate labeling, image-level split, balancing, augmentation |
| `tier2_field_classifier.py` | Phase 2/3/6 — `FieldCNN`, training, held-out test, ONNX export + agreement check |
| `tier2_field_pipeline_eval.py` | Phase 4/5 — end-to-end candidate→classifier eval + hard-case overlays, on TEST images only |

Artifacts written: `backend/models/field_classifier.{onnx,labels.json,preprocess.json,train_report.json}` (distinct filenames — `digit_cnn.onnx` untouched), `backend/app/eval/tier2_data/external_monitor_video/tier2_field_dataset/*`, `.../tier2_m2_report/*`. **190/190 existing backend tests still pass; all 52 `sample_XXXX.json` annotation files confirmed unmodified (0 touched in the last hour). Nothing committed or tagged.**

---

## 1. Existing CNN architecture assessment

`simulator/train/train_cnn.py`'s `DigitCNN` was **not** reused as-is, and shouldn't be — it solves a different problem:

| | `DigitCNN` (existing) | `FieldCNN` (new, this milestone) |
|---|---|---|
| Input | 28×28 grayscale, already-segmented single glyph | 64×64 grayscale, whole candidate box (multi-glyph field or non-field UI region) |
| Classes | 13 (`0`-`9`, `/`, `.`, `blank`) | 7 (`hr`,`spo2`,`nibp`,`etco2`,`temp`,`rr`,`not_a_vital`) |
| Task | "which character is this" | "does this region look like vital X (or nothing at all)", using layout/density/context, not one glyph's shape |
| Training data | Simulator-rendered, effectively unlimited, per-glyph labels from known rendered strings | Real annotated external-video frames only, ~200 positive candidates total |
| Preprocessing | `segment.normalize_cell` — binary-ish ink cell, tight crop | `tier2_field_dataset._letterbox_gray` — raw grayscale (not binarized), because a field classifier needs surrounding visual context (digit density, multi-line structure, whether there's a waveform trace nearby), not just ink presence |

Blindly repointing `DigitCNN` at field crops would have meant: no bounding-box/context signal (single glyph classifiers are trained to ignore everything outside a tight glyph box — exactly the wrong prior here), a 13-class digit vocabulary with no `not_a_vital` reject concept, and a 28×28 canvas that would crush a full NIBP two-line block or a 3-digit HR reading into a blur. `FieldCNN` (§7) reuses `DigitCNN`'s *style* — small conv stack, `torch.onnx.export` with the same opset/dynamic-axes pattern, same class-weighted-CrossEntropy + best-checkpoint-by-validation-metric training idiom — but is a distinct, bigger, 7-class network with BatchNorm added for stability on real photographic/JPEG noise that the synthetic digit dataset never had to handle.

---

## 2. Dataset construction

Built by `tier2_field_dataset.py` from **every candidate `adaptive_threshold_candidates_v2` (M1.1's recommended generator) produces on all 52 images** — 1,450 candidates total. Each candidate is scored by IoU against every ground-truth vital box present in that image; per M1/M1.1's own established convention (`TIER2_RECOGNITION_SPIKE.md` §03/§08), **IoU ≥ 0.3 is a match**. A candidate overlapping multiple GT boxes is assigned to the **highest-IoU** one (deterministic argmax; ties broken by fixed vital order `hr,spo2,nibp,etco2,temp,rr`). Below 0.3, or with no GT box in the image at all, the candidate is `not_a_vital` — **every candidate gets a label; none are dropped as ambiguous.**

```
1,450 candidates → hr:40  spo2:24  nibp:14  etco2:11  temp:52  rr:39  not_a_vital:1,270
```

This total is a ceiling, not a guarantee: candidate recall on the full 52-image set is 90.5% (M1.1), so some GT boxes contribute **zero** positive candidates (e.g. etco2 is 17 GT boxes but only 11 ever get a matching candidate anywhere in the dataset). That gap is intentional and is exactly what Phase 4 (§13) is built to separate from classifier error.

---

## 3. Exact train/validation/test IMAGE split

52 source images, one continuous recording, near-identical framing (per M1/M1.1). A plain per-image random split would still let a near-duplicate **neighbouring** frame land in a different split than its twin — real leakage even though it technically satisfies "no crop from one image in two splits." `build_image_split()` instead:

1. Groups images into **`CHUNK_SIZE=2` contiguous id-blocks** (26 chunks) so adjacent near-duplicate frames always stay together.
2. Finds the dataset's two globally rarest vitals by actual count (`etco2`=17, `nibp`=17 — computed, not assumed) and stratifies chunks by whether they contain both/one/neither.
3. Seeded (`SPLIT_SEED=0`) shuffle within each stratum; **val and test are each guaranteed one chunk containing BOTH rare vitals** before anything else is allocated, then topped up with "neither" chunks to ~15% of images each; everything left over goes to train. An assertion after the split hard-fails the run if val or test end up missing a rare vital — it didn't fire.

| Split | Images | IDs |
|---|---:|---|
| **Train** | 36 | `sample_0001–0010, 0013–0016, 0019–0022, 0029–0034, 0039–0042, 0045–0052` |
| **Val** | 8 | `sample_0011, 0012, 0023, 0024, 0027, 0028, 0043, 0044` |
| **Test** | 8 | `sample_0017, 0018, 0025, 0026, 0035, 0036, 0037, 0038` |

No image appears in more than one split (asserted in code). Full IDs and the split algorithm's provenance are also written to `tier2_field_dataset/report.json`.

---

## 4. Class distribution

**Before balancing**, per split (raw candidate counts):

| Split | hr | spo2 | nibp | etco2 | temp | rr | not_a_vital |
|---|---:|---:|---:|---:|---:|---:|---:|
| Train | 26 | 20 | 10 | 10 | 36 | 24 | **858** |
| Val | 8 | 2 | 2 | **0** | 8 | 8 | **204** |
| Test | 6 | 2 | 2 | 1 | 8 | 7 | **208** |

`not_a_vital` is 87–92% of every split — exactly the "98% accuracy by mostly predicting the majority class" trap the M2 spec warns about, which is why every metric below is reported per-class, never as bare accuracy. **Val has zero real `etco2` candidates** — no candidate anywhere in val's 8 images cleared IoU≥0.3 against an etco2 box, itself a candidate-generation-recall fact, not a labeling bug (etco2 recall is the weakest of M1.1's six vitals at 64.7% overall). This is reported plainly, not hidden: val's etco2 metrics are structurally undefined and are called out as such, not filled in.

**Training-split balancing** (val/test are **never** touched — they stay exactly as produced above, for honest evaluation):

| Class | Real (train) | Augmented added | **Final train count** |
|---|---:|---:|---:|
| hr | 26 | 4 | 30 |
| spo2 | 20 | 10 | 30 |
| nibp | 10 | 20 | 30 |
| etco2 | 10 | 20 | 30 |
| temp | 36 | 0 | 36 |
| rr | 24 | 6 | 30 |
| not_a_vital | 858 → **subsampled to 144** | 0 | 144 |

Strategy (chosen from the measured train distribution above, not guessed):
1. **Bounded negative subsampling** — `not_a_vital` capped at 4× the largest positive class's count (144), a seeded random subsample (no duplication), rather than left at a 24–86:1 raw ratio against individual positive classes.
2. **Controlled oversampling + augmentation** for positive classes below a floor (30 = half the largest real positive class) — every added example is an augmented (§6) copy of a **real** annotated crop, capped at 6× copies per original so no single real example gets cloned into false confidence. No synthetic positives were invented from nothing, per the M2 spec.
3. Residual imbalance (36 vs 30 vs 144) is absorbed by **inverse-frequency class weighting** in the training loss — `train_cnn.py`'s own existing idiom, reused unmodified in spirit.

Final materialized dataset: **train=330, val=232, test=234** crops (`field_crops.npz`).

---

## 5. Negative-class construction

`not_a_vital` candidates are the M1.1 v2 generator's own false-positive output on real UI — **not synthetic background**, per the spec's explicit requirement. A heuristic (position/shape only, **not ground truth** — this dataset has no per-negative content label, and the breakdown below is reported as an approximation, not measured fact) buckets them for reporting:

| Category | Train | Val | Test | What it approximates |
|---|---:|---:|---:|---|
| `near_miss_<vital>` (sum) | 104 | 16 | 20 | A candidate that partially found a real vital but stayed under IoU 0.3 — the hardest negative class, structurally close to a true positive |
| `header_band` | 155 | 40 | 33 | Top-12%-of-frame content — date/patient-banner region (M1.1 §9) |
| `toolbar_band` | 151 | 34 | 38 | Bottom-10%-of-frame content — button toolbar |
| `other` | 448 | 114 | 118 | Waveform panels, alarm banners, scale numbers, residual UI text — everything not caught by the buckets above |

`near_miss_*` negatives are the most valuable hard negatives here (a classifier that can't distinguish "almost NIBP" from "actually NIBP" would be useless) and are present in real numbers across every split — 104 in train alone. Full breakdown in `tier2_field_dataset/report.json`.

---

## 6. Augmentation strategy

Inspected `simulator/randomize/augment.py` (perspective/glare/dim/blur/noise/JPEG — proven, reusable in spirit) but that pipeline operates on full synthetic renders with known ground truth; `_augment_once()` in `tier2_field_dataset.py` is a fresh, smaller implementation operating directly on **real** crops, applied **only** to training-split positive classes below their floor (never to val/test, never to negatives — negatives are bounded-subsampled, not augmented, since there's no shortage of real ones):

- Brightness (±18) / contrast (0.85–1.15×) jitter
- Mild Gaussian blur (50% chance, kernel 3 or 5)
- Small scale (0.92–1.08×) + translation (±4%) + rotation (±4°) via one affine warp
- Mild perspective jitter (35% chance, ≤3% corner displacement)
- JPEG-compression re-encode (60% chance, quality 35–80)
- Sensor noise (50% chance, σ 2–8)

Deliberately **no colour jitter** — the classifier is grayscale end-to-end (`_letterbox_gray` converts before anything else touches the crop), so it structurally cannot use colour as a signal at all, satisfying the spec's "must learn visual structure … not colour" requirement by construction rather than by hoping augmentation would suppress a colour shortcut. No transform invents a monitor layout or changes what a human would call the crop's class (no large rotation/perspective that would turn a real digit block into something a monitor never renders).

---

## 7. Model architecture

`FieldCNN` (`tier2_field_classifier.py`) — 4 conv blocks with BatchNorm, ~277K parameters:

```
Input: 1×64×64 grayscale
Conv(1→16,3×3) → BN → ReLU → MaxPool(2)   → 32×32
Conv(16→32,3×3) → BN → ReLU → MaxPool(2)  → 16×16
Conv(32→64,3×3) → BN → ReLU → MaxPool(2)  → 8×8
Conv(64→96,3×3) → BN → ReLU → MaxPool(2)  → 4×4
Flatten(96×4×4=1536) → Linear(128) → ReLU → Dropout(0.45) → Linear(7)
```

Deliberately still small: with only a few hundred real training crops even after bounded augmentation, a bigger network would memorize rather than generalize. BatchNorm (absent from `DigitCNN`) was added because real photographic/JPEG noise makes training measurably less stable than the synthetic digit dataset — confirmed during development (loss without BN oscillated harder in early epochs on this data).

---

## 8. Training configuration

| Setting | Value |
|---|---|
| Optimizer | Adam, lr=1e-3, weight_decay=1e-4 |
| Loss | CrossEntropy, inverse-frequency class weights (from final balanced train set) |
| Batch size | 32 |
| Epochs | 40 |
| Seed | 0 (`_set_seed` — random/numpy/torch) |
| Checkpoint selection | Best **validation macro F1** (not accuracy — accuracy on an 88%-negative val set is a poor selection signal per the spec's own warning) |
| Device | CPU |
| Train wall-clock | 31.6s |

---

## 9. Training curves

Train loss falls sharply and stays low after ~epoch 5 (expected — 330 examples, small model); val macro F1 is the real signal and plateaus around epoch 25–27:

| Epoch | train_loss | val_loss | val_macro_F1 | val_weighted_F1 |
|---:|---:|---:|---:|---:|
| 1 | 1.2431 | 1.4935 | 0.156 | 0.823 |
| 5 | 0.1109 | 0.2371 | 0.743 | 0.949 |
| 10 | 0.2020 | 0.4693 | 0.629 | 0.920 |
| 15 | 0.0474 | 0.2251 | 0.767 | 0.968 |
| 20 | 0.0346 | 0.2192 | 0.763 | 0.965 |
| 25 | 0.0277 | 0.1464 | 0.775 | 0.974 |
| **27 (best)** | **0.0182** | **0.1716** | **0.7848** | **0.9786** |
| 30 | 0.0118 | 0.1528 | 0.756 | 0.975 |
| 35 | 0.0298 | 0.2341 | 0.767 | 0.968 |
| 40 | 0.0485 | 0.1016 | 0.7848 | 0.9786 |

**Why val macro F1 caps at ~0.78, not 1.0:** the epoch-27 checkpoint's val confusion matrix (reconstructed from the exported ONNX model, which matches the epoch-27 native weights exactly — see §15) shows **every real positive in val is recalled at 100%** — the gap is entirely **precision** on the negative class: 6 of val's 204 `not_a_vital` rows get misclassified as a vital (2→spo2, 1→etco2, 3→rr), dragging `spo2`'s F1 to 0.667 and `rr`'s to 0.842, and `etco2` (0 real val examples, but 1 of those 6 false-alarms lands there) scores F1=0.0 by definition of having no true examples to redeem it. This is a real, small-sample-visible weakness — flagged here and revisited in §17, not smoothed over by macro-F1 model selection alone.

Full per-epoch curve: `models/field_classifier.train_report.json`.

---

## 10. Confusion matrix (held-out TEST, n=234)

```
   true\pred        hr      spo2      nibp     etco2      temp        rr  not_a_vital
          hr         6         0         0         0         0         0         0
        spo2         0         2         0         0         0         0         0
        nibp         0         0         2         0         0         0         0
       etco2         0         0         0         1         0         0         0
        temp         0         0         0         0         8         0         0
          rr         0         0         0         0         0         6         1
 not_a_vital         1         0         0         0         0         2       205
```

---

## 11. Per-class precision/recall/F1 (held-out TEST)

| Class | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| hr | 6 | 0.857 | 1.000 | 0.923 |
| spo2 | 2 | 1.000 | 1.000 | 1.000 |
| nibp | 2 | 1.000 | 1.000 | 1.000 |
| etco2 | **1** | 1.000 | 1.000 | 1.000 |
| temp | 8 | 1.000 | 1.000 | 1.000 |
| rr | 7 | 0.750 | 0.857 | 0.800 |
| not_a_vital | 208 | 0.995 | 0.986 | 0.990 |

**Read the small-support classes for what they are, not what they look like:** spo2/nibp/etco2 showing "1.000/1.000/1.000" is n=2, n=2, n=1 — a single correct prediction each. This is **not** statistically meaningful evidence of near-perfect spo2/nibp/etco2 classification; it is a positive, honestly-reported data point that the model handles the *real annotated examples it was given*, on a dataset too small to claim more. Stated explicitly, as the spec requires.

---

## 12. Held-out test results

- **Overall accuracy: 98.3%** (230/234)
- **Macro F1: 0.9591**
- **Weighted F1: 0.9835**
- n = 234 candidate crops from **8 held-out test images** (§3), never touched during training, balancing, or hyperparameter selection (only val was used for checkpoint selection).
- **Statistical caveat, stated plainly per the spec:** with test support of 1 (etco2), 2 (spo2), 2 (nibp), 6 (hr), 7 (rr), and 8 (temp), only `not_a_vital` (n=208) carries enough support for its precision/recall to be trusted as a stable estimate. The other six numbers are real, correctly computed, and worth reporting — but a single wrong prediction on etco2 would swing its recall from 100% to 0%. This dataset (52 real frames, one video) cannot currently produce a statistically strong per-vital test estimate for the rare vitals; more real annotated frames (ideally from more than one source recording) are the direct fix, not a modeling change.

---

## 13. End-to-end candidate→classifier results (Phase 4, TEST images only)

Re-run fresh, end to end, on the 8 held-out test images: `adaptive_threshold_candidates_v2` → crop → `field_classifier.onnx` → compare to GT. This is the **only** place a GT box with zero matching candidates is counted at all (`tier2_field_dataset`'s materialized crops only ever include candidates that exist).

| Vital | GT boxes (n) | Candidate recall | End-to-end accuracy | Classifier accuracy \| candidate found |
|---|---:|---:|---:|---:|
| hr | 7 | 85.7% | 85.7% | 100.0% |
| spo2 | 2 | 100.0% | 100.0% | 100.0% |
| nibp | 2 | 100.0% | 100.0% | 100.0% |
| etco2 | 1 | 100.0% | 100.0% | 100.0% |
| temp | 8 | 100.0% | 100.0% | 100.0% |
| rr | 7 | 100.0% | 85.7% | 85.7% |

- **Overall candidate recall (test images): 96.3%** — consistent with M1.1's 90.5% full-dataset number; this 8-image slice happens to sample slightly easier frames.
- **Overall end-to-end accuracy (test images): 92.6%**
- **False-positive rejection rate (not_a_vital recall): 98.6%**
- **Classifier per-class precision/recall over all 234 test candidates** matches §11 exactly (both computed independently — Phase 3's npz-derived numbers and Phase 4's fresh end-to-end run agree bit-for-bit, a useful internal consistency check that the dataset-build and live-eval code paths aren't silently diverging).

**The two failure types, separated, with real examples (§14):**
1. **hr, 1 of 7 (85.7%→ miss): candidate-generation miss, not a classifier failure.** No candidate anywhere in the frame reached IoU≥0.3 against the full 3-digit HR box — the digits fragmented into 3 separate single-glyph candidates instead.
2. **rr, 1 of 7 (85.7%→ miss): classifier failure, not a candidate-generation miss.** A candidate *did* reach IoU 0.469 against the true RR box, but the classifier predicted `not_a_vital` (94.5% confidence — a confident wrong answer, not a borderline call).

---

## 14. Hard-case visual analysis

Overlays: `backend/app/eval/tier2_data/external_monitor_video/tier2_m2_report/debug/debug_sample_00{17,18,25,26,35,36,37,38}.png` (green=GT, cyan=candidate matched+correctly classified, magenta=candidate matched a vital but classifier got the vital wrong or said not_a_vital, red=classifier false-alarm on a true negative, grey=true negative correctly rejected).

- **`debug_sample_0017.png` — a clean pass.** HR("0"), SpO2("93"), EtCO2, NIBP's two-line `150/80`/`(103)` block, RR, and Temp are all found and correctly classified in one frame — direct visual confirmation that M1.1's NIBP two-line-merge fix and the field classifier compose correctly end to end.
- **`debug_sample_0038.png` — the HR fragmentation miss (Phase 4 case #1).** `"181"` splits into 3 separate ~89px-wide single-digit candidates (the same fragmentation class M1.1's 1.5× kernel widening fixed on 93.0% of HR cases — this frame is in the surviving 7%). None of the 3 fragments alone clears IoU 0.3 against the full 3-digit GT box, so **no candidate is ever offered to the classifier for the true HR reading** — a pure candidate-generation miss. Worse: the middle fragment (isolated "8", IoU 0.196 against HR — below threshold, so labeled `not_a_vital`) gets classified **`hr` at 98.7% confidence** — a single bold isolated digit is genuinely hr-shaped, and the classifier has no way to know it's only 1/3 of the real reading. This is the clearest evidence in this milestone that candidate-generation quality is a **hard ceiling** on end-to-end accuracy, not just a recall statistic: a classifier cannot correctly reject a fragment that looks exactly like a valid (partial) reading.
- **`debug_sample_0026.png` — the RR classifier miss (Phase 4 case #2).** A 163×149 candidate reaches IoU 0.469 against the true RR box (a real, decent-quality match — not a sliver) but the classifier predicts `not_a_vital` at 94.5% confidence. Unlike the HR case, a candidate WAS available and it was thrown away. This is the more concerning failure mode of the two, precisely because a correct candidate existed and the model was confidently wrong about it rather than uncertain.
- **NIBP two-line block:** correctly classified in every test-image occurrence (2/2) — M1.1's line-merge fix and the classifier compose without any new failure mode specific to NIBP's two-line shape.
- **Waveform / alarm-banner / toolbar false positives:** of 208 true-negative test candidates, 205 correctly rejected, 3 misclassified as a vital (§10's confusion matrix column sums) — the reject class does its job on the great majority of M1.1's remaining false-positive volume (23/image), though not perfectly (§17).

---

## 15. ONNX/native comparison

Verified on the full 234-crop held-out TEST set, PyTorch (native, CPU, eval mode) vs `onnxruntime` (CPU) on the exported `field_classifier.onnx`:

| Metric | Value |
|---|---|
| Prediction agreement rate | **100.0%** (234/234) |
| Max absolute probability difference | 1.10 × 10⁻⁶ |
| Mean absolute probability difference | 6.49 × 10⁻⁹ |
| Within tolerance (1e-4) | **True** |

Opset 17, `dynamo=False` (same exporter choice as `train_cnn.py`, for the same reason — no dynamic control flow in this network, the legacy TorchScript exporter is simpler and sufficient). No precision loss of any practical consequence between native and deployed inference.

---

## 16. Model artifact path

```
backend/models/field_classifier.onnx              (1,082.6 KB)
backend/models/field_classifier.labels.json        (7 classes, same order as logits)
backend/models/field_classifier.preprocess.json    (64x64 grayscale, /255 scale, letterbox convention documented)
backend/models/field_classifier.train_report.json  (full training curves, test metrics, ONNX agreement)
```

`digit_cnn.onnx` and its sidecars are untouched — confirmed distinct filenames throughout, never overwritten.

---

## 17. Limitations

- **Real dataset is tiny and single-source.** 52 frames, one continuous recording, one monitor UI style. Every number above — especially §11/§13's per-vital breakdowns — is a real, honestly-computed measurement on real annotated data, but it is not yet evidence of generalization to a *different* real monitor's font/layout/UI density. The next real dataset (a second device, or more frames from a different recording) is the highest-leverage thing that could firm these numbers up.
- **Rare-vital statistics are thin by construction**, not by an avoidable mistake: nibp/etco2 are only annotated on ~1/3 of frames in this specific recording (§4), and candidate-generation recall on etco2 is the weakest of the six vitals (64.7%, M1.1). Val has zero etco2 candidates at all; test has exactly one. Per-class numbers for these vitals are real but not statistically strong — stated in §11/§12, not hidden behind the strong overall accuracy.
- **Candidate generation is a hard ceiling on end-to-end accuracy** (§14, HR fragmentation case): a classifier — however good — cannot correctly identify a reading that was never offered to it as a candidate, and can be fooled by a plausible-looking single-glyph fragment of a real reading. M2's own numbers (96.3% candidate recall / 92.6% end-to-end accuracy on test images) show classification adds relatively little additional loss on top of M1.1's candidate-recall ceiling — but that ceiling is still the dominant limiting factor, not the classifier.
- **Negative-category breakdown (§5) is heuristic, not measured.** There is no per-negative content ground truth in this dataset; the position/shape buckets approximate "header banner" / "toolbar" / "near-miss" but are not verified against a human-labeled category.
- **A handful of confident wrong answers exist** (rr→not_a_vital at 94.5% confidence, §14) — the model is not simply "uncertain near the boundary," it can be confidently wrong on a specific hard crop. Confidence thresholding alone would not have caught this particular case.
- **Small model, small training set** (330 final training crops, 277K params) — appropriate for this milestone's data volume, but will need to grow if trained on a meaningfully larger/more diverse real dataset later.

---

## 18. Recommendation

# GO TO M3 INTEGRATION

Basis, weighed against the spec's own explicit failure conditions:

- **Not** a "98% accuracy by predicting the majority class" result: `not_a_vital` support is 208/234 (89%) of test, but every one of the six vital classes shows 100% recall (except rr at 85.7%) and precision ranging 75–100% — the model is doing real per-vital work, reported per-class throughout (§11–§13), never hidden behind overall accuracy.
- **Not** "excellent overall accuracy but poor EtCO2/NIBP recall" — both are 100% recall on their (thin, honestly-flagged) test support, and end-to-end accuracy is 100% for both in the Phase-4 pipeline run.
- Macro F1 (0.959 test, 0.785 val) and weighted F1 (0.984 test, 0.979 val) both clear a reasonable bar given the class imbalance, with the val/test gap explained concretely (§9) rather than left as an unexplained anomaly.
- Confusion matrix (§10) shows no systematic vital↔vital confusion at all — every off-diagonal count involves `not_a_vital` in one direction or the other, which is the class boundary this system is *supposed* to be conservative about, not a sign of the model confusing HR for SpO2 or similar.
- Held-out image-split integrity is enforced by code (assertion, §3) and confirmed clean — no image, and therefore no crop, appears in more than one split.
- ONNX/native agreement is effectively exact (§15) — the exported artifact is safe to integrate.
- End-to-end candidate→classifier evaluation (§13) cleanly separates the two error sources the spec asks for: candidate-generation misses (HR fragmentation, a known, already-partially-addressed M1.1-era problem) vs genuine classifier misses (one confident RR false rejection) — both real, both small in count, neither hidden.

**What GO does *not* mean here:** this is not evidence the system is ready for a monitor UI it hasn't seen. **Recommended scope for M3, not resolved here:**
- Wire `field_classifier.onnx` into an *isolated*, opt-in Tier-2 candidate-classification stage (mirroring `ROI_ENGINE`'s existing pattern, per `TIER2_RECOGNITION_SPIKE.md` §10) — still not the live camera path by default.
- Collect a second real-monitor recording (different device/UI) before trusting these numbers to generalize; treat this milestone's numbers as "real and correct on this data," not "final."
- The HR-fragmentation candidate-generation ceiling (§14) is worth one more targeted look before M3 locks in adaptive-threshold's current kernel parameters — it's the single clearest remaining lever on end-to-end accuracy.
- The one confident RR false-rejection (§14) is worth keeping an eye on with more data — not evidence of a systematic problem yet at n=7, but worth another look once a larger rr test set exists.

---

Confirmed before finishing: 190/190 existing backend tests pass. All 52 `sample_XXXX.json` annotation files' mtimes unchanged this session. No production code (`read_frame()`, `roi.py`, OCR, ONNX inference used by the live pipeline, reconciliation, persistence, WebSocket, `CameraSource`, frontend) was touched. `digit_cnn.onnx` untouched. Nothing committed or tagged.
