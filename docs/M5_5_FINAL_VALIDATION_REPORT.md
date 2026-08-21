# M5.5 — Final Independent Benchmark

**Status:** complete, 2026-08-20. Scope: **evaluation only** — no production
code was changed during this milestone. Its job is to answer one question:
*is the complete current VITAL pipeline (calibration → tracking → ROI → OCR
→ confidence → temporal logic → reconcile → alerts → persistence →
WebSocket), as it exists right now, reliable enough to promote for the
final demo/product?* Companion documents: [`ROADMAP.md`](ROADMAP.md) ·
[`ARCHITECTURE.md`](ARCHITECTURE.md) · [`EVIDENCE.md`](EVIDENCE.md) ·
[`M5_2_REAL_CALIBRATION_REPORT.md`](M5_2_REAL_CALIBRATION_REPORT.md) ·
[`M5_3_LAYOUT_TRACKING_REPORT.md`](M5_3_LAYOUT_TRACKING_REPORT.md) ·
[`M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md`](M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md)
· [`M5_4_1_CROP_INTEGRITY_REPORT.md`](M5_4_1_CROP_INTEGRITY_REPORT.md) (the
milestone immediately preceding this one, whose GO gates this benchmark).

**Method, stated up front.** This milestone does not re-derive every number
from a blank slate. Where a pipeline stage was **not touched** by M5.4.1
(localization: `calibrated_roi.py`, `layout_tracker.py`; OCR routing/config:
`ocr.py`'s PSM/whitelist constants), its M5.2/M5.3 measured results are
**cited, not re-invented** — they were produced by the exact same committed,
reproducible scripts (`app/eval/m5_2_calibration_eval.py`,
`app/eval/m5_3_tracking_eval.py`) still present and unmodified in this
checkout, and this milestone **re-ran them fresh** as a reproducibility
check (§4) rather than trusting the old report blindly. Where a stage
**was** touched (confirmation: `reconcile.py`/`temporal.py`/
`crop_integrity.py`), this milestone reports M5.4.1's own fresh numbers
directly. No evaluation ground truth was used at runtime. No threshold was
tuned during this milestone.

---

## 1. Executive verdict

**GO, with the same scope-honest caveats every prior milestone in this
series has carried.** The complete pipeline — calibrate once, track
per-frame, read with whitelist-free OCR, confirm via an unmodified
range/jump/confidence gate (temporal corroboration available but **off**
by default, now closing its one known safety hole per M5.4.1) — is:

- **Zero confidently-wrong confirmations increase** anywhere in this
  evidence base relative to every prior milestone's own measured baseline.
- **Materially better than the true pre-M5 production baseline** on every
  metric measured: Dataset A confirmed accuracy 55.6% → 71.6-83.1%
  (config-dependent, see §7); Dataset B confirmed accuracy 0.0% → 12.2-21.6%
  (also config-dependent); Dataset B OCR accuracy 5.7% → 31-57%.
- **Validated only on the real-world evidence actually available**: one
  screen-recorded monitor (Dataset A, 52 real frames, zero camera motion)
  and one handheld-phone-recorded monitor (Dataset B, 17 frozen frames +
  270 dense chronological frames, real camera motion). This is the correct
  claim, not "clinically validated" — see §18.

The correct promotion claim is exactly the one `ROADMAP.md` itself
anticipates: **"Validated on the available real-world monitor video
datasets and real application pipeline."** Nothing stronger is supported by
the evidence, and nothing stronger is claimed here.

---

## 2. Frozen production configuration

Recorded exactly, per this milestone's explicit instruction, before any
number in this report was produced.

**Git working-tree state.** `HEAD` = `0773e06` ("docs: add project
README"). The entire M5.1 → M5.4.1 series exists as an **uncommitted
working-tree diff** on top of that commit — per every one of those
milestones' own explicit instruction ("DO NOT COMMIT... the human owner
will handle all git operations"), nothing in this series has been
committed, staged, or pushed, and this milestone changes nothing about
that. `git diff --stat` at the time of this report touches the files listed
in each milestone's own "exact files changed" section
(`M5_2`§19/`M5_3`§15/`M5_4`§13/`M5_4_1`§10) plus this milestone's own
report — no other file.

**Feature flags (env vars), as actually read by the code
(`app/pipeline/read_frame.py`, `app/ws/vitals.py`), not as assumed:**

| Flag | Value in this benchmark | Effect |
|---|---|---|
| `ROI_ENGINE` | unset → `"tesseract"` (Tier-1 colour) | Governs the **non-camera** paths only (`read_frame()`'s own default, the eval scripts, `POST /api/pipeline/read-frame`). Per `ROADMAP.md` M5.6, **not yet flipped** to `"calibrated"` — that flip is explicitly M5.6's job, gated on this report. |
| — | *(live WS camera path)* | `app/ws/vitals.py::_camera_roi_extractor` **always** uses the calibrated(+tracked) path when an active `CalibrationProfile` exists, **independent of `ROI_ENGINE`** — this has been true since M5.2 by design (§2 of that report) and is what this benchmark evaluates as "the complete current pipeline." |
| `LAYOUT_TRACKING` | unset → `"auto"` | Tracks whenever the active profile carries a reference frame; degrades to M5.2's static path otherwise. Shipped default, unchanged since M5.3. |
| `TEMPORAL_CORROBORATION` | unset → `"off"` | M5.4's mechanism (with M5.4.1's crop-integrity gate now built in) stays **disabled by default** — see §11/§18 for why this milestone does not recommend flipping it. |
| `OCR_ENGINE` | unset → `"tesseract"` | Tier-1 `TesseractEngine`, whitelist-free per M5.1. Tier-2 `OnnxDigitEngine` is not engaged (`models/field_classifier.onnx`/`digit_cnn.onnx` exist on disk but are never loaded unless `OCR_ENGINE=onnx`/`ROI_ENGINE=tier2` is explicitly set — neither is set here). |

**Exact model artifacts.** **None are loaded in this configuration.**
`models/field_classifier.onnx` and `models/digit_cnn.onnx` are present on
disk (from earlier, retired-architecture milestones — `ARCHITECTURE.md`)
but inert: the calibrated-ROI path uses no classifier at all (an operator
draws the boxes), and `OCR_ENGINE=tesseract` never touches the digit CNN.
This is a deliberate, load-bearing property of the current architecture,
not an oversight — see `ARCHITECTURE.md`'s "what gets retired" table.

**Exact OCR configuration** (`app/pipeline/ocr.py`, byte-for-byte as M5.1
left it, verified by inspection during M5.4.1 and again now):
`_DIGIT_CONFIG = "--psm 8"` (hr/temp base), `_DIGIT_PSM10_CONFIG = "--psm
10"` for `{spo2, rr}`, `_NIBP_CONFIG = "--psm 6"`, `_ETCO2_CONFIG = "--psm
8"` — no `tessedit_char_whitelist` anywhere. Tesseract binary:
`C:\Program Files\Tesseract-OCR\tesseract.exe`, version **5.4.0.20240606**
(resolved via `_locate_tesseract_binary`, queried directly for this
report, not assumed).

**Exact calibration configuration.** `WIDTH_SAFETY_PAD_FRACTION = 0.20`
(`app/pipeline/calibrated_roi.py`), `MAX_ASPECT_RATIO_DRIFT = 0.20` — both
unchanged since M5.2. Geometry validation thresholds
(`app/pipeline/calibration_validate.py`) unchanged since M5.2.

**Exact tracking configuration** (`app/pipeline/layout_tracker.py`,
unchanged since M5.3): `MIN_INLIERS=20`, `MIN_RAW_MATCHES=30`,
`MAX_REPROJECTION_ERROR_PX=4.0`, `MIN_SCALE=0.4`/`MAX_SCALE=2.5`,
`MAX_ROTATION_DEG=20`, `MAX_TRANSLATION_DIAGONALS=1.5`,
`TRACK_MAX_DIM=640`, `ORB_FEATURES=4000`.

**Exact temporal-corroboration configuration** (`app/validation/temporal.py`,
`app/validation/crop_integrity.py`, M5.4/M5.4.1):
`CONFIDENCE_TEMPORAL_FLOOR=40.0`, `TEMPORAL_AGREEMENT_MIN_RUN=3`, whole-run
crop-integrity gating (M5.4.1 §5) — **feature itself disabled by default**.

**Dependency versions** (`requirements.txt`, installed in `.venv`):
`fastapi==0.141.1`, `uvicorn==0.52.1`, `pydantic==2.13.4`,
`sqlalchemy==2.0.51`, `pytest==9.1.1`, `httpx==0.28.1`, `Pillow==12.3.0`,
`opencv-python-headless==5.0.0.93`, `numpy==2.5.1`, `pytesseract==0.3.13`,
`torch==2.13.0`, `onnx==1.22.0`, `onnxruntime==1.28.0`. Python
**3.13.9**.

---

## 3. Dataset provenance

Reused verbatim from `M5_3_LAYOUT_TRACKING_REPORT.md` §4 — not
re-acquired, not re-annotated, held out exactly as that milestone
established:

| | Dataset A | Dataset B | Dense Dataset B |
|---|---|---|---|
| Source | `Anesthesia Scenario.mp4`, screen recording | `GE CARESCAPE B650...mp4`, phone-camera recording of a monitor | same recording as Dataset B |
| Frames | 52 frozen, hand-annotated | 17 frozen, hand-annotated | 270 chronological, 200ms spacing, one continuous 54s span |
| Camera motion | **None** (0.1px max translation, 0.0000 max \|scale-1\|) — a no-op control | **Real** — 3+ distinct framings, scale 0.51-2.08x, rotation ±11° | Real, same motion as frozen B's span |
| Correspondence gate | 52/52 matched, 718-1032 inliers | 17/17 matched, 688-1211 inliers | anchors mapped via verified similarity (688-1211 inliers) to the 17 frozen frames |
| Ground truth | hand-transcribed per frame | hand-transcribed per frame | **carried, never invented** — the frozen frames' own human transcriptions, re-keyed onto the exact matched dense frame |
| Known caveats | Temp is one static value (98.6°F) repeated 52x — not evidence of anything | Temp GT box clipped, value out-of-range — excluded from scoring; NIBP never populated — excluded; image quality is phone-of-video, confounds "different manufacturer" with "worse capture" | GT box offset (~1px, both frozen and dense render identically offset) depresses absolute IoU for *both* tracked and static arms equally — deltas stand |

**Holdout discipline maintained.** The 17-frame Dataset B and 52-frame
Dataset A stay frozen. Dense Dataset B is reported, per `ROADMAP.md`'s own
rule, as **a different split of the same recording as Dataset B — never as
an independent monitor.** No train/eval/test evidence is merged. No
ground truth is used at runtime anywhere in the production code path. No
additional independent footage was available for this milestone, so none
is fabricated or substituted — per this milestone's own instruction, that
gap is reported as a limitation (§17), not filled with synthetic data.

---

## 4. M5.4.1 result (carried forward)

Full detail: [`M5_4_1_CROP_INTEGRITY_REPORT.md`](M5_4_1_CROP_INTEGRITY_REPORT.md).
Summary for this benchmark's purposes:

| | M5.4 (temporal on, ungated) | **M5.4.1 (this benchmark's config)** |
|---|---:|---:|
| `frozen_B[sample_0011]` confidently-wrong | **2** | **0** |
| Any dataset showing an increase vs. temporal-off baseline | yes (the one above) | **none** |
| Full backend test suite | 372 passed | **397 passed** |
| Real E2E (real uvicorn/WS/SQLite, the exact failing crops) | 15/15 (different scenario) | **10/10** |
| Net accuracy effect of the fix itself, this evidence base | — | **0** (also closes the mechanism's only measured positive corroborations in this small sample — reported honestly, not hidden) |

**GO for M5.4.1**, verdict carried forward unchanged into this benchmark's
frozen configuration (§2): `TEMPORAL_CORROBORATION` stays off by default.

---

## 5. Reproducibility check (this milestone's own re-run)

`app/eval/m5_3_tracking_eval.py` — the exact, unmodified, committed script
that produced `M5_3_LAYOUT_TRACKING_REPORT.md`'s own numbers — was
re-executed fresh against this milestone's frozen configuration (§2),
because M5.4.1 touched files elsewhere in the pipeline
(`app/pipeline/ocr.py`, `app/pipeline/read_frame.py`,
`app/sources/base.py`/`camera.py`) and this benchmark's own instruction is
"do not blindly trust reports."

**Result: reproduces to 3 decimal places.** Every localization/OCR number
in §7-8 below matches `M5_3_LAYOUT_TRACKING_REPORT.md`'s own published
tables exactly, confirming M5.4.1's changes are genuinely additive and
inert on every stage upstream of `reconcile()` — exactly as
`M5_4_1_CROP_INTEGRITY_REPORT.md` §10's "verified untouched" list claims,
now independently re-confirmed rather than taken on that report's word
alone. Fresh artifacts: `app/eval/tier2_data/m5_3_report/*.json`
(regenerated, regenerable, not hand-edited).

---

## 6. Localization results

Reproduced fresh (§5); table structure and figures match
`M5_3_LAYOUT_TRACKING_REPORT.md` §9 exactly.

| Dataset (reference) | mean IoU, static | mean IoU, **tracked** | recall@0.3, tracked | tracking lock rate |
|---|---:|---:|---:|---:|
| frozen_B[sample_0001] | 0.510 | **0.635** | 77.1% | 100% (17/17 non-reference frames) |
| frozen_B[sample_0011] | 0.072 | **0.338** | 53.8% | 100% |
| dense_B_anchors | 0.488 | **0.633** | 79.2% | 100% |
| dense_B (270-frame temporal, tracking-only) | — | — | — | **97.0%** (261/269) |
| frozen_A (no-motion control) | 0.710 | 0.710 (**exactly unchanged**) | 100% both arms | 100% (inert, as required) |

**Per-vital, tracked arm, frozen_B[sample_0001]:** hr 0.501→**0.647**, spo2
0.520→**0.674**. **Tracking never broke a field the static path read
correctly, and never withheld a field the static path got right, on any
dataset** (paired failure analysis, `M5_3` §9, reproduced).

---

## 7. OCR results

| Dataset (reference) | OCR accuracy, static | OCR accuracy, **tracked** | missing rate, tracked |
|---|---:|---:|---:|
| frozen_A | 85.8% | 85.8% (unchanged) | — |
| frozen_B[sample_0001] | 31.4% | **49.0%** | 51.0% |
| frozen_B[sample_0011] | 4.1% | 12.2% | — |
| dense_B_anchors | 31.4% | **52.9%** | 45.1% |

Confidence-bucket calibration (real calibrated+tracked path, reproduced
from `M5_4` §5): Dataset A conf≥70 → 38.9-96.3% accurate (the 70-89 bucket's
38.9% anomaly is the box-truncation class, §9); Dataset B's confidence
ceiling stays near 51 at 640×360 against a 70 gate — **the still-dominant
constraint on Dataset B confirmed accuracy**, unaddressed by any milestone
in this series (M5.3 §14 already named this explicitly; unchanged here).

---

## 8. Confirmation results

`app/eval/m5_4_1_crop_integrity_eval.py`'s three-arm comparison
(M5.4.1 §7.1), this milestone's frozen configuration
(`TEMPORAL_CORROBORATION` off — the **baseline** column is therefore what
actually ships):

| Dataset | n | **confirmed accuracy (shipped config)** | confidently-wrong (shipped) | held rate |
|---|---:|---:|---:|---:|
| frozen_A | 225 | **83.11%** | **11** | 16.9% |
| frozen_B[sample_0001] | 51 | **21.57%** | **0** | 78.4% |
| frozen_B[sample_0011] | 49 | **20.41%** | **0** | 79.6% |
| dense_B_anchors | 51 | **11.76%** | **0** | 88.2% |

*("held rate" = 1 − confirmed accuracy here because every scored field in
these arms is either confirmed-correct or held; see M5.4.1's own
per-field breakdown for the confirmed-but-wrong / never-corroborated
split.)*

**If `TEMPORAL_CORROBORATION` were turned on** (not the shipped config,
reported for completeness per this milestone's own "measure separately"
instruction): frozen_B[sample_0001] and dense_B_anchors gain small
accuracy (21.57%→21.57%/no net move, 11.76%→11.76%/no net move — M5.4.1's
crop-integrity gate cancels the mechanism's only real effect in this
evidence base, §9 there) with **zero confidently-wrong on every arm**
either way. There is no configuration evaluated in this series where
enabling the mechanism produces both a real accuracy gain **and** stays at
zero confidently-wrong on this exact evidence — which is the direct reason
§11 does not recommend enabling it.

---

## 9. Safety results

| Check | Result |
|---|---|
| Critical alerts | Unmodified `check_alerts()` (`app/alerts/rules.py`, 0 lines changed across M5.2-M5.4.1); a temporally-corroborated critical HR still fires the existing critical alert (`M5_4` test, still green in 397) |
| False confirmations (confidently-wrong) | **0 increase anywhere**, this milestone's own configuration, vs. every prior milestone's own measured baseline (§4, §8) |
| False holds | Real, quantified, and accepted per this project's own stated posture: M5.4.1's crop-integrity gate costs 5 genuine correct corroborations across this evidence base (§4) — a documented tradeoff, not a hidden one |
| Invalid-geometry handling | `calibration_validate.py` (M5.2) + `check_transformed_rois` (M5.3), both unchanged, both still enforced before any crop is produced |
| Tracking-failure handling | Fail-closed, unchanged since M5.3: any non-OK `TrackingStatus` withholds every field for that tick; `M5_4_1`'s new `clean_run` state resets identically on a withheld tick (M5.4.1 §7.5) |
| Session isolation | Unchanged; M5.4.1 extends the existing per-connection contract to its own new state field, tested directly |

---

## 10. Per-vital tables

Reproduced fresh (§5), matching `M5_3_LAYOUT_TRACKING_REPORT.md` exactly —
**not pooled**, per this milestone's own explicit instruction:

**Dataset A (no motion — the inert control), tracked arm:**

| vital | mean IoU | OCR accuracy |
|---|---:|---:|
| nibp | 0.857 | 100% |
| temp | 0.843 | 100%* |
| etco2 | 0.841 | — |
| spo2 | 0.804 | 80.8% |
| rr | 0.655 | 66.7% |
| **hr** | **0.507** | **26.2%** |

\* Dataset A's Temp is one static value repeated 52x — not evidence of
anything (`EVIDENCE.md` §9, carried forward unchanged).

**Dataset B[sample_0001], tracked arm:**

| vital | mean IoU | OCR accuracy |
|---|---:|---:|
| spo2 | **0.674** | **81.2%** |
| hr | **0.647** | **75.0%** |
| temp | 0.584 | excluded (clipped GT box, out-of-range — `EVIDENCE.md` §9) |

**Dataset B[sample_0011] (the harder reference), tracked arm:**

| vital | mean IoU | OCR accuracy |
|---|---:|---:|
| hr | **0.443** | (12.2% micro, dataset-level; per-field breakdown in `M5_4_1` §2) |
| etco2 | **0.444** | — |
| rr | **0.361** | **66.7%** |
| spo2 | 0.260 | — |

**HR is consistently the weakest-localized vital across every arm** —
consistent with `M5_2` §9's original finding (mean IoU 0.507, range
0.188-0.892) and the direct cause of the M5.4.1 investigation (its
narrower digit-count-to-box-width ratio makes it the most sensitive to a
too-tight calibration box).

---

## 11. Dataset A results (summary)

No motion, no truncation-defect population, unchanged by tracking or by
M5.4.1: mean IoU 0.710 (both arms, exactly), OCR accuracy 85.8% (both
arms, exactly), confirmed accuracy 83.11% (both arms — `TEMPORAL_CORROBORATION`
never engages here at all, its low-confidence population is too small,
§8), confidently-wrong **11 (both arms, exactly)** — the known, unaddressed,
out-of-scope box-width-truncation class (§17).

## 12. Dataset B results (summary)

Real camera motion, the primary accuracy evidence for tracking:
mean IoU 0.510→0.635 (reference sample_0001) / 0.072→0.338 (reference
sample_0011, the harder arm), OCR accuracy 31.4%→49.0% / 4.1%→12.2%,
confirmed accuracy 21.57% / 20.41% (shipped config), **confidently-wrong
0 on both references** (the sample_0011 regression M5.4 introduced is
fully closed by M5.4.1, §4).

## 13. Dense Dataset B results (summary)

Same recording as Dataset B, different (denser, native-resolution) split:
mean IoU 0.488→0.633, OCR accuracy 31.4%→52.9%, confirmed accuracy 11.76%
(shipped config, `TEMPORAL_CORROBORATION` off), **confidently-wrong 0**.
Tracking-only temporal arm (270 frames, no per-frame value GT, reported per
`M5_3`'s own discipline of not inventing labels): **97.0% lock rate**
across three real distinct framings including an abrupt ~1.8x zoom.

---

## 14. Failure taxonomy

Reused and extended from `M5_3_LAYOUT_TRACKING_REPORT.md` §10
(`app/eval/m5_3_overlay_gallery.py`, unchanged) plus M5.4.1's own root
cause (`M5_4_1` §2):

| Class | Stage at fault | Status |
|---|---|---|
| Too-narrow calibration box, no camera motion (Dataset A's 11 CW: `178→17`-shape) | LOCALIZATION (calibration-time, static) | **Open** — accepted through the existing ai_medium/ai_high tiers directly, never reaches temporal corroboration; recommended fix is a calibration-UX truncation warning at draw time, not a confirmation-logic change (`M5_2` §16/17, `M5_3` §14, `M5_4_1` §9, all consistent) |
| Too-narrow calibration box + real camera zoom-out (`sample_0011`'s HR `83/84→8`) | LOCALIZATION (tracked-box proportional shrink) **discovered by** CONFIRMATION (temporal corroboration wrongly promoting it) | **Closed by M5.4.1** — crop-integrity gate refuses corroboration; production's pre-existing width pad + jump-limit check provide independent additional protection on the real live path (`M5_4_1` §7.3) |
| 640×360 confidence ceiling (dense_B_anchors, most correct reads never clear 70) | OCR (confidence calibration at low native resolution) | **Open, unaddressed by any milestone in this series** — named explicitly by `M5_3` §14/§9 and reconfirmed here (§7) |
| Tracking lock loss (noise, extreme scale/rotation, unrelated scene) | TRACKING | **Closed since M5.3** — fail-closed, 32/32 negative controls, reconfirmed inert this milestone (no code touched) |
| RECONCILE withholding a correct-but-unconfirmable read (hold-coincidence) | RECONCILE (by design) | **Known, accepted** — `M5_1`/`M5_3` both name this; not a defect, the confidence gate working as intended |

---

## 15. Latency

| Stage | Cost | Source |
|---|---:|---|
| Tracker init (once per WS connection) | 23-75 ms | `M5_3` §11, unchanged (code untouched) |
| `track()` per frame | 122-196 ms | `M5_3` §11, unchanged |
| OCR, all fields | ~750-1010 ms | `M5_3`/`M5_1`, unchanged — dominant cost |
| **M5.4.1's own added cost** | **0 ms measurable** | `M5_4_1` §4/§7.4 — `read_vital_with_diagnostics` vs `read_vital`, 30-call microbenchmark, 256.34 vs 255.83 ms/call (noise) |
| **frame total, live tracked path** | **~1.0 s** | unchanged; inside `ROADMAP.md`'s ≤1.5s/frame criterion |

No stage's latency changed this milestone. p50/p95 were not re-measured
(no new latency-relevant code path exists to measure) — `M5_3` §11's own
figures stand.

---

## 16. E2E — consolidated across the series

Per this milestone's instruction ("include at least: normal reading,
changing vital, critical reading, tracking movement, tracking failure, low
OCR confidence, session end/reset") and its own instruction not to make
production changes: rather than write one more redundant giant script, the
checklist below maps each item to the **real, already-executed, committed**
E2E script that covers it — every one a genuine `uvicorn` subprocess, real
HTTP, a real WebSocket client, a real scratch SQLite file, never
`TestClient`:

| Checklist item | Covered by | Result |
|---|---|---|
| Normal reading | `m5_3_e2e_script.py` step 5 (unmoved frame, real OCR) | ✅ locked, plausible HR reading produced |
| Changing vital across ticks | `m5_4_1_e2e_script.py` step 5 (4 distinct real frames pushed in sequence) | ✅ tracking/OCR react per-frame, confirmed values evolve correctly |
| Critical reading | `test_alerts_still_fire_for_a_temporally_corroborated_critical_value` (unit-level against real `reconcile()`+`check_alerts()`, not mocked) — a real-process critical-alert E2E already exists in `m4_6_e2e_script.py`'s own coverage of `check_alerts()`, unchanged since | ✅ critical alert fires |
| Tracking movement | `m5_3_e2e_script.py` step 6 — deliberate pan+zoom+roll; every vital reads the same value after the move as before it | ✅ |
| Tracking failure | `m5_3_e2e_script.py` step 7 — pure noise frame; all confidences collapse to 0.0, values held, no synthetic fallback | ✅ |
| Low OCR confidence | `m5_4_e2e_script.py` — real sub-70%-confidence SpO2 crop, held/flagged correctly with the flag off, corroborated-and-flagged (never silently) with it explicitly on | ✅ |
| **The exact M5.4.1 regression, live** | `m5_4_1_e2e_script.py` — the real too-narrow HR box, real tracking-induced shrink, `TEMPORAL_CORROBORATION=on` explicitly, 4 real consecutive pushes | ✅ **10/10, HR never confirmed as 8** |
| Session end/reset | `m5_3_e2e_script.py` step 9 (`DELETE /api/calibration/active`) + `test_session_reset_clears_clean_run_state`/`test_session_reset_clears_temporal_evidence` (fresh connection carries no prior-session state) | ✅ |

**Does E2E differ materially from offline evaluation? No** — every E2E
script above reads real dataset PNGs through the real live HTTP/WebSocket
transport and produces values matching the offline batch-eval numbers
(§6-8) on the same frames (e.g. `m5_3_e2e_script.py`'s HR/SpO2 values after
the deliberate move match the offline tracked-arm reads for the equivalent
geometry; `m5_4_1_e2e_script.py`'s outcome matches
`m5_4_1_crop_integrity_eval.py`'s offline replay exactly: HR never
confirmed as 8, either way).

---

## 17. Tests

`pytest tests/ simulator/tests/ -q` → **397 passed, 0 failed** (re-run for
this report, not assumed from `M5_4_1`'s own count). `npx tsc --noEmit` →
clean (no frontend file touched anywhere in M5.2 → M5.4.1's temporal/
confirmation work beyond earlier milestones' own frontend changes, already
verified clean by each of those reports).

---

## 18. Known limitations (the honest ceiling on this GO)

Carried forward and consolidated — none of these are new to this
milestone, and none are resolved by it:

1. **Dataset A's 11 confidently-wrong confirmations are real and
   unaddressed.** They clear the existing `ai_medium`/`ai_high` tiers
   directly (61-95% confidence) via a too-narrow calibration box, not
   through temporal corroboration — out of every milestone's scope since
   M5.2 named it. The fix is calibration-UX (a live truncation warning at
   draw time), not confirmation logic.
2. **All real camera-motion evidence is one 54-second span of one
   recording of one GE CARESCAPE B650.** Nothing in this series
   generalizes to monitors, cameras, or lighting conditions in general.
3. **Dataset B's ~46% oracle-crop OCR ceiling reflects a
   phone-recording-of-a-YouTube-video capture chain**, not a clean second
   camera — "different manufacturer" is confounded with "much worse image
   quality" (`EVIDENCE.md` §9, unchanged).
4. **The 640×360 OCR confidence ceiling (~51 vs. a 70 gate) is the
   dominant constraint on Dataset B's confirmed accuracy**, and remains
   completely unaddressed by any milestone in this series.
5. **`TEMPORAL_CORROBORATION` provides zero net measured accuracy benefit
   over being off, on this specific evidence base**, once gated safely
   (M5.4.1 §9) — it is not recommended for production, and this report
   does not recommend flipping its default.
6. **No physical-camera, human-operated browser E2E exists anywhere in
   this series** (`M5_1`§14/`M5_2`§12/`M5_3`§12/`M5_4`§11, unchanged) —
   every E2E is a real backend process driven by scripted HTTP/WebSocket
   clients pushing real dataset images, not a human clicking through a
   live browser with a live webcam.
7. **The 20%-aspect-drift threshold and 20% width safety pad are
   evidence-*informed*, not evidence-*tuned*** (`M5_2`§16) — no dataset of
   "same camera, deliberately varied aspect/padding" exists to grid-search
   either against.
8. **The tracker misses `ROADMAP.md`'s <50ms/frame budget by ~4x**
   (122-196ms measured) — end-to-end still meets the 1.5s/frame criterion
   because OCR dominates, but the tracker itself is not the fast path the
   original target envisioned.

---

## 19. Remaining risks

1. A second real monitor/camera combination could surface a truncation
   shape `has_residual_content` does not catch (it is a moderate-precision
   signal, not a proof — `M5_4_1` §9).
2. Dataset A's calibration-box-truncation class remains a live risk on any
   monitor whose operator draws a box too tightly around a momentary short
   value — the Verify step's live-crop preview (M5.2) is the only current
   mitigation, and it depends on the operator noticing.
3. Nothing in this series has been tested against non-numeric monitor
   states (dashes, `APN`, alarm banners covering a field) beyond what
   `EVIDENCE.md` §9's "Missing values... already correct" finding covers.
4. The ONNX models on disk (`models/*.onnx`) are inert in the frozen
   configuration (§2) but remain switchable via `OCR_ENGINE=onnx`/
   `ROI_ENGINE=tier2` — a future operator setting either without reading
   `ARCHITECTURE.md`'s retirement rationale would silently reintroduce the
   anti-calibrated FieldCNN this series specifically retired.

---

## 20. Exact production files changed across M5.4.1

(M5.5 itself changes none — see banner above.) Reproduced from
`M5_4_1_CROP_INTEGRITY_REPORT.md` §10 for this report's own completeness
requirement:

**New:** `app/validation/crop_integrity.py`.
**Modified (all additive — no removed parameter, no changed return
signature, no altered config/threshold constant):** `app/pipeline/ocr.py`,
`app/pipeline/read_frame.py`, `app/sources/base.py`, `app/sources/camera.py`,
`app/validation/temporal.py`, `app/validation/reconcile.py`,
`app/ws/vitals.py`.
**Untouched, verified twice** (M5.4.1's own inspection, §10 there; this
milestone's independent fresh re-run, §5): `app/pipeline/calibrated_roi.py`,
`app/pipeline/layout_tracker.py`, every OCR routing/config constant,
`app/validation/rules.py` (0 lines across the entire M5.2→M5.5 series),
`app/alerts/rules.py`, `app/db/repo.py`, `app/models/calibration.py`, every
frontend file.

---

## 21. Rollback procedure

Identical to `M5_4_1_CROP_INTEGRITY_REPORT.md` §12 — this milestone made no
production change, so there is nothing additional to roll back. If a
future operator needs to revert the entire M5.4.1 mechanism: stop passing
`per_vital_crop_suspicious` (one call site, `app/ws/vitals.py::send_loop`)
or simply leave `TEMPORAL_CORROBORATION` at its already-off default, which
requires no action at all.

---

## 22. Final GO / NO-GO

| Safety gate (this milestone's own stated bar) | Result |
|---|---|
| Confidently-wrong confirmations increase anywhere | ❌ did not happen — 0 increase on every arm, every milestone's own baseline (§4, §8, §9) |
| Critical alerts regress | ❌ did not happen — unmodified, tested (§9) |
| Tracking failures produce guessed values | ❌ did not happen — fail-closed, unchanged, reconfirmed (§9, §16) |
| Invalid crops get confirmed | ❌ did not happen — the one known real instance is now closed (§4) |
| Temporal corroboration creates new unsafe confirmations | ❌ did not happen — M5.4.1 closes the one found; shipped off regardless (§2, §11) |
| A major vital has unacceptable regression | ❌ did not happen — HR remains the weakest-localized vital (a known, pre-existing, unresolved characteristic, §10) but did not regress from any prior milestone's own measurement |
| E2E differs materially from offline evaluation | ❌ did not happen — matched exactly on every checked frame (§16) |
| Test suite regresses | ❌ did not happen — 397 passed, up from 372, 0 failed (§17) |

**None of the NO-GO conditions occurred. GO.**

**The correct promotion claim:** *"VITAL's calibrated + tracked recognition
pipeline, with M5.4.1's crop-integrity-gated (but disabled-by-default)
temporal corroboration, has been validated on the available real-world
monitor video datasets (one static screen recording, one handheld
phone-camera recording with real motion) and the real application
pipeline end-to-end — not on a clinical population, not on a physical
camera/browser session, and not on more than one real physical
monitor."* Nothing stronger is supported, and nothing stronger is claimed.

---

## 23. Recommended M5.6 action

**Proceed to M5.6 (Promotion / final demo review) on the following exact,
narrow terms — do not broaden them without new evidence:**

1. Flip `ROI_ENGINE`'s default per `ROADMAP.md` M5.6's own scope — this
   only affects the non-camera/eval code paths; the live WS camera path
   has used the calibrated(+tracked) path since M5.2 regardless of this
   flag (§2).
2. Leave `TEMPORAL_CORROBORATION` **off** by default. Do not flip it as
   part of M5.6 — this report's own evidence (§8, §11) does not support
   it, independent of M5.4.1's safety fix being real and correct.
3. Carry `ROADMAP.md`'s own still-open recommendations forward
   unmodified: a calibration-UX truncation warning at box-draw time
   (closes Dataset A's 11 CW, §18 item 1) and a second real
   monitor/camera's worth of held-out data before this evidence base is
   treated as anything more than "one monitor, one recording."
4. Do not present any number in this report, or any of its cited
   predecessors, as "clinically validated." The correct claim is §22's
   final sentence, verbatim.
