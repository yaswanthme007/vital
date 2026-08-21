# VITAL — Roadmap: M5.1 to demo-ready

**Status:** active plan as of 2026-08-19. Supersedes every roadmap in
[`archive/`](archive/) — in particular `TIER2_M5_...REPORT.md` §19, whose two lead
recommendations (investigate `detect_screen()`, fine-tune the FieldCNN) both target
dead ends. Architecture rationale: [`ARCHITECTURE.md`](ARCHITECTURE.md). Measurements:
[`EVIDENCE.md`](EVIDENCE.md).

**Budget:** days, demo imminent. **M5.1 + M5.2 + M5.3 are the demo-critical path**
(~3 days). M5.4 and M5.5 are do-if-time. M5.6 gates the default flip.

---

## Dependency order

```
M5.1  OCR confidence restoration        [0.5d]  <- START HERE
   |    unlocks measurability of everything downstream
   v
M5.2  Real calibration                  [1.5d]  <- the core
   |    homography + ROI slots + verify + persistence
   v
M5.3  Layout tracking                   [1.0d]  <- makes "until surgery is over" true
   |    needs the dense-frame dataset (see Data, below)
   v
M5.4  Multi-signal confidence           [1.0d]  optional
   v
M5.5  Cross-monitor benchmark           [0.5d]
   v
M5.6  Promotion                         [0.5d]  flip the ROI_ENGINE default
```

**Why M5.1 is first and not the localization work.** With the whitelist in place,
**0% of correct reads on Monitor B can clear the confirmation gate**. Fix
localization first and you produce correct crops the system still refuses to
believe — which looks exactly like the new architecture failing. M5.1 is half a day
and touches one file of constants.

---

## M5.1 — OCR confidence restoration

**Objective.** Finish the fix M4.4 made for NIBP/EtCO2 and explicitly deferred for
HR/SpO2/RR. This is completing existing, already-validated work, not new invention.

**Root cause.** `tessedit_char_whitelist` collapses this Tesseract build's reported
per-token confidence toward 0 on correct reads. Worst instance: production routes
SpO2 through `--psm 10` + whitelist, which on Dataset B reads **82% correctly at
confidence exactly 0**.

**Files.** `backend/app/pipeline/ocr.py` only — `_DIGIT_CONFIG`,
`_DIGIT_PSM10_CONFIG`, `_PSM10_VITALS`. Constants only.

**Experiments.** Per-field config sweep on oracle (ground-truth) crops, both
datasets. Commit it as `backend/app/eval/m5_1_ocr_config_sweep.py`.

**Expected result.**

| | before | after |
|---|---:|---:|
| Dataset B, mean confidence on correct reads | 19.9 | ~72 |
| Dataset B, correct reads clearing the >=70 gate | 0% | ~53% |
| Dataset A, OCR accuracy | 98.9% (oracle) | unchanged or better |

**Acceptance.** No Dataset A regression on any field. Dataset B's confidence
distribution regains dynamic range. `pytest tests/ simulator/tests/ -q` -> 284 passed.

**Rollback.** Revert `ocr.py`; constants-only change, zero structural risk.

**Explicitly do NOT.** Lower `CONFIDENCE_MEDIUM_MIN`. The gate is correct — see
[`ARCHITECTURE.md`](ARCHITECTURE.md#the-confidence-gate-is-correct-and-must-not-be-lowered).

---

## M5.2 — Real calibration

**Objective.** Make `CalibrationProfile` real and make it drive `read_frame()`.
Today the model exists and is referenced by nothing; the wizard's Detect /
Perspective / Regions steps are animated SVG mockups with hardcoded coordinates.

**Files.**

| Path | Change |
|---|---|
| `src/features/calibration/CalibrationPage.tsx` | replace mock Detect/Perspective/Regions SVGs with real corner-drag + box-draw on the live video feed |
| `backend/app/api/calibration.py` | **new** — POST/GET calibration profile |
| `backend/app/models/calibration.py` | add per-field unit + decimal format |
| `backend/app/db/models.py` | persist the profile |
| `backend/app/pipeline/calibrated_roi.py` | **new** — apply homography + stored boxes, same `Dict[str, Optional[VitalRoiResult]]` contract |
| `backend/app/pipeline/read_frame.py` | add `ROI_ENGINE=calibrated` **alongside** `tesseract` / `tier2` — remove neither |

**Calibration UX requirements (these are safety requirements, not polish).**

1. The operator draws the field's **display slot** — the region the monitor reserves
   — not the digits currently shown. Measured: a box drawn around a single-digit `0`
   caps that field at 28% forever once the reading goes to 3 digits.
2. Over-generous boxes hurt too (Dataset A: 71.5% -> 37.6% at 50% padding) by pulling
   in neighbouring text. The UI must show the live crop as the box is drawn.
3. **Verify OCRs every drawn box and blocks Save until the operator confirms each
   value.** This is the gate that catches (1) and (2) before a case starts.

**Expected result.** Dataset A >= 70%, Dataset B >= 35% OCR accuracy, pre-tracking.

**Acceptance.** Verify blocks Save on an unconfirmed field. Profile survives a
backend restart. `ROI_ENGINE` default still `tesseract` — the new path is opt-in.

**Rollback.** The new path is opt-in by env var; unset it.

---

## M5.3 — Layout tracking

**Objective.** Re-anchor the calibrated boxes every frame so the reading survives
camera drift for the length of a case. This is what makes the product claim
*"configure once, then it tracks until the surgery is over"* literally true.

**Method.** ORB features -> RANSAC partial-affine, estimated against the monitor's
**static chrome** (printed labels, panel borders, bezel). Not the digits — those are
exactly the thing that changes. A first attempt that templated the digit boxes
themselves tracked at IoU 0.51, *worse* than not tracking at all (0.54); anchoring on
static structure gets 0.68.

**Files.** `backend/app/pipeline/layout_tracker.py` (**new**), `calibrated_roi.py`,
`src/features/surgery/components/CameraOverlay.tsx` (draw tracked boxes + lock state).

**Dataset.** Requires the dense-frame extraction — see Data below. **The existing
17 sparse frames cannot validate tracking.**

**Expected result.** Reproduces the measured counterfactual:

| Mode | HR | SpO2 | mean IoU | OCR acc |
|---|---:|---:|---:|---:|
| current production Tier-2 | — | — | — | 5.7% |
| calibrated box, no tracking | 53% | 53% | 0.54 | 35% |
| calibrated box + tracking | **76%** | **94%** | **0.68** | **57%** |

**Acceptance.** Lock holds across a deliberate camera nudge. On lock loss the system
**holds the last confirmed value and says so** rather than reading a wrong region.
Tracker < 50 ms/frame.

**Rollback.** Disable tracking; fixed calibrated boxes still function (M5.2 result).

---

## M5.4 — Multi-signal confidence + temporal *(optional)*

**Objective.** Replace `min(classifier, ocr)` with the conjunction described in
[`ARCHITECTURE.md`](ARCHITECTURE.md#confidence-model).

**Files.** `backend/app/pipeline/ocr.py` (preprocessing agreement),
`read_frame.py` (fusion), `backend/app/validation/temporal.py` (**new**).
`reconcile.py` and `rules.py` stay **untouched**.

**Acceptance.** Every new term can only withhold. A synthetic confidently-wrong
injection is still rejected. No drop in Dataset A confirmed accuracy.

---

## M5.5 — Cross-monitor benchmark

Re-run through the calibrated path: Dataset A, Dataset B (frozen holdout),
Dataset B-dense, and >= 3 simulator-rendered unseen layouts.

**Report per-monitor, never pooled.** B-dense is a different split of the *same
recording* as B — it is not an independent monitor and must not be presented as one.

---

## M5.6 — Promotion

> **SUPERSEDED, 2026-08-20, by
> [`M5_6_FINAL_PRODUCTION_PROMOTION_REPORT.md`](M5_6_FINAL_PRODUCTION_PROMOTION_REPORT.md).**
> The instruction below — flip the `ROI_ENGINE` default to `calibrated` — was
> **not carried out, because it is unsafe.**
> `read_frame._build_roi_extractor_from_env` resolves `calibrated` by loading
> one profile from `CALIBRATION_PROFILE_PATH` and **raises** when that is
> unset, which production never sets. Flipping it would have 500'd
> `POST /api/pipeline/read-frame`, broken `ReplaySource("pipeline")`, and
> killed the camera WebSocket for any session with no profile yet. It would
> also have achieved nothing for the live path, which binds the database's
> active profile directly and never reads this flag.
> **What was promoted instead:** `POST /api/pipeline/read-frame` now prefers
> the active `CalibrationProfile`, matching the camera path, falling back to
> the previous behaviour when none exists. See §3 of the M5.6 report; the
> reason the flip was rejected is pinned by
> `tests/test_m5_6_promotion.py::test_flipping_roi_engine_default_to_calibrated_would_break_uncalibrated_paths`.

Flip the `ROI_ENGINE` default to `calibrated` **only if M5.5 passes every criterion
below**. Keep `tier2` and `tesseract` selectable.

---

## Acceptance criteria — GO / NO-GO

GO to promote requires **all** of:

1. Dataset A confirmed accuracy **>= 55.6%** (no regression vs the M4.6 baseline).
2. Dataset B frozen holdout micro OCR **>= 40%** — against 5.7% today.
3. **Zero confidently-wrong confirmations**: no field confirmed at >= 70 confidence
   with a wrong value, on any dataset. **This is the hard gate.** A lower accuracy
   with zero confident errors beats a higher one with any.
4. >= 2 unseen simulator layouts read >= 80% after calibration, **with no code change
   between them**. This is the generalization claim.
5. End-to-end latency <= 1.5 s/frame (from 2.8 s).
6. Tracking survives a deliberate camera nudge, or degrades to an explicit
   "recalibrate" state.
7. 284 backend tests green; `npx tsc --noEmit` and `npx vite build` clean.

**NO-GO / rollback if:** any confidently-wrong confirmation appears; Dataset A
regresses; or tracking silently drifts onto a wrong region without lowering
confidence.

---

## Data

| Need | Source | Why |
|---|---|---|
| **Validate tracking + temporal** | **Dense frame extraction from the B650 video already on hand** — every ~1 s over a continuous segment | Free, real monitor, real camera drift and blur. The existing 17 sparse screenshots **cannot** validate tracking or temporal consensus at all. Highest-value dataset; costs an afternoon. |
| **Prove layout-agnosticism** | **VITAL's own simulator** (`backend/simulator/render/`) — render unseen layouts, fonts, palettes | Cheap breadth. Proves calibration + OCR work on layouts nobody tuned against. Cannot validate tracking — no camera, no drift. |
| Later: real-world validity | Camera pointed at a physical monitor | The only thing that settles clinical claims. Not needed for the demo. |

**Not needed:** any new *annotated detection* corpus. This architecture requires **no
training data at all** — a large part of why it is the right call on a days-long
budget.

**Holdout discipline.** The 17-frame Dataset B stays frozen and untouched as the
holdout. Dense frames from the same video are a different split of the same
recording, and must be reported as such.

---

## Demo script

1. **Calibrate (~15 s, live).** Point the camera at the monitor. Drag the 4 screen
   corners. Draw 6 boxes — or hit *Suggest regions* and let the old candidate
   generator + FieldCNN propose them, then correct. **Show a wrong suggestion being
   corrected.** This makes human-in-the-loop the feature, not an apology.
2. **Verify.** Each box OCRs live, side by side with the monitor. Operator ticks
   each. Save -> `CalibrationProfile` persisted.
3. **Active Operation.** Vitals stream continuously with per-field confidence badges
   and confirmed/held status; the observation ledger fills in live as the camera
   confirms values. **(M5.7, 2026-08-20)** — see below.
4. ~~**Nudge the camera on purpose.** The tracked boxes follow. This is the money shot,
   and the thing the current architecture cannot do at all.~~
   **REMOVED FROM THE DEMO, 2026-08-20 (M5.6 §13.1).** The tracker does hold
   lock — 100% across a measured nudge — but OCR reading the motion-resampled
   crop misreads `8` as `3` (`38`→`33`, `98`→`93`) at 71-90% confidence, which
   clears the 70% confirmation gate: **38 confidently-wrong confirmations in
   101 frames, EtCO₂ failing at ~7px of movement.** Do not invite anyone to
   compare on-screen values against the monitor while the camera is moving.
   See [`../backend/CAMERA_DEMO.md`](../backend/CAMERA_DEMO.md).
5. **Occlude a field with your hand.** Confidence drops, the value holds, the badge
   goes amber. *Demonstrating a refusal to guess is a stronger clinical story than a
   high accuracy number.*
6. **Alerts** fire off confirmed values; flagged readings queue for review.
7. **Clinician sign-off** -> chart PDF, persisted, tamper-evident.
8. **The closer:** re-run calibration against a *second, different* layout — simulator
   or a second video — live, **with no code change**. Generalization demonstrated
   rather than asserted.

---

## Verification per milestone

- **M5.1** — rerun the per-config oracle-crop sweep on both datasets; confirm
  Dataset B's correct-read confidence rises ~20 -> ~72 and Dataset A regresses
  nowhere. `pytest tests/ simulator/tests/ -q`.
- **M5.2** — replay both datasets through `ROI_ENGINE=calibrated`; manually walk the
  wizard against a screen-shared browser tab (the existing *Share a Tab/Screen
  Instead* path needs no second device or camera).
- **M5.3** — replay the dense extraction; assert tracked-box IoU > fixed-box IoU;
  live-nudge the camera and confirm boxes follow and lock quality is reported.
- **End-to-end** — calibrate against a simulator-rendered *unseen* layout, run the
  live monitor, occlude a field, confirm the value holds and is flagged, sign off,
  export the chart.
- **M5.7** — `app/eval/tier2_data/m5_7_report/m5_7_e2e_script.py` against real
  `dense_B/` footage: continuous camera observation, confirmed-only persistence,
  `GET /readings` matching SQLite, Archive's real summary. See
  `docs/M5_7_CONTINUOUS_CAMERA_OBSERVATION.md`.

---

## M5.7 — Continuous Camera Observation (2026-08-20)

**VITAL continuously observes the anaesthesia monitor through the camera for the
duration of an active operation and records only confirmed camera-derived
observations into the case timeline.** Calibration verification is configuration
metadata, never patient history.

M5.1–M5.6 built a continuous, safe camera pipeline (`CameraSource → read_frame →
calibrated ROI + LayoutTracker → OCR → crop-integrity → reconcile → WebSocket →
SQLite`) that already ran on every pushed frame. Two defects made it behave like a
one-shot verification tool instead: (1) the Calibration → Start Case handoff always
routed through the New Case form, even mid-case, so the camera never stayed
connected long enough to matter; (2) `send_loop` persisted `reconcile()`'s *display*
output every tick, so a held/baseline value (the monitor briefly unreadable, a
hand over the display) was written to `vital_readings` as if it were a genuine
observation. See `docs/VITAL_LIVE_CAMERA_TRACKING_ARCHITECTURE_ANALYSIS.md` for the
full trace and evidence.

The fix splits LIVE STATE (what the UI displays — held values allowed, marked
stale) from the OBSERVED TIMELINE (what SQLite stores — confirmed fields only, on a
≥1s-or-changed cadence). Nothing in the M5.1–M5.6 safety stack changed: confidence
gate, range/jump validation, crop integrity, motion withholding, calibration
verification, `TEMPORAL_CORROBORATION` off, camera/Demo Mode isolation are all
untouched. The Active Operation workspace (formerly "Live Monitor") now owns a
visible camera feed, a live observation ledger, and confirmed/held status per
vital; the camera capture loop and vitals WebSocket moved to an app-root owner so
navigating to Review/Archive mid-case no longer pauses observation.
