> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# M1 Tier-2 Benchmark Report
### Real monitor benchmark + colour-agnostic ROI POC

- **Scope:** benchmark harness + two candidate-generator POCs, scored against ground truth. No CNN training, no production pipeline changes.
- **Reference:** [`TIER2_RECOGNITION_SPIKE.md`](TIER2_RECOGNITION_SPIKE.md) — architecture/decisions already made, not revisited here.
- **New files (all additive, nothing existing modified):**
  `backend/app/eval/tier2_common.py`, `tier2_candidates.py`, `tier2_proxy.py`, `tier2_benchmark.py`, `tier2_data/real_monitor/README.md`.

---

## 1. Current pipeline

Traced directly from source (matches the spike doc — confirmed against the actual implementation, not just re-cited):

```
frame (RGB uint8 ndarray, from PIL.Image.open(...).convert("RGB"))
  → detect_screen()            app/pipeline/detect.py  — Canny → largest convex quad ≥50% area → homography rectify. Colour-agnostic, unmodified.
  → extract_rois_by_colour()   app/pipeline/roi.py      — nearest-hue match against 6 fixed VITAL_COLORS. THE failing stage on real content.
  → OcrEngine.read_vital()     app/pipeline/ocr.py      — Tesseract or ONNX, doesn't know how its crop was found.
  → read_frame() assembles reading + confidence dict     app/pipeline/read_frame.py:62 is the replacement seam.
```

Frames enter via `POST /api/pipeline/push-frame/{channel}` (browser `getUserMedia()` JPEG capture → `frame_queue` → `CameraSource.stream()` → `read_frame()`, confirmed in `app/sources/camera.py` and `tests/test_camera_source.py`) — not a server-side `cv2.VideoCapture`, by deliberate design (Docker/webcam passthrough).

Ground-truth/eval convention (reused unmodified): a dataset dir of `sample_XXXX.png` + `sample_XXXX.json` pairs, `{"rois": {vital: [x,y,w,h], ...}, ...}`, read by `app.eval.harness.load_dataset()`; IoU/homography-warp math copied from `simulator/train/measure_roi_jitter.py` (already-proven, not reinvented).

No `tier2_poc.py` (the previous investigation's proxy script) exists in the repo — it wasn't committed. `tier2_proxy.py` here is a fresh implementation of the same idea (foreign-palette synthetic proxy), not a reuse of prior code.

---

## 2. Dataset

### Real monitor images: **none available.**

Checked, per the M1 task's explicit anti-fabrication constraint:
- The repository itself (`backend/`, `simulator/`, `tests/`, debug output) — no real photos, only synthetic simulator output.
- The session temp workspace — nothing.
- Common Windows screenshot/capture folders (`Pictures/Screenshots`, `Pictures/Camera Roll`, `Desktop`, `Downloads`) — two `Camera Roll` photos from 2026-07-10 were plausible candidates and were checked directly; both are photos of people, not a monitor.

No image was fabricated to fill this gap. **`backend/app/eval/tier2_data/real_monitor/README.md`** is the capture procedure (conditions to vary, exact file-naming/annotation format reusing the repo's own `[x,y,w,h]` `sample_XXXX.json` convention) — ready for 20–50 real photos to be dropped in directly, at which point `python -m app.eval.tier2_benchmark --dataset app/eval/tier2_data/real_monitor` produces the real-monitor numbers with no code changes.

### Synthetic/proxy dataset: 40 images, built for this milestone

`backend/app/eval/tier2_proxy.py` — a **foreign-palette** proxy (same purpose as the spike's own proxy, freshly implemented): 1280×800 canvas, rendered bezel + room background (so `detect_screen()` has an actual quad to find, not a screen-filling frame), 6 fields in a randomized 2×3 grid + one decoy waveform trace, deliberately **not** using `VITAL_COLORS`, Consolas, or the fixed grid/sidebar/compact layouts:

- Font pool: Segoe UI, Arial, Verdana, Tahoma, Calibri, Georgia (proportional — VITAL's own render is monospace).
- Palette: mostly near-greyscale text (white/pale-green/pale-blue on near-black, one light-background/dark-text polarity flip) — deliberately low-saturation, since real clinical monitors commonly render every numeral in **one** colour, not VITAL's per-field rainbow. This is also the most direct way to guarantee the proxy exercises the actual observed failure (Tier-1's `SAT_MIN=100` gate rejects grey text by construction).
- Camera-artifact diversity via `simulator/randomize/augment.py`'s existing `augment_frame()` (glare/dim/blur/noise/occlusion/perspective) — reused unmodified, per the spike's own assessment that this axis is "solid and reusable as-is."

15 clean + 25 `augment=random` samples, merged into `app/eval/tier2_data/proxy_full/` (40 total). **This is proxy data. It is not evidence of real-monitor generalization** — see the domain-gap caveat in section 10.

---

## 3. Tier-1 baseline (actual measured results)

Run via the same `extract_rois_by_colour()` production function, unmodified, over the 40-image proxy set:

**Overall recall: 0.0%** (0/6 vitals, every image). Matches the real-world bug report exactly — Tier-1 finds nothing on a foreign colour palette. This is expected, not a bug in the baseline: `roi.py`'s `SAT_MIN=100` gate rejects the proxy's intentionally-low-saturation text before hue matching ever runs. Measured latency: mean 138.5ms / median 144.8ms / p95 190.1ms (n=115).

---

## 4. Adaptive threshold results (SYNTHETIC / PROXY)

`adaptive_threshold_candidates()` — adaptive Gaussian threshold (both polarities, merged) + connected components, same dilate/box-tightening strategy `roi.py` already uses, minus the colour gate.

| Metric | Value |
|---|---:|
| Overall recall @ IoU≥0.3 | **100.0%** |
| Per-vital recall | 100.0% on all six (hr/spo2/nibp/etco2/temp/rr) |
| Avg candidates/image | 7.8 |
| Avg false positives/image | 1.02 |
| Latency (mean/median/p95, n=115) | 131.2ms / 115.1ms / 167.8ms |

Recall held at 100% across every condition bucket (glare, blur, low-brightness, perspective, occlusion, noise) on this proxy — no degradation observed.

---

## 5. Canny results (SYNTHETIC / PROXY)

`canny_contour_candidates()` — Canny edges + dilate + `findContours`.

| Metric | Value |
|---|---:|
| Overall recall @ IoU≥0.3 | **92.1%** |
| Weakest vitals | rr 75.0%, etco2 85.0% |
| Avg candidates/image | 6.5 |
| Avg false positives/image | 0.50 |
| Latency (mean/median/p95, n=115) | 7.9ms / 7.5ms / 12.4ms |
| Weakest condition | motion_blur 72.2% (n=3 — too few samples to trust this number on its own) |

**Methodology note, kept because it's a real finding, not just a bug fixed in passing:** the first implementation used `cv2.RETR_EXTERNAL`, which returns only outermost contours. Because a rectified crop can retain a thin sliver of bezel right at its own border, that sliver's edge formed one giant enclosing contour that silently swallowed every legitimate interior glyph contour — recall was 0% until switched to `RETR_LIST`. Documented in `tier2_candidates.py` since a real bezel or vignette could reproduce this on an actual photo.

Visual inspection (section 7) also showed Canny producing false-positive candidates directly on the decoy waveform trace in a case where adaptive-threshold correctly ignored it — consistent with its lower per-vital recall and matching the spike's own MSER/Canny discussion about edge-based methods being more sensitive to non-glyph edge content.

---

## 6. Comparison table — SYNTHETIC / PROXY RESULTS (n=40, IoU≥0.3)

| Method | HR | SpO2 | NIBP | EtCO2 | Temp | RR | Overall |
|---|---:|---:|---:|---:|---:|---:|---:|
| Tier-1 colour | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | **0.0%** |
| Adaptive threshold | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | **100.0%** |
| Canny+contour | 95.0% | 97.5% | 100.0% | 85.0% | 100.0% | 75.0% | **92.1%** |

## REAL MONITOR RESULTS

**Not available this milestone — no real photos exist yet (section 2).** This table is intentionally left unfilled rather than populated with proxy numbers relabeled as real. Once `backend/app/eval/tier2_data/real_monitor/` has photos + annotations (`README.md` there has the exact procedure), running:

```
python -m app.eval.tier2_benchmark --dataset app/eval/tier2_data/real_monitor --label "REAL MONITOR RESULTS"
```

fills this table with zero code changes to any file built this milestone.

---

## 7. Visual inspection

8 debug overlays generated at `backend/app/eval/tier2_data/proxy_full/tier2_m1_report/debug/debug_sample_aug_000{0-7}.png` (green = ground truth, cyan = adaptive-threshold candidates, magenta = Canny candidates), all from augmented (glare/blur/perspective/etc.) samples since `debug-n` took the first N in sorted order.

- `debug_sample_aug_0002.png` (perspective-warped): both generators tightly box all six numerals despite the warp; **Canny additionally boxes both decoy-waveform segments as candidates** (a real false positive — an ungated Canny candidate would reach the (not-yet-built) field classifier as a plausible "numeric readout" region), while adaptive-threshold correctly leaves the waveform unboxed.
- Across the sampled set: ground-truth boxes are consistently tight to the rendered value text; adaptive-threshold boxes are similarly tight; Canny boxes run slightly looser/larger (consistent with edge-dilation naturally growing past the true glyph outline more than a fill-based mask does).

(Regenerate the full 40-image set's overlays with `--debug-n 40` if more are wanted for review; not included here to keep this report's file list manageable.)

---

## 8. Performance

Measured on the development machine, n=115 (screen/tier1/candidate stages) or n=60 (rectify-only), never a single reading:

| Stage | Mean | Median | p95 |
|---|---:|---:|---:|
| Screen quad-find (`_find_screen_quad`) | 9.3ms | 8.9ms | 12.9ms |
| Perspective correction (rectify-only, quad already found) | 6.6ms | 6.6ms | 7.9ms |
| Tier-1 colour ROI (baseline, for comparison) | 138.5ms | 144.8ms | 190.1ms |
| Adaptive-threshold candidates | 131.2ms | 115.1ms | 167.8ms |
| Canny+contour candidates | 7.9ms | 7.5ms | 12.4ms |

No CNN inference included (doesn't exist yet, per M1 scope). Net: even the slower candidate generator (adaptive-threshold, ~131ms) is small next to the session's already-measured Tesseract OCR cost of 1.4–1.9s for all 6 vitals (`TIER2_RECOGNITION_SPIKE.md` section 09) — OCR remains the bottleneck either way, consistent with the spike's own conclusion.

One honest discrepancy worth flagging: the spike's original POC reported 50ms/13ms for these two families; this run measured noticeably higher (131ms/7.9ms respectively — Canny is faster here, adaptive-threshold slower). Both this proxy's larger canvas (1280×800 vs. the original 960×560) and different dev hardware/OpenCV build are plausible explanations; the absolute numbers should be re-measured on the real deployment target before being treated as a hard budget, but the *shape* of the result (candidate generation is cheap relative to OCR either way) holds.

---

## 9. Failure modes

From the proxy run (see section 5 for the Canny/RETR_EXTERNAL methodology finding, and section 7 for the visual false-positive finding):

- **Canny+contour is weaker on RR and EtCO2** (75%/85% vs. adaptive-threshold's 100%) and produces more false-positive boxes on non-glyph content (the decoy waveform) than adaptive-threshold does, despite a lower *average* FP-per-image count in aggregate (0.50 vs. 1.02) — the two don't always fail on the same content, which is exactly the ensemble argument the spike's §03 table makes for keeping Canny as a signal rather than the primary.
- **Adaptive-threshold has a higher average false-positive count** (1.02/image) despite 100% recall — worth watching once the field classifier exists, since false positives are exactly what its 7th `not-a-vital` reject class has to absorb (§06/§11); candidate generation is allowed to over-propose, it just can't under-propose.
- **No real-monitor failure modes are known yet** — this is the actual headline finding of this milestone, not a caveat. Every number in sections 3–8 is proxy-only. The single biggest known risk from the spike (§12) — genuine seven-segment LED/LCD digit displays, visually nothing like a rendered vector/UI font — is untested by this proxy, which only renders vector UI fonts. That risk is unchanged by this milestone's work and remains open.
- **Latency numbers should be re-measured on real device/camera-resolution images** before being used as a firm budget (section 8).

---

## 10. Decision

**GO WITH CHANGES** — but the required "change" is data, not architecture or code.

What this milestone establishes with real evidence: the hybrid's candidate-generation stage is technically sound and clearly superior to Tier-1 on colour-agnostic content — 100%/92% recall vs. Tier-1's measured 0%, at latency that stays a rounding error next to OCR's existing cost. Nothing here contradicts a single decision already made in `TIER2_RECOGNITION_SPIKE.md`; if anything, the RETR_EXTERNAL→RETR_LIST fix and the Canny/waveform false-positive finding sharpen the case for the doc's own hybrid choice (candidates + a 7-class reject-capable classifier) over trusting Canny alone.

What this milestone does **not** establish, and cannot fabricate: whether any of this holds on an actual physical monitor. Per this task's own Phase 11 instruction ("the decision must be based on measured real-monitor results") and Phase 10's acceptance criterion explicitly allowing "a documented capture procedure... if hardware access limits immediate collection" — that is the honest state here. Proceeding straight to CNN training (M2 in the spike's own milestone table) on proxy evidence alone would be exactly the "plausible-sounding technique, no real number" trap the spike itself warns against (§03, re: MSER).

**Recommended immediate action:** capture the real-monitor set using `backend/app/eval/tier2_data/real_monitor/README.md`'s procedure, then run the one command that folder documents. That run either confirms this milestone's proxy results (→ clean GO on the spike's M2/M3) or surfaces a real-content gap (most likely candidate per §12: LED/LCD seven-segment digits, which this proxy doesn't model) worth knowing before any training investment.

---

## 11. Recommended M2

Per `TIER2_RECOGNITION_SPIKE.md`'s own milestone table (§12), M2 is the layout/colour/font randomizer — but sequence it *after*, not instead of, the real-monitor capture above, since that capture is now the fastest way to learn whether M2's synthetic-diversity investment is even pointed at the right content style (vector-font UI vs. genuine LED/LCD segments). Not implementing either here — this stays a recommendation, per M1's scope.
