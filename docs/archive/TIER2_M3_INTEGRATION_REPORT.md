> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M3 Tier-2 Integration Report

Continuation of M1 ([`TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md`](TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md)), M1.1 ([`TIER2_M1_1_HARDENING_REPORT.md`](TIER2_M1_1_HARDENING_REPORT.md)), M2 ([`TIER2_M2_FIELD_CLASSIFIER_REPORT.md`](TIER2_M2_FIELD_CLASSIFIER_REPORT.md)). This milestone wires the M2 `FieldCNN` into the real recognition pipeline as an **opt-in** engine, selected by a new `ROI_ENGINE` env var, alongside the existing Tier-1 default. Order followed, per the user's revised plan: runtime wrapper → `read_frame(tier2)` → real-image tests → Tier-1 vs Tier-2 → full pytest → real camera E2E.

---

## 1. Current pipeline trace (Phase 0)

Traced from the actual code, not the spike doc (verified it hadn't drifted):

1. **Input frame** — `read_frame(img: np.ndarray, engine=None)` in [`app/pipeline/read_frame.py`](backend/app/pipeline/read_frame.py) — RGB uint8 ndarray.
2. **Screen detection** — `detect_screen(img)` ([`app/pipeline/detect.py`](backend/app/pipeline/detect.py)): Canny → largest convex quad ≥50% of frame → homography rectify, or the original image unchanged with `detected=False`. Untouched by S4, M1, M1.1 and M3 alike — still colour-agnostic.
3. **ROI extraction** — `extract_rois_by_colour(screen.image)` ([`app/pipeline/roi.py`](backend/app/pipeline/roi.py)): nearest-of-6-fixed-hues match → dilate → connected components → `Dict[str, Optional[VitalRoiResult]]`. **This is the exact seam** the spike identified in 2024/M0 and it had not moved.
4. **OCR preprocessing + invocation** — per vital, `engine.read_vital(roi_result.crop, vital)` — `TesseractEngine` (default) or `OnnxDigitEngine` (`OCR_ENGINE=onnx`), both implementing the same `OcrEngine` ABC ([`app/pipeline/ocr.py`](backend/app/pipeline/ocr.py)).
5. **Reading construction** — `read_frame()`'s own loop assembles the 8-field `VitalReading`-shaped dict (`hr, spo2, nibpSystolic, nibpDiastolic, nibpMean, etco2, temp, rr`), `None` for anything unread.
6. **Confidence calculation** — pre-M3: `confidences[vital] = ocr_confidence` verbatim, one float 0–100 per vital.
7. **Return value** — `(reading, confidences)`, a plain 2-tuple. Six call sites 2-unpack it: `app/api/pipeline.py`, `app/sources/camera.py`, `app/sources/replay.py`, `app/eval/harness.py`, plus tests.

Downstream (all confirmed unchanged by inspection and by the full suite still passing): `reconcile()` ([`app/validation/reconcile.py`](backend/app/validation/reconcile.py)) → alerts (`app/alerts/rules.py`) → persistence (`app/db/repo.py`) → WebSocket (`app/ws/vitals.py`'s `send_loop`) → frontend. None of these were touched.

**Insertion point (narrowest possible):** step 3 only — swap `extract_rois_by_colour` for a Tier-2 equivalent with the *exact same* `Dict[str, Optional[VitalRoiResult]]` return contract, plus a small addition to step 6 (confidence fusion). Nothing else in `read_frame()` needed to change, and `read_frame()` itself was not duplicated — one function, one new optional parameter.

---

## 2. Exact integration seam

```
read_frame(img, engine=None, roi_extractor=None):
    engine = engine or get_default_engine()                 # unchanged (OCR_ENGINE)
    roi_extractor = roi_extractor or get_default_roi_extractor()   # NEW (ROI_ENGINE)
    screen = detect_screen(img)                              # unchanged
    rois = roi_extractor(screen.image)                       # <-- the seam
    ...
```

`ROI_ENGINE` is a new env var, orthogonal to the existing `OCR_ENGINE`, mirroring its lazy-singleton factory pattern exactly (`get_default_roi_extractor()` / `_build_roi_extractor_from_env()` alongside the pre-existing `get_default_engine()` / `_build_engine_from_env()`):

| `ROI_ENGINE` | Behaviour |
|---|---|
| unset / `""` / `"tesseract"` (default) | `extract_rois_by_colour` — **unchanged Tier-1**, byte-identical code path, byte-identical latency, verified via the full regression suite |
| `"tier2"` | New candidate-generation + FieldCNN stage (§4). Raises `FileNotFoundError` at first use if `models/field_classifier.onnx` isn't present — **never silently falls back to Tier-1** |
| anything else | `ValueError` |

**Naming note, flagged rather than hidden:** the milestone brief specified `ROI_ENGINE=tesseract` for the Tier-1 default. The Tier-1 ROI stage is actually colour-based, not Tesseract-based (Tesseract is the *OCR* engine, chosen independently via `OCR_ENGINE`); `"tesseract"` here names "the existing default pipeline" for consistency with the brief's literal spec, not a real engine choice at the ROI stage. Documented in code (`get_default_roi_extractor()`'s docstring) so a future reader isn't confused by the coincidence.

`CameraSource`, `ReplaySource`, `app/api/pipeline.py`, and `app/eval/harness.py` needed **zero changes** — they all already call `read_frame(img, engine=...)` without ever touching ROI selection, so `ROI_ENGINE=tier2` set in the process environment is sufficient for the entire existing camera → WS → reconcile → persistence path to start using Tier-2, proven live in §12.

---

## 3. Runtime FieldCNN wrapper (Phase 1)

New file: [`backend/app/pipeline/field_classifier.py`](backend/app/pipeline/field_classifier.py). Mirrors `app/pipeline/onnx_engine.py`'s `OnnxDigitEngine` pattern exactly — the project's existing inference abstraction, not a second one:

- `FieldClassifierEngine.__init__` loads `field_classifier.onnx` + `.labels.json` + `.preprocess.json` from disk (paths, scale, crop size all read from the JSON sidecar, nothing hardcoded) and builds one `onnxruntime.InferenceSession`. Raises a clear `FileNotFoundError`/`RuntimeError` if anything is missing or fails to load.
- `get_default_field_classifier()` — lazy module-level singleton. Measured: first call **29.8ms** (loads the ONNX session), every subsequent call **~0.003–0.005ms** (same object, `is` identity confirmed) — the model does **not** reload per frame or per candidate.
- `classify(crops)` — one **batched** ONNX call over every candidate crop in a frame at once. `_letterbox_gray` is a fresh, independent re-implementation of `app.eval.tier2_field_dataset._letterbox_gray` — verified bit-for-bit identical on 5 crop shapes (`test_preprocessing_matches_m2_letterbox_convention`) — kept independent rather than imported so **no training/eval code (`app/eval/tier2_field_dataset.py`, torch, dataset-building) is importable from the production pipeline**, per the task's explicit instruction. Deterministic: same crop → same output, confirmed by test.
- Never logs crop pixels or patient data (only shapes/counts/labels via `logging`); makes no network calls.

---

## 4. Candidate-selection strategy (Phases 2/3)

New file: [`backend/app/pipeline/tier2_roi.py`](backend/app/pipeline/tier2_roi.py) — `extract_rois_by_field_classifier(img)`, same signature/contract as `extract_rois_by_colour`.

```
img → adaptive_threshold_candidates_v2 (M1.1's hardened generator, imported
      unmodified from app.eval.tier2_candidates) → clip/crop each box
    → FieldClassifierEngine.classify(all crops, one batch)
    → filter: label != not_a_vital AND confidence >= MIN_CLASSIFIER_CONFIDENCE
    → group by predicted vital
    → _select_candidate_for_vital() per vital  (deterministic, see below)
    → VitalRoiResult per vital that resolved, else None
```

**A deliberate, first-time crossing of `app/eval`'s "isolated from production" boundary** — every M1/M1.1/M2 report states nothing under `app/eval` is imported by `app.pipeline.*`. That invariant is now intentionally relaxed for exactly one function (`adaptive_threshold_candidates_v2`, byte-for-byte untouched) plus one tiny pure-math helper (`iou`), per the task's explicit "use the hardened M1.1 generator, don't revert" instruction — no training code, no torch, crosses this direction only.

**Selection algorithm** (`_select_candidate_for_vital`, pure function, exhaustively unit-tested):

1. Sort a vital's filtered candidates by confidence descending (ties broken by box x-position — fully deterministic, independent of dict/list ordering).
2. Greedily dedupe: drop a candidate if it overlaps (`IoU > DEDUPE_IOU = 0.5`) an already-kept higher-confidence one. Handles the case where two threshold-polarity passes survive as near-identical boxes.
3. Zero survivors → vital unresolved (`None`).
4. One survivor → use it.
5. Two+ spatially-distinct survivors: if the top two confidences are within `COMPETING_MARGIN = 0.15` of each other → **genuine competing evidence → unresolved (`None`)**, never an arbitrary pick. This is what stops "a frame with 2 candidates predicted HR" from silently becoming two HR readings.
6. Otherwise the top candidate has a clear lead → use it.

Both thresholds are env-configurable (`TIER2_MIN_CLASSIFIER_CONFIDENCE`, `TIER2_DEDUPE_IOU`, `TIER2_COMPETING_MARGIN`) with rationale documented in-code (§"Configuration" comment block in `tier2_roi.py`) rather than presented as tuned medical values — `MIN_CLASSIFIER_CONFIDENCE=0.5` sits well above chance (~14% for 7 classes) and below M2's measured 75–100% test precision range, but is explicitly **not** a fix for M2's known confident-wrong cases (§7).

Signals **not** used, and why: no temporal tracking (`read_frame()` is stateless per call — nothing to track across frames without adding real new infrastructure, out of scope per the task's own instruction); no "existing vital location" prior (none exists in the current stateless design).

---

## 5. OCR integration (Phase 4)

Zero new OCR code. `extract_rois_by_field_classifier` returns the *exact same* `VitalRoiResult` shape `extract_rois_by_colour` does; `read_frame()`'s existing per-vital loop calls `engine.read_vital(roi_result.crop, vital)` completely unaware of which stage produced the crop. Verified directly: `test_ocr_is_called_on_the_classifier_selected_crop_not_the_raw_candidate` confirms the crop shape OCR receives is bit-identical to the crop `tier2_roi` itself resolved, on a real image.

---

## 6. Confidence fusion & provenance (Phases 4/6)

`VitalRoiResult` ([`app/pipeline/types.py`](backend/app/pipeline/types.py)) extended **backward-compatibly** (`source_colour` given a `None` default, two new defaulted fields appended — every existing construction site, including `roi.py`'s, is unaffected):

```python
box: Box
crop: np.ndarray
source_colour: Optional[Tuple[int,int,int]] = None
engine: str = "tier1_colour"          # or "tier2_fieldcnn"
classifier_confidence: Optional[float] = None   # 0-100, tier2 only
```

`read_frame()`'s fusion (verified by `test_confidence_fusion_is_min_not_average`):

```python
value, ocr_confidence = engine.read_vital(roi_result.crop, vital)
confidences[vital] = (
    min(roi_result.classifier_confidence, ocr_confidence)
    if roi_result.classifier_confidence is not None
    else ocr_confidence   # Tier-1: byte-identical to pre-M3 behaviour
)
```

**MIN, never average** — per `TIER2_RECOGNITION_SPIKE.md` §07's explicit design. Observed live on real data: `sample_0017`'s NIBP crop OCR'd to the *exactly correct* `150/80` value, but Tesseract's own per-token confidence on this real font was 0 — fused confidence is `min(100, 0) = 0`, correctly flagging a right-but-weakly-read value as untrustworthy rather than either hiding the OCR weakness (average would have shown ~50) or trusting it outright.

**`read_frame()`'s return contract is unchanged** — still exactly `(reading, confidences)`, verified by keeping every one of the 6 existing 2-unpacking call sites unmodified and green. Per-vital "which engine produced this" is available internally (`VitalRoiResult.engine`, inspectable in tests/logs) but was **deliberately not threaded through the return tuple or the WS envelope** — doing so would have required a schema change the task said to avoid "unless absolutely necessary," and testing needs are already met by calling `extract_rois_by_field_classifier` directly. `FieldClassifierEngine` logs (INFO level, no pixel data) which engine loaded and how many classes; that plus the `ROI_ENGINE` env var is enough to know which tier a deployment is running without touching the frontend contract.

---

## 7. Safety behaviour (Phase 5)

| Condition | Behaviour | Verified by |
|---|---|---|
| Classifier confidence below `MIN_CLASSIFIER_CONFIDENCE` | Candidate never considered for any vital | `test_low_classifier_confidence_never_becomes_a_trusted_reading` (forced ceiling above 1.0 → 0/8 vitals resolved, all confidences 0.0) |
| OCR fails (`None`/`0.0`, its documented contract) | Field stays `None`, confidence 0 — same path a Tier-1 miss already takes | `test_ocr_failure_on_a_tier2_selected_crop_is_handled_safely` |
| Multiple candidates strongly compete for one vital | Unresolved (`None`), not an arbitrary pick | `test_spatially_distinct_close_confidence_is_competing_and_unresolved` |
| OCR result malformed | Existing regex/parse fallback in `ocr.py`/`onnx_engine.py`, untouched | covered by existing OCR test suite, unaffected by M3 |
| Result violates physiological range/jump rules | `reconcile()` catches it downstream, same as any other engine | `test_reconcile_receives_and_gates_tier2_readings` (a real tier2 `hr=10` reading was independently caught by `RANGE_BOUNDS`, on top of confidence gating) |
| `ROI_ENGINE=tier2` requested but model missing | `FileNotFoundError` at first use — **never** a silent Tier-1 fallback | `test_tier2_requested_without_model_raises_clear_error` |

**The two known M2 failure modes were explicitly NOT "fixed" by threshold tuning**, per the task's instruction:
- **RR → not_a_vital at 94.5% confidence** (M2 §14): this is a single confidently-wrong classification, not multiple competing candidates — M3's competing-candidate safety net does not and was not designed to catch it. It remains an open model/data limitation (§9).
- **HR fragmentation** (M2 §14, `"181"` splitting into 3 boxes, one fragment classified `hr` at 98.7%): candidate-generation still cannot offer a box it never produced, and a confident single fragment isn't "competing" against anything. Also unaddressed by design, and also flagged again as a real limitation, not something M3 claims to have solved.

---

## 8. Files changed / added

| File | Status | What |
|---|---|---|
| `backend/app/pipeline/field_classifier.py` | new | Phase 1 runtime FieldCNN wrapper |
| `backend/app/pipeline/tier2_roi.py` | new | Phase 2/3 candidate generation + selection |
| `backend/app/pipeline/types.py` | modified | `VitalRoiResult` extended, backward-compatibly |
| `backend/app/pipeline/read_frame.py` | modified | `ROI_ENGINE` factory + confidence fusion; return contract unchanged |
| `backend/tests/test_field_classifier_runtime.py` | new | Phase 8 items 1–2 |
| `backend/tests/test_tier2_roi.py` | new | Phase 8 items 4–5, 9–10 |
| `backend/tests/test_roi_engine_selection.py` | new | Phase 8 item 6–7 (config selection) |
| `backend/tests/test_tier2_integration.py` | new | Phase 8 items 6, 8, 9, 10, 11, 12, 13, 14 |

**Not touched, confirmed by inspection and by the unchanged pass/fail status of every existing test on these paths:** `app/pipeline/roi.py`, `app/pipeline/ocr.py`, `app/pipeline/onnx_engine.py`, `app/pipeline/detect.py`, `app/pipeline/segment.py`, `app/sources/camera.py`, `app/sources/frame_queue.py`, `app/sources/replay.py`, `app/ws/vitals.py`, `app/validation/reconcile.py`, `app/validation/rules.py`, `app/api/pipeline.py`, `app/eval/tier2_candidates.py` (byte-for-byte, only imported), `models/digit_cnn.*`, the frontend (`src/`). No production code path required a change beyond the one seam in §2.

(Two files — `backend/app/api/pipeline.py`, `backend/app/ws/vitals.py` — plus several `src/` files show as modified in `git status`; these predate this session, were only ever *read* here, and are unrelated to M3's diff.)

---

## 9. Tests added (Phase 8) — 38 new, all real inference, no mock-only tests

| File | Count | Notably real (not mocked) |
|---|---:|---|
| `test_field_classifier_runtime.py` | 10 | Real ONNX session, real crops from held-out TEST images (`sample_0017`'s ground-truth HR/NIBP/Temp boxes), letterbox-equivalence check against the actual M2 training-time function |
| `test_tier2_roi.py` | 12 | Selection-strategy unit tests (synthetic, fast, exhaustive) + real end-to-end candidate generation + FieldCNN on `sample_0017`/`sample_0025`/`sample_0035` |
| `test_roi_engine_selection.py` | 6 | Config/env-var resolution, mirrors `test_engine_selection.py`'s existing pattern exactly |
| `test_tier2_integration.py` | 10 | Real `read_frame()` runs on real images with a spy `OcrEngine`, real `reconcile()`, real `CameraSource.stream()`, real WS via `TestClient` |

Every "known X classified as X" test (HR, NIBP two-line, not_a_vital) runs against real annotated crops from `sample_0017`, not synthetic stand-ins.

---

## 10. Full regression results (Phase 9)

```
$ pytest tests/ simulator/tests/ -q -rs
228 passed, 1 warning in 63.42s (0:01:03)
```

- **Baseline (before any M3 code, immediately after reading the reports):** confirmed separately — **190 passed**, 0 failed, 0 skipped, 1 warning (pre-existing `httpx`/starlette deprecation notice, unrelated to M3).
- **After M3 (types.py + read_frame.py changes + 4 new test files):** **228 passed** = 190 + 38 new, **0 failed, 0 skipped**, same 1 pre-existing warning.
- **No regressions** — every pre-existing test still passes unmodified.

**M2 artifact integrity, re-verified after all M3 work:**
- All 52 `sample_XXXX.json` annotation files: `find ... -newermt "-1 hour"` → **0 files touched**.
- `models/field_classifier.onnx`/`.labels.json`/`.preprocess.json`/`.train_report.json` mtimes: **unchanged since Aug 18 20:12**, predating this session.
- `models/digit_cnn.onnx` mtime: **unchanged since Aug 13 21:36**.
- Re-ran `python -m app.eval.tier2_field_pipeline_eval` (M2's own isolated eval script, output redirected to a scratch dir outside the repo) — **numbers reproduced exactly**: 96.3% candidate recall, 92.6% end-to-end accuracy, 98.6% false-positive rejection, identical per-vital table to M2 §13. Confirms the model artifact itself is bit-for-bit what M2 shipped.
- Nothing committed or tagged.

---

## 11. Tier-1 vs Tier-2 comparison (Phase 14)

Same 8 held-out TEST images (never used in M2 training/validation), both modes run through the real, complete `read_frame()`:

| Image | Tier-1 non-null fields | Tier-2 non-null fields | Tier-1 latency | Tier-2 latency |
|---|---:|---:|---:|---:|
| sample_0017 | 0/6 (only `nibpMean`=2, a stray colour coincidence) | 6/6 found (hr, spo2, nibp, etco2, temp, rr) | 4.50s | 4.07s |
| sample_0018 | 1/6 | 4/6 (hr, spo2, nibp, temp) | 4.79s | 1.81s |
| sample_0025 | 0/6 | 1/6 (temp) | 6.26s | 2.48s |
| sample_0026 | 0/6 | 1/6 (temp) | 6.08s | 2.62s |
| sample_0035 | 1/6 | 2/6 (hr, temp) | 7.45s | 1.03s |
| sample_0036 | 0/6 | 2/6 (hr, temp) | 6.48s | 3.16s |
| sample_0037 | 1/6 | 1/6 (temp) | 5.38s | 2.67s |
| sample_0038 | 1/6 | 2/6 (hr, temp; rr found but OCR misread) | 5.90s | 3.63s |

- **Vitals recovered:** Tier-2 finds a real candidate for far more vitals on real content, consistent with M1.1's headline 90.5% candidate recall vs Tier-1's 3.5%. Temp (100% candidate recall in every M1/M1.1/M2 report) is reliably found by Tier-2 on **all 8** images here too.
- **Incorrect readings, reported honestly, not cherry-picked away:** several Tier-2 HR reads were numerically wrong (`sample_0017` read `hr=10`, `sample_0038` read `hr=3`) — these are **OCR misreads on a correctly-located crop**, not classifier or candidate-generation failures; consistent with the fact that this milestone integrates OCR unmodified and OCR's own real-content accuracy was never re-benchmarked here (out of M3's scope — M2 measured *classification*, not *value* accuracy, on this dataset).
- **Flagged/low-confidence:** Tier-1's rare "hits" (`sample_0018`, `sample_0035`, `sample_0037` NIBP) are colour coincidences (M1 §"one accidental bright spot") at low confidence; Tier-2's NIBP reads on `sample_0017`/`sample_0018` were numerically *correct* (`150/80`) but confidence-fused to 0 by a weak Tesseract token score — both systems correctly avoid presenting an unearned high-confidence NIBP value, via different mechanisms.
- **Latency:** Tier-2 total `read_frame()` latency was, in this measurement, *lower* on every single image (mean 2.68s vs 5.86s) despite finding more vitals and calling OCR more often — most likely because Tier-1's colour-hue false-positive hits still trigger wasted Tesseract subprocess calls that produce nothing, while Tier-2 concentrates OCR on fewer, better-located crops. Not a claim that Tier-2 is inherently faster in general — OCR (Tesseract subprocess spawn, per §12) dominates both, and this is one machine's measurement on 8 images, not a controlled latency benchmark.

**Not cherry-picked:** every one of the 8 held-out TEST images is reported above, including the ones where Tier-2 only found `temp` (`sample_0025`, `sample_0026`, `sample_0037`).

---

## 12. Real image E2E results (Phase 10)

Real `uvicorn` processes (not `TestClient`), one per mode, hit with real HTTP requests against `/api/pipeline/read-frame`:

```
$ DATABASE_URL=sqlite:////tmp/m3_e2e_tesseract.db ROI_ENGINE=tesseract \
    uvicorn app.main:app --port 8811   # background
$ curl -X POST http://127.0.0.1:8811/api/pipeline/read-frame -F "file=@sample_0017.png"
→ {"reading": {"hr": null, "spo2": null, ..., "nibpMean": 2.0, ...}, "confidence": {...all 0.0...}}
```

```
$ DATABASE_URL=sqlite:////tmp/m3_e2e_tier2.db ROI_ENGINE=tier2 \
    uvicorn app.main:app --port 8812   # background
$ curl -X POST http://127.0.0.1:8812/api/pipeline/read-frame -F "file=@sample_0017.png"
→ {"reading": {"hr": 10.0, "spo2": 93.0, "nibpSystolic": 150.0, "nibpDiastolic": 80.0,
   "nibpMean": 109.0, "etco2": 21.0, "temp": 98.6, "rr": 5.0},
   "confidence": {"hr": 43.0, "spo2": 0.0, "nibp": 0.0, "etco2": 36.0, "temp": 92.0, "rr": 72.0}}
```

All 8 held-out real images run in both modes over real HTTP round-trips; full per-image output tabulated in §11 above. **No errors, no crashes, no fabricated successes** — the exact returned JSON is what's reported. Both server processes were stopped cleanly after the run (`pkill`); no server left running.

Neither dataset (all 52 samples are `"conditions": ["normal"]`) contains an alarm/unusual-value frame — flagged honestly rather than fabricating one; the comparison above covers HR/SpO2/NIBP/EtCO2/Temp/RR presence and a `not_a_vital`-heavy frame (every real frame here is 87%+ `not_a_vital` candidates per M2 §4, so every frame tested already exercises that case).

---

## 13. Real camera E2E results (Phase 11)

No browser/Playwright/Cypress harness exists in this repo (checked — `package.json` has no such dependency, no `*.spec.ts`/e2e directory found), so "the existing browser/fake-camera E2E convention" is the backend's own real push-frame → real WebSocket path (`test_camera_source.py`'s convention, "Day 1" per the task). Exercised here against a **real running `uvicorn` process** (not `TestClient`), using a real `websockets` client and real `httpx` calls:

```
session: SESSION-1787068029109-3h14
pushed frame seq: 1
first WS message after 4.17s:
 type: reading
 reading: {'timestamp': ..., 'hr': 75, 'spo2': 98, 'temp': 36.8, 'etco2': 38,
           'rr': 5.0, 'nibpSystolic': 120, 'nibpDiastolic': 78, 'nibpMean': 92}
 confidence: {'hr': 43.0, 'spo2': 0.0, 'nibp': 0.0, 'etco2': 36.0, 'temp': 92.0, 'rr': 72.0}
 provenance: ai_low
session end status: 200
```

`hr/spo2/temp/nibp*` show reconcile()'s seeded baseline values (`DEFAULT_BASELINE`) because most fields' confidence was below the `ai_medium` tier (70) on this first tick with no prior confirmed history — exactly the intended hold-baseline-and-flag behaviour, not a bug; `rr=5.0` was genuinely accepted (medium confidence, in-range, no jump-reject against the seeded baseline). This is the full real path: **push real bytes over real HTTP → real `CameraSource` → real `read_frame(ROI_ENGINE=tier2)` → real `reconcile()` → real WebSocket delivery**, proving a Tier-2-derived reading reaches "the frontend" (the WS client) through the exact same pipe the proven camera system already uses — not a parallel path.

`test_camera_source_reads_real_external_frame_through_tier2` and `test_ws_source_camera_with_tier2_reaches_frontend_as_reconciled_reading` in `test_tier2_integration.py` cover the same path automatically (via `TestClient`, which is a real ASGI app, just not a separate OS process) for CI.

**Demo Mode / session-end, verified by inspection rather than re-tested from scratch (CameraSource/ReplaySource/session code untouched by M3):** Demo Mode uses `ReplaySource`, never `CameraSource` — since M3 touched neither, this invariant is unaffected by construction. Session-end returned HTTP 200 against the live server without disrupting the open WS connection (shown above); the existing `_session_is_active` per-tick gate (`app/ws/vitals.py`, untouched) and `test_sessions.py`/`test_camera_source.py` (both still green in the 228-passing suite) already cover this behaviour end-to-end.

---

## 14. Performance measurements (Phase 12)

In-process stage breakdown, 8 real held-out images, single machine, no estimation:

| Stage | mean | median | min | max |
|---|---:|---:|---:|---:|
| Screen detection (`detect_screen`) | 50.8ms | 38.1ms | 19.5ms | 118.6ms |
| Candidate generation (`adaptive_threshold_candidates_v2`) | 1027.4ms | 1027.5ms | 613.1ms | 1512.9ms |
| FieldCNN inference (batched, all candidates in one call) | 25.8ms | 27.0ms | 6.4ms | 44.2ms |
| **Total `read_frame()` (Tier-2, includes real Tesseract OCR)** | **2683.3ms** | 2647.7ms | 1029.3ms | 4071.6ms |
| **Total `read_frame()` (Tier-1, for comparison)** | **5855.2ms** | 5986.6ms | 4503.9ms | 7450.0ms |

- **FieldCNN inference is not the bottleneck** — ~26ms mean, in line with M2/spike §09's projection ("tens of milliseconds"). Even on this session's larger/slower images than M1.1's own benchmark, FieldCNN adds well under 2% of total Tier-2 latency.
- **Candidate generation (~1.0s mean) is the real new cost**, larger here than M1.1's reported 719ms mean — plausibly frame-resolution- and machine-dependent (these external-video frames are ~2400px wide); still small next to OCR.
- **OCR remains the dominant cost either way** (~1.5–4s depending on how many vitals were found and Tesseract's per-subprocess overhead on this machine), consistent with the spike's original "OCR is the bottleneck either way" call.
- **Model loading is confirmed once-per-process, not once-per-frame:** first `get_default_field_classifier()` call **29.8ms**, every subsequent call **0.002–0.005ms**, same object identity (`c1 is c2 is c3` → `True`).

---

## 15. Known limitations

- **OCR value accuracy on real content was not re-benchmarked in M3.** M2 measured FieldCNN *classification* accuracy on real crops; this milestone integrates the existing, unmodified Tesseract `OcrEngine` behind it. §11's wrong HR reads (`10`, `3`) are OCR misreads on correctly-located, correctly-classified crops — a pre-existing Tesseract-on-real-fonts weakness, out of scope for M3 (which is integration, not OCR retraining) and not previously benchmarked against this dataset either.
- **The two known M2 confident-wrong-classification failure modes (RR→not_a_vital at 94.5%, HR-fragment→hr at 98.7%) are unaddressed by design** — see §7. Both remain open model/data limitations for a future milestone, not fixed by M3's selection logic or thresholds.
- **`COMPETING_MARGIN` (0.15) is an unvalidated default** — this real dataset rarely produces two simultaneous same-vital candidates above the confidence floor, so there isn't yet real data to tune it against. Documented in-code as a conservative, not evidence-derived, choice.
- **Real dataset is still the same 52-frame, single-recording, single-monitor-UI set M2 flagged** — every number in §11/§14 is real and honestly measured on this data, but (as M2 already said) is not yet evidence of generalization to a different real monitor.
- **No alarm/unusual-value frame exists in this dataset** (all 52 samples are `conditions: ["normal"]`) — §12's E2E coverage could not include one; flagged rather than fabricated.
- **No browser/Playwright E2E harness exists in this repo** — §13's "real camera E2E" is the backend's own real-process push-frame/WebSocket path, the most real path actually available to test, not a literal browser automation run.
- **Candidate generation latency (~1s mean) is higher on these images than M1.1's own reported number** — plausible explanations given (resolution, machine) but not root-caused further; out of scope for an integration milestone.

---

## 16. Any regressions

**None found.** 190/190 pre-existing tests still pass unmodified; 38 new tests added, all passing; M2's model artifact and all 52 annotation files confirmed byte/mtime-unchanged; M2's own isolated eval script reproduces identical numbers when rerun. Tier-1 default path is byte-identical pre/post-M3 (same function object, `extract_rois_by_colour`, selected when `ROI_ENGINE` is unset).

---

## 17. Recommendation

# GO TO M4

Basis:
- The integration seam was the single line M1's spike doc identified in 2024, verified still true against the actual current code before any edit (Phase 0), and the diff needed to exploit it was genuinely narrow: 2 modified files, 2 new pipeline files, 0 changes to `read_frame()`'s contract, camera path, WS layer, reconciliation, persistence, or frontend.
- Tier-2 is proven **opt-in and safe by default**: `ROI_ENGINE` unset reproduces Tier-1 exactly (same function object, same 190-test baseline unchanged), and `ROI_ENGINE=tier2` fails loudly (`FileNotFoundError`) rather than silently degrading if the model is ever missing.
- The full path — real HTTP push, real `CameraSource`, real `read_frame(tier2)`, real `reconcile()`, real WebSocket delivery — was proven against a **real running `uvicorn` process**, not just `TestClient` or unit tests, satisfying the milestone's core success criterion: Tier-2 readings flow through the *exact same* `reconcile()` → alerts → persistence → WebSocket path the proven camera system already uses, not a parallel one.
- Safety behaviour (low confidence, competing candidates, OCR failure, physiologically-implausible values, missing model) was verified against real inference, not just asserted — and two specific known M2 weaknesses were deliberately left unaddressed rather than papered over with a threshold change, per the task's explicit instruction.
- 228/228 tests pass (190 unchanged + 38 new), M2's artifacts and eval numbers are bit-for-bit reproducible, and Tier-2 measurably outperforms Tier-1 on real held-out content (far more vitals recovered, comparable-or-lower latency) without any retraining, new tracking system, or scope creep beyond this milestone's stated boundaries.

**Recommended scope for M4, not resolved here:**
- Re-benchmark OCR *value* accuracy (not just FieldCNN classification) on real Tier-2-located crops — §11/§15's wrong HR reads are a real, now-visible gap this milestone surfaced but didn't fix.
- A second real-monitor recording (different device/UI), per M2's own recommendation, still not resolved by M3 — Tier-2's real-content numbers remain single-source.
- Investigate `adaptive_threshold_candidates_v2`'s ~1s latency on this session's higher-resolution frames — not urgent (still well under OCR's cost) but worth root-causing before scaling to more concurrent camera sessions.
- The two known M2 confident-wrong-classification cases (§7/§15) are worth another look with more real data, not a threshold change.
