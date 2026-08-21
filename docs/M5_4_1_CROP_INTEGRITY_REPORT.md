# M5.4.1 — Crop Integrity / Temporal Safety Report

**Status:** complete, 2026-08-20. Scope: root-causing and fixing the exact
held-out safety failure M5.4 found (a too-narrow calibrated/tracked crop
letting temporal corroboration confirm a truncated digit read), with the
hard requirement that confidently-wrong confirmations must not increase
anywhere relative to M5.4 with temporal corroboration disabled. Companion
documents: [`ROADMAP.md`](ROADMAP.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md)
· [`EVIDENCE.md`](EVIDENCE.md) ·
[`M5_2_REAL_CALIBRATION_REPORT.md`](M5_2_REAL_CALIBRATION_REPORT.md) ·
[`M5_3_LAYOUT_TRACKING_REPORT.md`](M5_3_LAYOUT_TRACKING_REPORT.md) ·
[`M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md`](M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md)
(the milestone this one fixes).

**No prior report, dataset, or evidence file was modified.**
`docs/M5_4_MULTI_SIGNAL_CONFIDENCE_REPORT.md` is untouched.

---

## 1. The failure this milestone addresses

M5.4 shipped `app.validation.temporal` (temporal corroboration: a value
below `CONFIDENCE_MEDIUM_MIN` can still be confirmed if the SAME value
repeats ≥3 consecutive ticks at ≥40% confidence). Its own held-out
validation (Phase 6, replaying real M5.3 tracked-arm OCR records through
the real `reconcile()`) found a genuine counter-example on
`frozen_B[sample_0011]`: HR truncated `83`/`84` → `8` repeated at confidence
58% and 66%, wrongly confirmed twice. M5.4 shipped the mechanism **off by
default** rather than tune the failure away, and named the fix this
milestone had to build: *"an independent signal that detects a
clipped/truncated digit read... before repetition can be trusted."*

---

## 2. Phase 1 — root cause, established against the real crops

Inspected directly (`/tmp` diagnostic scripts against
`app/eval/tier2_data/external_monitor_B/`, not assumed from the prior
report's prose):

1. **Is the ROI physically clipping a digit? Yes, confirmed visually.**
   `sample_0006`'s ground-truth HR crop shows `83`; the tracked crop for
   the identical frame shows only the leading `8`, the trailing `3` cut off
   at the crop's right edge. `sample_0007` (`84`) shows the same pattern.

2. **Where does the clipping come from?** Two compounding causes, both
   measured:
   - The calibration reference (`frozen_B[sample_0011]`) box for HR is
     `(1310, 70, 140, 120)` px — already tight around its own moment's `86`
     (visually confirmed: the `6` touches the box's right edge on the
     reference frame itself). This is the same **too-narrow calibration
     box** class `M5_2_REAL_CALIBRATION_REPORT.md` §9/§16 and
     `M5_3_LAYOUT_TRACKING_REPORT.md` §3/§14 already flagged as an open,
     unaddressed root cause.
   - `sample_0011` is a **zoomed-in** framing relative to `sample_0004`-
     `0007` (measured tracker scale ≈0.56-0.60 — the real `LayoutTracker`
     transform, not assumed). A similarity transform correctly,
     geometrically preserves the calibrated box's *proportions* when
     re-anchoring — so a box that was already tight in the zoomed
     reference gets proportionally **tighter still** once tracked down to
     0.56× onto a wider framing: `140px → ~81px`, while the same two
     digits actually span `~160px` on that wide framing (measured from its
     own ground-truth box). Tracking is not malfunctioning here — it is
     faithfully relocating an already-too-narrow box.
3. **Does the same issue occur on RR/SpO2?** RR/SpO2 truncation on this
   exact reference (`sample_0011`) also occurs (e.g. SpO2 `100`→`4`/`1`),
   but those never reach `reconcile()`'s confirmation path because
   `RANGE_BOUNDS["spo2"] = (50, 100)` already rejects `4`/`1` outright —
   `is_in_range()` runs **before** confidence/temporal is ever consulted.
   Dataset A's own known truncation class (`178`→`17`, `M5_3` §3) is a
   **different** mechanism (no camera motion at all — a pure too-narrow
   static box) that reads at 61-95% confidence, mostly **above**
   `CONFIDENCE_MEDIUM_MIN=70` — those cases are accepted through the
   *existing*, unmodified `ai_medium`/`ai_high` tiers, never through
   temporal corroboration, and are explicitly out of this milestone's scope
   (see §9, Limitations).
4. **Is there an existing geometric signal that detects it?** Not before
   this milestone. `TrackingResult.scale`/inlier counts exist but describe
   transform *quality*, not crop *content* — a geometrically perfect,
   high-confidence tracking lock still produces this failure, because
   tracking did its job correctly on a box that was wrong to begin with.
5. **Can the crop safely be expanded, and would that risk pulling in
   neighbouring UI?** Evaluated directly (§3) — see the answer below:
   partial, unreliable, and not chosen as the primary fix.
6. **Is there a reliable distinction between a legitimate single-digit
   value and a clipped multi-digit one?** This is the crux question,
   answered empirically in §3.

### 2.1 What Tesseract itself saw — the actual signal

Re-running the real, unmodified `TesseractEngine` (`--psm 8`,
whitelist-free, per M5.1) on the real truncated crops with full
`image_to_data` output (not just the parsed value) shows Tesseract does
**not** silently fail to see the clipped remainder — it recognizes
*something*, just not a clean digit:

| frame | tracked crop | OCR **raw text** | value the regex parses | ground truth |
|---|---|---|---:|---:|
| `sample_0004` | 81×71 px | `"8g"` | 8 | 82 |
| `sample_0005` | 80×69 px | `"8g"` | 8 | 81 |
| `sample_0006` | 82×72 px | `"8B"` | 8 | 83 |
| `sample_0007` | 82×71 px | `"8B"`/`"8g"` | 8 | 84 |

`app.pipeline.ocr._read_scalar`'s digit-extracting regex correctly keeps
only the leading digit run (`"8"`) and silently discards the rest — the
right thing for a *single* tick (a bare `8` is a plausible, if wrong, HR
value; `reconcile()`'s range check cannot tell the difference on one
sample) but exactly the information a *repeated-read* consumer like
temporal corroboration needed and did not have.

---

## 3. Phase 2 — candidates evaluated against real data

| Candidate | Verdict | Evidence |
|---|---|---|
| **A. Crop-boundary/margin detection** (raw thresholded-ink pixel-column margin to the crop's own edge) | **Rejected.** | Measured across 82 genuinely-correct frozen_A/frozen_B[0011] reads: **many correct reads also have ink flush against one or both edges** (calibrated/tracked boxes are frequently drawn or tracked tight by design — e.g. a correct RR="12" box shows `left=0, right=21`; a correct SpO2="86" shows `left=0, right=0`). A tight-but-correct box and a tight-because-truncated box are visually indistinguishable from raw ink geometry alone — this signal would hold far more good reads than it catches bad ones. |
| **B. Conservative horizontal ROI expansion** | **Not chosen as the primary fix.** | Re-testing with production's existing `WIDTH_SAFETY_PAD_FRACTION=0.20` (M5.2) applied to the same profile: the tracked crop for `sample_0006`/`0007` visibly *does* contain both digits ("83") to a human eye after padding, but Tesseract still returns **no parseable value at all** on all 4 padded ticks (`None`, not a wrong digit) — safe (routes to "unreadable"→HOLD) but not reliably a *correct* read either, and only tested on this one case. Padding is real, already-shipped, partial protection (confirmed live in §7's E2E) but not sufficient on its own — a wider pad also risks pulling in neighbouring UI (M5.2 §9's own measured 71.5%→37.6% regression from 50% padding), and there is no evidence-backed per-tick pad amount that closes this specific case reliably. |
| **C. Digit-content boundary detection** | Same conclusion as A — rejected for the same false-positive reason when implemented as raw ink geometry. |
| **D. OCR text geometry / bounding-box evidence** | **Chosen**, implemented as raw-text comparison rather than pixel bounding boxes (§4) — reuses evidence Tesseract already computes (`image_to_data`'s recognized text) instead of re-deriving glyph shape independently. Measured across 180 real (dataset, field) reads: catches **8/8** of the real target failure's ticks; also flags **21/107** genuinely correct reads (false positives — see §5 for the accepted cost) and **misses** Dataset A's `178→17`-class truncations (which don't need it — see §2.3 point 3). |
| **E. Digit-count/format consistency** | Not implemented standalone — informed the decision that legitimate single/short values must not be penalized purely for brevity (tested explicitly, §6). |
| **F. Temporal transition anomaly detection** | Not implemented — would require a baseline "expected width" per field/monitor this project has no evidence to derive safely (same caution `M5_4`'s own report gave per-vital policies, §6/§17 there). |
| **G. Combination** | **Shipped design is B (already existing, independent) + D (this milestone), stacked** — D gates temporal corroboration specifically; B remains an independent, unmodified layer that already helps on the live path (§7). |

**Do not choose a technique merely because it improves headline accuracy** —
per this instruction, candidate D was deliberately *not* tuned to maximize
corroboration count; §5 reports its real, non-trivial false-positive cost
rather than hiding it.

---

## 4. Phase 3 — the implemented fix

**New file:** `backend/app/validation/crop_integrity.py` — one pure
function, `has_residual_content(raw_text, matched_text) -> bool`: true when
the OCR engine's raw recognized text contains characters beyond the
substring that was actually parsed into a value (e.g. `"8g"` vs matched
`"8"`). No model, no threshold to tune, no network, deterministic.

**Extended, not changed: `app/pipeline/ocr.py`.** `OcrEngine.read_vital`'s
existing contract (`Tuple[OcrValue, float]`) is **untouched** — every
existing 2-tuple-unpacking call site (`read_frame.py`, every M4/M5 eval
script, every test) is unaffected, verified by the full test suite (§8). A
new, non-abstract `read_vital_with_diagnostics()` (default implementation:
delegates to `read_vital`, reports empty diagnostics — safe for engines
that can't produce this evidence, e.g. Tier-2's `OnnxDigitEngine`) is added
to the base class; `TesseractEngine` overrides it, sharing the *same*
internal dispatch (`_read_vital_diag`) `read_vital` itself now delegates
to, so there is no risk of the two ever reading a crop differently. **No
PSM, whitelist, or routing constant changed** — `_DIGIT_CONFIG`,
`_PSM10_VITALS`, `_NIBP_CONFIG` etc. are byte-for-byte as M5.1 left them.

**`app/validation/temporal.py`:** `TemporalFieldState` gains one field,
`clean_run: bool = True`. `observe()` gains an optional `crop_suspicious`
parameter (default `False` — every pre-M5.4.1 caller is unaffected) and
ANDs it cumulatively into `clean_run` across the run. `is_corroborated()`
additionally requires `state.clean_run`. **Whole-run policy, not
current-tick-only** — see §5 for the two policies compared and why the
more conservative one shipped.

**`app/validation/reconcile.py`:** one new optional parameter,
`per_vital_crop_suspicious: Optional[Dict[str, bool]] = None`, passed
straight through to `observe()`. Default `None` reproduces M5.4's exact
behaviour. `rules.py` — **0 lines changed.** The range/jump checks still
run, unconditionally, before this branch is ever reached; the new
parameter lives entirely inside the pre-existing `ai_low` branch, exactly
where `temporal_state` itself was added in M5.4.

**Plumbing (opt-in dict pattern, matching M5.4's own `temporal_state`
idiom):** `read_frame()` gains an optional `crop_integrity` output dict
(mutated in place, `None` by default — zero cost unless a caller opts in);
`Frame` (the source→WS envelope type) gains a `crop_suspicious: Dict[str,
bool]` field, defaulting to `{}` for every non-camera source;
`CameraSource` is the only source that populates it; `send_loop` passes it
into `reconcile()` unconditionally (cost-free when the dict is empty).

**Measured cost: zero.** `read_vital_with_diagnostics` calls the exact same
internal OCR dispatch as `read_vital` — no second Tesseract invocation.
30-call microbenchmark on a real crop: `read_vital` 256.34 ms/call,
`read_vital_with_diagnostics` 255.83 ms/call — within noise.

---

## 5. Design decision: whole-run vs. current-tick-only

Two policies were implemented and evaluated against the same real data
before choosing:

| Policy | frozen_B[0011] CW | dense_B_anchors correct corroborations preserved |
|---|---:|---:|
| **current-tick-only** (only this tick's evidence gates) | 0 | 1 of 4 |
| **whole-run** (any dirty tick in the run taints it, shipped) | 0 | 0 of 4 |

Both eliminate the target failure equally (0 confidently-wrong either way,
on every dataset). They differ only in cost: current-tick-only would have
recovered one additional genuine correct corroboration
(`dense_B_anchors/anchor_005749`, SpO2=98) that whole-run also holds.

**Whole-run was chosen** — the more conservative option, for a stated
reason, not by default: the failure this guards against is a **systematic**
defect (the same wrong crop, read the same wrong way, every tick — M5.4's
own root-cause language, §12 there). A run that showed the symptom even
once is not trustworthy evidence merely because a later tick happened not
to reproduce it (OCR noise on whether the clipped remainder forms a
recognizable extra glyph is itself somewhat tick-to-tick variable — see
§2.1's `"8g"`/`"8B"` variation across otherwise-identical frames). This
matches the project's own stated bias (`ARCHITECTURE.md`): a false hold is
an acceptable cost; a confidently-wrong confirmation is not. The one-case
cost is reported honestly (§6) rather than hidden.

---

## 6. Phase 4 — regression tests

`backend/tests/test_m5_4_1_crop_integrity.py` — **25 tests**, all real
production code, no reimplementation:

1. **`has_residual_content` unit tests (5).**
2. **`observe`/`is_corroborated` clean-run bookkeeping (6):** marks a run
   unclean on a suspicious tick; **stays unclean even once later ticks are
   clean** (the whole-run policy, directly tested); resets on value change
   and on missing read; `is_corroborated` requires `clean_run` in addition
   to floor + length.
3. **reconcile()-level synthetic regression (12), the exact reported shape**
   (`83`/`84`→`8`, confidence 58-66%) as **realistic test data, not a
   mechanism** — nothing in production branches on the literal values 8,
   83, or 84:
   - `test_old_behaviour_reproduces_the_truncated_value_being_confirmed` —
     proves the **pre-fix** mechanism (temporal on, no crop-integrity
     evidence) confirms `8`, reproducing M5.4's own regression.
   - `test_new_behaviour_refuses_to_confirm_the_truncated_value` — same
     ticks, `crop_suspicious=True` wired in → held, never confirmed.
   - Legitimate single-digit HR (a real, clean, low-confidence `8`) still
     corroborates — the fix is not a blanket "never trust short HR" rule.
   - Legitimate two-digit value with a clean run corroborates normally.
   - Clipped two-digit (`178→17`-shape) and three-digit truncations held.
   - RR and SpO2 equivalents held (the gate generalizes past HR).
   - A totally ordinary clean run is unaffected (no-op regression guard).
   - Tracking-failure-mid-run resets **both** `run_length` and `clean_run`
     (a prior dirty run cannot taint a fresh one).
   - Session reset clears `clean_run` along with everything else.
   - Omitting `per_vital_crop_suspicious` entirely reproduces M5.4's exact
     original behaviour (backward compatibility).
4. **Real-data end-to-end regression (1),
   `test_real_sample_0011_hr_truncation_is_prevented_end_to_end`:** the
   actual `sample_0004`-`0007` PNGs, the actual `frozen_B[sample_0011]`
   calibration profile, the real `LayoutTracker`, the real `TesseractEngine`,
   the real `reconcile()`/`temporal` pipeline — no synthetic stand-ins.
   Asserts the dataset itself still reproduces ground truth `[82, 81, 83,
   84]`, that the OLD mechanism (no crop-integrity evidence) confirms `8`,
   and that the NEW one does not — self-skips (rather than false-passing)
   if a different Tesseract build ever reads these exact crops differently.

**Full suite:** `pytest tests/ simulator/tests/ -q` → **397 passed**
(372 M5.4 baseline + 25 new, 0 modified beyond one drift-guard update, 0
deleted). One pre-existing test
(`test_m5_1_ocr_confidence_restoration.py::test_hr_confidence_no_longer_collapses_on_known_case`)
reached into `TesseractEngine._read_scalar`'s private 3rd return value
(now `OcrDiagnostics`, was previously absent) and was updated to
3-tuple-unpack, the same kind of drift-guard update M5.1/M5.2/M5.3 each
made to their own pinned-shape tests — not weakened, just updated to match
the new (additive) private signature.

`npx tsc --noEmit` — clean (no frontend file touched this milestone).

---

## 7. Phase 5 — full validation

### 7.1 The exact held-out failure

`app/eval/m5_4_1_crop_integrity_eval.py` (new, committed, reproducible) —
re-runs REAL OCR (not a replay of stored numbers) across all 4 of M5.4's
own arms, through the real `calibrated_roi`+`LayoutTracker` tracked path,
comparing three `reconcile()` arms on **identical** per-tick data:
`baseline` (temporal off — the pre-M5.4 floor), `m5_4` (temporal on, no
crop-integrity evidence — reproduces M5.4's own numbers), `m5_4_1`
(temporal on, crop-integrity evidence wired in — this milestone).

| dataset | n | suspicious ticks | baseline CW | m5_4 CW | **m5_4_1 CW** | m5_4 corrob. (correct/wrong) | m5_4_1 corrob. (correct/wrong) |
|---|---:|---:|---:|---:|---:|---:|---:|
| frozen_A | 225 | 52 | 11 | 11 | **11** | 0/0 | 0/0 |
| frozen_B[sample_0001] | 51 | 14 | 0 | 0 | **0** | 1/0 | 0/0 |
| **frozen_B[sample_0011]** | 49 | 13 | 0 | **2** | **0** | 0/2 | 0/0 |
| dense_B_anchors | 51 | 17 | 0 | 0 | **0** | 4/0 | 0/0 |

**The exact target failure is eliminated: `frozen_B[sample_0011]`'s
confidently-wrong count returns to 0, matching the temporal-off baseline.**
No dataset shows a confidently-wrong count above its own `baseline`
value — the hard gate ("must not increase confidently-wrong confirmations
relative to M5.4 with temporal corroboration disabled") is met on every
arm, not just the target one.

**Honest cost, not hidden:** the crop-integrity gate also blocks every
*correct* corroboration the ungated M5.4 mechanism had found in this
evidence base — `frozen_B[sample_0001]`'s one correct SpO2 corroboration
and all 4 of `dense_B_anchors`' correct SpO2 corroborations are now held
instead of confirmed (all 5 had `crop_suspicious=True` on the tick that
would have fired corroboration — real OCR noise, e.g. raw text `"98&"` /
`"100|"`, not a truncation). **Confirmed accuracy on `dense_B_anchors`
therefore reverts from M5.4's 15.69% back to the temporal-off baseline's
11.76%.** In this specific, small evidence base (n=51 with 1-4 real
corroboration opportunities per arm), the crop-integrity-gated mechanism
provides **zero net accuracy benefit over having temporal corroboration
off entirely**, while being materially safer than M5.4's ungated version.
This is reported as measured, not minimized — see §9.

### 7.2 Full backend test suite

`pytest tests/ simulator/tests/ -q` → **397 passed**, 0 failed (§6).

### 7.3 Real E2E — real uvicorn, real WebSocket, real SQLite

`backend/app/eval/tier2_data/m5_4_1_report/m5_4_1_e2e_script.py` — a real
`uvicorn` subprocess (not `TestClient`), `TEMPORAL_CORROBORATION=on`
explicitly, against a scratch SQLite file. Calibrates on the **real,
literal `sample_0011` HR box** (the actual too-narrow box that caused the
failure), attaches `sample_0011` as the tracking reference frame (real
`LayoutTracker` engages), then pushes the real `sample_0004`-`0007` PNGs
one at a time over the real WebSocket transport — the exact real
re-framing that shrinks the tracked box.

**10/10 checks passed.** HR is never confirmed as `8` across all 4 real
pushes (`[75, 75, 75, 75]`, held at baseline throughout); no flagged HR
entry ever says "temporal corroboration"; every flagged reason is one of
the safe non-confirming classes; the scratch SQLite file agrees with the
WebSocket envelopes exactly.

**A genuine, additional finding surfaced by this specific live run, stated
plainly:** through the real `POST /api/calibration` endpoint (unlike this
milestone's own unit-level regression test and
`m5_4_1_crop_integrity_eval.py`, both of which use the literal,
unpadded box to match M5.4's own methodology), `save_profile()` applies
M5.2's pre-existing `WIDTH_SAFETY_PAD_FRACTION=0.20` before persisting.
That changes the tracked crop's exact geometry enough that on this real
live run, tick 1 is instead caught by the **pre-existing jump-limit check**
(a raw `8` is an implausible jump from the seeded baseline of 75) and
ticks 2-4 come back **OCR-unreadable** rather than a repeatable clean `8`
— so the corroborating run never even accumulates 3 repeats. This is a
**real, independent, already-shipped** layer of protection (M5.2's width
pad + the pre-existing jump-limit check), not a substitute for this
milestone's fix — the isolated real-data regression test (§6, item 4)
reproduces the crop-integrity mechanism engaging directly, on the literal
box, over the same real `reconcile()`/`temporal` code path this live run
also exercises. Both layers working together (as they do in real
production) is why the E2E outcome is unconditionally safe regardless of
which one happens to fire first on a given box/framing combination.

### 7.4 Latency

Measured (§4): zero added OCR cost (256.34 ms vs. 255.83 ms/call, within
noise, 30-call microbenchmark on a real crop). `crop_integrity`'s own work
(`has_residual_content`, a string comparison) and `temporal.observe`'s
extra boolean AND are both O(1) and unmeasurable against Tesseract's
~250ms/field. No change to `LayoutTracker`, `calibrated_roi`'s crop
extraction, or any OCR routing/config — end-to-end per-frame latency is
unaffected.

### 7.5 Alerts, tracking-failure fail-closed, session isolation

- **Alerts:** unmodified — `check_alerts()` operates on the reconciled
  `reading` dict regardless of which path (`medium_confidence`,
  `temporal_corroboration`, or held-baseline) produced it. Covered directly
  by M5.4's own still-passing
  `test_alerts_still_fire_for_a_temporally_corroborated_critical_value`
  (unmodified, still green in the 397).
- **Tracking failure remains fail-closed:** `test_tracking_failure_mid_run_resets_clean_run_along_with_the_count`
  (new, §6) extends M5.4's existing guarantee — a withheld tick resets
  **both** `run_length` and `clean_run`, so neither a stale count nor a
  stale taint can survive a real extraction failure.
- **Session isolation:** `test_session_reset_clears_clean_run_state` (new,
  §6) — a fresh `initial_temporal_state()` carries no `clean_run` memory
  from a prior connection, matching M5.4's existing per-connection
  contract exactly.

---

## 8. Confidently-wrong confirmations — the hard gate, explicitly

| | baseline (temporal off) | M5.4 (ungated) | **M5.4.1 (this milestone)** |
|---|---:|---:|---:|
| frozen_A | 11 | 11 | **11** |
| frozen_B[sample_0001] | 0 | 0 | **0** |
| frozen_B[sample_0011] | 0 | **2** | **0** |
| dense_B_anchors | 0 | 0 | **0** |

**Zero increase anywhere, on every arm, versus the temporal-off baseline.
The one arm M5.4 regressed on is fully closed.**

---

## 9. Limitations, stated plainly

- **This milestone provides zero net accuracy benefit over temporal
  corroboration being off, on the specific evidence available.** The
  crop-integrity gate is deliberately conservative (whole-run policy, §5)
  and, on this small real-data sample, ends up blocking every genuine
  correct corroboration alongside the dangerous one. A different, larger,
  or less OCR-noisy dataset might show a different balance; this is not
  claimed to generalize.
- **Dataset A's 11 confidently-wrong confirmations remain unaddressed by
  this milestone, by design.** Those cases clear the *existing*
  `ai_medium`/`ai_high` confidence tiers directly (61-95% confidence) and
  never reach the `ai_low`/temporal-corroboration branch this milestone
  touches — `rules.py` and the medium/high acceptance paths are explicitly
  out of scope (`Do NOT weaken reconcile/rules`) and were not changed.
  They remain M5.2/M5.3's open item: better calibration-box drawing (a
  live-preview truncation warning at DRAW time), not a confirmation-logic
  fix.
- **`has_residual_content` is a moderate-precision signal, not a perfect
  one.** Measured false-positive rate on real correct reads: 21/107 in the
  broader signal-validation pass (§3, candidate D) — real OCR noise
  (stray characters like `"&"`, `"|"`, `"fi"`, `"~"`) sometimes looks
  identical to a genuine truncation artifact from this signal's point of
  view alone. The whole-run policy (§5) is the mitigation for repeated
  false triggering staying safe (a false hold, never a false confirm), not
  a claim that the signal itself is clean.
- **`has_residual_content` does not catch every truncation class.**
  Dataset A's `178→17` shape produces *clean* all-digit OCR text (no
  trailing garbage character) — but as established in §2.3, those cases
  never reach this milestone's gate at all (they clear the confidence
  gate directly), so this gap does not weaken the hard safety requirement
  this milestone is scoped to.
- **All held-out evidence remains one recording of one monitor** — M5.4's
  own §12/§16 limitation is unchanged by this milestone. A second real
  camera/monitor could surface a different truncation shape this process
  was not positioned to find.
- **`TEMPORAL_CORROBORATION` default is left OFF.** This milestone closes
  the *specific* counter-example M5.4 found, but M5.4's other stated
  precondition for a real GO — "more real, held-out camera-motion data
  from a second monitor/camera... before trusting run-length thresholds
  derived from n=5-25 populations at all" — is unchanged and unaddressed
  by this milestone. See §11 for the explicit recommendation this leaves.

---

## 10. Exact files changed

**Backend — new (production):**
- `app/validation/crop_integrity.py`

**Backend — modified (production):**
- `app/pipeline/ocr.py` (+`OcrDiagnostics`, +`read_vital_with_diagnostics`
  on `OcrEngine`/`TesseractEngine`; `read_vital`'s contract and every
  existing config/routing constant unchanged — internal dispatch
  refactored into one shared `_read_vital_diag`, not duplicated)
- `app/pipeline/read_frame.py` (+1 optional `crop_integrity` output param,
  `None` default — zero behaviour/cost change for every caller that omits it)
- `app/sources/base.py` (`Frame` +1 additive field, `crop_suspicious: Dict[str, bool] = {}`)
- `app/sources/camera.py` (`CameraSource._read` populates `crop_integrity`
  and threads it onto the returned `Frame`)
- `app/validation/temporal.py` (`TemporalFieldState` +1 field `clean_run`;
  `observe()` +1 optional param defaulting to backward-compatible
  behaviour; `is_corroborated()` +1 additional required condition)
- `app/validation/reconcile.py` (+1 optional parameter
  `per_vital_crop_suspicious`, threaded into the existing
  `temporal_signal.observe()` call; return signature unchanged; `rules.py`
  untouched — 0 lines)
- `app/ws/vitals.py` (`send_loop` passes `frame.crop_suspicious` into
  `reconcile()`; `vitals_ws()`'s own signature unchanged)

**Backend — new (eval-only, read-only against datasets, writes only under
`app/eval/tier2_data/m5_4_1_report/`):**
- `app/eval/m5_4_1_crop_integrity_eval.py` (Phase 4/5 — real OCR re-run +
  real reconcile() three-arm comparison)
- `app/eval/tier2_data/m5_4_1_report/m5_4_1_e2e_script.py` (Phase 5 real E2E)

**Backend — new (tests):**
- `tests/test_m5_4_1_crop_integrity.py` (25 tests)

**Backend — modified (tests, drift-guard only):**
- `tests/test_m5_1_ocr_confidence_restoration.py` (1 test updated to
  3-tuple-unpack `TesseractEngine._read_scalar`'s now-extended private
  return shape — same kind of update M5.1/M5.2/M5.3 each made to their own
  pinned-shape guards; the test's actual assertions are unchanged)

**Generated (regenerable):**
`app/eval/tier2_data/m5_4_1_report/m5_4_1_crop_integrity_eval.json`,
`m5_4_1_e2e_results.json`.

**Verified untouched:** `app/validation/rules.py` (0 lines),
`app/pipeline/calibrated_roi.py`, `app/pipeline/layout_tracker.py`,
`app/pipeline/ocr.py`'s PSM/whitelist/routing constants
(`_DIGIT_CONFIG`, `_DIGIT_PSM10_CONFIG`, `_PSM10_VITALS`, `_NIBP_CONFIG`,
`_ETCO2_CONFIG`, `_DECIMAL_CONFIG`), `app/alerts/rules.py`, `app/db/repo.py`,
`app/models/calibration.py`, every frontend file, every prior report and
dataset.

**Production vs eval-only, summarized:** 7 production files touched (1 new,
6 modified, all additive — no existing parameter removed, no existing
return signature changed, no existing config/threshold constant altered);
2 eval-only files (read-only against datasets, write only under
`m5_4_1_report/`); 1 new test file, 1 existing test file updated for a
private-signature drift only.

---

## 11. GO / NO-GO

| # | Criterion | Result |
|---|---|---|
| 1 | Exact `83`/`84`→`8` failure prevented | ✅ §7.1 (2→0 CW on frozen_B[sample_0011]) and §7.3 (real E2E, real transport) |
| 2 | No increase in confidently-wrong confirmations, any dataset | ✅ §8 — 0 increase everywhere vs. the temporal-off baseline |
| 3 | No material regression on reliable vitals | ✅ frozen_A (the no-truncation, high-reliability control) unchanged: 11→11 CW, 83.11%→83.11% confirmed accuracy, 0 corroborations before and after |
| 4 | Existing alerts still work | ✅ §7.5, unmodified `check_alerts()`, existing test still green |
| 5 | Tracking failure remains fail-closed | ✅ §7.5, new test extends the existing guarantee to `clean_run` |
| 6 | Session isolation remains correct | ✅ §7.5, new test |
| 7 | Full test suite passes | ✅ §6/§7.2 — 397 passed |
| 8 | Real E2E passes | ✅ §7.3 — 10/10, real uvicorn/WS/SQLite, real failure-reproducing crops |
| 9 | Latency remains acceptable | ✅ §7.4 — no measurable change |

**Verdict: GO for M5.4.1 as scoped.** The exact reported failure is
demonstrably prevented — synthetically (§6), against the real crops in
isolation (§6 item 4), and end-to-end over the real live transport (§7.3)
— with zero increase in confidently-wrong confirmations on any evaluated
arm and no regression on the one dataset with no truncation defect
(frozen_A). The full backend test suite and a real E2E both pass.

**What this GO does NOT claim.** It does not claim the crop-integrity gate
makes temporal corroboration a net-positive feature on the available
evidence (§9 — it currently costs the mechanism's only measured benefit in
this small sample) and it does not recommend flipping
`TEMPORAL_CORROBORATION` to on-by-default. `TEMPORAL_CORROBORATION` stays
OFF by default — this milestone closes the specific safety hole M5.4
found; it does not address M5.4's other stated precondition (a second
independent monitor/camera's worth of held-out data) for trusting the
mechanism's thresholds at all. See §12 for what would need to change
before a future milestone could reconsider that default.

---

## 12. Rollback procedure

1. **Structural rollback (recommended default posture is unaffected):**
   `TEMPORAL_CORROBORATION` is already unset in every environment unless
   explicitly set to `on` — no action is required; this milestone changes
   nothing about the shipped default.
2. **If the crop-integrity gate itself needs to be reverted while keeping
   M5.4's mechanism:** in `app/validation/reconcile.py`, stop passing
   `per_vital_crop_suspicious` into `temporal_signal.observe()` (or simply
   never populate `frame.crop_suspicious` — `app/sources/camera.py`'s one
   call site). `TemporalFieldState.clean_run` defaults `True` and
   `is_corroborated()`'s new condition is then always satisfied, exactly
   reproducing M5.4's original (ungated) behaviour.
3. **Full revert:** remove the `crop_integrity` parameter and its call site
   in `app/pipeline/read_frame.py`; remove `crop_suspicious` from `Frame`
   and its population in `app/sources/camera.py`; remove
   `per_vital_crop_suspicious` from `reconcile()` and `clean_run`/
   `crop_suspicious` from `app/validation/temporal.py`; delete
   `app/validation/crop_integrity.py`; remove
   `read_vital_with_diagnostics`/`OcrDiagnostics` from `app/pipeline/ocr.py`
   (the internal `_read_vital_diag` refactor can also be reverted to two
   separate 2-tuple/3-tuple implementations, or left as-is since
   `read_vital`'s own contract is unaffected either way). Every change
   listed is additive; removing all of them cannot affect any other code
   path, since no pre-M5.4.1 caller ever passed the new parameters.

---

## 13. What M5.5 inherits

- The crop-integrity signal and its eval harness
  (`app/eval/m5_4_1_crop_integrity_eval.py`) are committed and reusable
  against any future dense-frame dataset a second monitor/camera would
  provide — exactly the evidence M5.4 §12 named as the remaining
  precondition for a real GO on enabling `TEMPORAL_CORROBORATION` by
  default.
- The box-width-truncation root cause (Dataset A's 11 confidently-wrong
  confirmations, §9) remains the single most consequential open item
  across M5.2, M5.3, M5.4, and this milestone. It is unaddressed by
  design (out of this milestone's scope) and is still recommended as a
  calibration-UX fix (a live-preview truncation warning at DRAW time), not
  a confirmation-logic one.
- `TEMPORAL_CORROBORATION` stays off in the frozen production configuration
  M5.5 evaluates.
