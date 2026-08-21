# VITAL — Evidence base for the recognition architecture decision

Every number here was measured on 2026-08-19 by re-running the pipeline stage by
stage against both datasets, rather than by citing stored reports. Where a figure
contradicts an archived milestone report, the archived report is wrong — see
[`archive/README.md`](archive/README.md).

Decision this evidence supports: [`ARCHITECTURE.md`](ARCHITECTURE.md).
Plan it feeds: [`ROADMAP.md`](ROADMAP.md).

**Method.** Production code was imported and called unmodified. No model was trained,
no threshold tuned, no annotation altered. Diagnostics that change inputs (oracle
crops, kernel sweeps) are labelled as diagnostics and were never used to select a
production parameter.

---

## 1. Stage-by-stage, Dataset A vs Dataset B

| Stage | Metric | Dataset A (52 fr) | Dataset B (17 fr) |
|---|---|---:|---:|
| Screen detection | `detect_screen()` fires | **0/52** | **0/17** |
| Candidate generation | recall @ IoU>=0.3 | 90.5% | 32.9% |
| Candidate generation | dominant miss mode | `component_oversized` (rr 21/43) | `component_oversized` (**36/36**) |
| FieldCNN | **oracle GT crops** | **64.8%** | **4.3%** |
| FieldCNN | oracle crops, 10% box jitter | 49.7% | **0.0%** |
| FieldCNN | wrong calls clearing the confidence gate | 89% | **97%** |
| FieldCNN | mean confidence wrong vs right | 81.2 vs 90.0 | **92.1 vs 76.4** |
| OCR | **oracle crops, whitelist-free** | **98.4%** | 45.7% |
| OCR | production config, correct reads clearing >=70 | 96% | **0%** |
| End-to-end | micro OCR (M4.6 / M5, reproduced) | 61.6% | 5.7% |
| End-to-end | genuine confirmed accuracy | 55.6% | **0.0%** |
| **Calibrate-once + tracking** | **OCR accuracy** | 71.5% | **57%** |

The M5 report's headline numbers **reproduce exactly** (candidate recall 32.9%, micro
OCR 5.7%, screen detection 0/17). Its NO-GO verdict is correct. Two of its causal
attributions are not.

---

## 2. Screen detection is a dead stage, not a Dataset B finding

`_find_screen_quad` (`backend/app/pipeline/detect.py:27`) requires a convex 4-gon
contour covering `MIN_AREA_FRAC = 0.5` of the frame. Measured: **0/52 on Dataset A**
and 0/17 on Dataset B.

The M5 report presents the 17/17 failure as a Monitor-B property and a candidate root
cause, and recommends investigating it first. It fails identically on Dataset A, so
there is nothing recording-specific to find. The originating spike's claim that this
stage was "already Tier-2-ready as-is" was never tested against a real photo.

**Consequence.** Because it never fires, every downstream size constant is computed
against the raw camera frame, not the screen — so they are functions of camera
framing rather than of the monitor.

---

## 3. Candidate generation: over-merge, with no cross-monitor operating point

**Failure mode.** The dilation kernel is sized from the *image* (`min(h,w)*0.015` =
18x27 px on both datasets). On Monitor B it bridges the dense bottom info bar into
single blobs that then fail the size cap and are discarded whole:

| Vital (B) | merged blob | GT box | ratio |
|---|---|---|---:|
| temp | **2712 x 1220** (entire frame) | 125x75 | 353x |
| rr | 1425 x 1220 | 100x60 | 290x |
| etco2 | 1427 x 1148 | 95x65 | 265x |

All 36/36 Temp + EtCO2 + RR misses trace to this one mode. M1.1's
`_strip_line_artifacts` does not apply: it removes single thin-line components, and
this bridge is dense UI text plus bezel plus background clutter.

**Scale hypothesis — REFUTED.** Cropping Dataset B to the monitor region moves recall
32.9% -> 34.3%. Additionally rescaling so digit height matches Dataset A's makes it
**worse: 25.7%**. The gap is not a resolution or framing artifact.

**Kernel sweep (diagnostic only — not a proposed fix).** Overall candidate recall vs
dilation-kernel scale, production = 1.0:

| scale | Dataset A | Dataset B |
|---:|---:|---:|
| 0.25 | 45.7% | 27.1% |
| 0.40 | 59.3% | **40.0%** |
| 0.50 | 73.4% | 37.1% |
| 0.75 | 82.9% | **40.0%** |
| 1.00 | **90.5%** | 32.9% |

**A's optimum and B's optimum are mutually exclusive.** Whether two glyph clusters
should merge is a property of a specific monitor's typography and spacing; a global
morphological parameter cannot encode that. This is category *"solving the wrong
problem"*, not *"mis-parameterised"*.

---

## 4. The FieldCNN

### 4.1 It has never been meaningfully evaluated

From `backend/models/field_classifier.train_report.json`: `train_n = 330`. The test
set is **234 crops, 208 of them `not_a_vital`**, with per-class support of **1
(etco2)** and **2 (spo2)**. The reported 98.3% overall / 92.6% figures are dominated
by the negative class.

### 4.2 Given perfect crops it still fails

Hand-annotated ground-truth boxes fed straight to the production ONNX model — no
candidate generator involved:

| True class | Dataset A predictions | Dataset B predictions |
|---|---|---|
| hr | hr:35, not_a_vital:8 -> **81%** | not_a_vital:11, nibp:3, hr:3 -> **18%** |
| spo2 | spo2:27 -> **100%** | not_a_vital:15, nibp:2 -> **0%** |
| etco2 | **spo2:17 -> 0%** | not_a_vital:12 -> **0%** |
| temp | hr:31, temp:20, nibp:1 -> **38%** | not_a_vital:17 -> **0%** |
| rr | rr:30, spo2:13 -> **70%** | not_a_vital:7 -> **0%** |
| **overall** | **64.8%** | **4.3%** |

With 10% box jitter — less than any real camera introduces — Dataset A falls to
49.7% (temp to 4%) and **Dataset B to 0.0%**.

### 4.3 It is anti-calibrated, which is the safety problem

| | Dataset A | Dataset B |
|---|---:|---:|
| mean confidence when **correct** | 90.0% | 76.4% |
| mean confidence when **wrong** | 81.2% | **92.1%** |
| wrong calls clearing `MIN_CLASSIFIER_CONFIDENCE=0.5` | 89% | **97%** |

On Monitor B the model is *more* confident when wrong than when right.
`read_frame.py:151` fuses this into the safety gate via
`min(classifier_confidence, ocr_confidence)` on the premise that it is "the weakest
signal". It is not weak — it is **misleading**.

### 4.4 Why retraining does not fix it

`_letterbox_gray` discards colour by design; cropping discards position and the
adjacent printed label. What remains is glyph shapes on a square canvas — and HR
`84` and NIBP-diastolic `84` are then the same image. The confusions are exactly what
a shape/aspect-ratio cue predicts (all 17 etco2 -> `spo2`; 31/52 temp -> `hr`). The
discriminative information is **absent from the input**, not under-learned.

---

## 5. OCR is the healthiest stage, and has been mis-blamed since M4.1

Oracle ground-truth crops, both datasets:

| Config | A acc | A conf on correct | B acc | B conf on correct | B % clearing >=70 |
|---|---:|---:|---:|---:|---:|
| production (whitelisted) | — | 91.3 | 37.1% | **19.9** | **0%** |
| `--psm 8`, no whitelist | 98.9% | 88.3 | 37.1% | 37.5 | 4% |
| `--psm 7`, no whitelist | 94.0% | 93.8 | 41.4% | 70.2 | 52% |
| `--psm 10`, no whitelist | 94.0% | 93.7 | 42.9% | 69.6 | 50% |
| `--psm 6`, no whitelist | 94.0% | 93.8 | 42.9% | **71.9** | **53%** |
| best + 5% box padding | **98.4%** | — | 45.7% | — | — |

**Dataset A reads 98.4% given correct crops, against 61.6% end-to-end.** The entire
M1 -> M4.6 accuracy deficit on Dataset A is a localization deficit.

**Confidence collapse root cause: `tessedit_char_whitelist`.** The exact mechanism
M4.4 root-caused and fixed for NIBP/EtCO2, then explicitly deferred for HR/SpO2/RR.

**Worst single instance:** production routes SpO2 through `--psm 10` + whitelist. On
Dataset B that reads **82% correctly at confidence exactly 0** — the reading is right
and the system is structurally incapable of believing it.

### 5.1 The gate is well calibrated; do not lower it

Accuracy by confidence bucket, production per-field configs, oracle crops:

| | conf <40 | 40-69 | 70-89 | >=90 |
|---|---:|---:|---:|---:|
| Dataset A | n=21, **5%** | n=16, 88% | n=24, 100% | n=121, **100%** |
| Dataset B | n=60, **30%** | n=10, 90% | n=0 | n=0 |

Confidence predicts correctness wherever it has dynamic range. Lowering
`CONFIDENCE_MEDIUM_MIN` would admit Dataset B's 30%-accurate bucket. The fix is to
restore the signal's range, not move the bar.

---

## 6. Calibration + tracking — the decisive counterfactual

Calibrate on frame 1 (ground-truth boxes = what a clinician would draw), then
re-anchor per frame with ORB -> RANSAC partial-affine estimated from the monitor's
**static chrome**, not the digits.

**Dataset B — handheld, 3+ distinct framings, unseen monitor:**

| Mode | HR | SpO2 | mean IoU | OCR acc |
|---|---:|---:|---:|---:|
| M4.6 production Tier-2 | — | — | — | **5.7%** |
| calibrated box, no tracking | 53% | 53% | 0.54 | 35% |
| calibrated box + **layout tracking** | **76%** | **94%** | **0.68** | **57%** |
| per-frame oracle GT boxes | 53% | 82% | 1.00 | 47% |

**5.7% -> 57% on the unseen monitor with zero retraining.** ORB locks on every frame
(mean 226 inliers, min 23) despite the three framings. Tracking **beats the per-frame
oracle**, because one well-drawn calibration box carried forward beats 17
independently-drawn tight ones.

**Track the layout, not the values.** A first attempt templating each digit box
tracked at IoU 0.51 — *worse* than not tracking (0.54) — because the digits are
exactly what changes.

### 6.1 Two measured caveats that become UI requirements

- **Too tight.** Dataset A single-frame calibration gives 71.5% vs 95% oracle,
  because `sample_0002`'s HR box is drawn around a single-digit `0` and is too narrow
  once the reading goes to 3 digits. Even 50% padding only recovers it to 60%. The
  operator must draw the **display slot**, not the current digits.
- **Too generous.** Dataset A drops 71.5% -> 37.6% at 50% padding, by pulling in
  neighbouring text.

Both are caught by the Verify step OCR-ing each drawn box before Save is permitted.

---

## 7. Latency

Measured per frame on this machine:

| Stage | cost |
|---|---:|
| `detect_screen` | 22 ms |
| **candidate generation** | **911 ms** |
| FieldCNN (all candidates) | 3.5 ms |
| Tesseract x6 | 1012 ms |
| **ORB / template tracker** | **25 ms** |
| current end-to-end median | **2.8 s** |

Replacing candidate generation with the tracker is a **~900 ms/frame** saving.

---

## 8. Temporal information — real, but unvalidatable on current data

Ground truth changes frequently between sampled frames:

| | HR unchanged | SpO2 | EtCO2 | Temp | RR |
|---|---:|---:|---:|---:|---:|
| Dataset A | 40.0% | 33.3% | 23.1% | 100%* | 83.3% |
| Dataset B | **18.8%** | 62.5% | 36.4% | 68.8% | 33.3% |

Naive "same value N frames running" would almost never fire. Both datasets are
**sparsely sampled stills, not continuous video** — neither can validate a temporal
model. This is a data gap, not a design objection; it is why M5.3 requires the dense
extraction.

The correct formulation is **bounded-drift agreement**, using the physiological steps
already encoded in `JUMP_LIMITS`.

\* Dataset A's Temp is one static value — see below.

---

## 9. Dataset caveats that distort prior reports

- **Dataset A's Temp is `98.6` °F, identical in all 52 frames** (1 distinct value).
  Every "Temp 100% accuracy" claim is one static number read 52 times. It is not
  evidence of anything.
- **Dataset B's Temp ground-truth box is clipped** — visual inspection shows it crops
  `3.7` out of `23.7`, cutting the leading digit, and includes the T1/T2 labels. And
  `23.7` is outside `RANGE_BOUNDS["temp"] = (30, 44)` regardless (bench demo, probe
  unattached, reading room temperature). **Temp on Dataset B is no-data, not
  failure** — do not score it.
- **Dataset B conflates two variables.** It is phone screenshots of a YouTube video of
  a monitor, so "different manufacturer" is confounded with "much worse image
  quality". Its ~46% oracle-crop OCR ceiling therefore *understates* what a real
  camera on a real B650 would achieve. Do not present it as a clean
  manufacturer-generalization measurement.
- **17 frames, one recording, one monitor.** Nothing here generalises to
  "GE CARESCAPE B650s in general", let alone to all monitors.

---

## 10. Reproducing this

The diagnostics were run as throwaway scripts against the production modules. To
re-derive the key figures, the committed equivalents to build are:

| Figure | Script to commit |
|---|---|
| §1 stage table, §2 screen detection | `backend/app/eval/m5_1_stage_audit.py` |
| §3 over-merge + kernel sweep | `backend/app/eval/m5_1_candidate_trace.py` |
| §4 FieldCNN oracle-crop matrix | `backend/app/eval/m5_1_classifier_oracle.py` |
| §5 OCR config sweep + calibration | `backend/app/eval/m5_1_ocr_config_sweep.py` |
| §6 calibration + tracking counterfactual | `backend/app/eval/m5_3_tracking_eval.py` |

All read-only against `backend/app/eval/tier2_data/`; none modify a dataset or a
model.
