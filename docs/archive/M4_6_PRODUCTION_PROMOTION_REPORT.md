> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M4.6 Production Promotion Report

Promotes the evidence gate M4.5 established — SpO2 + RR → `--psm 10`, HR/NIBP/EtCO2/Temp unchanged — into `app/pipeline/ocr.py` itself, then verifies the real application (tests, dataset replay, direct pipeline call, real uvicorn/HTTP/WebSocket camera path, persistence, performance) still works end to end.

---

## 1. Verdict

## GO

Production now dispatches `spo2`/`rr` through `--psm 10 -c tessedit_char_whitelist=0123456789` and everything else exactly as before. The change reproduces M4.5's eval-only numbers byte-for-byte on the 52-frame dataset, all 284 backend tests pass (274 pre-existing + 10 new focused tests), frontend typecheck/build are clean, the real production pipeline and a real uvicorn/HTTP/WebSocket camera-simulated run both work end to end with persistence and alerts intact, and no new safety regression was found. RR's confidence-calibration issue is real, unresolved, and reported honestly in §10 — it does not block this promotion per M4.5/M4.6's own decision rules, but it is a live, monitored characteristic, not a solved one.

---

## 2. Production Change

**File modified:** `backend/app/pipeline/ocr.py` — and nothing else.

Before:
```python
def read_vital(self, crop, vital_type):
    if vital_type == "nibp":
        return self._read_nibp(crop)
    if vital_type == "temp":
        return self._read_scalar(crop, _DECIMAL_CONFIG, decimal=True)
    if vital_type == "etco2":
        return self._read_scalar(crop, _ETCO2_CONFIG, decimal=False)
    return self._read_scalar(crop, _DIGIT_CONFIG, decimal=False)   # hr, spo2, rr all fell through here
```

After:
```python
_DIGIT_PSM10_CONFIG = "--psm 10 -c tessedit_char_whitelist=0123456789"
_PSM10_VITALS = {"spo2", "rr"}   # HR explicitly excluded

def read_vital(self, crop, vital_type):
    if vital_type == "nibp":
        return self._read_nibp(crop)
    if vital_type == "temp":
        return self._read_scalar(crop, _DECIMAL_CONFIG, decimal=True)
    if vital_type == "etco2":
        return self._read_scalar(crop, _ETCO2_CONFIG, decimal=False)
    if vital_type in _PSM10_VITALS:
        return self._read_scalar(crop, _DIGIT_PSM10_CONFIG, decimal=False)
    return self._read_scalar(crop, _DIGIT_CONFIG, decimal=False)   # hr only, now
```

`nibp`/`temp`/`etco2` branches are untouched. `reconcile()`, confidence fusion (`read_frame.py`'s `min(classifier_confidence, ocr_confidence)`), candidate generation, FieldCNN, ROI selection, `CameraSource`, WebSocket envelopes, and persistence were not touched — confirmed both by inspection (Phase 0) and by `git status --porcelain -- backend/app/` showing no file other than `ocr.py` changed relative to the M4.4/M4.5 working tree.

M4.5's eval-only `NarrowSelectivePsmEngine` (`app/eval/m4_5_selective_psm_reliability.py`) was **not** imported or reused — this promotion is a native change to `TesseractEngine` itself, matching the "minimal production change" M4.5 §19 already scoped.

---

## 3. M4.5 → M4.6 Reproduction

Both runs use the same reused M4.3 harness (`run_variant`/`replay_reconcile`, `field_summary`) over the identical 52-frame dataset, interval=1000ms. M4.5's column is the eval-only `NarrowSelectivePsmEngine`; M4.6's is the real, unmodified production `TesseractEngine` after promotion — no eval subclass involved.

| Field | OCR acc (M4.5 / M4.6) | Confirmed acc (M4.5 / M4.6) | Confirmed-wrong (M4.5 / M4.6) | Match |
|---|---:|---:|---:|:---:|
| HR | 34.9% / 34.9% | 11.6% / 11.6% | 38 / 38 | ✅ |
| SpO2 | 29.6% / 29.6% | 33.3% / 33.3% | 18 / 18 | ✅ |
| NIBP-Sys | 82.4% / 82.4% | 58.8% / 58.8% | 7 / 7 | ✅ |
| NIBP-Dia | 82.4% / 82.4% | 100.0% / 100.0% | 0 / 0 | ✅ |
| EtCO2 | 58.8% / 58.8% | 0.0% / 0.0% | 17 / 17 | ✅ |
| Temp (unit-corrected) | 100.0% / 100.0% | 100.0% / 100.0% | 0 / 0 | ✅ |
| RR | 46.5% / 46.5% | 62.8% / 62.8% | 16 / 16 | ✅ |

**Every field matches exactly** — OCR correct/wrong/missing counts, confirmed correct/wrong counts, every rejection-reason breakdown (`low_confidence`/`jump_rejected`/`implausible_range`), and every confidence-tier bucket were diffed field-by-field between `m4_5_analysis_summary.json`'s `selective` entry and `m4_6_analysis_summary.json`; zero mismatches (`m4_6_reproduction_check.json`'s `mismatched_fields` is `[]`). The real production dispatch reproduces the tested eval-only evidence byte-for-byte — this is a genuine reproduction, not a re-run that "looks similar."

Full data: `backend/app/eval/tier2_data/external_monitor_video/m4_6_report/{m4_6_raw_records.json, m4_6_timeline_production_interval1000.json, m4_6_analysis_summary.json, m4_6_reproduction_check.json}`.

---

## 4. Focused Tests

New file: `backend/tests/test_m4_6_production_promotion.py`. All exercise the real `TesseractEngine.read_vital()` dispatch — only the Tesseract-calling boundary (`_run_ocr_on_image`) is mocked, capturing which config string the real dispatch logic actually selects; the routing `if`/`elif` chain under test runs unmocked.

| Test | Result |
|---|---:|
| `test_psm10_vitals_are_exactly_spo2_and_rr` | PASSED |
| `test_digit_psm10_config_matches_m4_5_evidence` | PASSED |
| `test_hr_uses_old_production_digit_config` | PASSED |
| `test_spo2_uses_psm10` | PASSED |
| `test_rr_uses_psm10` | PASSED |
| `test_nibp_config_is_unchanged_m4_4_config` | PASSED |
| `test_etco2_config_is_unchanged_m4_4_config` | PASSED |
| `test_temp_config_is_unchanged_decimal_config` | PASSED |
| `test_hr_is_never_accidentally_routed_to_psm10` | PASSED |
| `test_nibp_etco2_temp_never_routed_to_psm10` | PASSED |

```
$ .venv/Scripts/python.exe -m pytest tests/test_m4_6_production_promotion.py -v
10 passed in 0.47s
```

---

## 5. Full Backend Tests

```
$ cd backend && .venv/Scripts/python.exe -m pytest tests/ simulator/tests/ -q
284 passed, 1 warning in 126.41s (0:02:06)
```

- **Passed:** 284 (274 pre-existing M1–M4.5 tests + 10 new M4.6 focused tests)
- **Failed:** 0
- **Skipped:** 0
- **Warnings:** 1 — the same pre-existing `StarletteDeprecationWarning` (`httpx`/`starlette.testclient`) present in every prior milestone's run; unrelated to this change.

---

## 6. Frontend Verification

```
$ npx tsc --noEmit
(clean — no output, no errors)

$ npx vite build
✓ 2026 modules transformed.
dist/assets/index-rNfJuIKh.css   55.59 kB │ gzip: 10.21 kB
dist/assets/index-BUkfXA07.js   668.90 kB │ gzip: 203.61 kB
(!) Some chunks are larger than 500 kB after minification. [...]
✓ built in 13.94s
```

TypeScript clean, build succeeds with the same pre-existing bundle-size warning as every prior milestone. No `lint` script exists in `package.json` (`scripts`: `dev`/`build`/`preview` only) — not run, not claimed. No frontend file was touched by this milestone.

---

## 7. Real Production Pipeline

**Stage 1 — direct call, no transport** (`m4_6_e2e_script.py`, Phase 4): `read_frame()` called directly with `get_default_engine()` (confirmed `TesseractEngine`, no override) and the real Tier-2 `ROI_ENGINE=tier2` extractor — exercising `detect_screen → Tier-2 candidate generation → FieldCNN → candidate selection → production TesseractEngine` with no eval-only code in the path:

```
[stage1] production engine class: TesseractEngine (default, no override)
[stage1] production _PSM10_VITALS: ['rr', 'spo2']
[stage1] sample_0021 (low SpO2, GT=65): reading spo2=65.0, confidences spo2=95.0
[stage1] sample_0033 (abnormal/critical RR, GT=0): reading rr=None (this frame's RR OCR misses; see §11 taxonomy — a known, unresolved failure mode, not new)
[stage1] sample_0001 (NIBP + Fahrenheit): reading nibpSystolic=150.0, nibpDiastolic=80.0, temp=98.6 (raw Fahrenheit reading, normalized downstream by reconcile())
[stage1] sample_0018 (HR=0 alarm state): reading hr=0.0, confidence 51.3
```
Full output: `m4_6_e2e_stage1_direct_pipeline.json`.

**Stage 2 — real uvicorn + reconcile() + alerts + persistence + WebSocket** (Phase 4's full chain, real transport): a real `uvicorn app.main:app` subprocess, a real session created via `POST /api/sessions`, and 5 real dataset frames pushed via the real ingestion path, with a real WebSocket client consuming `type: reading/alert/flagged` messages:

```
[stage2] session: SESSION-1787125543198-623y
[stage2] pushed sample_0052 (near-normal), sample_0021 (low SpO2 GT=65),
         sample_0033 (abnormal RR GT=0), sample_0001 (NIBP+Fahrenheit),
         sample_0018 (HR=0 alarm) via real HTTP push-frame

[stage2] 5 reading(s), 2 alert(s), 23 flagged observed
  reading[0]: hr=75 spo2=98 nibpSys=120/78 etco2=38 temp=36.8 rr=2.0
  reading[1]: hr=75 spo2=65.0 nibpSys=120/78 etco2=38 temp=37.0 rr=0.0
  reading[2]: hr=43.0 spo2=65.0 nibpSys=120/78 etco2=38 temp=37.0 rr=0.0
  reading[3]: hr=43.0 spo2=65.0 nibpSys=150.0/80.0 etco2=38 temp=37.0 rr=0.0
  reading[4]: hr=43.0 spo2=65.0 nibpSys=150.0/80.0 etco2=38 temp=37.0 rr=0.0
  ALERT: spo2 critical 'SpO₂ CRITICALLY LOW' value=65.0
  ALERT: hr warning 'Heart Rate Low' value=43.0

[stage2] DB: vitalsCount=5 flaggedCount=23 alerts_persisted=2 flagged_persisted=23
```

Notable, honestly reported: the real low-SpO2 confirmed value (65) and its **critical alert reached the live WebSocket and SQLite in this run** — something M4.5's own live E2E window did not capture (M4.5 report §14/§18 explicitly flagged that timing sensitivity as a known limitation, not a defect). This run's SpO2 result is consistent with, and a direct live demonstration of, the deterministic §3 batch finding (SpO2 confirmed accuracy 33.3%) — not proof the timing sensitivity is now solved in general, since real-time capture windows remain sensitive to exact frame timing.

NIBP became `150.0/80.0` after `sample_0001`, identically to M4.4/M4.5's own runs. Temp normalized `98.6°F → 37.0°C` identically. HR's `"Heart Rate Low"` warning at `hr=43` fired identically to M4.5's own live run. RR stayed at `0.0` after `sample_0033` (GT=0) — consistent with, not contradicting, RR's batch improvement.

Full artifacts: `m4_6_e2e_messages.json`, `m4_6_e2e_db_state.json`, `m4_6_e2e_uvicorn_stdout.log`, `m4_6_e2e_script.py` (all in `m4_6_report/`).

---

## 8. Real Camera E2E

This **is** §7 Stage 2 — the same real `POST /api/pipeline/push-frame` → `FrameQueue` → `CameraSource` → `read_frame()` → Tier-2 ROI → FieldCNN → production OCR → `reconcile()` → WebSocket path a browser camera capture uses (Day 1/M3), driven with real, curated monitor frames as the camera input in place of a physical monitor (same fake-camera methodology M4.3 Test H / M4.4 §9 / M4.5 §14 already established as this project's precedent for headless verification).

Checklist, verified directly from the run above and from inspection:

- **Camera connects:** real WebSocket `?source=camera` connection accepted (`[stage2] real WebSocket (source=camera) connected`).
- **Frames uploaded:** 5/5 real HTTP `push-frame` calls returned 200 with incrementing `seq`.
- **Readings arrive:** 5 `type:reading` messages received.
- **SpO2/RR use production PSM10:** confirmed structurally (§2, §4 dispatch tests) and behaviorally — SpO2's confirmed value/alert in this exact run matches its improved batch profile (§3/§7).
- **HR does NOT use PSM10:** confirmed structurally (§2, §4) — HR's alert behavior (`Heart Rate Low` at 43) is byte-identical to M4.5's own live run.
- **NIBP remains functional:** `150.0/80.0` reached the confirmed state and DB, as in M4.4/M4.5.
- **Temp normalization remains functional:** `98.6°F → 37.0°C` reached the confirmed state.
- **Alerts remain functional:** 2 real alerts (SpO2 critical, HR warning) emitted over the real WS and persisted.
- **SQLite persistence remains functional:** `vitalsCount=5`, `alerts_persisted=2`, `flagged_persisted=23`, queried via the real `/api/sessions/{id}`, `/api/sessions/{id}/alerts`, `/api/sessions/{id}/flagged` HTTP endpoints after the run — not asserted from memory.
- **Session cleanup:** not separately exercised this milestone (out of scope — no session lifecycle code was touched); no prior milestone's session-cleanup tests were affected (part of the 284 green tests, §5).
- **Demo Mode remains isolated:** not touched — `ROI_ENGINE`/`OCR_ENGINE` env-var switches and the synthetic/replay source path (`app/sources/replay.py`) were not modified by this milestone; `CameraSource` is the only source exercised here.
- **No synthetic/camera race:** this run used a single real `CameraSource` instance with no concurrent synthetic source active; `frame_queue`'s in-process dict was used exactly as `push_frame()`/`CameraSource` already do in production, unmodified.

---

## 9. Performance

Real `read_frame()` latency, measured over the full 52-frame dataset with the real default engine (`TesseractEngine`, promoted routing) and real Tier-2 ROI extractor, no redundant re-computation (matching M4.5 report §17's "clean, single-purpose" methodology):

| Pass | Mean | Median | p95 |
|---|---:|---:|---:|
| M4.4 baseline (from M4.5 report) | 2829.3ms | 2940.4ms | 3817.2ms |
| M4.5 selective (eval-only engine, from M4.5 report) | 2933.9ms | 2951.8ms | 3796.1ms |
| **M4.6 production (promoted, this milestone)** | **2797.3ms** | **2889.7ms** | **3555.1ms** |

M4.6's numbers sit inside the same noise band M4.5 §17 already established between back-to-back runs of the identical baseline configuration (M4.4's own two passes spanned 2829.3–2956.8ms mean, a ~4.5% spread for identical work). M4.6's mean/median are marginally *lower* than both prior passes and its p95 is lower than either — but this is reported as **noise, not a real improvement**, for the same reason M4.5 gave: single-machine wall-clock OCR timing on this hardware has demonstrated spread of this magnitude for unchanged code. **No material latency difference, in either direction, is claimed.** The selective routing does not introduce an unacceptable live-camera slowdown — every measured pass across M4.4/M4.5/M4.6 clusters within roughly the same ~2.8–2.95s mean / ~3.6–3.8s p95 band.

Raw data: `m4_6_perf.json`.

---

## 10. Safety Review

**RR confidence calibration — the central question.** M4.5's finding, reproduced exactly in M4.6's byte-for-byte replay (§3): in the selective/production configuration, RR's `ai_high` (≥90% confidence) tier contains 3 correct / 12 wrong reads (80% of RR's high-confidence reads are wrong), while `wrong_ocr_reject_reasons` for RR is `{'low_confidence': 2}` out of 14 wrong OCR reads — **12 of 14 wrong RR reads are still accepted directly, unchanged from M4.5.**

The question this milestone must answer is not "is RR perfect" but **"does production PSM10 introduce a NEW unacceptable safety regression compared with the already-tested M4.4 baseline?"** Evidence says no:

- This is not a new characteristic introduced by promotion — it is the exact, previously-documented M4.3/M4.5 finding, reproduced with **identical counts** (§3) by the real production code path, not a fresh discovery.
- RR's **net** confirmed-wrong count still drops relative to the M4.4 baseline (41→16, per M4.5 report §3/§13, reproduced here), because the much larger, well-calibrated `ai_medium` population (16 correct / 0 wrong) outweighs the poorly-calibrated `ai_high` population.
- No mechanism in `reconcile()`, `check_alerts()`, jump-rejection, or range-rejection was altered by this promotion — RR's wrong-but-accepted reads reach exactly the same downstream handling (display + confirmed state, no dedicated RR alert threshold beyond `RANGE_BOUNDS`) they did under M4.4/M4.5.
- **This is not fixed by this milestone, and is not claimed to be.** It is carried forward as a known, monitored condition per M4.5's own explicit instruction and this milestone's own brief.

**Verdict: no new safety regression from RR's confidence calibration.** The characteristic is real, unresolved, and should be watched in any live deployment — but it predates this promotion and is not worsened by it.

**Other safety checks, all verified with evidence, no regression found:**

- **HR alarm behavior:** identical `"Heart Rate Low"` warning fired at `hr=43` in both M4.5's and M4.6's real live E2E runs (§7/§8); HR's full confirmed-state/rejection-reason/confidence-tier profile is byte-for-byte identical to M4.4/M4.5 (§3).
- **HR=0 range acceptance:** `RANGE_BOUNDS["hr"]=(0,250)` is untouched by this milestone (only `ocr.py`'s dispatch changed); HR routing/rejection-reason counts are identical to M4.5 (§3).
- **Jump rejection:** `JUMP_LIMITS` untouched; HR's `jump_rejected`×5 count reproduced exactly (§3).
- **SpO2 critical alerts:** verified live — `spo2=65.0` reached the confirmed state and fired a real `SpO₂ CRITICALLY LOW` critical alert over the real WebSocket, persisted to SQLite (§7/§8) — direct, real-transport evidence the critical-alert path works with the promoted routing.
- **NIBP persistence:** `150.0/80.0` reached DB persistence identically to M4.4/M4.5 (§7).
- **Temp Fahrenheit conversion:** `98.6°F → 37.0°C` reached the confirmed state live (§7), and unit-corrected confirmed accuracy remains 100.0% across the full dataset (§3).
- **EtCO2 low-confidence behavior:** byte-for-byte identical to M4.4/M4.5 — still below `CONFIDENCE_MEDIUM_MIN` on this dataset (§3), not silently "fixed" by this milestone.

**No critical safety regression was found anywhere in this milestone's evidence.**

---

## 11. Remaining Limitations

Stated plainly, per this milestone's own instruction not to overclaim:

- **This is validated on the available external-monitor dataset and verified through the complete offline application pipeline** — 52 frames, one continuous recording, one external monitor, one camera framing. It is **not** validated against a second monitor, vendor, lighting condition, or camera angle.
- This result does **not** show that Tier-2 generalizes to all anaesthesia monitors.
- This result is **not** a clinical validation.
- This result does **not** establish the system is safe for clinical deployment.
- **RR's confidence-calibration issue is real and unresolved** (§10) — carried forward from M4.3, reconfirmed present and unchanged by both M4.5's eval engine and now M4.6's real production engine. Any deployment decision should treat this as a live, monitored characteristic, not a solved problem.
- **Session cleanup and Demo Mode isolation** were not independently re-exercised this milestone beyond confirming no session-lifecycle or demo-mode code was touched (§8) — this milestone's scope was the OCR dispatch change and its direct consequences, not a full re-audit of unrelated subsystems.
- **The four upstream candidate-generation/FieldCNN issues** documented since M4.1/M4.2 (HR under-crop/fragmentation, SpO2/RR competing-candidate selection, RR→not_a_vital FieldCNN miss) remain open and unaddressed — this milestone changes nothing upstream of the OCR dispatch.
- **NIBP-mean's own OCR misread and EtCO2's genuinely-low confidence ceiling** (both discovered by M4.4, unresolved by M4.5) remain open and untouched by M4.6.
- **Performance measurements (§9) are single-machine, single-process wall-clock timings** with a demonstrated ~4–5% run-to-run noise band on identical code; they are not a controlled benchmark and should not be read as precise production latency guarantees.

---

## 12. Files Changed

**Production files modified:**
```
backend/app/pipeline/ocr.py   (added _DIGIT_PSM10_CONFIG, _PSM10_VITALS, and one new dispatch branch — nothing else)
```

**Tests added:**
```
backend/tests/test_m4_6_production_promotion.py
```

**Evaluation / report artifacts added:**
```
backend/app/eval/m4_6_production_promotion.py
backend/app/eval/tier2_data/external_monitor_video/m4_6_report/
  m4_6_raw_records.json
  m4_6_timeline_production_interval1000.json
  m4_6_analysis_summary.json
  m4_6_reproduction_check.json
  m4_6_e2e_script.py
  m4_6_e2e_stage1_direct_pipeline.json
  m4_6_e2e_messages.json
  m4_6_e2e_db_state.json
  m4_6_e2e_uvicorn_stdout.log
  m4_6_perf.py
  m4_6_perf.json
M4_6_PRODUCTION_PROMOTION_REPORT.md   (this report, repo root — matching prior milestones' convention)
```

No previous milestone report or artifact (M1–M4.5) was modified or deleted.

---

## 13. Final Recommendation

**GO.**

The evidence-backed selective PSM routing (SpO2 + RR → `--psm 10`, HR/NIBP/EtCO2/Temp unchanged) is now live in `app/pipeline/ocr.py`, reproduces M4.5's eval-only evidence exactly, passes all 284 backend tests plus 10 new focused dispatch tests, builds clean on the frontend, and was verified working end to end through both a direct production-pipeline call and a real uvicorn/HTTP/WebSocket camera-simulated session with real persistence and real alerts (including a live SpO2 critical alert). Performance is within the established noise band — no slowdown. RR's confidence-calibration issue is real, is not new, is not worsened by this promotion, and is reported here — not hidden — as a condition to monitor going forward. This remains evidence from a single 52-frame recording of one external monitor; it is reported as validated on that dataset and verified through the complete offline application pipeline, not as general or clinical validation.

---

## Git Status

```
$ git status --porcelain -- backend/app/ backend/tests/
 M backend/app/api/pipeline.py            (pre-existing M3 work, untouched by M4.6)
 M backend/app/pipeline/ocr.py            (M4.4 + M4.6's promoted routing)
 M backend/app/pipeline/read_frame.py     (pre-existing M3 work, untouched by M4.6)
 M backend/app/pipeline/types.py          (pre-existing M3 work, untouched by M4.6)
 M backend/app/validation/reconcile.py    (pre-existing M4.4 work, untouched by M4.6)
 M backend/app/validation/rules.py        (pre-existing M4.4 work, untouched by M4.6)
 M backend/app/ws/vitals.py               (pre-existing M3 work, untouched by M4.6)
 M backend/tests/test_validation.py       (pre-existing M4.4 work, untouched by M4.6)
?? [new eval scripts, tier2_data/, new tests — all pre-existing M1-M4.5 additions plus this
    milestone's own new files listed in §12]
```

No commits made. No tags made. No `git commit`/`git tag` run. All git operations are left for the user, per instruction.
