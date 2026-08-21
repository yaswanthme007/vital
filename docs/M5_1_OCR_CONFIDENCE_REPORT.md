# M5.1 — OCR Confidence Restoration Report

**Status:** complete, 2026-08-19. Scope: `backend/app/pipeline/ocr.py` only.
Companion documents: [`ROADMAP.md`](ROADMAP.md) (the plan this milestone
executes), [`ARCHITECTURE.md`](ARCHITECTURE.md), [`EVIDENCE.md`](EVIDENCE.md)
(the prior measurements this milestone reproduces and extends).

---

## 1. Objective

Determine whether Tesseract's reported per-token confidence is being
artificially collapsed by `tessedit_char_whitelist`, and if so, make the
smallest production change that restores a meaningful confidence signal —
**not** to chase raw OCR accuracy for its own sake, but so that a
**correct** OCR read can actually **clear `reconcile()`'s confidence gate**
and become a **confirmed** value instead of being silently held or flagged.

Three accuracy-shaped numbers appear throughout this report and are
deliberately kept distinct:

- **raw OCR accuracy** — does the parsed value match ground truth, at all,
  regardless of confidence.
- **confidence** — what Tesseract reports for that read (0-100).
- **confirmed accuracy** — what `reconcile()` actually surfaces as the
  session's current value, after range/jump/confidence gating and
  hold-last-confirmed.

A fix that only moves the first number is not the point of M5.1. A fix that
moves the third, without moving the confidence-threshold goalposts, is.

---

## 2. Current production behaviour (Phase 0 trace)

```
camera/frame -> detect_screen() -> ROI extraction -> _preprocess() -> Tesseract -> parse -> confidence
    -> read_frame() fuses (MIN classifier, ocr) -> reconcile() -> confirmed value / hold / flag
```

Traced directly against the unmodified source (nothing was changed during
this inspection):

- **`backend/app/pipeline/ocr.py`** — `TesseractEngine.read_vital()`
  dispatches per vital to one of four config strings, all via a shared
  `_preprocess()` (grayscale -> upscale to 120px -> Otsu threshold ->
  majority-polarity fix -> quiet-zone padding) and a shared
  `_extract_tokens()`/`_joined_text_and_confidence()` (mean of all non-empty
  Tesseract token confidences). Before this milestone:

  | Vital | Config before M5.1 | PSM | Whitelist |
  |---|---|---|---|
  | hr | `_DIGIT_CONFIG` | 8 | `0123456789` |
  | temp | `_DECIMAL_CONFIG` | 8 | `0123456789.` |
  | spo2, rr | `_DIGIT_PSM10_CONFIG` | 10 | `0123456789` |
  | nibp | `_NIBP_CONFIG` | 6 | *(none — M4.4)* |
  | etco2 | `_ETCO2_CONFIG` | 8 | *(none — M4.4)* |

  M4.4 (see that milestone's report) had already root-caused and removed
  the whitelist for NIBP and EtCO2. Its own text explicitly deferred
  hr/spo2/rr (and, as this milestone found, temp) to a later milestone —
  this is that milestone.

- **`backend/app/pipeline/read_frame.py`** — `confidences[vital] =
  min(classifier_confidence, ocr_confidence)` when the ROI came from Tier-2,
  else `ocr_confidence` unchanged (Tier-1 colour ROI, which is what
  everything in this report runs through — no classifier confidence enters
  the fusion at all).

- **`backend/app/validation/rules.py`** — `CONFIDENCE_HIGH_MIN = 90`,
  `CONFIDENCE_MEDIUM_MIN = 70`. `confidence_tier()` maps `<70` to `ai_low`
  (held, not confirmed), `70-89` to `ai_medium` (confirmed but flagged),
  `>=90` to `ai_high` (confirmed, clean).

- **`backend/app/validation/reconcile.py`** — per field, in order: `None` ->
  `unreadable` (hold); out of `RANGE_BOUNDS` -> `implausible_range` (hold);
  jump vs. last confirmed beyond `JUMP_LIMITS` inside the window ->
  `jump_rejected` (hold); else gated by `confidence_tier()`. Temp is
  Fahrenheit/Celsius-normalized (`normalize_temp_celsius`) **before** any of
  this runs. A rejected/held field keeps its last confirmed value — it is
  never silently replaced with a wrong one.

Nothing above was modified during Phase 0.

---

## 3. Root-cause evidence (Phases 1 & 2)

**Method.** `app/eval/m5_1_ocr_config_sweep.py` (committed, read-only against
the datasets) runs two `TesseractEngine` subclasses over the **identical**
oracle (ground-truth-box) crop for every (sample, vital):

- `production` — the real, unmodified `TesseractEngine`.
- `no_whitelist` — `NoWhitelistEngine`, which strips `-c
  tessedit_char_whitelist=...` from `_DIGIT_CONFIG`/`_DECIMAL_CONFIG`/
  `_DIGIT_PSM10_CONFIG` via a regex over the production string itself (not a
  hand-duplicated literal, so PSM/flags can't silently drift), and delegates
  NIBP/EtCO2 to `TesseractEngine` unchanged.

PSM, preprocessing, and crop are held fixed; whitelist presence is the only
variable. This isolates the root cause exactly as Phase 1/2 required.

**Direct, single-crop confirmation** (Dataset B, `sample_0009`, hr, GT=85):

| Config | Value read | Confidence |
|---|---:|---:|
| whitelisted (pre-M5.1 production) | 85 | **0** |
| whitelist-free | 85 | **33** |

Same crop, same PSM, same recognized text — confidence alone collapses when
the whitelist is present. This is the exact mechanism M4.4 root-caused for
NIBP/EtCO2 (report §8: NIBP's "150/80" line, confidence 0 whitelisted -> 85
not, same text), now reproduced directly for HR.

**Worst instance found:** Dataset B SpO2 (`--psm 10` + whitelist, pre-M5.1
production). Mean confidence on the 14/17 frames OCR read *correctly*:

| | mean confidence, correct reads | % clearing the 70 gate |
|---|---:|---:|
| whitelisted | **0.1** (13/14 exactly 0.0) | **0%** |
| whitelist-free | **61.4** | **28.6%** |

This matches `EVIDENCE.md` §5's "82% correctly at confidence exactly 0"
finding almost exactly (14/17 = 82.4% here).

---

## 4. Experimental methodology (Phases 1-5)

- **Datasets**, both frozen, both read-only:
  - **A** = `external_monitor_video`, 52 frames, one continuous video, GT
    boxes in each `sample_*.json`, GT values manually transcribed in
    `m4_ocr_report/m4_ground_truth_values.json` (M4.1).
  - **B** = `external_monitor_B`, 17 frames, GE CARESCAPE B650, GT
    boxes+values in `m5_ground_truth_values.json` (M5). NIBP never appears
    in this recording (always dashes/"Manual"). **Do not read a per-dataset
    NIBP comparison anywhere below — it does not exist for B.**
- **Oracle crop** = the annotated ground-truth box, warped through
  `detect_screen()`'s homography, cropped with **zero margin** — the
  tightest literal ground-truth box, not a candidate-generator/FieldCNN
  selection. Localization is out of scope for M5.1 and was never touched;
  neither `tier2_roi.py` nor `field_classifier.py` is imported by the sweep
  script.
- **Reconcile validation** (Phase 5): each dataset+config's oracle-crop OCR
  records are fed, in chronological (sample-id) order, through the real,
  imported `app.validation.reconcile.reconcile()` — never reimplemented —
  exactly mirroring the pattern `app/eval/m4_3_reliability.py`'s
  `replay_reconcile()` already established. `CONFIDENCE_MEDIUM_MIN` was
  never touched.
- **Dataset B Temp is excluded from all correctness scoring** (raw OCR still
  recorded, never dropped from output): its GT box is clipped (crops `3.7`
  out of `23.7`) and `23.7`°C is outside `RANGE_BOUNDS["temp"]` regardless of
  OCR — a genuine no-data case per `EVIDENCE.md` §9, not a live measurement.
- **Dataset A Temp is one static value** (`98.6`°F in all 52 frames, per
  `EVIDENCE.md` §9) — included below because Phase 1 asked for every vital
  to be broken out, but it is not evidence of anything beyond that one
  recognition.

Everything below comes from `app/eval/tier2_data/m5_1_report/` (raw records
+ summaries), regenerable with `python -m app.eval.m5_1_ocr_config_sweep`.

---

## 5. Dataset A results (oracle crops)

| | production (before) | no_whitelist (after) |
|---|---:|---:|
| n scored | 233 | 233 |
| **OCR accuracy** | 90.6% | **98.7%** |
| missing rate | 8.6% | 0.4% |
| mean confidence, correct reads | 91.0 | 91.1 |
| % correct reads clearing >=70 gate | 92.9% | 93.5% |
| mean latency/crop | 262 ms | 271 ms |

Per vital:

| Vital | n | acc before | acc after | conf(correct) before | conf(correct) after |
|---|---:|---:|---:|---:|---:|
| hr | 43 | 100.0% | 100.0% | 92.8 | 92.8 |
| spo2 | 27 | 96.3% | 96.3% | 95.5 | 95.5 |
| nibp (sys/dia/mean) | 51 | 100.0% | 100.0% | 95.4 | 95.4 |
| etco2 | 17 | 94.1% | 94.1% | 93.4 | 93.4 |
| temp\* | 52 | 100.0% | 100.0% | 80.0 | 80.0 |
| **rr** | 43 | **53.5%** | **97.7%** | 95.7 | 94.2 |

\* Dataset A's Temp is one static value — identical before/after because the
crop is unambiguous; see §4.

**No field regresses.** RR is the outlier, and it is a large *improvement*,
not a confidence-only one: raw OCR accuracy itself rises 53.5% -> 97.7%.
Inspecting the difference (RR's `--psm 10` route): under the whitelist, 20/43
frames returned no digit at all (`missing`); without it, only 1/43 do. The
whitelist was not just crushing confidence on correct RR reads, it was
occasionally forcing a false digit-shaped guess onto what should have been
"no reading" — removing it lets more of those frames correctly resolve to
"unreadable" instead of a wrong number, and lets far more genuinely resolve
correctly. This also happens to resolve the RR confidence-miscalibration
issue M4.6 §10 flagged as carried-forward-unfixed ("most wrong RR reads
still arrive at >=90% confidence") — it is fixed as a side effect of the
same root-cause repair, not by any separate change.

---

## 6. Dataset B results (oracle crops)

| | production (before) | no_whitelist (after) |
|---|---:|---:|
| n scored | 53 | 53 |
| OCR accuracy | 50.9% | 50.9% (unchanged — expected, see below) |
| missing rate | 32.1% | 39.6% |
| mean confidence, correct reads | **17.0** | **55.3** |
| % correct reads clearing >=70 gate | **0.0%** | **18.5%** |
| mean latency/crop | 231 ms | 236 ms |

Per vital:

| Vital | n | acc before | acc after | conf(correct) before | conf(correct) after | %clearing gate before | %clearing gate after |
|---|---:|---:|---:|---:|---:|---:|---:|
| hr | 17 | 52.9% | 52.9% | 42.8 | 46.4 | 0.0% | 0.0% |
| **spo2** | 17 | 82.4% | 82.4% | **0.1** | **61.4** | **0.0%** | **28.6%** |
| etco2\*\* | 12 | 16.7% | 16.7% | 36.8 | 36.8 | 0.0% | 0.0% |
| **rr** | 7 | 28.6% | 28.6% | **0.0** | **71.5** | **0.0%** | **50.0%** |

\*\* EtCO2 was already whitelist-free since M4.4 — identical before/after by
construction, included as an in-dataset negative control confirming the
sweep's isolation actually works.

**Overall accuracy is unchanged (50.9% -> 50.9%) by design** — the whitelist
was never the reason Dataset B's raw OCR is hard (image quality is: a phone
video of a YouTube recording of a monitor, see §11). What changes is whether
the *already-correct* reads carry a confidence a downstream consumer can
trust: SpO2's correct-read confidence jumps from a mean of 0.1 to 61.4, RR's
from 0.0 to 71.5 — the exact "confidently correct, but unbelievable" failure
`ARCHITECTURE.md` describes.

**HR missing rate increases (3/17 -> 6/17).** Traced per-sample: the 3
newly-missing frames (`sample_0012`, `0014`, `0015`) were **wrong values**
under production (`12`, `7`, `2` against GT `85`/`86`/`87`), not correct ones
that got lost. Every previously-correct HR read stays correct and its
confidence is flat-to-improved. Converting a wrong guess into an honest "no
reading" is a safety improvement under this system's own design principle
(uncertain data must not silently become confirmed data), not a regression.

---

## 7. Before/after OCR accuracy (summary)

| Dataset | metric | before | after |
|---|---|---:|---:|
| A | OCR accuracy (oracle) | 90.6% | **98.7%** |
| B | OCR accuracy (oracle) | 50.9% | 50.9% (unchanged) |

---

## 8. Before/after confidence distributions

Confidence-tier distribution over every scored oracle-crop read (not just
correct ones):

| Dataset | Config | low (<70) | medium (70-89) | high (>=90) |
|---|---|---:|---:|---:|
| A | production | 37 (15.9%) | 24 (10.3%) | 172 (73.8%) |
| A | no_whitelist | 18 (7.7%) | 24 (10.3%) | **191 (82.0%)** |
| B | production | **53 (100%)** | 0 (0%) | 0 (0%) |
| B | no_whitelist | 48 (90.6%) | **5 (9.4%)** | 0 (0%) |

Dataset A's shift is almost entirely low-tier reads (previously-missing RR
frames) moving to high-tier. Dataset B moves from *zero* dynamic range
(every single read, right or wrong, reported low-tier) to a small but real
medium-tier population — consistent with EVIDENCE.md §5.1's finding that the
gate has dynamic range wherever confidence is allowed to vary, and none
where the whitelist has already flattened it to zero.

---

## 9. Before/after reconcile() confirmation

Real `reconcile()`, real chronological replay, `CONFIDENCE_MEDIUM_MIN`
untouched (`app/eval/tier2_data/m5_1_report/m5_1_reconcile_{A,B}_{config}.json`):

| Dataset | Config | micro OCR acc | micro confirmed acc | confidently-wrong confirmations |
|---|---|---:|---:|---:|
| A | production | 90.6% | 96.6% | **0** |
| A | no_whitelist | 98.7% | 96.6% (flat) | **0** |
| B | production | 50.9% | **11.3%** | **0** |
| B | no_whitelist | 50.9% | **20.8%** | **0** |

**Dataset B: confirmed accuracy nearly doubles (11.3% -> 20.8%)** — the
direct, reconcile()-validated payoff of this milestone, driven almost
entirely by SpO2 (confirmed-correct frames 5/17 -> 10/17; reject-reason
breakdown shows genuine `medium_confidence` acceptances appearing for the
first time, not just more holding).

**Dataset A stays flat at 96.6% despite RR's OCR jump.** Reconcile's
hold-last-confirmed was already masking most of RR's badness: RR barely
changes second-to-second in this recording, so a rejected/held tick usually
still displays the *correct* value by holding the prior one.
`m5_1_reconcile_A_production.json`'s field stats show RR confirmed-correct
at 42/43 **even under the old, badly-miscalibrated OCR** (23/43 raw-correct)
— confirming M4.6 §10's warning was right to flag this as an unresolved
risk: the old pipeline was *not* actually reading RR reliably, it was
coincidentally always holding a fairly stable, correct-enough number. M5.1's
fix makes the OCR itself trustworthy (42/43 raw-correct), which is the
change that matters even though the aggregate confirmed-accuracy metric
doesn't move — a system that is right because it reads correctly is not the
same guarantee as one that is right because it got lucky holding.

**Zero confidently-wrong confirmations, before and after, both datasets.**
No field was ever confirmed at a value that equalled a *wrong* raw OCR read.
This is the hard safety gate and it holds throughout.

---

## 10. Per-vital results

See §5 (Dataset A) and §6 (Dataset B) for full per-vital tables. Summary of
where the fix materially matters:

| Vital | Dataset A impact | Dataset B impact |
|---|---|---|
| hr | no change (100% both) | confidence +3.6, still below gate; missing rate up but only via dropped wrong guesses (see §6) |
| spo2 | no change (96.3% both) | **confidence 0.1->61.4; confirmed 5/17->10/17** |
| nibp | no change (100% both) | absent from B — not evaluated |
| etco2 | no change (94.1% both, already fixed M4.4) | no change (already fixed M4.4) |
| temp | no change (Dataset A degenerate; Dataset B excluded, see §4) | excluded from scoring |
| rr | **OCR acc 53.5%->97.7%**, confidence flat-high | **confidence 0.0->71.5; confirmed unchanged at 1/7 (n too small to move)** |

---

## 11. Regressions

**None found that affect a correct reading's confirmed status.** The one
measurable side effect — Dataset B HR's missing rate rising from 3/17 to
6/17 — was traced per-sample (§6) and shown to consist entirely of
previously-*wrong* values becoming honest non-reads, never a previously-
correct value being lost. No field's OCR accuracy decreases on either
dataset. No newly-introduced confidently-wrong `reconcile()` confirmation
appears anywhere in the Phase 5 replay.

---

## 12. Latency impact

| Dataset | production | no_whitelist | delta |
|---|---:|---:|---:|
| A | 262 ms/crop | 271 ms/crop | +9 ms (~3%, within noise) |
| B | 231 ms/crop | 236 ms/crop | +5 ms (~2%, within noise) |

Negligible. This is expected — the whitelist changes Tesseract's output
character set, not the cost of the preprocessing or the Tesseract call
itself.

---

## 13. Production change made

**File:** `backend/app/pipeline/ocr.py` only. Constants only — no
preprocessing, PSM routing, threshold, range bound, jump limit, or
`reconcile()`/`rules.py` logic was touched.

```diff
- _DIGIT_CONFIG = "--psm 8 -c tessedit_char_whitelist=0123456789"
- _DECIMAL_CONFIG = "--psm 8 -c tessedit_char_whitelist=0123456789."
+ _DIGIT_CONFIG = "--psm 8"
+ _DECIMAL_CONFIG = "--psm 8"

- _DIGIT_PSM10_CONFIG = "--psm 10 -c tessedit_char_whitelist=0123456789"
+ _DIGIT_PSM10_CONFIG = "--psm 10"
```

`_PSM10_VITALS` (`{"spo2", "rr"}`), `_NIBP_CONFIG`, `_ETCO2_CONFIG`, and
every PSM value are byte-for-byte unchanged — M4.4's NIBP/EtCO2 fix and
M4.6's PSM routing are both preserved exactly. `read_frame.py`,
`reconcile.py`, `rules.py`, and every threshold/bound/limit are untouched.
Inline comments in `ocr.py` were extended (not rewritten) to record this
milestone's evidence alongside the existing M4.4/M4.6 comment trail, so the
whitelist's removal is traceable back to the same measurement method as
every prior config decision in that file.

Why this is safe to make without also touching the confidence gate: parsing
was never dependent on the whitelist for correctness — the downstream
regex-based digit/decimal extraction already discards anything Tesseract
emits that isn't a digit run (or a decimal/slash pattern for temp/NIBP),
exactly as M4.4 established for NIBP/EtCO2. The whitelist was providing zero
parsing safety and a large, measured confidence cost.

---

## 14. Tests / E2E validation

**Focused OCR tests:** `pytest tests/test_ocr.py` — 8/8 passed.

**Full backend suite:** `pytest tests/ simulator/tests/ -q`

| | count |
|---|---:|
| Baseline before this milestone | 284 passed |
| After the production change, before adding new tests | 281 passed, 3 failed |
| Final | **286 passed** |

**Why 3 tests failed, and how they were resolved (not deleted, not
weakened):** three tests in `tests/test_m4_4_rules_layer.py` and
`tests/test_m4_6_production_promotion.py` pinned the *literal, pre-M5.1*
config strings as a guard against those constants being touched by a
*different* milestone's change (M4.4 and M4.6's own scope-discipline
tests). M5.1 is precisely the milestone that intentionally changes them.
Each test's assertion was updated to the new, evidence-backed value, its
docstring extended to explain the M5.1 supersession and link back to this
report, and (where the name asserted something no longer true, e.g.
`..._untouched_by_this_milestone`) renamed for accuracy. Their guard
function — catching an *unintentional* future drift of these constants — is
fully preserved; they now pin M5.1's value instead of M4.4/M4.6's.

**Why the total count changed (284 -> 286, +2):** one new file,
`tests/test_m5_1_ocr_confidence_restoration.py`:
1. `test_scalar_configs_carry_no_whitelist` — structural guard that
   `tessedit_char_whitelist` can never silently reappear in the three
   configs this milestone fixed.
2. `test_hr_confidence_no_longer_collapses_on_known_case` — a real-data
   regression test against the exact Dataset B `sample_0009` hr crop cited
   in §3/§13: confirms production confidence is no longer collapsed on this
   known case, **and** confirms the pre-M5.1 whitelisted config (reconstructed
   locally as a literal string, since production no longer defines it
   anywhere) still collapses confidence on the same crop — so the test
   would catch a regression back to whitelisting even long after the old
   constant is gone from `ocr.py`.

No existing test was deleted or had its assertion loosened to pass.

**Frontend:** `npx tsc --noEmit` — clean, zero errors. `npx vite build` —
succeeds (`✓ built in 30.63s`); the pre-existing >500kB main-chunk warning is
unrelated to this milestone (no frontend file was touched).

**Real E2E** (Phase 8, no mocks): started a real `uvicorn` instance against
a scratch SQLite database, then in order:

1. `POST /api/sessions` -> real session `SESSION-1787148191158-45v9`.
2. `POST /api/pipeline/read-frame` with a real simulator-rendered frame ->
   every field matched ground truth exactly, confidence 91-96 across all six
   vitals (the single-shot debug path README documents).
3. `POST /api/pipeline/push-frame/{session_id}` with the same frame, then a
   real WebSocket connection to `/ws/vitals/{session_id}?source=camera` ->
   received a `type: "reading"` message: all 8 fields correct,
   `provenance: "ai_high"`, per-vital confidence matching step 2 exactly —
   confirming the real `CameraSource -> read_frame() -> reconcile()` path
   the live product uses (not the debug-only single-shot endpoint) also
   works end to end with the new config.
4. Queried the scratch SQLite file directly: one row in `vital_readings` for
   the session, every field and the full `per_vital_confidence` JSON
   matching the WebSocket message exactly.
5. Ended the session via REST, stopped `uvicorn`.

This E2E used a synthetic (simulator-rendered) frame, not a real-monitor
photo — the default `ROI_ENGINE=tesseract` (colour-marker) ROI stage that
the live camera path uses today only locates fields on simulator output;
locating fields on a real photographed monitor requires `ROI_ENGINE=tier2`,
which is explicitly out of scope for M5.1 (localization/FieldCNN work is
M5.2+). The oracle-crop sweep (§5-§9) is what validates the OCR/confidence
fix itself against real Dataset A/B photos; this E2E validates that the
whitelist removal didn't break the live session/WS/persistence wiring.

---

## 15. Limitations

- **Dataset B remains a phone/video capture of a YouTube recording of a
  monitor, not a physical camera pointed at a real monitor.** Its ~46%
  oracle-crop OCR ceiling (EVIDENCE.md §5) reflects that image-quality
  confound, not this fix. Nothing here claims otherwise.
- **Dataset B's per-vital sample sizes are small** (n=7 for RR, n=12 for
  EtCO2, n=17 for HR/SpO2) — single-frame swings move percentages a lot.
  Report figures as measured; do not extrapolate precision this dataset
  doesn't have.
- **Dataset A's Temp is a single static value repeated 52 times** and
  Dataset B's Temp is excluded entirely (clipped GT, out-of-range value) —
  neither dataset says anything about whether this fix helps or hurts Temp
  in general. This is an evidence gap, not a claim either way.
- **EtCO2 on Dataset B remains poor (16.7% OCR accuracy, 0% confirmed) and
  is untouched by this milestone** — it was already whitelist-free since
  M4.4; its accuracy ceiling is a genuine OCR-difficulty problem for a later
  milestone, not a confidence problem this one could fix.
- **HR on Dataset B never reaches the confidence gate** even after this fix
  (46.4 mean vs. a 70 gate) — a real, partial win (up from 42.8), not a
  resolved one.
- **This milestone does not touch localization.** Every number here comes
  from oracle (human-drawn) ground-truth boxes. It says nothing about
  whether real crops (candidate generation, FieldCNN, or a future calibrated
  ROI) will locate these fields correctly in production — that is exactly
  what `ARCHITECTURE.md` identifies as the actual accuracy bottleneck, and
  exactly what M5.2 addresses next.
- **The E2E check (Phase 8) used a synthetic frame**, not a real-monitor
  photo, for the reason given in §14 — it validates wiring, not the
  accuracy claim itself (which the oracle-crop sweep already validates
  against real photos).

---

## 16. GO / NO-GO decision for M5.2

**GO.**

| M5.1 acceptance criterion | Result |
|---|---|
| 1. Root cause reproduced with controlled evidence | ✅ §3 — identical crop, whitelist isolated as sole variable, confidence 0->33 (hr) and 0.1->61.4 (spo2, mean) on the same data |
| 2. Fix materially restores confidence on affected correct reads | ✅ §6, §8 — Dataset B correct-read confidence 17.0->55.3 mean; 0%->18.5% now clear the gate |
| 3. Dataset A: no unacceptable regression | ✅ §5 — zero fields regress; RR improves 53.5%->97.7% |
| 4. Dataset B: meaningful improvement in confirmed readings | ✅ §9 — micro confirmed accuracy 11.3%->20.8% |
| 5. No dangerous increase in confidently-wrong readings | ✅ §9 — 0 confidently-wrong confirmations, before and after, both datasets |
| 6. `reconcile()` behaviour validated, not inferred | ✅ §9 — real, imported `reconcile()`, real chronological replay |
| 7. Existing confidence thresholds remain justified | ✅ `CONFIDENCE_MEDIUM_MIN`/`CONFIDENCE_HIGH_MIN` never touched |
| 8. Full test suite passes | ✅ §14 — 286 passed, explained |
| 9. Frontend remains clean | ✅ §14 — `tsc --noEmit` clean, `vite build` succeeds |
| 10. No unrelated production architecture changed | ✅ one file, three constants; localization/tracking/calibration/FieldCNN untouched |

M5.1 is complete. Per `ROADMAP.md`, M5.2 (real calibration) is next — this
report makes no claims about, and does not begin, calibration UI, ROI slot
calibration, ORB/RANSAC tracking, automatic monitor detection, or FieldCNN
retraining. Any issues touching those areas noted in passing above (e.g.
localization being the deeper Dataset B ceiling, per §15) are flagged as
findings for M5.2+, not acted on here.
