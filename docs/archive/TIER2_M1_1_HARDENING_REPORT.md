> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M1.1 Candidate Generator Hardening Report

Continuation of M1 ([`TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md`](TIER2_M1_EXTERNAL_VIDEO_BENCHMARK_REPORT.md)). Candidate-generation only — no CNN training, no production code touched, same frozen 52-image/199-box annotation set (`backend/app/eval/tier2_data/external_monitor_video/`, unmodified — verified again at the end).

**Command used for the "new" numbers, exactly matching M1's own convention:**
```
python -m app.eval.tier2_benchmark --dataset app/eval/tier2_data/external_monitor_video --label "EXTERNAL REAL-WORLD MONITOR VIDEO (M1.1 hardened v2)" --generators v2
```
`--generators` (new flag, default `v1`) is the only change to `tier2_benchmark.py`'s behavior — omitting it reproduces the original M1 numbers exactly (verified: reran with `--generators v1` to a scratch dir, byte-for-byte identical recall/candidate/FP numbers, latency varying only by normal timing noise). `v1`'s candidate-generator code in `tier2_candidates.py` is untouched; v2 is new, additive functions in the same file.

---

## 1. What failed in M1

| Vital | M1 adaptive recall | Symptom |
|---|---:|---|
| NIBP | 0.0% | Candidates land on the right area (best IoU ~0.22–0.26) but never clear 0.3 |
| EtCO2 | 17.6% | Mostly complete misses (best IoU = 0.000, not near-miss) |
| HR | 58.1% | Near-misses (~0.17–0.30) |
| SpO2 | 85.2% | A handful of the same near-misses as HR |
| Screen detection | 0/52 | No quad found on any frame |
| False positives | 22.9/image | Waveforms, banners, toolbar text all boxed |

## 2. Root cause for each — from actual candidate boxes/masks, not guessed

Investigated directly on the real dataset (`sample_0008` for EtCO2, `sample_0001` for NIBP, `sample_0036` for HR) before writing any fix code:

- **EtCO2 (and part of NIBP):** a thin, long gridline in the raw threshold mask — measured directly: **1286×5px**, one continuous connected component — survives thresholding and bridges two unrelated UI regions into one oversized blob after dilation. `_components_to_boxes`'s own max-size filter then correctly rejects that blob for being too big — but that means the legitimate content merged into it (etCO2's digit) is lost too, not just the blob. Confirmed with `cv2.connectedComponentsWithStats`, not inferred.
- **HR/SpO2:** `sample_0036`'s `"178"` was fragmenting into 2–3 separate components (`"1"`, `"78"`, etc.) with measured gaps of 18–38px between them. The merge-dilation kernel's real per-side reach is `~(kernel_size-1)/2 ≈ 8.5px` (an 18×18 all-ones kernel doesn't grow shapes by the full 18px, a detail the original implementation's own comment didn't account for) — short of both gaps.
- **Screen detection:** swept `min_area_frac` from 0.5 down to 0.01 against real frames — **zero quads found at any threshold**. Not a tunable-parameter problem: these screenshots frame the monitor edge-to-edge with no bezel edge for Canny to lock onto at all. `detect_screen()`'s existing fallback (unrectified frame, `detected=False`, no exception) already handles this correctly — verified, not modified.
- **False positives:** the same gridline/banner/waveform-panel content that causes the merge bug above is also exactly what shows up as false-positive candidates.

## 3. Changes made

All in `backend/app/eval/tier2_candidates.py`, as new `_v2`-suffixed functions (`GENERATORS_V2` dict) alongside the completely untouched originals:

| Fix | What | Where applied |
|---|---|---|
| Line-artifact stripping | Zero out raw (pre-dilation) connected components with extreme aspect ratio spanning a large frame fraction (gridlines/baselines/separators) before dilation ever runs | Both generators |
| Wider merge kernel (anisotropic, horizontal only) | `kernel_width_mult=1.5` for adaptive-threshold; **1.0 (unchanged) for Canny** | Adaptive only — see below |
| Aspect-ratio cap on finished candidates | Reject boxes with w/h outside 0.2–5.0 (every real vital box measured on this dataset is 0.5–2.4) | Both generators |

**Two things were tried, measured on the full 52-image set, and explicitly rejected** — kept in the code (unused, documented) rather than deleted, because the negative result is itself worth keeping:

- **`_merge_vertically_stacked`** (a post-hoc "merge vertically stacked, x-overlapping boxes" pass, built specifically to fix NIBP's two-line split). Line-stripping alone already got NIBP to 82.4% with no merge pass needed. Adding the merge pass on top was then swept across `max_gap_ratio ∈ {0.9, 0.5, 0.3, 0.15}` — **every single setting made overall recall worse** (e.g. adaptive-threshold: 90.5% with no merge vs. 50.8%/57.3%/58.8%/78.9% at those four ratios), by incorrectly bridging already-correct boxes into wrong, larger ones (Temp measured directly: a clean 0.66 IoU candidate collapsed to 0.03 after an incorrect merge). A heuristic that looked reasonable and appeared harmless on a handful of spot-checked cases turned out net-harmful at full-dataset scale — the spot-check sample size was the mistake, not the idea in isolation.
- **Applying the wider kernel to Canny too.** Canny already dilates with `iterations=2` (double adaptive-threshold's `iterations=1`), so its effective reach is already larger; widening on top collapsed HR/SpO2/RR recall to ~0% (swept 1.0/1.2/1.5/2.0/2.5/3.0 — recall degrades starting at any multiplier above 1.0). Canny's v2 default is `kernel_width_mult=1.0`, i.e. unchanged from v1 in that respect — only line-stripping and the aspect filter apply.

## 4–7. Old vs new — recall, false positives, latency, per-vital

Full official benchmark reruns (not the ablation sweeps above — same `tier2_benchmark.py` CLI, same dataset, same IoU≥0.3):

| Method | HR | SpO2 | NIBP | EtCO2 | Temp | RR | **Overall** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tier-1 colour (unchanged) | 0.0% | 0.0% | 41.2% | 0.0% | 0.0% | 0.0% | 3.5% |
| Adaptive **OLD** | 58.1% | 85.2% | 0.0% | 17.6% | 100.0% | 90.7% | 71.4% |
| Adaptive **NEW** | **93.0%** | **88.9%** | **82.4%** | **64.7%** | 100.0% | 90.7% | **90.5%** |
| Canny **OLD** | 39.5% | 11.1% | 0.0% | 0.0% | 100.0% | 30.2% | 42.7% |
| Canny **NEW** | 41.9% | 11.1% | 5.9% | 0.0% | 100.0% | 30.2% | 43.7% |

| Metric | Adaptive OLD | Adaptive NEW | Canny OLD | Canny NEW |
|---|---:|---:|---:|---:|
| Avg candidates/image | 27.9 | 27.9 | 22.2 | 30.3 |
| Avg false positives/image | 22.87 | 23.10 | 20.25 | **28.17** |
| Latency mean | 534ms | 719ms | 14.8ms | 43.3ms |
| Latency median | 535ms | 677ms | 14.2ms | 43.0ms |
| Latency p95 | 635ms | 1149ms | 19.9ms | 54.3ms |

**Reading this honestly:** adaptive-threshold is a large, broad win — every M1.1 acceptance target (§8 below) is met. Canny is a small, narrow win on recall (+1.0pp) bought at a real cost (+7.9 FP/image, ~3x latency, still 0% on EtCO2) — line-stripping helps it a little but doesn't fix its core weakness, and it was never the primary generator in this architecture (`TIER2_RECOGNITION_SPIKE.md` §03/§04 already ranked adaptive-threshold as primary, Canny as an optional ensemble signal). Latency on both stays trivial next to OCR's already-measured 1.4–1.9s (`TIER2_RECOGNITION_SPIKE.md` §09) — the recall/FP tradeoffs matter far more than these latency increases do.

## 8. Acceptance target check

| Target | Adaptive (recommended) | Canny |
|---|---|---|
| ~90%+ overall recall | ✅ 90.5% | ❌ 43.7% |
| No vital near-zero | ✅ weakest is EtCO2 at 64.7% | ❌ EtCO2 still 0.0% |
| NIBP not 0% | ✅ 82.4% | ⚠️ 5.9% (technically non-zero, still weak) |
| EtCO2 not "near 17%" | ✅ 64.7% | ❌ unchanged at 0.0% |

**Adaptive-threshold clears every target. Canny clears none of them fully.** This is consistent with the architecture already chosen in the spike doc (adaptive-threshold primary, Canny as an optional secondary signal, not a gate) — nothing here argues for re-opening that choice, it just confirms it with real numbers instead of the earlier proxy-only ones.

## 9. Visual evidence

New comparison overlays (green = ground truth, magenta = OLD/v1, cyan = NEW/v2), `backend/app/eval/tier2_data/external_monitor_video/tier2_m1_1_debug/`:

- `compare_sample_0001_nibp_fp_heavy_adaptive.png` — NIBP's `150/80`/`(103)` block: OLD candidates are a handful of giant mega-blobs (the entire ECG waveform strip, the entire Pleth panel, the entire CO2 waveform panel each boxed as ONE candidate); NEW replaces them with tight, correct, individual boxes on NIBP, Temp, and the HR/SpO2 alarm-limit text — visibly confirms both the merge-bug fix and a real (if partial, per the aggregate FP numbers) cleanup of the worst false positives on this particular frame.
- `compare_sample_0008_etco2_adaptive.png` — NEW puts a clean, tight box directly on `"34"` (etCO2's current value) where OLD found nothing at all; HR/SpO2/NIBP/Temp/RR are all simultaneously tight and correct in the same frame.
- `compare_sample_0036_hr_adaptive.png` — the `"178"` fragmentation case: NEW's candidate(s) now closely track the full 3-digit ground-truth box.
- `compare_sample_0020_spo2_adaptive.png` and all four `_canny.png` counterparts also generated for direct comparison.

Official v2 debug overlays (ground truth + only-NEW candidates, same style as M1's own debug images): `backend/app/eval/tier2_data/external_monitor_video/tier2_m1_report_v2/debug/debug_sample_0001-0008.png`.

## 10. Any regressions

- **No per-vital recall regression on either generator** — every vital is equal or better, old vs. new (checked all 6 × 2 generators individually).
- **Real regressions elsewhere, reported plainly:**
  - Canny's false-positive rate got worse (20.25 → 28.17/image) — line-stripping frees some content that used to be swallowed into a rejected mega-blob to instead survive as several separate, smaller false-positive candidates.
  - Both generators got slower (adaptive: 534ms→719ms mean; Canny: 14.8ms→43.3ms mean) from the added line-stripping/aspect-filter passes — still trivial next to OCR's cost, but a real, honest cost.
  - Two implementation attempts (`_merge_vertically_stacked`, Canny kernel-widening) were built, measured, and found net-harmful — not shipped, but real engineering time spent on approaches that didn't survive full-dataset validation. Documented in-code rather than hidden.

## 11. Recommendation

# GO TO M2 — CNN classifier

Basis: adaptive-threshold candidate generation, hardened by this milestone, clears every M1.1 acceptance target on the real external-video dataset (90.5% overall, no vital near-zero, NIBP and EtCO2 both pulled far off zero) — the field classifier will not be starved of ground-truth-adjacent candidates to train/evaluate against. The false-positive rate (23/image) is high but is explicitly the reject class's job to absorb, not candidate generation's (`TIER2_RECOGNITION_SPIKE.md` §06/§11) — nothing here suggests that rate is unmanageable for a classifier, only that one is now genuinely needed.

**Recommended scope carried into M2, not resolved here:**
- Use adaptive-threshold as the sole/primary candidate source; Canny's marginal recall gain doesn't currently justify its FP/latency cost as a mandatory second signal — worth revisiting only if M2's classifier ends up needing an ensemble vote, not before.
- EtCO2 (64.7%) is the weakest surviving vital on adaptive-threshold — worth watching in M2's per-vital classifier accuracy, not necessarily worth another candidate-generation iteration first.
- Screen-detection's 0/52 quad-find rate on this dataset is a known, confirmed-not-fixable-by-tuning gap specific to edge-to-edge framed content — doesn't block M2 (candidate generation demonstrably works fine on the unrectified frame), but is a real gap if a future real-camera capture also frames edge-to-edge.

---

Confirmed before finishing: all 52 `sample_XXXX.json` annotation files' mtimes are still older than every run this session (re-checked). 190 existing backend tests still pass. No production code (`read_frame()`, `roi.py`, OCR, ONNX, reconciliation, persistence, WebSocket, `CameraSource`, frontend) was touched. Nothing committed or tagged.
