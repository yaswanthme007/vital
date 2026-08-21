# M5.6 — Final Production Promotion & Demo Readiness

**Status:** complete, 2026-08-20. **The final engineering milestone.** Scope:
promote the M5.5-validated configuration, freeze it, regress it, and establish
whether the product can actually be demonstrated. No accuracy tuning, no new
ML/OCR/tracking work. Companion documents: [`ROADMAP.md`](ROADMAP.md) ·
[`ARCHITECTURE.md`](ARCHITECTURE.md) · [`EVIDENCE.md`](EVIDENCE.md) ·
[`M5_5_FINAL_VALIDATION_REPORT.md`](M5_5_FINAL_VALIDATION_REPORT.md) (the
milestone whose GO gates this one) · machine-readable config:
[`M5_6_FROZEN_CONFIG.json`](M5_6_FROZEN_CONFIG.json) · operator runbook:
[`../backend/CAMERA_DEMO.md`](../backend/CAMERA_DEMO.md).

---

## 1. Executive summary

**M5.6 = GO for the promotion. The product is demo-ready, with one new
measured safety limitation that changes the demo script.**

Three things happened in this milestone.

**First, the promotion.** `ROADMAP.md` §M5.6 instructed "flip the `ROI_ENGINE`
default to `calibrated`". Phase 0 found that instruction **unsafe as written**
and did not carry it out — `ROI_ENGINE=calibrated` *raises* unless
`CALIBRATION_PROFILE_PATH` names a profile JSON on disk, which production never
sets (§3). What was promoted instead is the *behaviour* the roadmap was
reaching for: `POST /api/pipeline/read-frame` now prefers the database's active
`CalibrationProfile`, exactly as the live camera path already did. The camera
path itself was not touched, and §8 proves it did not move.

**Second, the regression.** 414 backend tests pass (397 baseline + 17 new, none
weakened or deleted), `tsc` clean, `vite build` succeeds, and the M5.5
evaluation re-run reproduces **bit-identically** — zero differences in any
localization, OCR, confirmation, confidently-wrong, or tracking-status value
across all five dataset arms (§7).

**Third, and this is the finding that matters most: the first camera-motion
evidence this project has ever gathered at a resolution where OCR confidence
clears the confirmation gate shows the pipeline confirming wrong values.**
Under a controlled camera nudge the tracker holds lock 101/101 frames, and the
OCR stage — reading a correctly-located but resampled crop — misreads the digit
`8` as `3` (`38`→`33`, `98`→`93`) at 71-90% confidence, above the 70% gate.
**38 confidently-wrong confirmations across 101 frames.** This is *not* a
regression introduced by M5.6 (the camera path is byte-identical to M5.5) and
*not* a tracking failure. It is a pre-existing property that M5.5's evidence
base could not have surfaced, because its only real-motion dataset is 640×360
footage whose confidence never reaches the gate at all — the gate was being
protected by poor image quality rather than by correctness (§13).

The consequence is concrete and narrow: **"nudge the camera to show off
tracking" must be removed from the demo script.** It is currently step 4 of
`ROADMAP.md`'s own demo script and was described there as "the money shot". The
runbook now says so explicitly.

---

## 2. M5.5 starting configuration

Taken from `M5_5_FINAL_VALIDATION_REPORT.md` §2 and re-verified against the
running process, not transcribed:

| | Value |
|---|---|
| `ROI_ENGINE` | unset → `tesseract` (governs non-camera paths only) |
| `LAYOUT_TRACKING` | unset → `auto` |
| `TEMPORAL_CORROBORATION` | unset → `off` |
| `OCR_ENGINE` | unset → `tesseract` |
| OCR config | `--psm 8` / `--psm 10` for {spo2, rr} / `--psm 6` NIBP, no whitelist |
| Tesseract | 5.4.0.20240606 |
| Calibration | `WIDTH_SAFETY_PAD_FRACTION=0.20`, `MAX_ASPECT_RATIO_DRIFT=0.20` |
| Tracking | `MIN_INLIERS=20`, `ORB_FEATURES=4000`, `TRACK_MAX_DIM=640`, … |
| Confidence gate | `CONFIDENCE_MEDIUM_MIN=70`, `CONFIDENCE_HIGH_MIN=90` |
| Models | `field_classifier.onnx`, `digit_cnn.onnx` present on disk, **never loaded** |
| Python | 3.13.9 |
| Backend tests | 397 passing |

Every one of these was re-confirmed at the start of M5.6 by running the suite
(397 passed, 91s) before a single line was changed.

---

## 3. M5.6 changes — and the instruction that was not carried out

### 3.1 Why the ROI_ENGINE flip was rejected

`app/pipeline/read_frame.py::_build_roi_extractor_from_env` resolves
`ROI_ENGINE=calibrated` by loading **one fixed profile from a JSON file named
by `CALIBRATION_PROFILE_PATH`**, and raises `ValueError` when that variable is
unset. Production never sets it. Flipping the default would therefore have
broken, on the next restart:

- `POST /api/pipeline/read-frame` → 500 (this is the OCR Debug page's backend)
- `ReplaySource(mode="pipeline")` → raises on construction
- **the live camera WebSocket, for any session with no calibration profile
  yet** — `_camera_roi_extractor` returns `None` in that case, `read_frame()`
  falls through to the env default, and the `ValueError` propagates out of
  `asyncio.to_thread` and kills `send_loop`

Meanwhile the flip would have achieved nothing for the path it was aimed at:
`app/ws/vitals.py::_camera_roi_extractor` binds the DB's active
`CalibrationProfile` and a `LayoutTracker` directly and **never reads
`ROI_ENGINE` at all** when a profile exists. The instruction was simultaneously
dangerous and unnecessary.

This finding is pinned in executable form so nobody re-proposes it without
rediscovering why:
`tests/test_m5_6_promotion.py::test_flipping_roi_engine_default_to_calibrated_would_break_uncalibrated_paths`.

### 3.2 What was promoted instead

The real inconsistency was between two server paths reading the same frame:

| Path | Before M5.6 | After M5.6 |
|---|---|---|
| Live camera WS | active `CalibrationProfile` + `LayoutTracker` | **unchanged** |
| `POST /api/pipeline/read-frame` | Tier-1 colour ROI (M5.1 measured 0/17 fields located on real photographed monitors) | active `CalibrationProfile`, falling back to the old behaviour when none exists |

`POST /api/pipeline/read-frame` backs the OCR Debug page and is the only other
server-side frame reader. Before M5.6 it was structurally incapable of agreeing
with the live path on the same monitor. It now takes the same profile, reusing
`repo.get_active_calibration_profile()` and
`calibrated_roi.make_extractor()` — no new module, no new helper.

Three deliberate limits:

- **No tracker.** `LayoutTracker` is built once per WebSocket connection and
  re-anchors across a stream; this endpoint is single-shot and owns no such
  lifetime. M5.6 promotes M5.2's calibrated *localization* here, not M5.3's
  per-frame re-anchoring. Asserted by test.
- **Fail-open.** Any failure — no profile, DB error, unbuildable extractor —
  returns the pre-M5.6 behaviour. Same posture as `_camera_roi_extractor`'s own
  `except Exception`. Three tests cover the three failure modes.
- **It says which path ran.** The response gained `roiSource: "calibrated" |
  "default"` so no reader has to guess which localization produced a number.

### 3.3 Everything explicitly NOT changed

`read_frame.py`, `ws/vitals.py`, `ocr.py` (every PSM/whitelist constant),
`calibrated_roi.py`, `layout_tracker.py`, `reconcile.py`, `rules.py`,
`temporal.py`, `crop_integrity.py`, `alerts/rules.py`, `db/repo.py`. No
threshold, no gate, no model, no alert rule, no persistence semantic.

---

## 4. Exact final configuration

Machine-readable and regenerable:
[`M5_6_FROZEN_CONFIG.json`](M5_6_FROZEN_CONFIG.json), produced by
`backend/app/config_snapshot.py` and servable live from
**`GET /api/config/snapshot`** — so the configuration can be *asked of the
running process* rather than trusted from a document. Its flag defaults are
asserted against the code that actually reads them, so a drift between
"documented default" and "real default" fails a test rather than surprising
someone on stage.

Identical to §2 in every value. `TEMPORAL_CORROBORATION` remains **off**;
the mechanism, its tests and its eval harness all remain in the tree as an
experimental feature, verified reachable via `TEMPORAL_CORROBORATION=on`.

---

## 5. Production files changed

| File | Change |
|---|---|
| `backend/app/api/pipeline.py` | prefer the active calibration profile; report `roiSource`; fail open |
| `backend/app/config_snapshot.py` | **new** — the frozen configuration, read from live constants |
| `backend/app/main.py` | `GET /api/config/snapshot` |
| `src/features/calibration/CalibrationPage.tsx` | "Start a Case with This Profile" CTA on the Complete screen; corrected the copy that promised more than holds across a reload (§11) |
| `index.html` | inline data-URI favicon (removed the only console error in the browser audit) |
| `backend/DEMO.md` | points at the camera runbook; corrected the "fully offline" claim (backend yes, frontend loads Google Fonts) |
| `backend/CAMERA_DEMO.md` | **new** — the operator runbook for the actual demo |

## 6. Eval / test files changed

| File | Purpose |
|---|---|
| `backend/tests/test_m5_6_promotion.py` | **new**, 17 tests — promotion behaviour, the rejected flip, flag defaults, snapshot integrity |
| `backend/app/eval/tier2_data/m5_6_report/m5_6_e2e_script.py` | **new** — real-process E2E (§9) |
| `backend/app/eval/m5_6_motion_stress.py` | **new** — the camera-motion safety measurement (§13) |
| `scripts/cdp.mjs`, `scripts/m5_6_browser_e2e.mjs` | **new** — zero-dependency Chrome DevTools Protocol driver + browser camera E2E (§10) |
| `scripts/m5_6_ui_audit.mjs` | **new** — route sweep + Demo Mode isolation (§11, §12) |
| `scripts/make_fake_camera_video.py` | **new** — builds the Y4M videos Chrome's fake capture device reads |
| `backend/app/eval/tier2_data/m5_6_report/m5_5_baseline/*.json` | M5.5's eval artifacts, copied before the re-run so §7's comparison is against preserved evidence rather than memory |

**No existing test was modified, weakened, skipped or deleted.** No historical
report was edited.

---

## 7. Regression results

| Check | Result |
|---|---|
| `pytest tests/ simulator/tests/ -q` | **414 passed, 0 failed** (397 baseline + 17 new), 64.9s |
| `npx tsc --noEmit` | **clean** |
| `npx vite build` | **succeeds**, 8.4s |
| Bundle | 667.13 kB JS / 203.67 kB gzip (was 666.60 / 203.45 — **+0.53 kB**, the calibration CTA) |
| Build warnings | one, pre-existing: single chunk >500 kB |

---

## 8. M5.5 vs M5.6 comparison

The committed, unmodified M5.5 eval scripts were re-run against the promoted
configuration. M5.5's own output artifacts were **copied aside first**, so this
is a diff against preserved files.

**Result: zero non-latency differences, across all five arms.** A leaf-by-leaf
diff of every regenerated JSON against its M5.5 baseline found differences only
in wall-clock timing fields.

| Arm (tracked) | M5.5 | M5.6 | Δ |
|---|---:|---:|---:|
| frozen_A mean IoU | 0.710 | 0.710 | 0 |
| frozen_A OCR accuracy | 85.8% | 85.8% | 0 |
| frozen_A confirmed accuracy | 83.11% | 83.11% | 0 |
| frozen_A **confidently-wrong** | **11** | **11** | **0** |
| dense_B_anchors mean IoU | 0.633 | 0.633 | 0 |
| dense_B_anchors OCR accuracy | 52.9% | 52.9% | 0 |
| dense_B_anchors confirmed accuracy | 11.76% | 11.76% | 0 |
| dense_B lock rate | 97.0% (261/269) | 97.0% (261/269) | 0 |

Confirmation arm (`m5_4_1_crop_integrity_eval.py`), shipped config:

| Dataset | confirmed accuracy | confidently-wrong |
|---|---:|---:|
| frozen_A | 83.11% | 11 |
| frozen_B[sample_0001] | 21.57% | 0 |
| frozen_B[sample_0011] | 20.41% | 0 |
| dense_B_anchors | 11.76% | 0 |

Every figure matches `M5_5_FINAL_VALIDATION_REPORT.md` §8 exactly. **The
promotion caused no source/path regression and no accuracy movement.**

---

## 9. Real-process E2E

`app/eval/tier2_data/m5_6_report/m5_6_e2e_script.py` — a real `uvicorn`
subprocess, real HTTP, a real standalone WebSocket client, a real scratch
SQLite file. Never `TestClient`. **42 checks, 42 passed.**

Design note worth recording: an earlier version opened a fresh WebSocket per
step and could not observe a genuine hold, because `reconcile()`'s confirmed
state is per-connection and re-seeds from `DEFAULT_BASELINE` on every new one.
The script now drives four frames through **one** connection, the way a browser
does.

| Step | Result |
|---|---|
| `GET /api/config/snapshot` reports temporal OFF, tracking auto, no ONNX loaded | ✅ |
| read-frame reports `roiSource=default` with no profile (real Dataset B photo) | ✅ |
| Verify → Save → attach reference frame → tracking enabled | ✅ |
| read-frame reports `roiSource=calibrated` once a profile exists | ✅ **the promotion, on the real transport** |
| Unmoved frame → tracking locked, SpO₂ 98 @ conf 93 | ✅ |
| Moved frame (pan+zoom+roll) → still locked, every vital unchanged | ✅ |
| Critical frame (SpO₂ 88) → confirmed, `SpO₂ CRITICALLY LOW` on the wire | ✅ |
| **Safety: truncated HR never confirmed as `14`** | ✅ |
| Noise frame → lock loss, all confidences 0.0, values held, no alert invented | ✅ |
| Readings + critical alert persisted to SQLite | ✅ |
| Session end → persistence stops (4 → 4 rows) | ✅ |
| Camera cleanup → a later connection is served no stale frame | ✅ |
| Session isolation — session B fires its **own** alert inside A's 30s throttle window | ✅ |
| Demo Mode isolation — synthetic connection ignores queued camera frames | ✅ |

The session-isolation test deserves a note: comparing *values* between sessions
proves nothing when both read the same monitor. The discriminator used is the
**alert throttle** — session B firing its own critical alert well inside
session A's 30-second suppression window is positive evidence of genuinely
fresh per-connection state.

---

## 10. Camera E2E — in a real browser

`scripts/m5_6_browser_e2e.mjs`, driving the user's installed Chrome over the
DevTools Protocol with **zero new dependencies** (Node 24's built-in
`WebSocket`; no Playwright, no Puppeteer, no browser download).

```
REAL Chrome → REAL getUserMedia → REAL <video>/<canvas> capture
→ REAL JPEG encode → REAL POST /api/pipeline/push-frame
→ REAL CameraSource + calibrated ROI + tracking + OCR
→ REAL reconcile/alerts/persistence → REAL WebSocket → REAL UI
```

**What is simulated, stated plainly: the camera sensor.** Chrome runs with
`--use-fake-device-for-media-stream --use-file-for-fake-video-capture=<Y4M>`.
This is **not** a physical webcam pointed at a physical monitor. M5.5's
limitation #6 is *narrowed*, not closed — see §15.

| Arm | Result |
|---|---|
| normal (steady monitor) | **24/24** |
| critical (SpO₂ 88) | **25/25** |

Covered: permission accepted · stream connected at the source resolution ·
calibration receives live frames · six ROIs drawn by **real mouse drags**
through `Input.dispatchMouseEvent` · Verify runs real OCR · **Save blocked
before verification and still blocked with fields unconfirmed** · Save enabled
only after confirmation · profile + reference frame persisted · session enters
camera mode · `Capture ON` · live camera-derived values in the UI · critical
alert surfaced in the UI · readings persisted · session end leaves Live
Monitor · zero uncaught page errors.

**A flaw in this test worth recording.** The first passing version asserted
"SpO₂ 98 appears on screen". That assertion was worthless: `DEFAULT_BASELINE`
seeds a new connection with `spo2 98, etco2 38, temp 36.8, rr 14` — four values
this simulator monitor also displays. The assertion now uses values that
**cannot** come from the seed (NIBP diastolic 80 vs seed 78; the critical arm's
SpO₂ 88 vs seed 98).

Two real product findings came out of building this, both fixed (§11).

---

## 11. Calibration UX and final UI audit

`scripts/m5_6_ui_audit.mjs` swept every route in a real browser: **17/17
passed, 0 findings.** All six routes render with content and controls, no
uncaught errors, no horizontal overflow at 1440px or 1280px, empty states
explanatory.

The calibration flow itself (Connect → Draw 6 → Verify → Save → Start) was
walked 6+ times end-to-end. Controls all live; validation messages
understandable; the live crop preview updates as boxes are drawn; **Save cannot
bypass Verify** (asserted twice per run); editing a box correctly invalidates
that field's prior verification; no stuck loading states observed.

**Three issues found and fixed:**

1. **Calibration Complete was a dead end.** It reported success and offered no
   route onward; the only way to start a case was TopNav → Live Monitor →
   redirect to `/start`, which is not discoverable. Added a **"Start a Case
   with This Profile"** primary CTA.

2. **A promise that a page reload silently breaks.** The Complete screen said
   the profile would drive Live Monitor "until you recalibrate". The profile
   does survive (it is a database row) but `cameraMode` lives in a
   non-persisted zustand store, so a **reloaded tab streams synthetic vitals
   against a live calibration profile**. The browser E2E hit exactly this — it
   navigated with a full page load and the case silently ran synthetic. The UI
   is *honest* when it happens (CameraOverlay reads "Synthetic vitals — no
   camera/OCR active this session", and the header reads `Synthetic`), so this
   is a misleading *promise*, not a misleading *display*. Copy corrected, and
   the CTA now keeps the operator in-app. **The underlying reload behaviour is
   unchanged and is a documented limitation** (§15) — fixing it means
   persisting session state, which is a behaviour change this freeze milestone
   declined to make.

3. **Missing favicon** — the only console error in the whole audit, and a
   blank demo tab icon. Inline data-URI favicon added.

**Two observations recorded but deliberately NOT changed:**

- **No End-case control on Live Monitor.** `SurgeryHeader` offers Pause/Resume
  and Review; End lives in TopNav, which does not render on the full-screen
  `/surgery` route. The real path is Review → End. It works and is reachable;
  moving it is a layout change with no safety content.
- **The UI's confidence colour thresholds (90/75) differ from the backend's
  confirmation gate (90/70).** A value confirmed at 72% shows a red bar. This
  is conservative rather than misleading, and aligning it would mean touching
  a threshold during a freeze.

---

## 12. Demo Mode isolation

Verified in the browser, after the promotion, by instrumenting `window.fetch`
and `window.WebSocket` before activating a scenario:

| Check | Result |
|---|---|
| push-frame calls during Demo Mode | **0** |
| camera WebSockets opened during Demo Mode | **0** |
| tracking envelope present | none (camera path never engaged) |
| visibly labelled as simulated | yes |
| still works after the promotion | yes |

Backed in code by `SurgeryPage.tsx` (`cameraStreamingEnabled` requires
`!demoActive`) and `useVitalsSimulation.ts` (returns before opening any socket
when demo is active), and independently by the real-process E2E step 15: a
synthetic-source connection left a queued camera frame completely untouched,
proven by a subsequent camera connection still finding it.

---

## 13. Safety audit — including the new finding

### 13.1 The camera-motion finding

`app/eval/m5_6_motion_stress.py` replays a controlled camera nudge (ramp to
45px pan, 1.10× zoom, 6° roll over 100 frames) through the exact production
calibrated+tracked path.

```
vital     frames  raw misreads   CONFIDENTLY WRONG  mean conf
hr           101            11                   3       66.8
spo2         101            13                  12       89.8
etco2        101            56                  23       70.2
rr           101             0                   0       95.7
temp         101             1                   0       44.3

tracking lock rate: 101/101 (100.0%)
TOTAL confidently-wrong confirmations: 38
```

**The tracker is not at fault — it held lock on every single frame.** The
failure is OCR reading a correctly-located but resampled crop, and the
confidence gate believing it. The pattern is a single characterisable
confusion: **the digit `8` read as `3`** — `38`→`33`, `98`→`93`, plus HR
truncating `74`→`7` at the largest displacements.

Onset by motion magnitude:

| Vital | First confidently-wrong at | Displacement there |
|---|---|---|
| **etco2** | frame 15 | **~7px pan, 1.015×, 0.9°** |
| spo2 | frame 62 | ~28px pan, 1.06×, 3.7° |
| hr | frame 89 | ~40px pan, 1.09×, 5.3° |

EtCO₂ fails at a camera movement small enough to be an accidental bump.

**Corroboration and its limits.** The browser nudge arm independently recorded
`spo2 = 93.0 at confidence 86` in SQLite while the monitor displayed 98 — same
failure, through a real video pipeline and a real JPEG encode. That is genuine
corroboration of the *class*, but not independent evidence: the browser was
shown the same warped frames. This has **not** been observed on real camera
footage, because the only real-motion dataset (dense Dataset B, 640×360) never
clears the confidence gate at all.

**Why M5.5 could not have caught this.** M5.5's own §18.4 records the "640×360
confidence ceiling (~51 vs a 70 gate)" as the dominant constraint on Dataset B
accuracy. That ceiling was also, unrecognised, the thing preventing motion
misreads from being *confirmed*. At 960×560, confidence rises above the gate
and the same misreads get through. **The gate was being protected by poor image
quality, not by correctness.**

**Attribution.** Not caused by M5.6: the camera path is byte-identical to M5.5
(§8 proves the whole evaluation reproduces bit-identically), and this milestone
changed no OCR, tracking, confidence or reconcile code.

### 13.2 The thirteen invariants

| # | Invariant | Result | Evidence |
|---|---|---|---|
| 1 | No synthetic values in camera mode | **⚠ partial — see below** | §13.3 |
| 2 | No camera values in Demo Mode | ✅ | §12, E2E step 15 |
| 3 | Tracking failure fails closed | ✅ | E2E step 10 — confidences to 0.0, values held |
| 4 | Invalid ROI fails closed | ✅ | `calibration_validate` + `check_transformed_rois`, unchanged, 414 tests |
| 5 | Crop-integrity failure cannot promote a value | ✅ | crop-integrity eval reproduces exactly |
| 6 | Temporal corroboration remains OFF | ✅ | snapshot endpoint, tests, E2E step 1 |
| 7 | Confidence gate intact | ✅ | 70/90 in snapshot; observed withholding HR at 67% |
| 8 | Range checks intact | ✅ | `rules.py` 0 lines changed since M5.2 |
| 9 | Critical alerts still fire | ✅ | E2E step 9 + browser critical arm + persisted to SQLite |
| 10 | Session state cannot leak | ✅ | E2E step 14, alert-throttle discriminator |
| 11 | Camera capture stops after session end | ✅ | E2E step 13 (channel cleared), browser step 9 |
| 12 | Persistence correct | ✅ | E2E steps 11-12 |
| 13 | WebSocket state reflects backend state | ✅ | tracking envelope matches tracker status every arm |

### 13.3 Invariant 1, precisely

No synthetic *stream* is ever mixed into camera mode — that is proven, twice.
But `reconcile()` seeds every new connection's confirmed state from a
hardcoded `DEFAULT_BASELINE` (`hr 75, spo2 98, nibp 120/78/92, etco2 38, temp
36.8, rr 14`), and any field that never clears the 70% gate keeps displaying
that seed.

Measured on the demo monitor: HR is read **correctly as 74 at confidence 67**,
withheld by the gate, and the UI displays the seeded **75**.

The system does not conceal this. On that same tick it emits **flagged entries
for HR (67%), EtCO₂ (82%) and Temp (60%)**, each carrying its real confidence,
and the vital card shows a red confidence bar. But the number on screen is a
default, not a measurement, and nothing on the card says "held".

This is the designed fail-safe contract working (refuse, hold, flag) with a
presentational gap at the very start of a case, before anything has been
confirmed even once. **It is pre-existing, not introduced here.** The smallest
corrective change would be to display `--` or an explicit HELD marker until a
field has been confirmed at least once — a change to displayed clinical values,
which is exactly what a freeze milestone should not do without its own
validation. Recommended as follow-up work, not done here.

It also makes a strong demo moment rather than a weak one, and the runbook says
so: *"it read 74, but only at 67% confidence, under our 70% gate — so it
refuses to confirm it, holds the last trusted value, and files it for review."*

---

## 14. Performance

Measured on an idle machine (`app/eval/m5_3_performance.py`, unchanged):

| Stage | p50 | p95 | Budget |
|---|---:|---:|---|
| Tracker init (once per connection) | 14-64 ms | — | — |
| `track()` per frame, 640×360 | 59 ms | 63 ms | 50 ms — **missed** |
| `track()` per frame, 1280×720 | 56 ms | 61 ms | 50 ms — **missed** |
| `track()` per frame, 1920×1080 | 57 ms | 64 ms | 50 ms — **missed** |
| OCR, all fields | 575-605 ms | — | dominant cost |
| **Frame total, tracked** | **633-661 ms** | — | 1500 ms — **OK** |

API path, against the live backend:

| Endpoint | p50 | p95 |
|---|---:|---:|
| `POST /api/calibration/verify` | 1280 ms | 1364 ms |
| `POST /api/calibration` (save) | 16.5 ms | 16.5 ms |
| `POST /api/pipeline/push-frame` | 3.0 ms | 3.4 ms |
| `POST /api/pipeline/read-frame` | 1376 ms | 1459 ms |

**On the M5.5 latency discrepancy, honestly.** M5.5 §15 reports `track()` at
122-196 ms; this milestone measures 55-59 ms mean. **No tracking code
changed** — §8 proves the evaluation reproduces bit-identically. The difference
is measurement environment: an intermediate run of the same eval *during* this
milestone, with builds and tests running concurrently, measured 213 ms mean on
the identical code. **This is not an improvement and is not claimed as one.**
The defensible statement is that tracker latency is load-sensitive, spanning
~55 ms idle to ~215 ms under concurrent load, and that the original <50 ms
budget is missed in every case. End-to-end stays inside 1.5 s/frame because OCR
dominates.

**Promotion latency cost:** `read-frame` p50 1381 ms with a calibrated profile
vs 1558 ms without (n=7, p95 variance 1847-2294 ms). No measurable cost; the
apparent gain is within noise at this sample size and is **not** claimed.

**Frontend build:** 667.13 kB JS / 203.67 kB gzip, +0.53 kB vs pre-M5.6. One
pre-existing chunk-size warning.

---

## 15. Known limitations

Carried forward from M5.5 §18 unchanged, plus what this milestone added:

1. **Dataset A's 11 confidently-wrong confirmations remain unaddressed** —
   too-narrow calibration boxes clearing the gate directly. The fix is
   calibration UX (a truncation warning at draw time), still not built.
2. **NEW: under camera motion the pipeline confirms wrong values** — 38
   confidently-wrong across a 101-frame nudge, `8`→`3` at 71-90% confidence,
   EtCO₂ failing at ~7px of movement (§13.1). Measured on synthetic motion over
   a simulator render; corroborated through the real browser path on the same
   source; never observed on real footage, which is confidence-capped.
3. **NEW: `DEFAULT_BASELINE` values display as vitals** until a field is first
   confirmed (§13.3). Flagged in the data, unmarked on the card.
4. **NEW: a browser reload silently drops camera mode** while leaving the
   calibration profile active. The UI reports the resulting synthetic state
   honestly; the operator must notice. Runbook says do not reload.
5. **All real camera-motion evidence is still one 54-second span of one
   recording of one GE CARESCAPE B650.** Nothing generalises to monitors,
   cameras or lighting in general.
6. **Dataset B's ~46% oracle-crop OCR ceiling** reflects a
   phone-recording-of-a-YouTube-video capture chain.
7. **The 640×360 confidence ceiling remains unaddressed** — and §13.1 shows it
   was masking a second problem.
8. **`TEMPORAL_CORROBORATION` provides zero net measured benefit** and stays
   off.
9. **Still no physical-camera, human-operated browser E2E.** M5.6 narrows this
   — the browser, permission flow, capture, upload and UI are all real — but
   the sensor is a Y4M file. **No physical webcam has ever been pointed at a
   physical monitor in this project.**
10. **The 20% aspect-drift threshold and 20% width pad are
    evidence-*informed*, not evidence-*tuned*.**
11. **The tracker misses the <50 ms budget** at ~56-59 ms idle, worse under
    load.
12. **The frontend fetches two display fonts from Google Fonts** — the app
    works offline but falls back to system fonts. The backend genuinely makes
    no outbound calls.

---

## 16. Remaining risks

1. **A judge nudging the camera during the demo can produce a visibly wrong
   number.** Highest-probability demo risk. Mitigation is procedural (§ the
   runbook's "What NOT to do on stage"), not technical.
2. **A second real monitor/camera could surface a truncation shape
   `has_residual_content` does not catch.**
3. **An operator drawing a box tight to today's digits** reintroduces the
   truncation class. The Verify step is the only mitigation and depends on the
   operator noticing.
4. **The ONNX models remain switchable** via `OCR_ENGINE=onnx` /
   `ROI_ENGINE=tier2`, which would silently reintroduce the retired FieldCNN.
   The snapshot now surfaces both their presence and their loaded state.
5. **Non-numeric monitor states** (dashes, `APN`, alarm banners over a field)
   remain untested beyond `EVIDENCE.md` §9.
6. **A mid-demo browser reload** drops camera mode (§15.4).

---

## 17. Rollback procedure

The promotion is one function and one call site.

- **Revert the promotion:** in `backend/app/api/pipeline.py`, replace
  `read_frame(img, roi_extractor=roi_extractor)` with `read_frame(img)` and
  drop `roiSource` from the response. Nothing else depends on it. Or simply
  `DELETE /api/calibration/active` — with no active profile the endpoint is
  already byte-for-byte its pre-M5.6 self.
- **Revert the config snapshot:** delete `app/config_snapshot.py` and the route
  in `main.py`. Nothing reads them; no pipeline behaviour changes.
- **Revert the UI changes:** `CalibrationPage.tsx` (CTA + copy) and
  `index.html` (favicon) are presentational and independently revertible.
- **`TEMPORAL_CORROBORATION`** requires no action to stay off; it is already
  the default.

No database migration, no configuration change, no data rewrite.

---

## 18. Final acceptance checklist

| Gate | Result |
|---|---|
| M5.5 configuration successfully promoted | ✅ §3 |
| ROI_ENGINE default behaves as intended | ✅ unchanged, and the flip's danger pinned by test §3.1 |
| Camera path remains unchanged/validated | ✅ §8 bit-identical |
| TEMPORAL_CORROBORATION remains OFF | ✅ §4, verified in the running process |
| OCR configuration unchanged | ✅ §3.3 |
| Tracking configuration unchanged | ✅ §3.3 |
| Calibration remains functional | ✅ §10, §11 |
| Crop-integrity protection remains functional | ✅ §8 |
| Full backend tests pass | ✅ 414 |
| TypeScript passes | ✅ |
| Vite production build succeeds | ✅ |
| M5.5 regression shows no safety regression | ✅ zero delta |
| Real uvicorn E2E passes | ✅ 42/42 |
| Real WebSocket E2E passes | ✅ |
| SQLite persistence passes | ✅ |
| Critical alert passes | ✅ wire + UI + DB |
| Session cleanup passes | ✅ |
| Camera cleanup passes | ✅ |
| Demo Mode isolation passes | ✅ 0 frames, 0 sockets |
| Calibration UX is demo-ready | ✅ 3 issues found and fixed |
| **No new confidently-wrong confirmation introduced** | ✅ **introduced: none.** Newly *measured* on a new evidence arm: yes — §13.1 |
| No unsafe fallback introduced | ✅ promotion fails open to prior behaviour |
| Final demo can be completed reliably | ✅ 25/25, 17.4s machine-paced, repeated |
| Final configuration is documented | ✅ §4 + machine-readable + live endpoint |

---

## 19. Final GO / NO-GO

**M5.6 = GO.**

Every promotion gate passes. The promoted change is minimal, fails open,
touches no threshold, and provably does not move the validated camera path. The
regression is clean at every level available: unit, offline evaluation,
real-process, and — for the first time in this project — real browser.

**The GO is on the promotion, and it is qualified by §13.1.** The
camera-motion finding is not a regression and does not undo M5.5's verdict, but
it is a real, reproducible condition under which the shipped pipeline confirms
wrong values, and it was found by this milestone rather than inherited. Calling
the product "final" without stating it would be dishonest. The correct
engineering response is the one taken: measure it, characterise it, document
it, change the demo procedure — **not** patch the pipeline during a freeze on a
single evidence arm.

**Smallest corrective work item, if the owner chooses to do one** (this report
does not create it, does not name it a milestone, and does not start it):
suppress confirmation while the tracker reports significant inter-frame motion.
The signal already exists — `TrackingResult` carries scale, rotation and
translation per frame, and `reconcile()` already accepts per-field suppression
inputs. That is a withhold-only change, in the spirit of every safety mechanism
in this codebase.

**The defensible claim, verbatim:**

> *VITAL is validated on the available real-world monitor video datasets and
> the real application pipeline.*

It is a technical demonstration and prototype. It is **not** clinically
validated, **not** medically certified, **not** approved for patient care, and
**not** established as diagnostically accurate.

**Engineering work is complete. Next activity is demo, presentation and
submission preparation.**
