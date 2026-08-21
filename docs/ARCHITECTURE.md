# VITAL — Recognition Architecture

**Status:** current as of 2026-08-19. Supersedes everything in [`archive/`](archive/).
Companion documents: [`ROADMAP.md`](ROADMAP.md) (how we get there) ·
[`EVIDENCE.md`](EVIDENCE.md) (the measurements behind every claim here).

---

## The one-sentence version

VITAL is **monitor-agnostic after a ~15-second calibration**, not universally
monitor-agnostic without one — because the information that distinguishes one vital
field from another is *position and the printed label beside it*, and no amount of
model training recovers information the input never contained.

---

## Why the previous architecture is being retired

The Tier-2 pipeline (M1 → M4.6) was:

```
frame → detect_screen → candidate generation → FieldCNN → selection → OCR → reconcile
```

Re-run stage-by-stage against both datasets, three of those five stages do not work.

**`detect_screen()` has never fired on real data.** 0/52 on Dataset A, 0/17 on
Dataset B. It requires a convex 4-gon covering >=50% of the frame; a monitor
photographed in a room is a bright screen inside a dark bezel inside clutter, and
that quad never closes. Every downstream size constant is therefore computed against
the raw camera frame rather than the screen — i.e. they are functions of *camera
framing*, not of the monitor.

**Candidate generation has no cross-monitor operating point.** It merges glyphs with
a globally-sized dilation kernel, then discards components exceeding a size cap. On
Monitor B that kernel bridges Temp/EtCO2/RR into one blob — Temp's is **2712x1220,
the entire frame** — which is then dropped. All 36/36 misses are this single mode. A
kernel sweep shows Dataset A's optimum and Dataset B's optimum are **mutually
exclusive**: whether two glyph clusters *should* merge is a property of a specific
monitor's typography, which a global parameter cannot encode.

**The FieldCNN is being asked an underdetermined question.** It receives a grayscale
64x64 letterboxed crop — colour, position, and the adjacent printed label all
discarded by design. At that point HR `84` and NIBP-diastolic `84` are the *same
image*. Given **perfect** ground-truth crops it scores **4.3% on Monitor B** and
**0.0% with 10% box jitter**; on its own source monitor it manages 64.8%, calling all
17 etco2 crops `spo2`.

It also fails **confidently**: on Monitor B its mean confidence when *wrong* (92.1%)
exceeds its confidence when *right* (76.4%), and 97% of wrong calls clear
`MIN_CLASSIFIER_CONFIDENCE`. `read_frame()` fused this into the safety gate as "the
weakest signal". It is not a weak signal — it is an **anti-correlated** one.

Meanwhile the two stages that were being blamed are healthy: given correct crops and
a whitelist-free config, **OCR reads Dataset A at 98.4%** (against 61.6%
end-to-end), and `reconcile()` behaved correctly throughout — it rejected everything
because everything handed to it was untrustworthy.

**The M1 to M4.6 accuracy deficit was a localization deficit.** Every milestone from
M4.1 onward tuned OCR to compensate for boxes that were in the wrong place.

---

## Target architecture

Stop *inferring* layout. Be **told** it once, by the operator, then **track** it.

```
+-- CALIBRATION - once per monitor/room, ~15 s, human-in-the-loop -------------+
|                                                                              |
|  live frame                                                                  |
|      |                                                                       |
|      +--> operator drags 4 screen corners ------------> homography           |
|      |       (replaces the dead detect_screen)                               |
|      |                                                                       |
|      +--> operator draws / confirms 6 field SLOTS -----> roi_boxes           |
|      |       optionally pre-proposed by the OLD candidate generator +        |
|      |       FieldCNN - being wrong is harmless here, a human confirms       |
|      |                                                                       |
|      +--> VERIFY: OCR each box live, operator ticks each value               |
|      |       ^ the gate that catches a badly-drawn box before it ships       |
|      |                                                                       |
|      +--> ORB keypoints of the STATIC chrome ---------> layout_anchor        |
|              (labels, borders, bezel - never the digits, those change)       |
|                                                                              |
|                     v  persisted as CalibrationProfile                       |
+------------------------------------------------------------------------------+
                      |
+-- LIVE LOOP - 1 Hz --v-------------------------------------------------------+
|                                                                              |
|  frame --> ORB match vs layout_anchor --> per-frame affine        (~25 ms)   |
|              |                                                               |
|              +- inliers < N --> DEGRADE: hold last confirmed value,          |
|              |                  surface "monitor lost - recalibrate"         |
|              v                                                               |
|         map the 6 calibrated boxes through the affine                        |
|              v                                                               |
|         6 crops --> OCR, whitelist-free, per-field PSM          (~1000 ms)   |
|              v                                                               |
|         multi-signal confidence (see below)                                  |
|              v                                                               |
|         reconcile()  -- UNCHANGED -->  alerts . persistence . WebSocket      |
+------------------------------------------------------------------------------+
```

### What this measured

Calibrate on one frame, then track: **Monitor B goes 5.7% -> 57%** OCR accuracy with
**zero retraining**. HR 53->76%, SpO2 53->94%, mean box IoU 0.54->0.68. ORB locks on
every frame (mean 226 inliers, min 23) despite three distinct camera framings.

Tracking *beats the per-frame oracle* (47%), because one well-drawn calibration box
carried forward is better than 17 independently-drawn tight ones.

### What gets retired, and where it goes

| Component | Runtime role | New role |
|---|---|---|
| `detect_screen()` | removed | replaced by operator-placed 4-corner homography |
| `adaptive_threshold_candidates_v2()` | removed | **calibration assist** — proposes boxes for a human to accept |
| `FieldClassifierEngine` | removed from the decision path | **calibration assist** — labels proposed boxes for a human to accept |
| `TesseractEngine` | kept, whitelist-free | unchanged interface |
| `reconcile()` / `rules.py` | kept **unchanged** | unchanged |

Nothing is deleted from the codebase. The classical CV stack moves from the runtime
decision path — where being confidently wrong is a safety failure — to the
calibration-assist path, where a human is the check.

**Latency:** −900 ms/frame. Candidate generation measures 911 ms/frame; the ORB
tracker replacing it costs 25 ms. Current end-to-end median is 2.8 s.

---

## Confidence model

`min(classifier_confidence, ocr_confidence)` is replaced by a conjunction of
**independently-failing** signals:

```
confirm  <==  OCR engine confidence      (whitelist-free - see below)
          AND preprocessing agreement    (N binarisations -> same digits?)
          AND tracking lock quality      (ORB inlier count)
          AND range + jump plausibility  (reconcile(), unchanged)
          AND temporal consistency       (bounded-drift agreement)
```

**Every term can only *withhold* confirmation, never manufacture it.** The core
invariant — *uncertain data must not silently become confirmed data* — is preserved
by construction rather than by tuning.

### The confidence gate is correct and must not be lowered

`CONFIDENCE_MEDIUM_MIN = 70` is well calibrated wherever it has dynamic range:
Dataset A conf<40 -> 5% accurate, conf>=70 -> **100%** accurate. Dataset B conf<40 ->
30%, conf 40-69 -> 90%. Lowering the threshold would admit the 30% bucket.

The problem was never the bar — it was that **`tessedit_char_whitelist` crushes the
signal's dynamic range to zero.** This is the exact mechanism M4.4 root-caused and
fixed for NIBP/EtCO2, then explicitly deferred for HR/SpO2/RR. Removing it takes
Monitor B's correct-read confidence from 19.9 to ~72, and the share of correct reads
clearing the >=70 gate from **0% to 53%**. That is M5.1.

### Temporal consistency is bounded-drift, not repeat-voting

Ground-truth values change frequently between sampled frames (HR unchanged in only
18.8% of consecutive pairs on Dataset B), so "same value N frames running" would
almost never fire. At 1 Hz a real HR moves 1-2 bpm; consecutive reads should agree
*within a physiological step*, and `JUMP_LIMITS` in `validation/rules.py` already
encodes exactly those bounds. Temporal consensus is an additional **withholding**
term — a read contradicting a stable recent history is held — never a promotion rule
that upgrades a low-confidence read.

---

## What already exists

The wizard shell, the camera hook, and the data model are all present — the work is
wiring them to reality, not building them:

- **`backend/app/models/calibration.py`** defines `CalibrationProfile` with exactly
  the right shape (`homography`, `roi_boxes`, `layout_id`) — and **is referenced by
  nothing**. No API, no DB table, no pipeline use.
- **`src/features/calibration/CalibrationPage.tsx`** has the 5-step wizard, but its
  Detect / Perspective / Regions steps are **animated SVG mockups with hardcoded
  coordinates**. Only camera-connect and the single Verify `readFrame` call are real.
- **`backend/app/pipeline/read_frame.py`** already has the `ROI_ENGINE` swap seam
  (`tesseract` | `tier2`); the new path adds `calibrated` alongside them.

---

## Safety posture

The clinical claim gets **stronger**, not weaker. A clinician-confirmed ROI map is
more defensible than a black-box detector: an operator saw each region, saw it read
correctly, and signed off before the case started. Every failure mode below degrades
to *withholding a value and saying so*, never to a silent wrong reading.

| Risk | Behaviour |
|---|---|
| Camera moved or knocked | ORB inliers fall -> hold last confirmed + "recalibrate" banner |
| Monitor changes page/layout | same degrade path via falling inlier count |
| Glare / lighting / occlusion | lock quality and OCR confidence both fall -> withhold |
| Missing values (dashes, `APN`) | OCR returns `None` -> `unreadable` -> hold. Already correct |
| Badly-drawn calibration box | caught by the Verify step before Save is permitted |
| Confidently-wrong reading | the FieldCNN leaves the decision path entirely; every confidence term can only withhold |
