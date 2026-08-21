# M5.3 — Layout Tracking Report

**Status:** complete, 2026-08-20. Scope: per-frame re-anchoring of the
calibrated ROI boxes (`layout_tracker`), reference-frame persistence, the
transform safety layer, live lock-state UI, and a dense-frame temporal
dataset extracted from the original source recording. Companion documents:
[`ROADMAP.md`](ROADMAP.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
[`EVIDENCE.md`](EVIDENCE.md) ·
[`M5_1_OCR_CONFIDENCE_REPORT.md`](M5_1_OCR_CONFIDENCE_REPORT.md) ·
[`M5_2_REAL_CALIBRATION_REPORT.md`](M5_2_REAL_CALIBRATION_REPORT.md).

**No prior report, dataset, or evidence file was modified by this milestone.**
Where this report corrects an earlier one, it does so by citation (§3).

---

## 1. Objective

M5.2 shipped operator calibration: normalized ROI boxes drawn once, verified
by real OCR, persisted, and applied to every later frame. Those boxes are
**static**. If the camera or the monitor moves, they keep cropping the pixels
that *used* to hold each vital.

M5.3 adds per-frame re-anchoring so the calibrated geometry moves with the
monitor:

```
CALIBRATED ROIS + TRACKED GEOMETRY = STABLE CROPS UNDER CAMERA MOTION
```

with OCR (M5.1) and `reconcile()`/`rules.py` **completely unchanged** — the
tracker may only relocate a crop or refuse; it can never manufacture
confidence.

---

## 2. Phase 0 — inspection findings

Answered against the actual code before anything was written.

| # | Question | Finding |
|---|---|---|
| 1 | Where does the calibration reference frame live? | **Nowhere.** `CalibrationProfile` stored `reference_width`/`reference_height` only — the numbers needed to *interpret* normalized boxes, not the pixels they were drawn on. |
| 2 | Does the profile persist a reference image? | No. `calibration_profiles` had no image column and no related table. |
| 3 | Minimum safe mechanism? | Persist the reference **image**; derive features at load (§6). |
| 4 | Where should anchor metadata live? | A new table, plus two metadata fields on the profile (§6). |
| 5 | Can `CalibrationProfile` be extended safely? | Yes — its own docstring reserved exactly this, "once something actually consumes it". Both new fields default to the pre-M5.3 answer, so M5.2 profiles deserialise unchanged. |
| 6 | Where should per-frame tracking happen? | Inside the existing `make_extractor()` closure — the one seam `read_frame()`, `CameraSource` and the Verify endpoint already share. |
| 7 | Can that closure stay the seam? | Yes, with **no signature change**: `img -> Dict[str, Optional[VitalRoiResult]]` is preserved exactly. |
| 8 | OpenCV available? | `opencv-python-headless==5.0.0.93` — ORB, BFMatcher, FLANN, `estimateAffinePartial2D`, ECC all present. No new runtime dependency was added. |
| 9 | Which tracker? | **ORB + BFMatcher(crossCheck) + RANSAC.** FLANN-LSH was implemented and measured: slower *and* worse (§7). ECC needs a good initial guess and cannot survive the 1.9× scale jumps in this footage. |
| 10 | Which transform? | **Similarity** (translation + rotation + uniform scale). Homography and full affine rejected (§5). |
| 11 | On poor confidence? | **Fail closed** — withhold every field (§8). |
| 12 | Detecting bad transforms before OCR? | A two-stage gate: transform-level bounds, then transformed-ROI geometry checks (§7). |

---

## 3. A correction to the M5.2 report, and what it changes

M5.2 §9 attributed its residual confidently-wrong confirmations on Dataset A
to *"box-position drift (the calibrated box staying fixed while the
monitor/camera framing shifts across this recording)"* and handed that to
M5.3 as the fix.

**That attribution is wrong, and this milestone's first measurement showed
it.** Tracking every Dataset A frame against `sample_0002`:

| Dataset A, 52 frames | measured |
|---|---:|
| max \|translation\| | **0.1 px** |
| max \|scale − 1\| | **0.0000** |
| max \|rotation\| | **0.002°** |

There is no camera motion in Dataset A at all — and there cannot be: the
source recording (§4) is a **screen recording**, not a camera pointed at a
monitor. The per-frame variation in its ground-truth boxes is **width**, not
position: HR's annotated box ranges 105→360 px wide as the digit count
changes, while its centre moves by σ≈5 px (annotation jitter).

**Consequence, stated plainly: M5.3 does not and cannot reduce Dataset A's
confidently-wrong confirmations.** They are a too-narrow-box artifact, which
is a calibration-drawing problem (M5.2's Verify gate and width padding), not
a tracking problem. Measured below: Dataset A is a **perfect no-op**, 11
confidently-wrong before and after.

The M5.2 report file is left exactly as written; this section is the
correction of record.

---

## 4. Data provenance

`app/eval/m5_3_dense_extract.py` (committed, reproducible).

**Acquisition.** Automated retrieval from both cited URLs failed on this
machine (Dataset A: *"This video is not available"*; Dataset B: HTTP 403/416
across every yt-dlp player client, one reporting the video as DRM protected).
The project owner supplied both files directly; the script prefers a local
file and needs no network. A partial download produced during the failed
attempts was deleted.

| | Dataset A | Dataset B |
|---|---|---|
| File | `Anesthesia Scenario.mp4` | `GE CARESCAPE B650 Anesthesia Patient Monitor.mp4` |
| sha256 (first 16) | `e732fe4830a6b031` | `8b3bbc89ae53f560` |
| Resolution / fps | 638×360 @ 30 | 640×360 @ 30 |
| Duration / frames | 491.1 s / 14 734 | 219.5 s / 6 586 |

**Correspondence gate — the step that makes these videos usable as
evidence.** Every frozen sample was ORB-matched back into its source video
and had to clear 25 inliers (unrelated image pairs score 3–13, §7):

| | matched | inliers | time span located |
|---|---:|---:|---|
| Dataset A | **52/52 (100%)** | 718–1032 | 5.7 s → 481.0 s |
| Dataset B | **17/17 (100%)** | 688–1211 | 165.7 s → 219.5 s |

Both gates PASSED decisively, in strict chronological order. The frozen
datasets are confirmed captures of these exact recordings.

**Dense extraction (Dataset B).** The 17 frozen samples occupy one contiguous
54-second span, so that span — chosen by the data, not by hand — was
extracted end to end at **200 ms (5 fps) → 270 chronological frames**
(`dense_B/`), each carrying video sha256, frame index and presentation
timestamp. No frame in the span was skipped, filtered, or dropped for being
hard.

Measured motion across that span (the reason it is worth having):

| segment | framing |
|---|---|
| 165.7 – 189.7 s | static (scale 1.000, translation ≈0.1 px) |
| 189.7 – 206.7 s | abrupt zoom to **1.6–2.08×**, translation to 622 px, roll to ±10° |
| 207 – 219.5 s | third framing, ≈1.36× |

**Ground truth on dense frames — carried, never invented.**
`app/eval/m5_3_dense_annotate.py` maps each frozen sample's **human-drawn**
boxes onto its own source video frame through the verified similarity between
them (688–1211 inliers — the same picture at a different resolution, not two
similar pictures), producing 17 `dense_B_anchors/`. Values are the unchanged
human transcriptions, re-keyed. Anchors use the **exact** matched frame, never
a neighbour, so no transcription is attributed to a moment nobody read.

**Verified visually, and it found something.** Rendering the mapped boxes
showed them sitting slightly up-and-left of the digits — so the frozen
originals were rendered with their own annotations for comparison, and show
**exactly the same offset**. The mapping is faithful; Dataset B's human
annotation is systematically offset (consistent with `EVIDENCE.md` §9's note
that its Temp box is clipped). **This depresses absolute IoU on Dataset B for
both arms equally, so the A/B deltas stand while the absolute values
understate true localization quality.** Overlays: `m5_3_report/gt_mapping_check/`.

**Holdout discipline.** The frozen 17- and 52-frame datasets were not
modified. `dense_B` is a *different split of the same recording* and is
reported as such — never as an independent monitor.

**Dataset A dense frames were not extracted**: with zero measured camera
motion, they cannot exercise a tracker, and the 52 frozen frames already serve
as the no-op control.

---

## 5. Tracking architecture

`backend/app/pipeline/layout_tracker.py` — isolated, deterministic, bounded,
no network, no model, no synthetic fallback.

```
reference frame (the frame the boxes were VERIFIED on)
    └─ ORB features, digit ROIs MASKED OUT  ──── computed once per connection
current frame
    └─ ORB features
         └─ BFMatcher(NORM_HAMMING, crossCheck)
              └─ RANSAC → 2×3 similarity
                   └─ transform-level gates (§7)
                        └─ transform the calibrated boxes
                             └─ transformed-ROI geometry gates (§7)
                                  └─ crop → M5.1 OCR → reconcile()   [both unchanged]
```

**Track the chrome, not the digits.** `from_reference_image(exclude_boxes=…)`
masks the calibrated ROI regions out of the *reference* feature set, so the
anchor is printed labels, panel borders and bezel — implementing
`ROADMAP.md`'s finding that templating the digits tracked *worse* than not
tracking (IoU 0.51 vs 0.54), because the digits are exactly what changes.

**`TrackingResult`** carries `status`, the transform, match/inlier counts,
inlier ratio, reprojection error, scale, rotation, translation, reject
reasons and elapsed time. Failures are **never** collapsed to `None`:
`NO_REFERENCE_FEATURES · NO_FRAME_FEATURES · LOW_FEATURE_MATCHES ·
ESTIMATION_FAILED · LOW_INLIER_COUNT · HIGH_REPROJECTION_ERROR ·
INVALID_SCALE · INVALID_ROTATION · INVALID_TRANSLATION · ROI_GEOMETRY_FAILURE`.

**Determinism** is pinned by a test: fixed ORB parameters, crossCheck
matching, and `cv2.setRNGSeed` immediately before each RANSAC call (nothing
else in this codebase consumes cv2's RNG). Repeated calls return
byte-identical transforms.

### Transform model — why similarity

Measured real motion in this footage is pan + zoom + a few degrees of roll
(scale 0.51–2.08, rotation within ±11°). A **homography**'s 8 DOF are badly
under-constrained at the 24–100 inliers the hard frames actually produce, and
an under-constrained homography fails by *warping a crop into nonsense*
rather than by refusing — the worst possible failure shape here. **Full
affine** adds shear a rigid screen does not exhibit. `estimateAffinePartial2D`
(4 DOF) cannot express shear or perspective at all, so a bad estimate stays
geometrically sane enough for the bounds to catch it.

**Cost of that choice, stated: genuine perspective change is not corrected.**
It degrades as rising reprojection error and is a known limitation (§13).

---

## 6. Reference-frame design

**Chosen: persist the reference IMAGE; derive ORB descriptors at load.**

Rejected — persisting descriptors/keypoints — because it freezes the schema
to one ORB parameterisation (unable to be re-derived if feature count or
working resolution changes, both of which this milestone did change during
tuning), and because descriptors cannot be audited by a human against the
only question that matters: *is this the frame the operator calibrated on?*
Descriptors are a derived cache; the image is the source of truth.

**Storage:** a new table `calibration_reference_frames` (image bytes, mime,
sha256, width, height), **not** columns on `calibration_profiles`. Two
reasons: this codebase creates tables with `Base.metadata.create_all` and has
no migration tooling — `create_all` adds missing *tables* to a deployed
`vital.db` but never missing *columns*; and the ~20–400 KB blob stays out of
`get_active_calibration_profile()`, which runs on every WebSocket connection.

**Which frame — and how that is enforced.** The reference is the frame
**Verify** ran against: the exact frame every box was OCR'd and confirmed on.
`CalibrationPage` already clears each field's `verified` flag whenever a box
is moved or resized, so a saved profile's boxes were necessarily verified on
the frame being uploaded; the page now also discards the held frame on any
geometry edit. The server independently rejects a reference frame whose
dimensions disagree with the profile's `reference_width`/`height` (422), which
is the "do not accidentally use a different frame" requirement enforced rather
than trusted.

The profile gains exactly two fields — `has_reference_frame`,
`reference_frame_sha256` — both defaulting to the pre-M5.3 answer, so every
M5.2 profile loads unchanged and runs untracked.

---

## 7. Safety layer and thresholds

### How the thresholds were derived

From a measured separation between genuine tracking and deliberate negative
controls, **not** fitted to maximise any accuracy number:

| signal | genuine frames | negative controls |
|---|---|---|
| raw matches | 451 – 3 425 | 13 (blur), 171–1 030 (noise/other monitor) |
| **RANSAC inliers** | **≥ 24** | **3, 4, 11, 12** |
| inlier **ratio** | **0.03 – 0.98** | 0.23 (blur) |
| mean reprojection error | 0.2 – 2.9 px | 1.2 – 2.7 px |
| scale | 0.51 – 2.08 | 0.029, 0.063, 0.112 |
| rotation | ≤ 11° | 88°, 141°, 152°, 158° |

Two findings shaped the design:

- **Inlier ratio is actively misleading and is therefore NOT gated.** The
  genuine large-motion frames tracking exists to fix run at ratio 0.03–0.11 —
  *below* a blurred negative control's 0.23. Gating on ratio would reject
  exactly the frames that matter. The constant
  `INLIER_RATIO_IS_NOT_A_GATE` exists to document that decision.
- **Reprojection error barely separates** (genuine 0.2–2.9, failures 1.2–2.7),
  so it is set loose and catches only grossly inconsistent fits.
- **Inlier *count* is the load-bearing gate.** On the dense recording, frames
  yielding 9–13 inliers returned transforms whose rotation swung ±10° between
  adjacent half-second samples — visibly unreliable — while ≥50-inlier frames
  were stable. `MIN_INLIERS = 20` sits in that measured gap.

| Gate | Value | Basis |
|---|---:|---|
| `MIN_RAW_MATCHES` | 30 | blur control produced 13 |
| **`MIN_INLIERS`** | **20** | failures ≤12; genuine ≥24 |
| `MAX_REPROJECTION_ERROR_PX` | 4.0 | weak discriminator, deliberately loose |
| `MIN_SCALE` / `MAX_SCALE` | 0.4 / 2.5 | genuine 0.51–2.08; failures ≤0.11 |
| `MAX_ROTATION_DEG` | 20 | genuine ≤11°; failures ≥88° |
| `MAX_TRANSLATION_DIAGONALS` | 1.5 | pathological-translation guard |
| `MIN_ROI_VISIBLE_FRACTION` | 0.6 | box must be mostly on-screen |
| ROI area ratio | 0.25× – 4× | ROI size sanity |
| ROI pairwise overlap | ≤ 0.3 | mirrors `calibration_validate` |

**Honest margin note:** the hardest genuine frozen frame yields **23** inliers
against a different-monitor control's **12**. That is a real but narrow
margin, and the single most likely threshold to need revision on new footage.
Every statistic is emitted into the eval artifacts so all of these can be
re-derived rather than re-guessed.

### Negative controls — 32/32

`app/eval/m5_3_negative_controls.py`, run at both 2712×1220 and 640×360:

| class | cases | result |
|---|---|---|
| **MUST_REJECT** | different monitor · pure noise · black · uniform grey · extreme scale 4.7× · extreme rotation 81° · vertical flip | **all rejected**, each with a named status |
| **MUST_TRACK** | darkened 60% · 30% occluded · JPEG-degraded | all tracked at identity |
| **MUST_RECOVER** | known warps (1.25×, 0.75×, 8°, 1.4×/−5°) | all recovered within 0.05 scale / 1.5° |
| **SAFE_EITHER_WAY** | heavy blur · 90% occluded | see below |

**A case where my own test design was wrong, not the tracker.** "Heavy blur"
and "90% occluded" were initially classed MUST_REJECT. They are not geometry
failures: a smeared lens or a blacked-out sliver does not *move* the monitor,
so identity is the correct transform, and the tracker is not qualified to
judge legibility. The classes were corrected rather than the tracker — but
because that reasoning rests entirely on a downstream claim, the harness now
**proves** it: it runs the real production OCR over the resulting crops and
requires that nothing clears `reconcile()`'s gate.

> Measured: both cases track OK and produce **max OCR confidence 0.0 against
> a gate of 70.** The layered defence holds — geometry says "unmoved", OCR
> confidence says "unreadable", `reconcile()` holds the last confirmed value.

The harness also **raises rather than passing** if that assertion cannot be
run (an early version silently skipped it and still printed PASS; a skipped
safety check must never look like a passed one).

---

## 8. Integration and the fail-closed contract

`make_extractor(profile, tracker=None, on_tracking_result=None)` — closure
signature **unchanged**, so `read_frame()`, `CameraSource` and the Verify
endpoint are untouched.

- `tracker is None` → **byte-identical M5.2 static path** (the rollback state).
- tracking OK → boxes mapped into reference pixel space, transformed, geometry-gated, cropped.
- **any** non-OK status → **every field withheld** for that frame.

Withholding surfaces downstream as confidence 0.0 → `reconcile()` holds the
last confirmed value and flags it — existing, unmodified behaviour.

**No stale-transform reuse, and no static fallback, in the shipped default.**
Both were considered; the `m5_3_static_fallback` arm exists in the eval purely
to put a number on that choice rather than assume it. On this data it was
uninformative — lock rate on the frozen/anchor sets was 100%, so it never
triggered and scored identically to the tracked arm. Fail-closed therefore
costs nothing measurable here while remaining the safe default.

**Live path:** `_camera_roi_extractor` builds the tracker **once per
WebSocket connection** (never per frame), from the active profile's reference
frame. `LAYOUT_TRACKING=off` restores M5.2 exactly. A missing reference frame,
a corrupt reference image, or a DB error all degrade to the static path —
never a crash, never an untrusted transform.

The `reading` envelope gains an **additive** `tracking` key (absent entirely
when tracking is not running, so a client is never told "unlocked" by a
pipeline that never tracks).

---

## 9. Results — M5.2 static vs M5.3 tracked, identical frames

Every arm below runs on identical frames, from an identical single-frame
calibration profile, against identical ground truth.

> **On the baseline.** `m5_2_calibration_eval` builds its profile from the
> earliest frame annotating *each* vital, so different vitals can come from
> different frames. That is coherent without tracking but not with it — a
> Phase 0 probe mixing per-vital calibration frames drove RR's tracked IoU to
> **0.000**. Every arm here therefore calibrates all boxes from **one** frame,
> so these `m5_2_static` numbers are *not* the M5.2 report's published
> figures and are not meant to be. They are the correct like-for-like
> baseline. The M5.2 report's own numbers stand unchanged.

### Frozen Dataset B — reference `sample_0001`

| metric | M5.2 | M5.3 | delta |
|---|---:|---:|---:|
| mean IoU | 0.510 | **0.635** | +0.125 |
| IoU recall @0.3 | 50.0% | **77.1%** | +27.1 pp |
| IoU recall @0.5 | 50.0% | 62.5% | +12.5 pp |
| OCR accuracy | 31.4% | **49.0%** | +17.6 pp |
| OCR missing rate | 64.7% | 51.0% | −13.7 pp |
| confirmed accuracy | 11.8% | 21.6% | +9.8 pp |
| **confidently-wrong** | **0** | **0** | **0** |

Per-vital IoU: hr 0.501→**0.647**, spo2 0.520→**0.674**, temp 0.508→0.584.
Per-vital OCR: hr 50.0→**75.0%**, spo2 50.0→**81.2%**.

### Frozen Dataset B — reference `sample_0011` (reported, not hidden)

Reference choice is a real operator decision with a large effect, so both are
reported. `sample_0011` sits in the *zoomed* framing; calibrating there and
applying it statically to wide frames is catastrophic (IoU 0.072), and
tracking recovers most of it:

| metric | M5.2 | M5.3 | delta |
|---|---:|---:|---:|
| mean IoU | 0.072 | **0.338** | +0.266 |
| IoU recall @0.3 | 7.7% | **53.8%** | +46.2 pp |
| OCR accuracy | 4.1% | 12.2% | +8.2 pp |
| confirmed accuracy | 12.2% | 20.4% | +8.2 pp |
| **confidently-wrong** | **0** | **0** | **0** |

Per-vital IoU: hr 0.058→**0.443**, etco2 0.091→**0.444**, rr 0.167→**0.361**,
spo2 0.048→0.260, temp 0.062→0.231. RR OCR 16.7→**66.7%**.

### Dense Dataset B anchors (original recording, 640×360)

| metric | M5.2 | M5.3 | delta |
|---|---:|---:|---:|
| mean IoU | 0.488 | **0.633** | +0.145 |
| IoU recall @0.3 | 50.0% | **79.2%** | +29.2 pp |
| OCR accuracy | 31.4% | **52.9%** | +21.6 pp |
| OCR missing rate | 66.7% | 45.1% | −21.6 pp |
| confirmed accuracy | 15.7% | 11.8% | **−3.9 pp** |
| **confidently-wrong** | **0** | **0** | **0** |

Per-vital IoU: hr 0.481→**0.658**, spo2 0.503→**0.670**, temp 0.482→0.572.
Per-vital OCR: hr 50.0→68.8%, **spo2 50.0→100%**.

### Frozen Dataset A — the no-op control

| metric | M5.2 | M5.3 | delta |
|---|---:|---:|---:|
| mean IoU | 0.710 | 0.710 | **0.000** |
| IoU recall @0.3 / @0.5 | 100% / 77.2% | 100% / 77.2% | 0.000 |
| OCR accuracy | 85.8% | 85.8% | 0.000 |
| confirmed accuracy | 83.1% | 83.1% | 0.000 |
| **confidently-wrong** | **11** | **11** | **0** |

**Tracking is inert to three decimal places on a recording with no motion, on
all six vitals.** This is a required safety result: a tracker that invented
motion here would be actively dangerous. It is also the concrete demonstration
of §3 — Dataset A's 11 confidently-wrong confirmations are **untouched** by
M5.3, because they are a box-width artifact, not a position-drift one.

### Paired failure analysis (identical frame/field pairs)

| | frozen_B[0001] | frozen_B[0011] | dense anchors | frozen_A |
|---|---:|---:|---:|---:|
| fixed by tracking | 9 | 4 | 11 | 0 |
| **broken by tracking** | **0** | **0** | **0** | **0** |
| wrong in both | 26 | 43 | 24 | 32 |
| right in both | 16 | 2 | 16 | 193 |
| withheld that static got **right** | **0** | **0** | **0** | **0** |
| withheld that static got wrong | 19 | 0 | 19 | 0 |

**Tracking never broke a field that the static path read correctly, and never
withheld a field the static path got right, on any dataset.**

### Temporal arm — dense_B, 269 frames

No per-frame value ground truth exists for the full sequence, so this arm
reports **only** what needs none. No accuracy is claimed here.

| | |
|---|---|
| lock rate | **97.0%** (261/269) |
| failures | 5 `roi_geometry_failure`, 3 `low_inlier_count` |
| transform stability (consecutive locks) | translation step mean 13.4 px, max 235 px; rotation step mean 0.68°, max 9.6° |

Tracking holds across all three framings of a continuous real recording,
including the abrupt ~1.8× zoom, and the 8 frames it cannot trust it refuses.

### The confirmed-accuracy anomaly, root-caused

Dense anchors show OCR **up** 21.6 pp while confirmed accuracy falls 3.9 pp.
Root cause, measured rather than guessed — SpO2 on the dense anchors:

| | correct reads | mean confidence on correct | clearing the 70 gate |
|---|---:|---:|---:|
| M5.2 static | 8/16 | 50.7 | **1/8** |
| M5.3 tracked | **16/16** | 50.8 | **3/16** |

SpO2 becomes perfectly readable and *still* almost never clears the gate,
because 640×360 crops cap OCR confidence near 51 against an unchanged
threshold of 70. Confirmed accuracy on Dataset B is therefore dominated by
**hold-last-confirmed coincidence**, not by genuine confirmations — the same
mechanism M5.1 §9 identified for Dataset A's RR ("right because it got lucky
holding"). With n≈51 and only 1–3 real confirmations per arm, this metric
carries almost no signal on Dataset B; it moves the other way (5→8 confirmed
SpO2) on frozen_B. Reported as measured, claimed as neither a win nor a
regression. **Confidently-wrong stayed 0 throughout.**

---

## 10. Failure taxonomy

`app/eval/m5_3_overlay_gallery.py` → `m5_3_report/gallery/` (14 overlays).
Each renders the static box, the tracked box and ground truth on the real
frame, with the tracking statistics and the stage genuinely at fault:
**TRACKING** (refused) · **LOCALIZATION** (locked but box off-field) ·
**OCR** (box right, read wrong) · **RECONCILE** (read right, gate withheld) ·
**NONE**. Cases are selected by a stated rule — largest IoU gain, largest
loss, one per distinct tracking status, then evenly spaced samples — never by
appearance.

The clearest single example (`dense_B_anchors__best_delta__anchor_006585`):
the static boxes sit on the waveform at IoU 0.00 while the tracked boxes land
squarely on `85` / `100` / `23.2` (IoU 0.43 / 0.56 / 0.39) at 204 inliers,
scale 1.274. The same image shows HR read **correctly at confidence 0** — the
360p confidence ceiling of §9, visibly a downstream problem rather than a
localization one.

---

## 11. Latency

Measured in isolation (`app/eval/m5_3_performance.py`), because the figures
embedded in the accuracy harness are taken while Tesseract competes for the
same cores. Tracking is **pure added work**; no speed-up is claimed.

| | 640×360 | 1280×720 | 1920×1080 |
|---|---:|---:|---:|
| tracker init (once per connection) | 75 ms | 23 ms | 27 ms |
| **`track()` per frame** | **148 ms** | **191 ms** | **196 ms** |
| ROI transform + crop | 57 ms | 1.5 ms | ~0 ms |
| OCR, all fields | 805 ms | 757 ms | 791 ms |
| frame total, M5.2 | 805 ms | 757 ms | 792 ms |
| **frame total, M5.3** | **1010 ms** | **949 ms** | **987 ms** |

**`ROADMAP.md`'s "<50 ms/frame" tracker target is MISSED by roughly 4×.** The
end-to-end budget (≤1.5 s/frame) is met with margin, because OCR still
dominates.

Two things were done about it, both measured:

- **Detector reuse.** `cv2.ORB_create` was being constructed per frame,
  costing ~130 ms of a 196 ms total — more than the matching it fed. Building
  it once per tracker took the same work to ~62 ms at identical feature count,
  lock rate and inlier counts.
- **Working resolution swept**, at realistic live resolution:

  | max dim | features | lock rate | mean ms |
  |---:|---:|---:|---:|
  | 480 | 2000 | 74.1% | **21** |
  | 640 | 3000 | 98.1% | 139 |
  | **640** | **4000** | **100.0%** | **122** |
  | 960 | 4000 | 98.1% | 235 |

  640 halves 960's cost while slightly *improving* lock. **480 would meet the
  50 ms target but loses 26% of locks** — and under the fail-closed contract a
  lost lock withholds every vital for that frame. Buying latency with lock
  rate is the wrong trade for a system whose value is a continuous trustworthy
  reading, so the budget miss is reported rather than engineered away.

FLANN-LSH was implemented and measured as the alternative matcher: **125 ms
and 73.1% lock**, against BF-crossCheck's **62 ms and 100%**. Rejected on
evidence.

---

## 12. Live E2E — real uvicorn, real WebSocket

`m5_3_report/m5_3_e2e_script.py` — a real `uvicorn` subprocess on a scratch
SQLite DB, real HTTP, a real standalone WS client. **26/26 checks passed.**

1. Verify → 200, real OCR, HR read correctly.
2. Save → 201; `hasReferenceFrame: false`.
3. Attach reference frame → sha256 round-trips; bytes retrievable.
4. Session created; frame pushed.
5. Unmoved frame → `tracking: {enabled, locked, status: ok, inliers: 102, scale: 1.0}`.
6. **Deliberately moved frame (pan 38/26 px, zoom 1.06×, roll 2°)** → still
   locked (58 inliers), tracker recovered `scale 1.06`, and **every vital read
   the same value as before the move**: hr 75→75, spo2 98→98, etco2 38→38,
   temp 36.8→36.8, rr 14→14.
7. Untrackable frame → `low_inlier_count`, reason *"only 4 RANSAC inliers
   (need 20)"*, **all six confidences 0.0**, values **held** at last
   confirmed. No synthetic fallback, no stale-transform crop.
8. Scratch SQLite queried directly: readings persisted; reference frame row
   present with matching sha256 and dimensions.
9. `DELETE /api/calibration/active` → profile and reference frame both 404.

**A real bug this caught.** `TrackingState` and `send_loop`'s parameter had
been added but never wired into the `vitals_ws` endpoint, so `tracking` was
`null` on every envelope — invisible to unit tests that exercised
`_camera_roi_extractor` directly. Fixed, and step 5 now pins it.

Step 6 also confirms the M5.2-documented padding effect is unrelated to
tracking: HR reads 75 rather than 74 because `save_profile()` applies
`WIDTH_SAFETY_PAD_FRACTION` before persisting (M5.2's own E2E saw the same
74→75). Stable across the move, which is what M5.3 claims.

**Not covered:** a physical camera with a human clicking through a browser.
No browser-automation framework exists in this project; same scoping call as
M5.1 §14 and M5.2 §12, for the same reason. `tsc --noEmit` and `vite build`
confirm the UI compiles and builds.

---

## 13. Tests

**New:** `backend/tests/test_m5_3_layout_tracking.py` — **34 tests**:
transform recovery (identity, 4 known similarities, determinism), safety gates
(unrelated scene, featureless, noise, extreme scale/rotation, no reference
features, digit masking), transformed-ROI geometry (off-frame, overlap, area
sanity, the passing case, corner mapping), fail-closed integration (tracked
crop moves, failure withholds all, untracked path is M5.2-identical, aspect
guard preserved, observer called on success *and* failure, a raising observer
cannot break the pipeline), reference-frame persistence (round-trip, absent
case), API lifecycle (attach/fetch, wrong-dimension rejection, 404 paths), and
live wiring (tracker built when a reference exists, untracked without one,
`LAYOUT_TRACKING=off`, `CameraSource` acceptance).

**Updated, not weakened:** `tests/test_models.py::test_calibration_profile_instantiate`
— the schema drift-guard. M5.3 legitimately adds two fields, so the pinned
shape was updated and the docstring extended to explain the supersession,
exactly as M5.1 and M5.2 did for their own constants. Its guard function is
fully preserved.

| | count |
|---|---:|
| M5.2 baseline | 314 |
| **M5.3 final** (`pytest tests/ simulator/tests/ -q`) | **348 passed** |

**Frontend:** `npx tsc --noEmit` clean; `npx vite build` succeeds
(`✓ built in 53.88s`). The >500 kB chunk warning is pre-existing and unrelated.

---

## 14. Limitations

- **The tracker misses `ROADMAP.md`'s <50 ms/frame budget by ~4×** (§11). The
  configuration that meets it loses 26% of locks. End-to-end stays within the
  1.5 s criterion.
- **Dataset A's 11 confidently-wrong confirmations are not addressed** and
  cannot be by tracking (§3). They need better calibration-box drawing.
- **Only one recording contains real camera motion.** All motion evidence
  comes from a single 54-second span of one phone-camera recording of one
  GE CARESCAPE B650. Nothing here generalises to monitors or cameras in
  general.
- **Dense frames are 640×360**, well below the frozen screenshots' 2712×1220.
  Their absolute OCR numbers are not comparable to M5.2's, and the 640×360
  confidence ceiling (~51 vs a gate of 70) means most correct reads still
  cannot be confirmed. **Tracking fixes localization; it does not fix the
  confidence ceiling, and this milestone does not claim to.**
- **Dataset B's human box annotations are systematically offset** (§4),
  depressing absolute IoU for both arms equally.
- **Confirmed accuracy on Dataset B is dominated by holding**, not
  confirmation, with n≈51 and 1–3 genuine confirmations (§9). Treat those
  deltas as noise.
- **Perspective change is not corrected** — the similarity model's deliberate
  cost (§5).
- **The 23-vs-12 inlier margin is narrow** (§7), the most likely threshold to
  need revision on new footage.
- **The fail-closed-vs-static-fallback comparison is inconclusive on this
  data**, because lock rate was 100% on every scored set, so the fallback arm
  never triggered (§8).
- **`ROI_ENGINE=calibrated` (the env-var/script path) remains untracked.**
  Tracking engages only on the live WS path, from the DB's active profile.
- **No physical-camera browser E2E** (§12).

---

## 15. Exact files changed

**Backend — new**
- `app/pipeline/layout_tracker.py`
- `app/eval/m5_3_dense_extract.py`, `m5_3_dense_annotate.py`
- `app/eval/m5_3_tracking_eval.py`, `m5_3_negative_controls.py`
- `app/eval/m5_3_performance.py`, `m5_3_overlay_gallery.py`
- `app/eval/tier2_data/m5_3_report/m5_3_e2e_script.py`
- `tests/test_m5_3_layout_tracking.py`

**Backend — modified**
- `app/pipeline/calibrated_roi.py` (tracker param on `make_extractor`; static path untouched)
- `app/models/calibration.py` (+2 metadata fields)
- `app/db/models.py` (+`CalibrationReferenceFrameRow`)
- `app/db/repo.py` (+3 reference-frame functions; conversion made reference-aware)
- `app/api/calibration.py` (+PUT/GET reference-frame; existing JSON save unchanged)
- `app/ws/vitals.py` (+`TrackingState`, `LAYOUT_TRACKING`, tracker construction, additive envelope key)
- `tests/test_models.py` (1 drift-guard updated to the new shape)

**Frontend — modified**
- `src/types/calibration.ts`, `src/lib/api.ts`, `src/store/vitalsStore.ts`,
  `src/hooks/useVitalsSimulation.ts`,
  `src/features/calibration/CalibrationPage.tsx`,
  `src/features/surgery/components/CameraOverlay.tsx`

**Generated (regenerable):** `app/eval/tier2_data/dense_B/` (270 frames),
`dense_B_anchors/` (17 + GT), `m5_3_report/**`.

**Verified untouched:** `app/pipeline/ocr.py`, `app/validation/reconcile.py`,
`app/validation/rules.py`, `app/pipeline/read_frame.py`,
`app/pipeline/calibration_validate.py`, `app/pipeline/roi.py`,
`tier2_roi.py`, `field_classifier.py`, `detect.py`, `app/sources/replay.py`,
and every prior report and dataset.

---

## 16. Rollback

1. **`LAYOUT_TRACKING=off`** — one env var. The live path reverts to M5.2's
   static calibrated boxes immediately, on the next WS connection. No data
   change, no redeploy of anything else.
2. **Per-profile:** a profile with no reference frame is already untracked;
   deleting its `calibration_reference_frames` row disables tracking for it
   while leaving the calibration intact.
3. **Full revert:** remove the `tracker=` argument at
   `app/ws/vitals.py:_camera_roi_extractor`. `make_extractor(profile)` with no
   tracker is byte-identical M5.2 behaviour — pinned by
   `test_untracked_extractor_is_unchanged_m5_2_behaviour`.
4. The new table and the two profile fields are additive and nullable; leaving
   them in place after a rollback is harmless.

---

## 17. GO / NO-GO

| # | Criterion | Result |
|---|---|---|
| 1 | Tracking works on real sequential monitor frames | ✅ 97.0% lock over 269 chronological frames spanning three real framings |
| 2 | Localization improves materially on moving frames | ✅ mean IoU +0.125 / +0.145 / +0.266; recall@0.3 +27 to +46 pp |
| 3 | HR/RR localization improves specifically | ✅ HR +0.146 / +0.178 / +0.384; RR +0.194 (small n) |
| 4 | OCR does not materially regress | ✅ +17.6 / +21.6 / +8.2 pp on B; exactly 0.000 change on A |
| 5 | Confirmed accuracy improves or stays safely stable | ⚠️ +9.8 and +8.2 pp on frozen B; **−3.9 pp** on dense anchors, root-caused to hold-coincidence in a regime where reads almost never clear the gate (§9) |
| 6 | **Confidently-wrong confirmations do NOT increase** | ✅ **0→0 on every Dataset B arm; 11→11 on A (unchanged, and not M5.3's to fix — §3)** |
| 7 | Tracking failures fail closed | ✅ all fields withheld, values held, reason reported — unit-tested and confirmed live |
| 8 | Sanity checks prevent pathological crops | ✅ 32/32 negative controls, including the brief's 4.7×/81° examples |
| 9 | Live E2E works | ✅ 26/26 on real uvicorn + real WS, including a deliberate camera move |
| 10 | Full test suite passes | ✅ 348 passed |
| 11 | Frontend builds cleanly | ✅ `tsc` clean, `vite build` succeeds |
| 12 | Runtime cost acceptable | ⚠️ **misses the <50 ms tracker target (~150–200 ms)**; end-to-end ~1.0 s, inside the 1.5 s criterion |

**Verdict: GO for M5.3 as scoped.**

Ten of twelve criteria are fully met, and — the criterion that actually
governs this project's safety claim — **confidently-wrong confirmations did
not increase anywhere, tracking never broke a field the static path read
correctly, and it never withheld a field the static path got right.** The two
qualified criteria are reported rather than argued around: criterion 12 is a
measured budget miss with an explicit, evidence-backed reason for not buying
the latency back with lock rate; criterion 5 is a metric with almost no signal
on this dataset, root-caused rather than explained away.

**What this milestone does NOT claim.** It does not improve Dataset A (by
design — there is no motion to correct). It does not fix Dataset B's
confidence ceiling, which is now the binding constraint on confirmed accuracy
and is a genuinely different problem from localization. And one 54-second span
of one recording of one monitor is the entire real-motion evidence base;
`ROADMAP.md` M5.4/M5.5 remain the right next steps, with the confidence
ceiling — not localization — as the thing to attack next.
