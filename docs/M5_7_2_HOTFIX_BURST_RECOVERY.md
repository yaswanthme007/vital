# M5.7.2 hotfix — corroborated-recovery tier for burst verification

## Problem (real-camera demo)

With a laptop webcam pointed at a real anesthesia monitor, and ROI boxes
correctly positioned over the digits, Calibration's Verify step was
rejecting fields with strong, clearly-readable agreement:

- HR `90` on 86% of captures → rejected
- SpO2 `98` on 83% of captures → rejected
- Temp `98.6` on 83% of captures → rejected
- EtCO2 `35` on 50% of captures → rejected
- RR `12` on 100% of captures → accepted

The UI told the operator to redraw a box that was already correctly
positioned.

## Root cause

`app/pipeline/burst_verify.py`'s stability rule
(`BURST_AGREEMENT_MIN_FRACTION = 0.5`, i.e. a strict majority) was never the
bottleneck — 83–86% agreement clears a 50% bar comfortably. The bottleneck
was `BURST_CONFIDENCE_FLOOR` (`CONFIDENCE_MEDIUM_MIN`, 70): the agreeing
samples' **mean confidence** had to independently clear the exact same bar
a **single, uncorroborated frame** needs on the live path.

That defeats the statistical point of doing burst verification at all. A
value that four independently-captured real camera frames agree on is
objectively stronger evidence than one 70%-confident frame — but the
shipped rule treated them identically. Real, non-studio Tesseract
confidence on a 7-segment monitor display through a webcam routinely lands
in the 45–65% range even on a correct, cleanly-cropped read (verified
against Dataset A/B's own score distribution and the real Verify screenshots
from the demo laptop) — below 70, so bursts of genuinely-agreeing, clean
reads were being thrown away wholesale.

This was traced with `git`-free inspection of `_aggregate_field`/
`_evaluate`: agreement_fraction and all_clean both passed; only
`mean_confidence >= confidence_floor` failed. Not an aggregation bug, not a
denominator bug, not a residual-content false-positive — a single
threshold that didn't distinguish "one untrusted frame" from "N frames that
agree".

## Fix

Added a second, narrower stability tier to `_evaluate()` in
`app/pipeline/burst_verify.py` — reached only when the full-confidence tier
(unchanged) fails:

```
Tier A (full trust, unchanged):
  agreement_fraction > 0.5  AND  all agreeing samples clean
  AND mean_confidence >= BURST_CONFIDENCE_FLOOR (70, CONFIDENCE_MEDIUM_MIN)
  -> stable, recovered=False

Tier B (corroborated recovery, NEW):
  agreement_fraction >= BURST_SUPERMAJORITY_FRACTION (0.8, i.e. at most
    one disagreeing/invalid sample out of a 5-frame primary burst)
  AND len(agreeing samples) >= BURST_RECOVERY_MIN_AGREEING_SAMPLES (3)
  AND all agreeing samples clean of residual content (SAME check as Tier A
    — never relaxed)
  AND mean_confidence >= BURST_RECOVERY_CONFIDENCE_FLOOR (40, reused
    verbatim from app.validation.temporal.CONFIDENCE_TEMPORAL_FLOOR)
  -> stable, recovered=True

Otherwise -> unstable, with a reason: "no_reading" | "low_agreement" |
  "geometry" (majority reached but not clean — crop-integrity fired) |
  "low_confidence" (majority reached and clean, but neither tier's
  confidence floor was cleared)
```

Worked examples (exactly the brief's regression scenarios):

- `90, 90, 90, 9, 90` → 4/5 agree (0.8, clears supermajority), the "9" is a
  single independent outlier. If the four agreeing 90s are clean and their
  mean confidence is e.g. 58 (below 70, at/above 40) → **verified as 90,
  recovered=True**.
- `98, 98, 98, 98, 9` → same shape → **verified as 98, recovered=True**.
- `90, 83, 9, null, 98` → no value reaches even a strict majority → **stays
  unstable** (`low_agreement`), regardless of the recovery tier.
- `83, 8, 8, 8, 8` (systematic truncation) → even at 5/5 (100%) agreement,
  if the agreeing samples carry residual OCR content (crop-integrity's
  truncation signature), **all_clean fails before either confidence tier is
  even consulted** → stays unstable (`geometry`). The recovery tier cannot
  bypass this; the check runs first and is identical for both tiers.
- A bare 3-of-5 (60%) majority below the supermajority bar, even at a
  confidence between 40 and 70, does **not** qualify for recovery — it
  still needs the full 70 floor. This is the explicit guard against "accept
  anything that looks common": only a supermajority (at most one outlier)
  is treated as strong enough corroboration to relax the per-sample bar.

## Why this isn't "lower the confidence threshold"

`BURST_CONFIDENCE_FLOOR` (70) is untouched — Tier A is byte-identical to
the original M5.7.2 rule. Nothing is globally relaxed. The new floor
(`BURST_RECOVERY_CONFIDENCE_FLOOR` = `CONFIDENCE_TEMPORAL_FLOOR` = 40) only
applies inside a narrower gate (supermajority agreement + minimum absolute
sample count + still-clean), and that number itself is not invented for
this feature — it's the same constant `app.validation.temporal` already
validated (M5.4) for exactly this "multiple corroborating reads justify a
lower per-read confidence than one untrusted tick would need" role. The
M5.7.2 report's original floor=40-vs-70 comparison measured a **flat**
floor=40 applied unconditionally (quadrupling the WRONG rate on real data);
this hotfix does not do that — it only reaches 40 when the agreement is
already a supermajority, which the original comparison never isolated.

## Safety protections preserved

- **Crop-integrity / residual-content check**
  (`app.validation.crop_integrity.has_residual_content`) is evaluated
  identically for both tiers, and evaluated *before* either confidence
  check — a systematically truncated/malformed value can never reach
  `stable=True` via either tier, no matter the confidence or agreement.
- **Strict-majority gate** (`BURST_AGREEMENT_MIN_FRACTION`, 0.5) is
  unchanged and still the first filter applied.
- **`TEMPORAL_CORROBORATION` was not touched or enabled** — this fix is
  entirely local to `burst_verify.py`'s own aggregation logic, reusing
  `CONFIDENCE_TEMPORAL_FLOOR` as a constant, not turning on the live-path
  mechanism it's declared in.
- **`value` is still never invented** — an unstable field still reports
  `value: null`, with `bestGuess*` fields for the UI, exactly as before.
- **No test was weakened.** All existing M5.7.2 tests pass unchanged; the
  hotfix only added a new, narrower acceptance path.

## Files changed

- `backend/app/pipeline/burst_verify.py` — new `BURST_SUPERMAJORITY_FRACTION`,
  `BURST_RECOVERY_MIN_AGREEING_SAMPLES`, `BURST_RECOVERY_CONFIDENCE_FLOOR`
  constants; `_evaluate()` now returns `(stable, recovered, unstable_reason,
  mode_key, mode_samples, valid)` and implements the two-tier rule;
  `FieldBurstResult` gained `recovered: bool` and
  `unstable_reason: Optional[str]`; `_aggregate_field` propagates both.
- `backend/app/api/calibration.py` — `/verify-burst` response's per-field
  `burst` object gained `recovered` and `unstableReason`.
- `backend/app/eval/m5_7_2_burst_verification_eval.py` — updated to the new
  `_evaluate()` return arity (no behavior change to the eval's own
  burst70/burst40 comparison other than burst70 now including the recovery
  tier, since it shares the same default `confidence_floor`).
- `backend/tests/test_m5_7_2_burst_verification.py` — 11 new regression
  tests (unit-level, direct `_evaluate()`/`_Sample` calls, plus one
  endpoint-shape test) covering every scenario in the hotfix brief.
- `src/types/calibration.ts` — `CalibrationBurstFieldMeta` gained
  `recovered: boolean` and `unstableReason: '...' | null`.
- `src/features/calibration/CalibrationPage.tsx` — four-state messaging:
  STABLE, MOSTLY STABLE / recovered ("...One noisy frame was discarded."),
  UNSTABLE ("Hold the camera steady and improve lighting..."), and BAD
  GEOMETRY ("...redraw the box around the full digits.") — the redraw
  suggestion is now shown only when `unstableReason === 'geometry'`, never
  as a default fallback for ordinary noise.

## Verification

- `pytest tests/ simulator/tests/`: **466 passed** (455 + 11 new). No test
  weakened or skipped.
- `npm run build` (`tsc && vite build`): clean.
- `node scripts/m5_7_1_flow_e2e.mjs <fakecam-dir> <out-dir>`: real Chrome,
  real fake-camera-device burst capture, real OCR, full New Case →
  Calibration (burst verify) → automatic Operation → continuous tracking →
  Archive flow. **29/29 passed** — including "every drawn field (4) was
  confirmable within 3 burst attempts", "camera-OCR value (not the default
  baseline) reached the UI", and 12 persisted, source=camera Archive rows
  with distinct timestamps.
- `python -m app.eval.m5_7_2_burst_verification_eval`: re-run against real
  Dataset A/B (274 scored fields, 2 noise levels):

  | condition | success/stable | correct | WRONG |
  |---|---|---|---|
  | single-frame (production `/verify`) | 85.8–86.1% | 66.4–67.5% | 18.2–19.7% |
  | burst70 (Tier A only — original M5.7.2, historical) | 44.2–46.4% | — | 0.7–1.1% |
  | **burst70 + recovery tier (this hotfix, shipped)** | **50.0–50.7%** | 46.0–46.7% | **3.3–4.7%** |
  | burst40 (flat floor=40, considered & rejected) | 50.0–50.7% | 46.0–46.7% | 4.0–4.7% |

  **Read honestly, not cherry-picked:** the recovery tier measurably raises
  the stable/success rate (44–46% → 50–51%) but also raises the WRONG rate
  versus the original Tier-A-only floor=70 (0.7–1.1% → 3.3–4.7%) — because on
  this dataset, cases with a supermajority-but-sub-70-confidence agreement
  turn out to overlap substantially with the cases flat-floor=40 would also
  have accepted (appearance-only synthetic noise on a static frame tends to
  produce either strong agreement or clear disagreement, not much of a
  middle band). The WRONG rate stays **4–6x lower than raw single-frame**
  (18.2–19.7%) and crop-integrity still unconditionally blocks the
  systematic-truncation failure class regardless of confidence (see the
  `test_unanimous_but_systematically_truncated_box_never_stabilizes` and
  `test_systematic_truncation_is_never_accepted_even_at_full_agreement`
  tests, both passing). This is a real, disclosed trade-off, not a hidden
  regression: the brief explicitly asked to trade some of the original
  floor=70's extreme conservatism for a working demo, while keeping the
  crop-integrity defense absolute — that is exactly what shipped. Full
  per-record data: `app/eval/tier2_data/m5_7_2_report/`.
- Real physical-camera path: not exercised in this sandboxed environment
  (no attached webcam / physical monitor available here). The fix targets
  exactly the confidence-floor logic the demo laptop's own screenshots
  showed failing, and the E2E run above exercises the identical
  `verify_burst`/`_evaluate` code path end-to-end through a real browser.
  **The operator should re-run the real Calibration → Verify flow once on
  the actual demo laptop/monitor to confirm before the judge-facing
  session** — that step could not be performed from this environment.
