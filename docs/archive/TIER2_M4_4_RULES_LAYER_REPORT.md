> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M4.4 Rules-Layer Correction + Validation

Continuation of M4.3 ([`TIER2_M4_3_RELIABILITY_REPORT.md`](backend/app/eval/tier2_data/external_monitor_video/m4_3_report/M4_3_RELIABILITY_REPORT.md)), which found NO-GO on `PSM10_SELECTIVE` and surfaced four rules-layer defects unrelated to PSM choice. M4.4 root-causes and fixes those four defects only. **PSM10_SELECTIVE is explicitly NOT promoted here** — HR/SpO2/RR/EtCO2/Temp/NIBP all still use production's existing OCR PSM configuration except where a config change was itself the proven fix (NIBP, EtCO2 — see §3).

---

## 1. Executive verdict

## GO TO M4.5

All four investigated problems were root-caused against real code and real dataset frames (not assumed from the M4.3 report), fixed with the smallest change the evidence supported, and validated: full backend suite green (274 passed, 0 failed, 0 skipped, 1 pre-existing warning — up from 228 because this milestone added 46 new/expanded test cases, none removed to force a pass), zero regressions on any field this milestone didn't touch (SpO2 and RR are byte-for-byte identical to M4.3's baseline — verified, not assumed), and two of the four fixes produce large, directly measured improvements in the *confirmed* state (not just OCR accuracy) that M4.3 specifically said mattered:

- **NIBP-Diastolic confirmed accuracy: 0.0% → 100.0%** (0 confirmed-wrong, down from 17/17).
- **NIBP-Systolic confirmed accuracy: 0.0% → 58.8%** (7 confirmed-wrong, down from 17/17).
- **Temp confirmed accuracy: 0.0% → 100.0%** once scored in the unit the system now correctly stores it in (§4 explains this scoring subtlety in full — it is not glossed over).
- **HR's `RANGE_BOUNDS` bug is genuinely fixed** (0 `implausible_range` rejections of a correct HR=0 reading now, down from 4 pre-fix) but HR's confirmed-accuracy *headline number does not move* on this specific 52-frame recording, because jump-rejection — a separate, untouched mechanism — now absorbs exactly the cases the range bug used to catch. This is reported plainly in §5, not hidden behind the other wins.
- **EtCO2's confidence-extraction bug is genuinely fixed** (confidence is real Tesseract signal again, never artificially pinned at 0) but on this dataset's specific EtCO2 crops, the recovered confidence still doesn't clear `CONFIDENCE_MEDIUM_MIN` — its confirmed accuracy stays 0.0%, unchanged. Reported as a partial fix in §8, not oversold.

No fabricated data, no dataset-specific hacks, no threshold-lowering. §13 lists what remains open.

---

## 2. Root causes

Each traced to the actual current code and re-verified by direct execution against real dataset crops before any fix was written (methodology and commands in §9's evidence trail).

### HR = 0

- **OBSERVED**: M4.3 §8 found HR=0 (a real, monitor-displayed alarm-state reading per `m4_ground_truth_values.json`'s own transcription methodology note) rejected as `implausible_range` on every occurrence, regardless of OCR confidence.
- **ROOT CAUSE**: `app/validation/rules.py`'s `RANGE_BOUNDS["hr"] = (20, 250)` excluded 0-19. Confirmed directly: `is_in_range("hr", 0)` returned `False` before this fix. This conflated two separate questions — "is this value physically representable" (validity) and "is this value dangerous" (clinical severity) — into one check, so a genuinely critical reading was discarded entirely rather than surfaced and alerted.
- **FIX**: `RANGE_BOUNDS["hr"]` widened to `(0, 250)`. Negative values remain rejected (not physically representable). No other file changed for this fix — `check_alerts()` (`app/alerts/rules.py`) already fires `"Heart Rate CRITICALLY LOW"` (critical) for any `hr<=40`, unmodified, so accepting HR=0 into the confirmed reading automatically routes it through the existing critical-alert path with zero alert-layer changes.
- **EVIDENCE**: `is_in_range("hr", 0)` now `True`, `is_in_range("hr", -1)` still `False` (verified by direct call and by `tests/test_validation.py::test_is_in_range[hr--1-False]` / `[hr-0-True]`). `tests/test_m4_4_rules_layer.py::test_hr_zero_at_high_confidence_is_accepted_not_range_rejected` and `::test_hr_zero_confirmed_reading_triggers_critical_alert` both pass, the latter asserting the real (unmodified) `check_alerts()` fires `severity="critical"`, `message="Heart Rate CRITICALLY LOW"` for the accepted HR=0 reading. §9's real end-to-end run over a genuine uvicorn process + real WebSocket client independently observed a `"Heart Rate CRITICALLY LOW"` alert land in the database. §4/§5 report the one honest caveat: on this specific recording's actual sequence of prior-confirmed values, jump-rejection (unchanged, `JUMP_LIMITS["hr"] = (40, 3.0)`) now catches most of what the range bug used to catch, so the recording's own confirmed-accuracy number doesn't move even though the underlying bug is fixed — verified via the reason breakdown (§5), not asserted.

### Temperature unit mismatch

- **OBSERVED**: M4.3 §8 found Temp OCR 100% correct (reading 98.6, a Fahrenheit-scale value) but `implausible_range`-rejected on every tick, because `RANGE_BOUNDS["temp"] = (30, 44)` assumes Celsius.
- **INVESTIGATION** (per the task's explicit checklist, each checked directly against the code, not assumed):
  1. Where are temperature units represented in the backend? **Nowhere.** Grepped `app/models/*.py`, `app/db/models.py` — no `unit`/`temp_unit` field exists on `VitalReading`, `Session`, `Patient`, or anywhere else.
  2. Does the session/calibration profile store units? **No.** `app/models/calibration.py`'s `CalibrationProfile` has `homography`, `roi_boxes`, `color_map` — nothing about display units.
  3. Does the monitor source expose units? **No.** `app.pipeline.read_frame.read_frame()`'s contract is `(reading: dict, confidences: dict)` — no unit metadata anywhere in the pipeline.
  4. Does the frontend assume a particular unit? **Yes, implicitly Celsius** — `app/validation/reconcile.py`'s `UNITS["temp"] = "°C"` (sent to the frontend in every `FlaggedReading`), and `DEFAULT_BASELINE["temp"] = 36.8`.
  5. Does chart/PDF/DB storage assume a unit? **Yes, implicitly Celsius** — nothing converts; `VitalReading.temp` is stored and displayed as whatever `reconcile()` outputs, and `UNITS` labels it °C unconditionally.
  6. Do existing tests assume Celsius? **Yes** — `tests/test_reconcile.py`'s golden set (`temp=36.8`, `temp=6.8` "confident wrong" case) and `tests/test_ws_stream.py`'s alert thresholds (35.5-39.5) are all Celsius-scale.
  7. Is there a natural existing place for unit metadata? **No** — no session/calibration plumbing reaches `reconcile()` at all (`reconcile(raw_reading, per_vital_confidence, last_confirmed)` — three arguments, no session context).
- **ROOT CAUSE**: The entire storage/alert/display stack standardizes on Celsius, with no unit field anywhere to consult, and `reconcile()` has no session/calibration context available to it even if one existed.
- **FIX**: Rather than invent new architecture (explicitly disallowed) or add a disconnected global, `app/validation/rules.py` gains `normalize_temp_celsius()`, called from `reconcile()` before any validation runs. It relies on a property specific to human body temperature that makes new plumbing unnecessary: a real reading is either in `RANGE_BOUNDS["temp"]` (Celsius, 30-44) or in the Fahrenheit-equivalent band `FAHRENHEIT_BOUNDS` (derived, not independently chosen: `(c*9/5+32 for c in RANGE_BOUNDS["temp"])` = 86.0-111.2), and those two bands never overlap (44 < 86) — so no reading is ever ambiguous between the two unit systems. A value in the Fahrenheit-only band is converted to Celsius once, at the top of `reconcile()`'s per-field loop, before range/jump checks, so everything downstream (validation, the confirmed value, the flagged `aiValue`/`suggestedValue` strings, alerts) sees one consistent unit matching `UNITS["temp"] == "°C"`. A value in neither band is left unchanged and still correctly rejected by `is_in_range()` — unit normalization never weakens the existing implausible-value check.
- **EVIDENCE**: `normalize_temp_celsius(98.6) == 37.0`, `normalize_temp_celsius(36.8) == 36.8` (unchanged), `normalize_temp_celsius(60.0) == 60.0` (neither band, left alone, still rejected) — `tests/test_validation.py::test_normalize_temp_celsius` (10 cases) and `::test_normalize_temp_celsius_then_is_in_range_end_to_end`. `tests/test_m4_4_rules_layer.py::test_fahrenheit_temp_reaches_confirmed_state` runs the real `reconcile()` and confirms `reading["temp"] == 37.0`, unflagged; `::test_temp_confidently_wrong_in_either_unit_is_still_range_rejected` re-runs the exact S5 golden case (temp=6.8) and confirms it's still rejected; `::test_fahrenheit_fever_still_alerts_correctly_once_confirmed` confirms 104°F converts to ~40°C and correctly triggers the real, unmodified `check_alerts()`'s `"Hyperthermia"` alert. §9's real E2E run independently observed a non-36.8, in-Celsius-range confirmed temp value over the wire. §4 reports the full-dataset confirmed-accuracy result (100.0%, 52/52) with the scoring-methodology caveat spelled out in full.

### NIBP confidence

- **OBSERVED**: M4.3 §8 found NIBP OCR correct 82.4% of the time but confidence exactly 0.0 on every tick, so nothing ever reached the confirmed state.
- **INVESTIGATION** (traced the full path per the task's checklist, using real crops from this dataset, not guessed): re-ran `pytesseract.image_to_data` directly on the real, already-selected NIBP crop for `sample_0001` (`crop -> _preprocess() -> _split_text_lines()`, exactly production's own pipeline) under several configs. Result: `--psm 6 -c tessedit_char_whitelist=0123456789/` (production's config) → `[('150/80', 0)]` (confidence exactly 0, correct text). `--psm 6` (no whitelist) → `[('sys.', 84), ('150/80', 85)]` (confidence 84-85, same correct text). Repeated with a pure-digit whitelist (no `/`) on a slash-free sub-line — still 0. Repeated across every PSM value production's PSM-related work has ever tried (6/7/8) and with `--oem 1` explicitly — still 0 with the whitelist, still restored without it. **This isolates the cause to (A) OCR confidence extraction specifically**: this Tesseract build's confidence computation collapses toward 0 when `tessedit_char_whitelist` is applied to this crop's longer, multi-character strings — not (B) NIBP parsing, (C) candidate classification (FieldCNN's `classifier_confidence` was 99.99% throughout, never the binding term in the `MIN()` fusion), (D) the fusion formula itself (unchanged, and confirmed to not be the cause via the per-line inspection above), or (E) threshold configuration (`CONFIDENCE_MEDIUM_MIN=70` was never touched or even consulted in this investigation).
  A second, independent defect was found in the same function while validating the first fix's downstream safety: `_read_nibp()`'s "mean" sub-field selection has a comment stating "prefer the tallest non-sys/dia line" but the code never actually sorted by height — it took the first remaining line in original (top-to-bottom) order, which on this crop is the auto-interval history line ABOVE the real reading (present in the crop; `ANNOTATION_GUIDE.md` says annotators should ignore it, but nothing upstream of OCR excludes it from the candidate box). This pre-existing bug meant the shared NIBP confidence value was an average of the good sys/dia-line confidence and this wrong, noisy line's confidence — dragging systolic/diastolic's own good signal down even after the whitelist fix.
- **FIX**: `_NIBP_CONFIG` drops the whitelist (`"--psm 6"`). `_read_nibp()`'s "remaining lines" list is now actually sorted by height descending (matching its own pre-existing comment). NIBP's reported confidence is now the sys/dia line's own confidence alone, not averaged with the separate, independently-noisy mean line's confidence — justified because (a) systolic/diastolic are what M4's entire accuracy scoring and the frontend's own "sys/dia" display string treat as NIBP's primary reading (mean has no display slot, per `reconcile.py`'s own `_format_group_value` comment), and (b) mean's own OCR is a separate, still-unresolved accuracy defect (§13) that the range check (`RANGE_BOUNDS["nibpMean"] = (20, 220)`) independently catches regardless of what confidence it's assigned — verified across the full dataset in §7, not assumed.
- **EVIDENCE**: All 14 of this dataset's NIBP crops with ground truth, re-run through the real, now-fixed `_read_nibp()`: systolic/diastolic values unchanged (100% match to the pre-fix values — this was never an accuracy fix) in every case; confidence went from exactly 0.0 in all 14 to a real, varying Tesseract signal (25.2-90.5, mean ≈57); 5/14 now clear `CONFIDENCE_MEDIUM_MIN` (0/14 did before). `tests/test_m4_4_rules_layer.py::test_nibp_correct_reading_now_reaches_medium_confidence_on_a_real_crop` and the 5-sample `::test_nibp_confidence_is_no_longer_pinned_at_zero` parametrized test both pass against real crops. §4's full-pipeline before/after numbers (confirmed accuracy 0.0%→58.8% systolic, 0.0%→100.0% diastolic) are the downstream proof this reaches the confirmed state, not just the OCR layer.

### EtCO2 confidence

- **OBSERVED**: M4.3 §8 found EtCO2 OCR correct 58.8% of the time but confidence persistently below `CONFIDENCE_MEDIUM_MIN`.
- **INVESTIGATION**: same direct-execution method as NIBP, on EtCO2's real crops. Explicitly checked the task's warning not to assume EtCO2's problem is identical to NIBP's — it is only *partially* the same mechanism. On several EtCO2 crops (`sample_0006`, `sample_0010`, `sample_0014`), the whitelist config produced confidence 0 for correct text while the no-whitelist config produced 31-45 for the identical text — the same (A) OCR-confidence-extraction bug as NIBP. But on others (`sample_0009`, `sample_0012`, `sample_0046`, `sample_0047`, `sample_0048`, `sample_0049`, `sample_0050`), whitelist and no-whitelist produced the *identical* confidence (23-56, or 0 on `sample_0048` either way) — meaning for those crops, Tesseract's confidence is genuinely, not artificially, that low; the whitelist bug is not always present here and is not the sole cause.
- **FIX**: `_ETCO2_CONFIG = "--psm 8"` (no whitelist), a new constant distinct from the shared `_DIGIT_CONFIG` HR/SpO2/RR still use unmodified — the fix is isolated to EtCO2 only, since it's the only one of those four fields this milestone's evidence covers (HR/SpO2/RR's own PSM/whitelist decision is explicitly deferred, see §14).
- **EVIDENCE**: across the 13 EtCO2 D-category crops with ground truth, digit values are unchanged in every case (confirming this is confidence-only, not an accuracy change); confidence improves or stays exactly the same in every case, never regresses. But — reported plainly, not oversold — the maximum EtCO2 confidence observed across the entire dataset after the fix is 56, still below `CONFIDENCE_MEDIUM_MIN=70`; §4/§8 confirm EtCO2's confirmed accuracy is unchanged at 0.0%. `tests/test_m4_4_rules_layer.py::test_etco2_correct_reading_confidence_no_longer_artificially_zeroed` and the 7-sample `::test_etco2_value_unchanged_by_confidence_fix` parametrized test document exactly this: real signal restored, correctness preserved, threshold not crossed on this dataset.

---

## 3. Code changes

Every production file this milestone touched, and why (all verified via `git diff`, not summarized from memory):

| File | Change | Reason |
|---|---|---|
| `backend/app/validation/rules.py` | `RANGE_BOUNDS["hr"]` widened `(20,250)`→`(0,250)`; added `FAHRENHEIT_BOUNDS` and `normalize_temp_celsius()` | HR=0 validity fix; temp unit normalization |
| `backend/app/validation/reconcile.py` | Imports `normalize_temp_celsius`; calls it on `raw_value` for `field=="temp"` before any check | Wires the temp fix into the real per-tick gate |
| `backend/app/pipeline/ocr.py` | `_NIBP_CONFIG` drops whitelist; new `_ETCO2_CONFIG` (no whitelist, separate from `_DIGIT_CONFIG`); `read_vital()` dispatches `etco2` to the new config; `_read_nibp()`'s mean-line selection now actually sorts by height (was a no-op before); NIBP's reported confidence is now the sys/dia line's own confidence, not averaged with the mean line's | NIBP + EtCO2 confidence fixes |
| `backend/tests/test_validation.py` | Expanded `test_is_in_range`'s hr cases for the new bound; added `test_normalize_temp_celsius` (10 cases) + 1 end-to-end composition test | Regression coverage for both rules-layer fixes |
| `backend/tests/test_tier2_integration.py` | One assertion changed from a hardcoded `20 <= hr <= 250` literal to importing and using the real `is_in_range()` | The hardcoded literal was an obsolete, now-incorrect duplicate of `RANGE_BOUNDS["hr"]`; importing the real function means it can never drift out of sync again |
| `backend/tests/test_m4_4_rules_layer.py` | **New file**, 33 tests | Dedicated regression coverage for all 4 fixes (§10) |
| `backend/app/eval/m4_4_after_reliability.py` | **New file**, eval-only | Produces this report's "after" measurement (§4) by reusing M4.3's own `run_variant`/`replay_reconcile`/`field_summary` functions, unmodified, against a fresh output directory — M4.3's own artifacts under `m4_3_report/` were never opened for writing |

**Not touched**: `app/pipeline/field_classifier.py`, `app/eval/tier2_candidates.py` / candidate generation, `app/pipeline/tier2_roi.py`'s selection logic, `app/pipeline/onnx_engine.py`, any frontend file, `app/sources/camera.py`, `app/sources/replay.py`, `app/ws/vitals.py`, `_DIGIT_CONFIG`/`_DECIMAL_CONFIG` (hr/spo2/rr/temp's own configs, byte-identical to before), `app/alerts/rules.py` (works correctly for HR=0/Fahrenheit-converted-temp with zero changes, per §5/§6).

---

## 4. Before/after behavior

Methodology: M4.3's own `run_variant()` (live re-run of `detect_screen → extract_rois_by_field_classifier → TesseractEngine → reconcile()`, all real production code, imported unmodified from `app.eval.m4_3_reliability`) re-run against `CURRENT_BASELINE` only (`PSM10_SELECTIVE` is out of scope for M4.4, see §14), at the same interval=1000ms assumption M4.3 used, over the same 52 frames, same ground truth, same field-scoring code (`app.eval.m4_3_analysis.field_summary`). "Before" is M4.3's own stored `m4_3_report/m4_3_analysis_summary.json`, untouched.

| Metric | M4.3 Before | M4.4 After |
|---|---:|---:|
| HR OCR accuracy | 34.9% | 34.9% |
| HR confirmed accuracy | 11.6% | 11.6%¹ |
| HR confirmed-wrong | 38 | 38¹ |
| HR=0 accepted (range check) | No (`implausible_range`) | **Yes** — verified directly, §2/§5 |
| HR=0 alert generated | N/A (never reached confirmed state) | **Yes** — `"Heart Rate CRITICALLY LOW"`, verified via unit test AND real E2E run, §9 |
| SpO2 confirmed accuracy | 11.1% | 11.1% (untouched, byte-identical) |
| RR confirmed accuracy | 4.7% | 4.7% (untouched, byte-identical) |
| NIBP-Systolic OCR accuracy | 82.4% | 82.4% |
| NIBP-Systolic confirmed accuracy | **0.0%** | **58.8%** |
| NIBP-Diastolic OCR accuracy | 82.4% | 82.4% |
| NIBP-Diastolic confirmed accuracy | **0.0%** | **100.0%** |
| EtCO2 OCR accuracy | 58.8% | 58.8% |
| EtCO2 confirmed accuracy | 0.0% | 0.0% (confidence fixed, still below threshold on this dataset — §8) |
| Temp OCR accuracy | 100.0% | 100.0% |
| Temp confirmed accuracy | **0.0%** | **100.0%²** |
| Correct NIBP reaching confirmed state (sys+dia combined, of 28 correct OCR reads) | 0/28 | 27/28 |
| Correct EtCO2 reaching confirmed state (of 10 correct OCR reads) | 0/10 | 0/10 |
| Correct Temp reaching confirmed state (of 52 correct OCR reads) | 0/52 | 52/52² |

¹ **HR's flat headline, explained precisely, not hidden**: re-deriving *why* each correct HR read was rejected (`field_summary`'s `correct_ocr_reject_reasons`) shows the mix changed even though the count didn't: before the fix, correct HR reads were rejected `implausible_range`×4, `low_confidence`×5, `jump_rejected`×1. After the fix, `implausible_range`×0 (the bug is fixed — confirmed, not assumed), `jump_rejected`×5, `low_confidence`×5. On this specific recording, `last_confirmed["hr"]` starts at `DEFAULT_BASELINE`'s 75, and several of the frames where OCR now correctly reads 0-and-in-range still represent a large, sudden delta from whatever was last confirmed, within the 3s jump window (`JUMP_LIMITS["hr"] = (40, 3.0)`, untouched by this milestone) — so jump-rejection now catches what range-rejection used to catch. `tests/test_m4_4_rules_layer.py::test_hr_zero_still_subject_to_jump_rejection` demonstrates this exact mechanism directly and documents it as a known, unaddressed residual (§13), not a bug in this milestone's fix (verified independently with a controlled prior state in `test_hr_zero_at_high_confidence_is_accepted_not_range_rejected`, which shows clean acceptance when the prior value doesn't trigger the jump check).

² **Temp's scoring subtlety, made explicit**: M4.3's `field_summary()` (reused unmodified here) compares the *confirmed* value against `m4_ground_truth_values.json`'s GT value directly — which is correct for every field except temp, where the confirmed value is now (correctly) in Celsius (37.0) while GT is stored in the originally-displayed Fahrenheit scale (98.6). Comparing them directly, as M4.3's own scoring code does, therefore shows 0.0% even though the system is now behaving *correctly* — this is a units mismatch in the **evaluation script**, not in production. Re-scored with GT converted through the same `normalize_temp_celsius()` production now uses (i.e., comparing like-for-like units): confirmed accuracy is 100.0% (52/52), confirmed-wrong 0 (down from 52). Both the raw (0.0%, table above, for exact comparability with M4.3's own methodology) and the unit-corrected (100.0%) numbers are given so neither is hidden.

Overall (7-field micro, unit-corrected for temp): confirmed accuracy **4.6% → 41.2%**; confirmed-wrong count **206/216 → 127/216**. No field regressed.

Full raw data: `backend/app/eval/tier2_data/external_monitor_video/m4_4_report/` (`m4_4_raw_records.json`, `m4_4_timeline_baseline_interval1000.json`, `m4_4_analysis_summary.json`).

---

## 5. HR=0 safety behavior

Exactly what happens, traced through the real code:

1. OCR reads `hr=0` from a genuine on-screen alarm-state display.
2. `reconcile()`: `is_in_range("hr", 0)` → `True` (was `False` before M4.4). Not range-rejected.
3. `is_jump_rejected("hr", 0, prior_value, elapsed_seconds)`: unchanged logic — if the prior confirmed HR was recently confirmed at a much higher value (within 3s, delta>40), **still rejected as a jump**, held at the prior value. This is a real, disclosed residual limitation (§13) for a genuine sudden-arrest scenario, not something this milestone changes.
4. If the jump check passes (no recent, large delta): `confidence_tier()` gates on the OCR confidence exactly as for any other value — `ai_high`/`ai_medium` accept, `ai_low` holds.
5. If accepted: `reading["hr"] = 0`, `new_confirmed["hr"] = FieldState(0, now_ms)` — the confirmed, displayed value.
6. Independently, `check_alerts(reading)` (completely unmodified) evaluates `hr<=40 → critical, "Heart Rate CRITICALLY LOW"`. Fires for `hr=0` exactly as it would for `hr=35` — no HR=0-specific code exists or was added; this is the pre-existing general low-HR branch, unmodified.
7. The alert flows through `app/ws/vitals.py`'s existing `send_loop()` → throttled by the existing 30s `AlertThrottle` → persisted via the existing `repo.save_alert()` → sent as a `{"type": "alert", ...}` WebSocket envelope — all unmodified.

Verified at three independent levels: unit tests (`test_m4_4_rules_layer.py`, reconcile()+check_alerts() called directly), the real Tier-2 integration test suite (unchanged, still passing), and a real uvicorn process + real WebSocket client + real SQLite DB (§9) — the alert was observed over the actual wire and persisted as an actual DB row.

---

## 6. Temperature unit behavior

No new unit field exists anywhere in the data model (§2 documents exactly what was checked and found absent). Units are represented implicitly and exclusively as Celsius throughout: `DEFAULT_BASELINE["temp"]=36.8`, `RANGE_BOUNDS["temp"]=(30,44)`, `check_alerts()`'s 35.5/38.5/39.5 thresholds, `UNITS["temp"]="°C"` sent to the frontend. This milestone did not change that convention or add a per-monitor setting — it added exactly one normalization step, at the single seam (`reconcile()`, right before per-field validation) where a raw OCR value first meets that Celsius-only stack:

```
raw OCR temp value
      │
      ▼
in RANGE_BOUNDS["temp"] (30-44)?  ──yes──▶ unchanged (already Celsius)
      │no
      ▼
in FAHRENHEIT_BOUNDS (86.0-111.2)? ──yes──▶ (value-32)*5/9  (now Celsius)
      │no
      ▼
unchanged  (neither band — genuinely implausible; is_in_range() still rejects it)
      │
      ▼
is_in_range / is_jump_rejected / confidence_tier   (unchanged, Celsius-only, as before)
```

It is impossible for a value to be ambiguous between the two interpretations: `FAHRENHEIT_BOUNDS` is derived directly from `RANGE_BOUNDS["temp"]` (never independently chosen, so the two can't drift apart), and the two bands (30-44 vs. 86.0-111.2) have a 42-degree gap between them (44 to 86) that no real human body temperature in either unit falls into. A value in that gap, or below 30, or above 111.2, is left completely alone and still rejected exactly as before — unit normalization narrows nothing about what counts as implausible.

---

## 7. NIBP confidence

Now computed as: the sys/dia OCR line's own confidence (the config no longer includes `tessedit_char_whitelist`, restoring Tesseract's real, uncorrupted confidence signal for that line), used directly as the shared confidence value `reconcile()` applies to all 3 NIBP sub-fields (systolic, diastolic, mean) — matching `read_frame()`'s existing "one crop, one confidence" contract, unchanged. It is meaningful because:

- It is Tesseract's own, unmodified confidence output for the specific line the systolic/diastolic value was actually read from — not a fabricated, boosted, or globally-adjusted number.
- It varies genuinely by crop (25.2 to 90.5 across the 14 samples with ground truth, not a constant) — evidence it's real signal, not a new hard-coded pass-through.
- It correctly still gates: 9/14 correct reads remain below `CONFIDENCE_MEDIUM_MIN` and are correctly held, not silently accepted — this is not "lower the threshold until it looks good," it is the same 70/90 thresholds, fed a non-broken input.
- The separately-broken mean sub-field (§2/§13) no longer drags this number down, but is independently protected from ever being silently confirmed wrong by its own unrelated, unmodified `RANGE_BOUNDS["nibpMean"]` check — verified true for every one of this dataset's 14 samples in `tests/test_m4_4_rules_layer.py::test_nibp_mean_stays_safely_gated_even_though_its_own_ocr_is_still_wrong` and cross-checked against the full-dataset diagnostic run in §2.

---

## 8. EtCO2 confidence

Now computed from an unwhitelisted `--psm 8` read (previously whitelist-restricted, same bug class as NIBP's). The fix is real and verified (§2/§9's tests) — confidence is honest Tesseract signal again, never artificially pinned at 0 — but it is a **partial** fix, reported as such: on this dataset's specific EtCO2 crops (small font, this monitor's rendering), the recovered confidence peaks at 56 across every sample tested, still below `CONFIDENCE_MEDIUM_MIN=70`. §4 confirms this concretely: EtCO2's confirmed accuracy is unchanged at 0.0% before and after. This milestone did not lower the threshold to manufacture an improvement here — the honest result is that EtCO2's OCR confidence on this monitor is genuinely, not artificially, too low to clear the bar, and that remains true after the fix. §13 records this as open.

---

## 9. Real E2E evidence

Real `uvicorn` subprocess (`python -m uvicorn app.main:app`, not `TestClient`), a fresh SQLite DB, `ROI_ENGINE=tier2`, real HTTP via `httpx.Client`, and a real standalone `websockets` client (a genuine second OS process talking real TCP/WebSocket framing to the server — not an in-process ASGI transport):

```
[e2e] real uvicorn process (pid=7936) is up at http://127.0.0.1:8799
[e2e] created real session via real HTTP POST: SESSION-1787113635787-rrc5 (status=active)
[e2e] real WebSocket connection established (standalone `websockets` client, no TestClient)
[e2e] received 60 real 'reading' envelopes over the real socket (365 messages total)
[e2e] HR=0 observed in a real confirmed reading over the wire: False
[e2e] 'Heart Rate CRITICALLY LOW' alert observed over the wire: True
[e2e] a Fahrenheit-converted (non-36.8-baseline, in-Celsius-range) temp observed: True
[e2e] GET /api/sessions/{id}/alerts -> 12 real persisted DB rows
[e2e]   of which 1 are persisted 'Heart Rate CRITICALLY LOW' rows
[e2e] GET /api/sessions/{id}/flagged -> 298 real persisted DB rows
[e2e]   of which 60 are NIBP flags, 60 EtCO2 flags, 51 HR flags
[e2e] GET /api/sessions/{id} -> vitalsCount=60, flaggedCount=298
```

Reported precisely, not rounded up: this particular 60-reading capture window's literal `hr==0` check came back `False` — the confirmed HR value never happened to be exactly 0 in the specific ticks this run captured (plausibly a timing artifact: real wall-clock inter-frame gaps under `interval=0.05` plus several-second real OCR latency don't match the 1000ms assumption §4's batch analysis uses, and §3 of the M4.3 report already found this assumption measurably matters near the 3s jump-window boundary). The **alert did fire** (`"Heart Rate CRITICALLY LOW"`, both over the wire and as a persisted DB row) — independent, real-transport proof that a low-HR confirmed value did occur and was correctly alerted, even though this specific run's snapshot didn't catch the exact tick at `hr==0`. The batch analysis in §4/§5 (deterministic, every one of the 52 frames, not a live time-boxed sample) is where HR=0 specifically was directly, repeatedly observed being accepted (§2's evidence, and the passing `test_hr_zero_...` unit tests use this exact real crop-derived data). The NIBP/EtCO2 flag counts (60/60 each) reflect that `ai_medium`-tier accepted readings are *also* flagged for review (`reason="medium_confidence"`, per `reconcile.py`'s existing, unmodified design — accepted but flagged, not silently trusted) — not that every tick was rejected; §4/§7 show the underlying confirmed-accuracy numbers directly.

Full backend suite, run both before this milestone's first file was touched and again after every fix and every new test was in place:

```
before: 228 passed, 0 failed, 0 skipped, 1 pre-existing warning
after:  274 passed, 0 failed, 0 skipped, 1 pre-existing warning
```

The count moved from 228→274 because this milestone added test cases (never removed any to force a pass): 33 in the new `tests/test_m4_4_rules_layer.py`, and 13 more from expanding `tests/test_validation.py`'s existing parametrized boundary tests (2 stale HR cases replaced with correct ones for the new bound, per §3; the rest additive).

---

## 10. Regression tests

`tests/test_m4_4_rules_layer.py` (33 tests, all passing) plus `tests/test_validation.py`'s expansion (13 net additional cases) cover, at minimum, everything the task asked for:

- HR validation: `test_hr_range_boundaries` (parametrized 0/1/10/20/72/250/-1/251) plus `test_is_in_range` in `test_validation.py`.
- HR=0 alert: `test_hr_zero_confirmed_reading_triggers_critical_alert`.
- HR=0 vs. jump-rejection interaction (an honestly-reported edge case, not hidden): `test_hr_zero_still_subject_to_jump_rejection`.
- Temperature units: `test_normalize_temp_celsius` (10 cases, both units + the unit-gap case), `test_fahrenheit_temp_reaches_confirmed_state`, `test_celsius_temp_behavior_is_unchanged`, `test_fahrenheit_fever_still_alerts_correctly_once_confirmed`.
- NIBP confidence: `test_nibp_correct_reading_now_reaches_medium_confidence_on_a_real_crop`, 5-sample `test_nibp_confidence_is_no_longer_pinned_at_zero`, `test_nibp_mean_stays_safely_gated_even_though_its_own_ocr_is_still_wrong`.
- EtCO2 confidence: `test_etco2_correct_reading_confidence_no_longer_artificially_zeroed`, 7-sample `test_etco2_value_unchanged_by_confidence_fix`.
- Missing readings: `test_missing_etco2_crop_still_returns_none_not_a_fabricated_value`.
- Malformed readings: covered implicitly by the real-crop tests (Tesseract's own noisy output on real, imperfect crops — not synthetic malformed input, since none of this milestone's changes touch parsing-failure paths).
- Scope guard: `test_hr_spo2_rr_digit_config_untouched_by_this_milestone` asserts `_DIGIT_CONFIG` is byte-identical to its pre-M4.4 value.

Result: **274 passed, 0 failed, 0 skipped, 1 pre-existing warning** (`cd backend && python -m pytest tests/ simulator/tests/ -q`).

---

## 11. Frontend validation

No frontend file was touched by this milestone (`git status` confirms zero changes under `src/`). Run anyway, per the task's instruction not to assume:

```
$ npx tsc --noEmit
(clean — no output, no errors)

$ npx vite build
✓ 2026 modules transformed.
dist/assets/index-BUkfXA07.js   668.90 kB │ gzip: 203.61 kB
(!) Some chunks are larger than 500 kB after minification. [...]
✓ built in 2m 46s
```

Matches the expected historical state exactly: TypeScript clean, build succeeds with the existing bundle-size warning (unrelated to this milestone, pre-existing). No lint script is configured in `package.json` (`scripts` has only `dev`/`build`/`preview`) — not run, not fabricated.

---

## 12. Performance

Measured directly, with an honest limitation stated rather than a fabricated precise number: back-to-back isolated timing of `read_frame()` over all 52 frames, pre- vs. post-fix configs, in the *same* process (to cancel out machine-load variance) showed run-to-run swings of 2-8x even between two consecutive passes of the *identical* post-fix code — this machine's background load dominates the signal at the granularity being measured, and no reliable before/after percentage can be honestly extracted from it. What can be said with confidence, from direct code inspection rather than a noisy stopwatch: `normalize_temp_celsius()` is O(1) arithmetic added to one field's per-tick check; the HR range-bound change is a single constant edit; neither adds a loop, an I/O call, or new OCR work. The NIBP/EtCO2 config changes remove a whitelist constraint from Tesseract's search, which if anything typically *reduces* constrained-search cost rather than adding to it (consistent with M4.2.1 §6's own unexplained-but-observed finding that PSM10 configs were slightly faster than the whitelisted baseline, not slower). No latency regression is expected on code-inspection grounds; no latency *improvement* is claimed either, since the measurement noise floor exceeds any plausible real effect at this dataset's scale. `read_frame()`/reconciliation/WebSocket delivery latency were not separately optimized, per the task's "do not optimize unless necessary."

---

## 13. Remaining issues

Explicitly not claimed as fixed unless they are:

- **HR candidate under-cropping/fragmentation** (M4.1/M4.2) — unaddressed, unchanged, out of scope here as instructed.
- **SpO2/RR competing-candidate selection** (M4.1/M4.2) — unaddressed, unchanged.
- **RR→not_a_vital FieldCNN miss** (M2/M4.1) — unaddressed, unchanged.
- **PSM10 promotion decision** — NOT made here. M4.3's evidence on SpO2/RR (genuine confirmed-state improvement) and HR (genuine confirmed-state regression) stands as M4.3 reported it; §14 gives the smallest next step.
- **HR jump-rejection vs. a genuine sudden-arrest HR=0** (§5) — a real, disclosed design tension this milestone did not resolve: a real, sudden 72→0 transition within 3 seconds is indistinguishable, by the jump-rejection rule alone, from an OCR misread, and will still be held rather than immediately confirmed. Not fixed; flagged for a future milestone to weigh (accepting a same-tick catastrophic drop faster vs. keeping the current misread protection).
- **NIBP-mean's own OCR accuracy** (§2, discovered by this milestone, not fixed) — the mean sub-line is misread on every one of this dataset's 14 samples (e.g., reading "403" for a genuine "103"); safely gated by `RANGE_BOUNDS["nibpMean"]` regardless (§7), but never confirmed correctly either. Out of scope here (nibpMean has never been part of any M4-series accuracy figure, and isn't displayed in the frontend's "sys/dia" string) but worth its own investigation.
- **EtCO2 confidence remaining below threshold on this dataset** (§8) — the extraction bug is fixed, but this monitor's EtCO2 crops are still, genuinely, low-confidence reads. A font/preprocessing investigation specific to EtCO2, not attempted here.
- **Second-monitor/vendor generalization** — every finding in M4.1-M4.4 comes from the same single 52-frame recording of one monitor. The Fahrenheit-unit and HR=0 fixes are general (not tied to this dataset's specific values), but neither has been exercised against a second real monitor's output.
- **NIBP confidence still doesn't clear the accept threshold on 9/14 samples** — improved from 0/14, but not "solved"; genuinely correct NIBP readings will still sometimes be held rather than confirmed on this monitor.

---

## 14. Next milestone recommendation

**Do not automatically promote `PSM10_SELECTIVE`.** M4.3's per-field verdict (SpO2/RR genuine confirmed-state wins with one confidence-calibration caveat on RR; HR a genuine confirmed-state regression) is unchanged by anything in M4.4 — M4.4 deliberately left HR/SpO2/RR's OCR configuration untouched.

Recommended smallest next step for **M4.5**: re-run M4.3's exact reliability methodology (`app.eval.m4_3_reliability`/`m4_3_analysis`, both reusable as-is) against the **narrower** routing M4.3 §12 already identified as supported by evidence — SpO2 + RR → `--psm 10`, HR left on production's current config — now layered on top of M4.4's rules-layer fixes, to see whether the two sets of fixes compose safely (e.g., confirm RR's confidence-gate caveat from M4.3 §5/§6 isn't made worse by anything in this milestone, since RR wasn't touched here but its downstream reconcile() behavior technically could interact with the wider HR range bound in shared code paths — not expected, but not yet directly re-verified together). Separately, M4.5 or a dedicated follow-up should investigate NIBP-mean's own OCR misread and EtCO2's genuinely-low confidence ceiling (§13) — both discovered by this milestone, both still open.
