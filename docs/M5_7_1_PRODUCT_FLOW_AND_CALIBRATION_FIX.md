# M5.7.1 — Product Flow + Calibration Reliability Fix

Two independent, real bugs, both root-caused by reading the actual code (not
assuming the M5.6/M5.7 reports already covered them — they didn't):

1. **Routing**: a case could reach Active Operation without ever completing
   camera calibration.
2. **Calibration OCR**: a correctly-drawn box could intermittently read `-`
   for the same physical camera/monitor setup, no confidence-threshold
   explanation.

A third issue was found *while fixing #1*, not in the original brief: once
calibration became a mandatory step that happens **after** session creation,
the app-root-mounted synthetic vitals feed started persisting fabricated
readings into the session's real observation history during the setup
window. Documented and fixed below (see "Setup-phase pollution").

---

## 1. Routing root cause

`StartPage.handleStart()` created the session then unconditionally
`navigate('/operation')` — [src/features/start/StartPage.tsx](../src/features/start/StartPage.tsx).
`OperationPage`'s only guard was `if (!activeSession) navigate('/start')` —
it never checked `sessionStore.cameraMode` (the existing, already-correct
signal for "this case's calibration was saved and activated"), so a session
with no calibration rendered Operation fine, on the synthetic fallback feed,
with no error and no redirect.

**Fix**: `StartPage` now routes to `/calibration`. `OperationPage` now also
redirects to `/calibration` (with an explanatory toast) when `activeSession`
exists but `cameraMode` is false. No new session states, no backend changes
— this stays a frontend-only invariant, matching the architecture of the
existing (and already-correct) "no session → `/start`" guard.

**Reusable vs. mandatory calibration**: `CalibrationProfile` is deliberately
not tied to a session in the data model (same camera/monitor rig outlives
any one case). Decision: geometry is reusable (CalibrationPage pre-fills
boxes from the currently-active profile, if one exists), but Save/Verify
stays mandatory every case — Save always re-runs live OCR and requires every
field re-confirmed before (re)activating a profile. `/calibration` is always
visited after case creation; nothing skips it.

## 2. Calibration OCR root cause

Traced camera frame → ROI extraction → OCR → parsing
([app/pipeline/read_frame.py](../backend/app/pipeline/read_frame.py) →
[detect.py](../backend/app/pipeline/detect.py) →
[calibrated_roi.py](../backend/app/pipeline/calibrated_roi.py) →
[ocr.py](../backend/app/pipeline/ocr.py)).

`read_frame()` unconditionally ran every frame through `detect_screen()`
before handing it to whichever `roi_extractor` was active:
```python
screen = detect_screen(img)
rois = roi_extractor(screen.image)
```
`detect_screen()`'s Canny+contour quad search is inherently nondeterministic
frame-to-frame on a real camera (glare/reflection/bezel-edge visibility) —
when it fires, it perspective-warps the frame to a **different size** than
the raw frame it started with. A calibrated profile's `NormalizedBox`
coordinates are established directly against the **raw** frame the operator
drew on (`calibrated_roi.py`'s own module docstring: boxes map "via a plain
rescale... without needing any geometric correction"). So on a frame where
`detect_screen()` happens to fire, the same normalized box silently lands on
the wrong pixels — some fields' boxes still coincidentally overlap digits,
others don't. This matches the reported symptom exactly: same setup,
intermittent, some fields fine.

Reproduced deterministically in
[test_calibrated_box_lands_on_the_wrong_pixels_without_the_fix](../backend/tests/test_m5_7_1_routing_and_calibration.py):
a bright monitor-like rectangle inset in a dark background (the same
screen-vs-bezel contrast a real camera photo has) with a known, distinctly-
coloured patch standing in for a vital's digits — recovered correctly only
when `detect_screen()` is skipped for a calibrated box.

**Fix**: `read_frame()` gained an opt-in `skip_screen_detection: bool =
False` parameter (default preserves every pre-M5.7.1 call site's behaviour
byte-for-byte). Set `True` only where a calibrated (operator-drawn-box)
extractor is definitely in play:
- `POST /api/calibration/verify` (always calibrated boxes)
- `POST /api/pipeline/read-frame` (iff the DB's active profile resolved)
- The live camera path (`CameraSource`, via `app.ws.vitals._camera_roi_extractor`,
  iff a calibrated extractor — static M5.2 or tracked M5.3 — was built)

No change to confidence gates, `reconcile()`, range/jump validation, crop
integrity, or alert logic.

**Secondary, real but not the intermittency itself**: Verify ran against
the box exactly as drawn, while Save padded it (`WIDTH_SAFETY_PAD_FRACTION`)
— so a value that verified could read differently in production. Verify now
pads identically. Also, `/api/calibration/verify` never surfaced *why* a
field returned `null` — it now returns per-field `diagnostics` (raw/matched
OCR text), reusing the existing `read_vital_with_diagnostics()` machinery
(no second Tesseract call), so the UI can say "OCR saw X but couldn't parse
it" instead of an unexplained `-`.

## 3. Setup-phase pollution (found while implementing #1)

`useVitalsSimulation()` is mounted once at the app root
([src/App.tsx](../src/App.tsx)) so observation survives navigation between
Operation/Review/Archive (M5.7's whole point). It opens a WS connection the
moment `activeSession.status === 'active'`, picking `source: cameraMode ?
'camera' : 'synthetic'`.

Once New Case always creates the session **before** routing to mandatory
Calibration, there is a real window — the entire time the operator is
drawing/verifying ROIs — where the session is already `active` but
`cameraMode` is still `false`. Before this fix, that window opened a
`source='synthetic'` connection and persisted fabricated readings into the
session's real history before Calibration even finished. Caught empirically
by the real-browser E2E script (`vital/scripts/m5_7_1_flow_e2e.mjs`), which
found `source` values `["synthetic","camera"]` on one archived session's
persisted rows.

**Fix**: `useVitalsSimulation` now also requires `cameraMode` before opening
any connection at all. Calibration is setup, not observation; nothing is
persisted until it succeeds and Active Operation actually begins. (The
backend's own `docs/DEMO.md` "Stage-2" synthetic-only demo runbook is
unaffected — it drives the backend directly via `demo_run.py`/`smoke_ws.py`
and never touches this frontend hook or the React UI at all.)

## Files changed

Backend: `app/pipeline/read_frame.py`, `app/api/calibration.py`,
`app/api/pipeline.py`, `app/sources/camera.py`, `app/ws/vitals.py`,
`tests/test_m5_7_1_routing_and_calibration.py` (new, 9 tests).

Frontend: `src/features/start/StartPage.tsx`,
`src/features/operation/OperationPage.tsx`,
`src/features/calibration/CalibrationPage.tsx`, `src/types/calibration.ts`,
`src/hooks/useVitalsSimulation.ts`.

E2E: `scripts/m5_7_1_flow_e2e.mjs` (new, real Chrome + real backend, 29
checks).

## Verification

- `pytest tests/ simulator/tests/`: 441 passed.
- `npm run build` (`tsc && vite build`): clean.
- `python app/eval/tier2_data/m5_7_report/m5_7_e2e_script.py`: 21/21 passed
  (continuous-observation contract unaffected).
- `node scripts/m5_7_1_flow_e2e.mjs <fakecam-dir> <out-dir>`: 29/29 passed —
  real Chrome, real getUserMedia (fake video device), real OCR, real
  backend, proving the corrected routing, automatic camera start, no
  manual-capture affordance, continuous observation across navigation,
  reload durability with no duplicate session, and a genuine multi-point
  camera-tagged Archive timeline.

## Remaining limitations

- `detect_screen()`'s own quad-detection accuracy on the Tier-1/Tier-2
  default (non-calibrated) paths is unchanged — this fix only removes its
  interaction with calibrated boxes.
- The M5.6-documented "confidently wrong under camera motion" finding
  (38/101 confident misreads under pan/zoom/roll) is unchanged; out of
  scope here, as it was for M5.7.
- `OcrDebugPage` remains a static mock (pre-existing, unrelated to this
  milestone); the new per-field diagnostics were wired into Calibration's
  own Verify step instead, where the honesty requirement actually applies.
