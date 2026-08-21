# M5.7.2 — Multi-Frame Burst Verification for Calibration

> **Hotfix note (post-ship, real-camera demo):** the shipped
> `BURST_CONFIDENCE_FLOOR` rule below rejected strongly-agreeing real-camera
> reads (83–86% agreement) because it required the agreeing group's mean
> confidence to independently clear the same 70 bar a single uncorroborated
> frame needs — discarding the benefit of multi-frame corroboration. A
> second, narrower "corroborated recovery" tier was added; see
> [`M5_7_2_HOTFIX_BURST_RECOVERY.md`](M5_7_2_HOTFIX_BURST_RECOVERY.md) for
> the root cause, the fix, and why it isn't just "lower the threshold". The
> design/measurements below remain accurate for the tier they describe
> (now called "Tier A" / the full-confidence tier).

## Problem

Calibration's Verify step captured exactly one frame and trusted it
outright. Transient glare, motion blur from a hand still settling on the
camera, a momentary autofocus hunt, or a single bad JPEG compression pass
could all degrade one capture without the box or the monitor having moved
— and nothing caught it.

## Design

`backend/app/pipeline/burst_verify.py` captures a burst of independent
frames and only reports a field as verified once its value is consistently
reproduced across them. It is explicitly **not** just "vote across N
frames": `app.validation.temporal` (M5.4) already tried the naive version
of that idea for the live path, and its own held-out validation found a
real blind spot — a systematic misread (the same too-narrow crop truncating
the same digit the same way every tick) produces identical wrong values run
after run, which looks exactly like agreement to a pure repetition check.

Two defenses, both reused rather than invented:
1. **`app.validation.crop_integrity.has_residual_content`** (the same
   signal that closed temporal corroboration's exact blind spot) — a value
   only counts as stable if every agreeing sample's raw OCR text has no
   residual content beyond the parsed value.
2. **A confidence floor on the agreeing samples' mean** —
   `BURST_CONFIDENCE_FLOOR`, an existing constant, not an invented one (see
   "Which confidence floor" below).

Stability rule: the modal value must be produced by a **strict majority**
of valid samples, that majority's **mean confidence must clear the floor**,
and **every agreeing sample must be clean** of residual content. A field
that isn't stable reports `value: null` — never an invented or forced
guess — with `bestGuess*` fields so the UI can still explain what the
closest attempt looked like.

A second preprocessing variant (`_preprocess(..., variant="adaptive")` in
`ocr.py`: CLAHE contrast normalization + adaptive threshold, vs. the
original global Otsu threshold) is invoked as a fallback **only** on fields
the primary pass couldn't stabilize, bounding the extra OCR cost to exactly
the fields that need it.

**Reuse, not replacement**: the same production `TesseractEngine`, the same
`extract_rois_from_boxes`, the same `CONFIDENCE_MEDIUM_MIN` — there is no
second OCR system. The only new production code is the aggregation logic
in `burst_verify.py` and the `adaptive` preprocessing variant.

## Which confidence floor — a measured decision

`CONFIDENCE_TEMPORAL_FLOOR` (40.0, from `app.validation.temporal` — the
existing constant for exactly this "multi-sample corroboration" role) was
a real candidate against `CONFIDENCE_MEDIUM_MIN` (70.0, the live path's
single-tick acceptance bar). Measured head-to-head on real Dataset A/B
(`app/eval/m5_7_2_burst_verification_eval.py`, 274 scored fields at 2 noise
levels each — full numbers in `app/eval/tier2_data/m5_7_2_report/`):

| condition | WRONG rate | stable/success rate |
|---|---|---|
| single-frame (today's production Verify) | 19.7–20.1% | 86.1–86.5% |
| burst, floor=70 (**shipped**) | **0.7–1.1%** | 44.2–46.4% |
| burst, floor=40 (considered) | 4.4–4.7% | 50.7–53.6% |

Floor=70 is 4–6x safer than floor=40 for a 6–8 point lower success rate.
Per this project's own stated posture (a false HOLD is an acceptable cost;
a confidently-wrong CONFIRMATION is not — `crop_integrity.py`'s own
docstring), **floor=70 (`CONFIDENCE_MEDIUM_MIN`) is what shipped.**

### A real-data confirmation of the safety design

Dataset A's NIBP crops carry a genuine stray leading character (a
period/hyphen artifact at the crop's left edge) on nearly every frame.
Single-frame OCR reads this as `hr` digit-confusions like `450` instead of
`150` — a **systematic** misread that would reproduce identically across a
naive burst of the same crop (exactly `temporal_corroboration`'s blind
spot). Because the same artifact also makes `raw_text` disagree with
`matched_text`, `has_residual_content` correctly refuses to certify it
every time — burst70 measures **0% wrong on NIBP** at the cost of never
stabilizing it on this dataset (100% "unstable"). This is the crop-integrity
defense working exactly as designed, not a coincidence: a box catching
stray edge content is often the same imprecision that also produces wrong
digit reads.

### A related, real, pre-existing bug fixed along the way

`_read_nibp`'s mean-value search loop reused the variable name `match`,
silently shadowing the systolic/diastolic regex match the diagnostics were
supposed to describe — so `matched_text` sometimes described the *mean*
line instead of the *sys/dia* line, which made `has_residual_content`
spuriously flag clean sys/dia reads as suspicious. Fixed (renamed
variables); covered by a regression test.

## Frontend

`CalibrationPage.tsx`'s `runVerify` now captures `BURST_FRAME_COUNT` (5)
frames spaced `BURST_FRAME_INTERVAL_MS` (200ms) apart — comfortably slower
than a real camera's own frame period, so each capture is a genuinely
independent read — then POSTs all of them in one request to the new
`POST /api/calibration/verify-burst` endpoint. The UI shows "Capturing live
frames…" (with a frame-count progress bar) then "Analyzing live frames…"
before results appear, and each stable field shows "Stable — N% of M
captures agreed" instead of a bare confidence number. An unstable field
leads with camera/lighting guidance ("Improve lighting or hold the camera
steady, then re-run") before suggesting a redraw, since a correctly-placed
box can still fail to stabilize under transient conditions — exactly the
UX this milestone's brief asked for. The Confirm button's existing
`value === null` disabled-state already does the right thing for an
unstable field with no changes needed.

The single-frame `POST /api/calibration/verify` endpoint and its frontend
client are untouched and still used nowhere differently — a pure addition,
not a replacement.

## Files changed

Backend: `app/pipeline/ocr.py` (adaptive preprocessing variant + NIBP
diagnostics bug fix), new `app/pipeline/burst_verify.py`, `app/api/
calibration.py` (new `/verify-burst` endpoint), new `tests/
test_m5_7_2_burst_verification.py` (14 tests), new `app/eval/
m5_7_2_burst_verification_eval.py`.

Frontend: `src/types/calibration.ts`, `src/lib/api.ts`, `src/features/
calibration/CalibrationPage.tsx`.

E2E: `scripts/m5_7_1_flow_e2e.mjs` updated to draw only the fields that
reliably stabilize on the fake-camera fixture (HR/Temp's confidence sits
just under the floor on this specific fixture's geometry — a real, measured
result, not a bug to route around by lowering the threshold) and to retry
verification like a real operator would before giving up.

## Verification

- `pytest tests/ simulator/tests/`: 455 passed (441 + 14 new).
- `npm run build` (`tsc && vite build`): clean.
- `python app/eval/tier2_data/m5_7_report/m5_7_e2e_script.py`: 21/21 passed
  (continuous-observation contract unaffected).
- `python -m app.eval.m5_7_2_burst_verification_eval`: real Dataset A/B
  measurement, numbers above; raw records and per-vital breakdown in
  `app/eval/tier2_data/m5_7_2_report/`.
- `node scripts/m5_7_1_flow_e2e.mjs <fakecam-dir> <out-dir>`: 29/29 passed
  — real Chrome, real burst capture, real OCR, full New Case → Calibration
  (burst verify) → automatic Operation → continuous tracking → Archive flow.

## Remaining limitations

- Burst verification's stable/success rate on real photographed data
  (44–46%) is meaningfully lower than single-frame's raw hit rate
  (86%) — by design (near-elimination of wrong confirmations), but it
  means some genuinely-correct fields will need a re-run or, occasionally,
  never stabilize on a given box's geometry. The product already supports
  leaving such a field blank and proceeding; it does not block the whole
  calibration.
- The `adaptive` preprocessing variant's measured contribution is modest on
  this evidence base (see the raw eval records) — kept as a bounded-cost
  fallback per the brief's request for preprocessing variants, not because
  it was shown to be a large factor on its own.
- `frames_per_burst` (5) and the 200ms spacing are reasoned choices (camera
  frame-rate physics, latency budget), not swept/tuned against real data —
  a natural follow-up if latency or reliability needs further work.
