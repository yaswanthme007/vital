# M5.4 — Multi-Signal Confidence / Temporal Corroboration Report

**Status:** complete, 2026-08-20. Scope: measuring whether signals beyond raw
OCR confidence can safely confirm a value the existing gate refuses, and a
narrowly-scoped, evidence-backed, **default-off** production mechanism for
the one signal the evidence partially supported. Companion documents:
[`ROADMAP.md`](ROADMAP.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
[`EVIDENCE.md`](EVIDENCE.md) ·
[`M5_1_OCR_CONFIDENCE_REPORT.md`](M5_1_OCR_CONFIDENCE_REPORT.md) ·
[`M5_2_REAL_CALIBRATION_REPORT.md`](M5_2_REAL_CALIBRATION_REPORT.md) ·
[`M5_3_LAYOUT_TRACKING_REPORT.md`](M5_3_LAYOUT_TRACKING_REPORT.md).

**No prior report, dataset, or evidence file was modified by this
milestone.** `docs/M5_3_LAYOUT_TRACKING_REPORT.md` is untouched.

---

## 1. Objective

M5.3 closed localization: real Dataset B temporal tracking works, dense
Dataset B tracking materially improves box placement, Dataset A is correctly
inert, and 97% tracking lock was achieved over 269 chronological real
frames. Its own §17 verdict named the remaining binding constraint
explicitly: **OCR confidence / confirmation**, not localization.

M5.4's question, verbatim from its own brief: *can multiple independent
signals safely establish that an OCR value is trustworthy even when raw
Tesseract confidence sits below the existing gate* — **measured first**, not
assumed, and implemented only as far as the measurement supports, with **zero
increase in confidently-wrong confirmations** as the one non-negotiable
gate.

The answer this milestone reaches, stated up front because it governs
everything below: **partial GO**. One candidate signal (temporal
corroboration — the same OCR value repeating across several consecutive
ticks) is real, measurable, and narrowly useful, but a systematic
localization failure mode already known from M5.2/M5.3 (a too-narrow
calibrated box truncating a multi-digit reading) defeats it in exactly the
population it would need to help — discovered specifically because this
milestone's own held-out validation, not its discovery sample, was checked
before shipping. The mechanism is implemented, tested, and shipped
**disabled by default**, with the counter-example that keeps it disabled
documented in the code itself, not just this report.

---

## 2. M5.3 starting point (verified against current code, not assumed)

Re-read before any change, per this milestone's own audit requirement:

| Claim | Verified against |
|---|---|
| 348 backend tests pass | `pytest tests/ simulator/tests/ -q` re-run at the start of this milestone: **348 passed**, matching the M5.3 report exactly |
| Dataset A inert to tracking | `app/eval/tier2_data/m5_3_report/m5_3_frozen_A.json`: `m5_2_static` and `m5_3_tracked` arms identical to 3 decimal places |
| 97.0% dense lock rate | `m5_3_dense_B_tracking.json`: 262/270 locked |
| 11 confidently-wrong on Dataset A, 0 on every Dataset B arm | reproduced by this milestone's own Phase 6 replay (§9) |
| The binding constraint is confidence, not localization | Dataset B tracked-arm OCR accuracy 49-57% while confirmed accuracy stays 12-22% (§9 of the M5.3 report) — reproduced |

Nothing above needed correcting.

---

## 3. Current confidence architecture (Phase 0 trace)

```
camera frame
  -> calibrated ROI (app/pipeline/calibrated_roi.py: make_extractor)
       -> LayoutTracker.track() if a reference frame exists (app/pipeline/layout_tracker.py)
            -> any non-OK status: EVERY field withheld this tick (fail-closed)
       -> crop
  -> OCR (app/pipeline/ocr.py: TesseractEngine.read_vital) -> (value, confidence 0-100)
  -> read_frame() fuses: MIN(classifier_confidence, ocr_confidence) if classifier_confidence
     exists (Tier-2 only; the calibrated path carries none, so this is a no-op today)
  -> reconcile() (app/validation/reconcile.py):
       raw_value is None                          -> "unreadable", HOLD
       not is_in_range(field, raw_value)           -> "implausible_range", HOLD (critical)
       is_jump_rejected(...)                        -> "jump_rejected", HOLD (critical)
       else: confidence_tier(confidence) (rules.py)
         < 70   ai_low     -> "low_confidence", HOLD      <-- M5.4's only new branch is HERE
         70-89  ai_medium  -> "medium_confidence", CONFIRM (flagged)
         >=90   ai_high    -> CONFIRM (clean, unflagged)
  -> confirmed state, flagged entries
  -> alerts (app/alerts/rules.py: check_alerts on the confirmed reading, unmodified)
  -> persistence (app/db/repo.py: save_reading / save_flagged, unmodified)
  -> WebSocket envelope (app/ws/vitals.py: send_loop, additive `tracking` key since M5.3)
```

**Where confirmation depends on confidence.** Exactly one place:
`confidence_tier()` in `app/validation/rules.py`
(`CONFIDENCE_HIGH_MIN=90`, `CONFIDENCE_MEDIUM_MIN=70`), consulted once per
field inside `reconcile()`. This milestone does not touch either constant —
`rules.py` is byte-for-byte unmodified.

**Is tracking quality available at reconcile()'s decision point?** **No.**
`TrackingResult` reaches `app/ws/vitals.py`'s `TrackingState` via the
`on_tracking_result` observer callback and is serialized into the WS
envelope's `tracking` key for the *frontend* — it never reaches
`per_vital_confidence` or `reconcile()`. A tracking failure's only effect on
`reconcile()` is indirect: `calibrated_roi.make_extractor` withholds every
field on any non-OK tracking status, so `raw_value` is `None` that tick,
which `reconcile()` already treats as `"unreadable"`. This indirection turns
out to be exactly what makes M5.4's design safe without any new plumbing
(§7).

**Is temporal history already available safely?** Partially.
`reconcile()` receives `last_confirmed: Dict[str, FieldState]` — the last
**confirmed** value and its timestamp, used only for the jump-limit check.
It does **not** track what raw OCR read on recent ticks that were *held*,
which is exactly the information "has this value repeated N times" needs.
This did not exist anywhere in the codebase before this milestone.

**Existing tests pinning confirmation behaviour**, all read before any
change: `tests/test_reconcile.py`, `tests/test_m4_4_rules_layer.py`,
`tests/test_validation.py`, `tests/test_m5_1_ocr_confidence_restoration.py`,
`tests/test_m5_2_calibration.py`, `tests/test_m5_3_layout_tracking.py`. None
were weakened.

**What M5.3's dense/frozen artifacts already contain**
(`app/eval/tier2_data/m5_3_report/*.json`), inventoried before writing any
new code: per (frame, field) records with OCR confidence, correctness
against ground truth, parse/missing status, and (for the tracked arm)
tracking status/inliers/scale/rotation — everything Phase 1 needed except
genuine temporal (sub-second) sequences with OCR actually run on them, which
existed only as tracking-only records (no OCR) before this milestone (§5).

---

## 4. Signal inventory and what was actually measured

| # | Signal (from the brief) | Measured this milestone? | Where |
|---|---|---|---|
| 1 | OCR confidence | Yes — confidence-bucket tables, real calibrated+tracked path | §6 Part A |
| 2 | OCR parse quality | Indirectly — "missing" rate already tracked in every dataset used | §6 |
| 3 | Tracking lock/status | Structurally, not as a separate reconcile() input (§3, §7) — a lock failure already reaches reconcile() as `raw_value=None` | §7 |
| 4 | Tracking inlier evidence | Reused from M5.3's own measured gates (`MIN_INLIERS=20` etc.); not re-derived | n/a |
| 5 | ROI geometry validity | Unchanged — `check_transformed_rois` (M5.3) already gates this before OCR ever runs | n/a |
| 6 | Temporal consistency (consecutive frames) | Yes — the primary measurement, both sparse-chronological and a new 270-frame real dense OCR run | §6 Parts B/C |
| 7 | Repeated agreement of the same value | Yes — same measurement as #6 | §6 |
| 8 | Per-vital behaviour | Yes — per-vital corroboration counts in §9 | §9 |
| 9 | Value/range validity | Unchanged — `is_in_range`/`is_jump_rejected` run **before** any new logic and cannot be bypassed by it (tested, §10) | §10 |
| 10 | OCR stability across nearby frames | Same as #6/#7 | §6 |

Signals 3-5 were **not** turned into new reconcile() inputs. §7 explains why
that is a deliberate, evidence-based design choice rather than an omission.

---

## 5. Phase 1 — signal predictiveness experiment

**New, eval-only script:** `backend/app/eval/m5_4_signal_predictiveness.py`.
Reused, unmodified, real production code throughout
(`TesseractEngine`, `LayoutTracker`, `calibrated_roi.make_extractor`) —
nothing here is a reimplementation.

**Part A/B — confidence buckets and chronological agreement**, computed
directly from the already-committed M5.3 tracked-arm records (frozen
Dataset A, frozen Dataset B reference `sample_0001`, dense_B_anchors). No
new inference.

**Part C — a genuinely new measurement**: real OCR (production
`TesseractEngine`) run across all 270 chronological frames of the dense_B
recording (200ms spacing, real camera motion), through the real
`LayoutTracker` + `make_extractor`, for HR/SpO2/Temp (the vitals this
recording's own calibration profile covers). M5.3 measured tracking-only
statistics on this recording (lock rate, transform stability); this is the
first time OCR has actually been run across it. Cost: ~190s (Tesseract
dominates, matching M5.1/M5.3's own per-crop latency figures).

**Part D** — the same agreement computation restricted to the
sub-`CONFIDENCE_MEDIUM_MIN` population specifically, since that is the only
population any new signal could possibly help.

### Results

**Confidence buckets** (real calibrated+tracked path, not oracle crops):

| dataset | 0-39 | 40-69 | 70-89 | 90-100 |
|---|---|---|---|---|
| frozen_A | n=3, 100% | n=2, 0% | **n=18, 38.9%** | n=190, 96.3% |
| frozen_B[0001] | n=6, 100% | n=15, 100% | n=3, 100% | n=1, 100% |
| dense_B_anchors | n=7, 85.7% | n=18, 100% | n=2, 100% | n=1, 100% |

**frozen_A's 70-89 bucket reading 38.9% correct is a real anomaly**, root
caused (§8): it is dominated by HR/SpO2 reads that are systematically
**truncated** by a too-narrow calibrated box (`178`→`17`, `100`→`10`), read
confidently and consistently because the crop genuinely, unambiguously
contains only the truncated digits. This is the exact same class of failure
Phase 6 (§9) later found defeating temporal corroboration on Dataset B — the
first hint of it, found in Phase 1 before any production code existed.

**Chronological agreement** (frozen A/B, real per-field OCR sequences):

| dataset | run≥1 | run≥2 | run≥3 | agreeing-but-WRONG occurrences |
|---|---|---|---|---|
| frozen_A | n=68, 83.8% | n=23, 82.6% | n=15, 80.0% | **9** (all HR/SpO2 truncation, confidence 76-95 — already ≥70, already confirmed via the existing gate today) |
| frozen_B[0001] | n=17, 100% | n=4, 100% | n=2, 100% | 0 |
| dense_B_anchors | n=18, 94.4% | n=4, 100% | n=2, 100% | 0 |

**Dense temporal run** (real OCR, 270 frames, 97.0% locked): 14 usable
anchor-vital pairs (many anchor moments have no OCR read at all at
640×360 — the confidence-ceiling problem M5.3 §9 already identified).
Single-frame baseline: 13/13 correct. Agreement run≥2: 11/11 correct.
Run≥3: 10/10. **Zero dangerous cases found in this arm** — but n=9-14 is
too small on its own to trust (§8).

**Sub-gate population (Part D)**, the only population a new signal could
help:

| dataset | all low-confidence | low-conf run≥2 | low-conf run≥3 |
|---|---|---|---|
| frozen_A | n=5, 60% | n=0 | n=0 |
| frozen_B[0001] | n=21, 100% | n=5, 100% | n=2, 100% |
| dense_B_anchors | n=25, 96% | n=9, 100% | n=5, 100% |

At this point (Phase 1 alone), the evidence looked like a clean win for
temporal agreement within the sub-gate band. **§9 shows why that conclusion
was premature.**

---

## 6. Candidate strategies considered

| Strategy | Verdict |
|---|---|
| A. Per-vital confidence thresholds | Not evidence-backed at this milestone's sample sizes — would require re-deriving `CONFIDENCE_MEDIUM_MIN` per vital from data this thin, exactly the "tune until it looks good" the brief forbids. Rejected. |
| B. Temporal confirmation requiring repeated agreement | **Chosen for further evaluation** — see §5, §7, §9. |
| C. Confidence + tracking-quality gating | Structurally already true (§3, §7) without adding a new reconcile() input; adding one anyway would duplicate an existing guarantee for no measured benefit. Rejected as a separate mechanism. |
| D. Confidence + temporal consistency (conjunction) | This **is** what B became once implemented — `is_corroborated()` requires both a confidence floor AND a run length, never repetition alone. |
| E. A bounded confidence score (numeric fusion) | Rejected explicitly (§7): fusing an "effective confidence" upstream of `reconcile()` would let a genuinely low OCR confidence be silently reported as if it cleared the real gate, which is dishonest to the reviewing clinician and a real regression in explainability. A distinct, honestly-labelled acceptance path was chosen instead. |
| F. State machine (candidate → corroborated → confirmed) | This is effectively what shipped: `low_confidence` (candidate) → `temporal_corroboration` (corroborated, still flagged) → the pre-existing `medium_confidence`/clean-accept tiers are untouched. Not built as a literal named state machine because two states plus the existing tiers already express it without new machinery. |
| G. Different policies per vital | Not attempted — n per vital is even smaller than n per dataset; would be pure overfitting. |

`ARCHITECTURE.md`'s own conceptual diagram (`OCR confidence AND
preprocessing agreement AND tracking lock quality AND range+jump AND
temporal consistency`) was evaluated as literally proposed and **not**
implemented as drawn: it implies fusing tracking/temporal signals into one
score before `reconcile()`, which is strategy E, rejected above for the same
honesty reason. §7 details the deviation.

---

## 7. Chosen strategy and why `reconcile()`/`rules.py` were touched

`ROADMAP.md`'s original M5.4 sketch (written before any of this milestone's
evidence existed) named `reconcile.py`/`rules.py` as files that "stay
untouched," with fusion happening in `read_frame.py` instead. That plan is
superseded here, for a reason stated plainly rather than silently deviated
from:

**Fusing an adjusted confidence number upstream (before `reconcile()`) would
require faking a confidence value** the real OCR engine never reported —
e.g. reporting `70` when Tesseract actually said `45`, so the *existing*
`ai_medium` branch fires. That is strictly less explainable than a new,
honestly-labelled reason (`"temporal_corroboration"`) that shows the real,
sub-gate confidence *and* says why it was still accepted. Given this
milestone's explicit requirement that "the new behavior is explainable from
observable signals," honest labelling was chosen over literal adherence to
a pre-evidence plan — the same kind of documented correction M5.3 §3 made to
M5.2's report.

**What was NOT changed:** `rules.py` — zero lines. `CONFIDENCE_MEDIUM_MIN`,
`CONFIDENCE_HIGH_MIN`, `confidence_tier()` are byte-for-byte as M5.1 left
them. The confidence gate itself is not lowered, moved, or bypassed anywhere.

**What was added, and where:**

| File | Change |
|---|---|
| `backend/app/validation/temporal.py` | **new** — `TemporalFieldState`, `observe()`, `is_corroborated()`, `initial_temporal_state()`, the two constants |
| `backend/app/validation/reconcile.py` | **+1 optional parameter** (`temporal_state`, default `None`), **+1 branch** inside the existing `ai_low` case, **+1 reason string**. Return signature (3-tuple) is **unchanged** — 15+ existing call sites across production and every prior eval script were greped and confirmed to 3-tuple-unpack; `temporal_state` is mutated in place instead of returned, so none of them needed touching. |
| `backend/app/ws/vitals.py` | **+1 optional parameter** on `send_loop` (`temporal_state`), **+1 helper** (`_temporal_corroboration_enabled`, env var `TEMPORAL_CORROBORATION`, default `off`) |

**Why bad tracking cannot increase trust (item required by the brief).** No
new plumbing was added from `LayoutTracker`/`TrackingResult` into
`reconcile()` at all. A tracking failure already, structurally, makes
`calibrated_roi.make_extractor` withhold the field (`raw_value=None`);
`temporal.observe()` resets the agreement run to zero on exactly that input
(tested: `test_extraction_failure_mid_run_resets_corroboration`). Bad
tracking cannot silently keep an old run alive.

**Why range/jump checks cannot be bypassed.** The new branch lives *inside*
the existing `else:` block that only executes after `is_in_range` and
`is_jump_rejected` have already passed — structurally identical to where
`ai_medium` already sits. Tested directly:
`test_repeated_implausible_value_is_never_corroborated_into_confirmation`.

**State ownership.** `temporal_state` is a plain `Dict[str,
TemporalFieldState]` (two numbers per field, 8 fields), created fresh per
WebSocket connection by `initial_temporal_state()` inside `send_loop`
exactly like `confirmed_state` and `TrackingState` already are — same
lifetime, same "never survives past this connection" contract, no new
persistence, no cross-session sharing (tested:
`test_session_reset_clears_temporal_evidence`,
`test_different_sessions_do_not_share_confirmation_history`).

---

## 8. Temporal consistency — the dangerous case, explicitly measured

The brief's own worked example (SpO2=98 at low confidence repeating 3
times, versus SpO2=96 repeating 3 times while the truth is 98) was checked
directly, on real data, twice:

**In Phase 1** (§5): zero cases of a sub-gate reading repeating ≥2 times
while wrong, across frozen A, frozen B[sample_0001], and the dense
recording. On the strength of that alone, the constants
`CONFIDENCE_TEMPORAL_FLOOR=40` (the M5.1-established boundary below which
confidence itself carries almost no signal) and
`TEMPORAL_AGREEMENT_MIN_RUN=3` (a conservative margin above the minimally
observed run≥2) were chosen and implemented.

**In Phase 6** (§9), replaying the *same* mechanism against Dataset B's
**second** reference frame — `frozen_B[sample_0011]`, which
`M5_3_LAYOUT_TRACKING_REPORT.md` §9 already flags by name as the harder,
previously-unflattering calibration arm, reported specifically because it
is adversarial, not convenient — the dangerous case appeared for real: HR
truncated `83`/`84`→`8` repeats at confidence 58% and 66%, both inside
`[CONFIDENCE_TEMPORAL_FLOOR, CONFIDENCE_MEDIUM_MIN)`, and gets corroborated
**wrong**. No confidence floor in that band separates it from the genuine
corroborations measured in the very same arm (correct SpO2 reads sit at
44-58.5%, overlapping the wrong HR reads' 58-66% almost entirely) — checked
directly, not assumed, before writing this section.

**Root cause.** Temporal repetition is a valid corroborator only against
**independent, per-tick noise**. A too-narrow calibrated box clipping a
digit is a **systematic** failure: the same wrong crop produces the same
wrong reading every tick, which looks identical to genuine repeated signal
from a mechanism that only measures "did the value repeat." This pipeline
has now produced that exact failure mode **twice**, independently (Dataset
A's own known HR/SpO2 truncation, §5 above and
`M5_3_LAYOUT_TRACKING_REPORT.md` §3; Dataset B[sample_0011]'s HR truncation,
found here) — it is a recurring risk, not a one-off unlucky sample.

**This is why the Phase 6 held-out check exists and why it is reported
honestly rather than argued around.** Discovery-sample evidence supported
the mechanism; held-out evidence, checked before shipping, found a real
counter-example the discovery sample did not contain. No threshold was
adjusted after the fact to make this specific case disappear — see §12.

---

## 9. Phase 6 — real reconcile()-level validation (the decisive measurement)

**New, eval-only script:** `backend/app/eval/m5_4_confirmation_eval.py`.
Replays the identical per-(frame, field) OCR records M5.3 already produced
and validated (the real `m5_3_tracked` arm — real calibrated+tracked crops,
real Tesseract reads) through the real, imported `reconcile()` **twice**:
once exactly as before (`temporal_state=None`) and once with M5.4 enabled
(`temporal_state=initial_temporal_state()`) — the only variable between the
two runs is this milestone's own change. "Confidently-wrong confirmation"
uses the **exact same definition** `m5_2_calibration_eval.run_reconcile_replay`
already established (a tick where the raw OCR read was wrong AND the value
shown as confirmed equals that wrong read AND it doesn't match ground
truth) — reused, not redefined, so these numbers are directly comparable to
every prior milestone's own count.

| dataset | n | baseline conf_acc | baseline CW | **enabled** conf_acc | **enabled CW** | new corroborations (correct / wrong) |
|---|---:|---:|---:|---:|---:|---:|
| frozen_A | 225 | 83.11% | 11 | 83.11% | 11 | 0 / 0 |
| frozen_B[sample_0001] | 51 | 21.57% | 0 | 21.57% | 0 | 1 / 0 |
| **frozen_B[sample_0011]** | 49 | 20.41% | **0** | 20.41% | **2** | **0 / 2** |
| dense_B_anchors | 51 | 11.76% | 0 | **15.69%** | 0 | 4 / 0 |

**frozen_B[sample_0011] shows exactly the regression §8 predicts.** Two
genuinely new confidently-wrong confirmations — HR read as `8` (truncated
from `83`/`84`) at confidence 58% and 66%, corroborated by 3+ repeats,
wrong. **This is a real violation of this milestone's own hard gate** ("zero
increase in confidently-wrong confirmations") on real, held-out data.

Every other arm shows zero regressions, and dense_B_anchors shows a genuine
uplift (11.76%→15.69% confirmed accuracy, entirely from 4 correct SpO2
corroborations, 0 wrong) — the mechanism does what it was designed to do
**when the underlying crop is sound**. The problem is specifically, and
only, when it isn't.

This table is the actual basis for §12's verdict, not a summary of it.

---

## 10. Tests

`backend/tests/test_m5_4_temporal_corroboration.py` — **24 tests**, all
passing:

1. `app.validation.temporal` unit tests (5): run-length reset on missing
   read, reset on value change, increment on repetition, the confidence
   floor + run-length conjunction, fresh-state coverage of every field.
2. Backward compatibility (3): `reconcile()` with `temporal_state=None`
   (the default) is unaffected by unlimited repetition; a normal
   high-confidence correct read is unaffected by merely *passing* a
   `temporal_state`; a high-confidence *wrong* (range-implausible) value is
   not shielded by it.
3. The new path, opted in (9): isolated low-confidence read stays held;
   repeated low-confidence read corroborates after exactly
   `TEMPORAL_AGREEMENT_MIN_RUN`; one tick short still holds; confidence
   below the floor never corroborates regardless of run length; a repeated
   **implausible** (out-of-range) value is never corroborated — range/jump
   checks are never bypassed; a repeated-but-*plausible*-and-possibly-wrong
   value **is** accepted (the documented, deliberate tradeoff — verified as
   always flagged with its real sub-gate confidence visible, never
   disguised as `ai_medium`/`ai_high`); an extraction failure mid-run resets
   the count; NIBP's 3 sub-fields corroborate independently.
4. Session isolation (2): a fresh session carries no memory of a prior
   near-complete run; two concurrent sessions never share state.
5. Alerts (1): a critically-low HR confirmed via corroboration still raises
   the existing, unmodified critical-HR alert.
6. Persistence (1): real `SessionLocal` + real `app.db.repo`, not a mock —
   a corroborated reading's flagged reason round-trips through actual
   SQLite and the reading persists at the corroborated value.
7. Env var + `send_loop` wiring (3): default is off; `on`/`off` both work;
   `send_loop` reproduces byte-identical envelopes with the flag off and
   engages the mechanism with it on, over real `Frame`/`reconcile()`
   objects (not mocked).

`pytest tests/ simulator/tests/ -q`:

| | count |
|---|---:|
| M5.3 baseline | 348 passed |
| **M5.4 final** | **372 passed** |

No existing test was modified, weakened, or deleted.

**Frontend:** `npx tsc --noEmit` — clean. `npx vite build` — succeeds
(`✓ built in 8.84s`); the pre-existing >500 kB chunk warning is unrelated
and unchanged (no frontend file was touched this milestone — the flagged
reason is a plain string the existing `FlaggedReading.frameNote: string`
type already accepts).

---

## 11. Real E2E — real uvicorn, real WebSocket, real SQLite

`backend/app/eval/tier2_data/m5_4_report/m5_4_e2e_script.py` — a real
`uvicorn` subprocess (not `TestClient`) against a scratch SQLite file, real
HTTP, a real WebSocket client, run **twice** (env vars are fixed at process
spawn, so the flag cannot be flipped mid-process): once with
`TEMPORAL_CORROBORATION` unset (the shipped default), once with it
explicitly `on`.

**Method.** Dataset B's real `sample_0009.png` (a real photographed
monitor, not a synthetic render) calibrated on its own ground-truth SpO2
box (measured confidence 47-59% depending on the exact padded crop — always
inside the corroboration band), pushed to the same session 5 times,
interleaved with the WebSocket connection one push at a time (the frame
queue is single-slot/"latest wins" — confirmed by reading
`app/sources/frame_queue.py` before writing the script, not assumed).

**15/15 checks passed:**

- Calibration → Verify (real OCR) → Save → session creation, all real HTTP.
- **Flag off:** every flagged SpO2 tick renders as plain `"...below the
  ai_low threshold..."` — never `"temporal corroboration"` anywhere in 5
  ticks. (SpO2's ground truth, 98, happens to equal
  `DEFAULT_BASELINE["spo2"]`, so the raw confirmed *value* alone cannot
  distinguish "held at the seeded baseline" from "genuinely confirmed" here
  — a live instance of the exact "hold-coincidence" effect
  `M5_1`-`M5_3`'s own reports flag repeatedly. The flagged **reason text**
  is what was actually checked, honestly, rather than a value that would
  have passed for the wrong reason.)
- **Flag on:** at least one real, HTTP-fetched, SQLite-persisted flagged row
  shows `"...accepted via temporal corroboration, flagged for review"` with
  the real 59% confidence visible in the same string.
- A reading row persists to the real scratch SQLite file in both phases.

**Scope note, stated plainly:** no `LayoutTracker`/reference-frame was
attached in this script (calibration only, no tracking engaged) — the
tracking/temporal interaction is already covered at the unit level
(`test_extraction_failure_mid_run_resets_corroboration`), and M5.3's own
E2E already proved tracking-through-WebSocket works; re-proving both
together was judged disproportionate effort for a mechanism this
milestone's own verdict says should not run in production (§12).

---

## 12. GO / NO-GO

| # | Criterion | Result |
|---|---|---|
| 1 | Confirmed accuracy improves materially OR becomes materially safer | ⚠️ Mixed — dense_B_anchors +3.9pp with 0 new CW; frozen_B[0001] flat; frozen_A flat; **frozen_B[0011] introduces 2 new confidently-wrong confirmations** |
| 2 | **No increase in confidently-wrong confirmations** | ❌ **Violated on `frozen_B[sample_0011]`** — the hard gate this milestone named as non-negotiable |
| 3 | Existing critical alerts still work | ✅ §10 — unmodified `check_alerts()`, tested directly against a corroborated critical value |
| 4 | No regression on previously reliable vitals | ✅ the mechanism only ever activates on values the existing gate already holds; every other field/dataset combination is unaffected (§9 table) |
| 5 | Dataset A does not regress | ✅ 0 new corroborations fired on Dataset A at all (its low-confidence population is too small, n=5, for any to occur) — 83.11%/11 CW, identical before and after |
| 6 | Dataset B does not regress | ❌ see #2 |
| 7 | Dense Dataset B evidence supports the new behaviour | ⚠️ Supports it (4/4 correct, 0 wrong) but n is small and this arm did not exercise the harder calibration reference that caused #2 |
| 8 | Tracking failure remains fail-closed | ✅ structurally — a withheld field resets the run (§7, tested) |
| 9 | Session isolation is correct | ✅ tested directly (§10) |
| 10 | Full test suite passes | ✅ 372 passed |
| 11 | Frontend builds | ✅ `tsc`/`vite build` clean |
| 12 | Real-process E2E passes | ✅ 15/15, both flag states |
| 13 | Runtime cost acceptable | ✅ O(1) per field per tick (two numbers), no measurable latency added |
| 14 | Explainable from observable signals | ✅ the entire reason for §7's deviation from `ROADMAP.md`'s original plan |
| 15 | No ground truth required at runtime | ✅ `is_corroborated()` takes only confidence and run length, both derived from OCR output alone |

**Verdict: partial GO.** The infrastructure — `app.validation.temporal`,
the `reconcile()` integration, the tests, the eval harnesses — is real,
correct, isolated, reversible, and does not regress anything when disabled.
**The feature itself is NOT recommended for production use and ships
disabled by default** (`TEMPORAL_CORROBORATION` unset = off,
`app/ws/vitals.py::_temporal_corroboration_enabled`). Criterion 2 is this
milestone's own declared hard gate, and it fails on real, held-out data.
Per this milestone's explicit instruction — *"If an approach improves
confirmed accuracy but increases confidently-wrong confirmations, it is NOT
a GO"* — this is not tuned away. `TEMPORAL_AGREEMENT_MIN_RUN` and
`CONFIDENCE_TEMPORAL_FLOOR` are left at their discovery-sample values,
documented in the source as **not sufficient for safety**, rather than
adjusted post-hoc to make `frozen_B[sample_0011]`'s specific counter-example
disappear — doing that would be fitting two data points, not evidence.

**What remains necessary before this could become a real GO:**

1. **An independent signal that detects a clipped/truncated digit read** —
   e.g. checking whether the recognized digit run touches the crop's own
   left/right edge, which a systematically-too-narrow box would produce
   consistently and an independent per-tick misread would not. This is a
   genuinely new signal this milestone did not have data to design or
   validate ex nihilo; inventing one now, untested, would repeat exactly
   the mistake this milestone's own process was built to avoid.
2. **Fixing the underlying box-width calibration issue** M5.2/M5.3 already
   identified and left open (`M5_3_LAYOUT_TRACKING_REPORT.md` §14: "Dataset
   A's 11 confidently-wrong confirmations are not addressed and cannot be
   by tracking... they need better calibration-box drawing"). Every
   dangerous case found across M5.2, M5.3, and this milestone traces to the
   same root cause. It is a calibration-UX problem, not a confirmation-logic
   problem, and no amount of confirmation-side engineering fixes it.
3. **More real, held-out camera-motion data from a second monitor/camera**
   before trusting run-length thresholds derived from n=5-25 populations at
   all — this milestone's entire evidence base remains, as M5.3 already
   noted of itself, one 54-second recording of one monitor.

---

## 13. Exact files changed

**Backend — new (production):**
- `app/validation/temporal.py`

**Backend — modified (production):**
- `app/validation/reconcile.py` (+1 optional parameter, +1 branch inside the
  existing `ai_low` case, +1 reason string; return signature unchanged)
- `app/ws/vitals.py` (+1 optional `send_loop` parameter, +1 helper function,
  +1 import; `vitals_ws()`'s own signature is unchanged)

**Backend — new (eval-only, read-only against datasets, writes only to
`app/eval/tier2_data/m5_4_report/`):**
- `app/eval/m5_4_signal_predictiveness.py` (Phase 1)
- `app/eval/m5_4_confirmation_eval.py` (Phase 6)
- `app/eval/tier2_data/m5_4_report/m5_4_e2e_script.py` (Phase 6/real E2E)

**Backend — new (tests):**
- `tests/test_m5_4_temporal_corroboration.py` (24 tests)

**Generated (regenerable):** `app/eval/tier2_data/m5_4_report/*.json`
(signal-predictiveness output, confirmation-eval output, dense temporal OCR
records, real-E2E results).

**Verified untouched:** `app/validation/rules.py` (0 lines — confidence
gate constants and `confidence_tier()` unmodified),
`app/pipeline/ocr.py`, `app/pipeline/calibrated_roi.py`,
`app/pipeline/layout_tracker.py`, `app/pipeline/read_frame.py`,
`app/alerts/rules.py`, `app/db/repo.py`, `app/models/calibration.py`, every
frontend file, and every prior report/dataset.

**Production vs eval-only, summarized:** 3 production files touched
(1 new, 2 modified, ~90 net lines including comments); 3 eval-only files
(read-only against datasets, write only under `m5_4_report/`); 1 new test
file. No dataset, no ground-truth file, no prior report was modified.

---

## 14. Test results

```
pytest tests/ simulator/tests/ -q
348 passed  (M5.3 baseline, reproduced at the start of this milestone)
372 passed  (M5.4 final: +24 new, 0 modified, 0 deleted)

npx tsc --noEmit     -> clean
npx vite build       -> succeeds, 8.84s, pre-existing >500kB warning only
```

## 15. Benchmark / evidence results (summary)

| Measurement | Result |
|---|---|
| Phase 1 discovery sample: sub-gate agreement, run≥2/3 | 0 wrong cases (n=5-14 per dataset) |
| Phase 6 held-out validation: same mechanism, harder reference (`sample_0011`) | **2 wrong cases** (HR truncation, confidence 58-66%) |
| dense_B_anchors confirmed accuracy (real OCR, real reconcile) | 11.76% → 15.69%, 0 new confidently-wrong |
| frozen_B[sample_0001] confirmed accuracy | 21.57% → 21.57% (1 corroboration, correct, too small to move the aggregate) |
| frozen_A confirmed accuracy | 83.11% → 83.11% (0 corroborations — its low-confidence population is too small to trigger the mechanism at all) |
| Runtime cost | O(1) per field per tick; no measurable latency in the Phase 6 replay or the real E2E |
| Real E2E | 15/15 checks, both flag states, real uvicorn/WebSocket/SQLite |

## 16. Remaining risks

1. **The shipped-off mechanism is still in the codebase and can be turned
   on** via `TEMPORAL_CORROBORATION=on`. Nothing prevents a future operator
   from doing so without reading this report. The env var's own docstring
   in `app/ws/vitals.py` and the constant's own comment in
   `app/validation/temporal.py` both carry the counter-example inline,
   specifically so this cannot be missed by reading only the code.
2. **The root cause (box-width truncation) is unfixed** and will keep
   producing confidently-wrong confirmations at the existing
   `CONFIDENCE_MEDIUM_MIN` gate regardless of anything in this milestone —
   this was already true before M5.4 and remains M5.2/M5.3's open item, not
   newly discovered.
3. **All held-out evidence remains one recording of one monitor.** A second
   real camera/monitor could easily surface a different failure mode this
   milestone's process was not positioned to find.
4. **`dense_B`'s temporal arm never exercised the harder calibration
   reference** that caused the Phase 6 regression — it is possible (not
   measured) that a comparable dense recording calibrated the harder way
   would show the same danger at higher volume than the 17-frame sparse
   arm did.

---

## 17. Limitations (stated, not hidden)

- This milestone's headline result is a **documented NO-GO for enabling**
  a real, implemented mechanism — not a confirmed win. That is reported as
  measured, per the brief's own explicit instruction not to manufacture a
  positive result.
- Every quantitative claim above rests on n=2-51 per dataset arm; nothing
  here should be read as more precise than that.
- No new signal (digit-edge-touching, or any other localization-quality
  check) was designed or implemented — §12 names it as necessary future
  work rather than attempting it without evidence to validate it against.
- Per-vital policy differences (strategy G, §6) were not attempted for the
  same small-n reason.

---

## 18. Rollback procedure

1. **Already the default.** `TEMPORAL_CORROBORATION` is unset in every
   environment unless explicitly set to `on` — no action is required to
   keep the pre-M5.4 behaviour running.
2. **If it was ever set to `on` anywhere:** remove the environment variable
   (or set it to anything other than `on`) and restart the process; the
   next `send_loop` invocation passes `temporal_state=None` into
   `reconcile()` and behaviour reverts to byte-identical pre-M5.4.
3. **Full revert:** delete the `temporal_state` parameter and its call site
   in `app/ws/vitals.py::send_loop`, and the corresponding parameter +
   branch in `app/validation/reconcile.py::reconcile`. Both are additive;
   removing them cannot affect any other code path, since every other
   caller of `reconcile()` never passed the parameter to begin with.
4. `app/validation/temporal.py` is a new, self-contained file; deleting it
   is safe once the two call sites above are reverted.

---

## 19. What M5.5 inherits

- The confidence-ceiling problem M5.3 identified (§9: 640×360 crops cap OCR
  confidence near 51 against a 70 gate) is **still the dominant constraint**
  on Dataset B confirmed accuracy, and this milestone does not address it.
- The box-width-truncation root cause (§8, §12) is the single most
  consequential open item across M5.2, M5.3, and this milestone. It is a
  calibration-UX problem (M5.2's Verify step should visibly warn when a
  drawn box's digit run touches its own edge), not a confirmation-logic
  problem, and is recommended as the next milestone's actual target rather
  than further confidence-fusion work.
- `app/eval/m5_4_signal_predictiveness.py` and
  `app/eval/m5_4_confirmation_eval.py` are committed, reproducible, and
  reusable against any future dense-frame dataset a second monitor/camera
  would provide.
