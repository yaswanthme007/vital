> ⚠️ **SUPERSEDED — ARCHIVED 2026-08-19.** This document is retained only as a
> historical record of what was measured and decided at the time. It does **not**
> describe the current architecture, and several of its conclusions and
> recommendations were later shown to be wrong. Do not use it as guidance.
>
> **Current documentation:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ·
> [`docs/ROADMAP.md`](../ROADMAP.md) · [`docs/EVIDENCE.md`](../EVIDENCE.md)
> See [`docs/archive/README.md`](README.md) for what specifically was superseded.

---

# Tier-2 Generalized Monitor Recognition
### Architecture & Feasibility Spike — No implementation changes

Can VITAL's OCR pipeline read vitals off *any* real anaesthesia monitor, not just its own colour-coded synthetic render? A trace of the current pipeline, three real proof-of-concept experiments, and an engineering answer.

- **Scope:** read-only investigation, zero code changes
- **Trigger:** real monitor photo → 0 vitals read
- **Repo:** `backend/app/pipeline`, `simulator/`

---

## Verdict: Feasible — not in 4 days

**Yes, full Tier-2 is realistically achievable** with this repository's existing tooling — the plugin seam already exists in exactly the right place, a proven glyph-classifier training pipeline already exists, and the screen detector is already colour-agnostic. That's the good news.

The honest number is **~2–3 calendar weeks** for a first benchmarked version — not a single build, and not the original 4-day window (correctly abandoned). The recommended path is a **hybrid**: classical candidate-region generation (proof-of-concept below: 100% recall, ~50ms) feeding a small reused-architecture CNN that classifies each candidate by vital type. A full trained object detector has a higher ceiling but no training infrastructure exists for it today — bigger lift, unproven return.

## Contents

1. [Current pipeline audit](#01--current-pipeline-audit)
2. [The Tier-1 problem, precisely](#02--the-tier-1-problem-precisely)
3. [Candidate approaches + POC](#03--candidate-approaches-with-real-evidence)
4. [Recommended architecture](#04--recommended-architecture)
5. [Training data strategy](#05--training-data-strategy)
6. [Detection target design](#06--detection-target-design)
7. [Confidence fusion](#07--confidence-fusion-design)
8. [Real-monitor benchmark](#08--real-monitor-benchmark-plan)
9. [Performance targets](#09--performance-targets)
10. [Backward compatibility](#10--backward-compatibility)
11. [Failure safety](#11--failure-safety)
12. [Effort, milestones, risks](#12--effort-milestones-and-risks)

---

## 01 — Current pipeline audit

### Where a frame goes today

Traced from `backend/app/pipeline/read_frame.py` outward. Every stage is already interface-clean — the seam Tier-2 needs already exists.

- **`detect_screen()`** — Canny edges → largest convex quadrilateral covering ≥50% of frame → homography rectify. **Zero colour dependency.** This stage is already Tier-2-ready as-is.
- **`extract_rois_by_colour()`** — HSV nearest-hue match against six **fixed** hues (`VITAL_COLORS`), duplicated verbatim between `app/pipeline/roi.py` and `simulator/render/common.py`. Connected components, density-filtered. **This is the exact stage that fails on a real monitor** — see §02.
- **`OcrEngine.read_vital(crop, vital_type)`** — Tesseract (default) or an ONNX CNN, swappable behind one interface. Neither knows or cares how its crop was located.
- **`read_frame()`** assembles the reading dict + per-vital confidence (0–100) → `reconcile()` (range/jump/confidence-tier accept-or-hold, per field, independently) → persistence, alerts, WebSocket — all untouched by anything below.

> **Replacement seam:** `read_frame.py:62` — `rois = extract_rois_by_colour(screen.image)`. A Tier-2 detector only has to return the same `Dict[str, Optional[VitalRoiResult]]` shape. Everything downstream — OCR, reconcile, alerts, persistence, the WebSocket, the whole live pipeline built this session — needs **zero** changes.

---

## 02 — The Tier-1 problem, precisely

### Why the real photo returned nothing

`extract_rois_by_colour`'s entire localization mechanism is: is this pixel within hue-distance 20° (of 180°) of one of six specific hex values VITAL renders its own practice monitor in? A real device's colour key, font, and layout share none of that — the ~50% "confidence" seen on the Regions step was near-miss colour coincidence, not real detection, and Verify correctly reported zero readable vitals rather than guessing.

This is a documented design boundary, not a bug — `roi.py`'s own comment says it mirrors "the frontend colour conventions." Building against *any* real monitor means replacing the localization *signal* itself: from "which fixed colour is this" to "does this region look like a numeric readout, and which vital is it."

---

## 03 — Candidate approaches, with real evidence

### Six options, ranked against three POC experiments

No file access to the photographed device was available in this session, so the POCs below run against a synthetic "foreign-palette" proxy frame — same task shape (six labeled fields, one waveform-like decoy), deliberately built with colours, font, and layout *different* from `VITAL_COLORS` to stand in for an unseen real monitor.

| Rank | Approach | Recall@IoU .3 | Latency | Data/training | Verdict |
|---|---|---|---|---|---|
| 1 | **E — Hybrid:** classical candidates + small CNN classifier | 100%* | ~50ms + CNN | Reuses existing cell-training pipeline | recommended |
| 2 | **B — Adaptive threshold + connected components** | 100% | 50ms | None | POC-validated |
| 2 | **C1 — Canny + contour + dilate** | 100% | 13ms | None | POC-validated |
| 3 | **C2 — Tesseract native text detection** | untested | unknown | None | spike next, on a real photo |
| 4 | **D — Lightweight trained object detector** | untested | unknown | Full detection dataset + training loop (doesn't exist yet) | highest ceiling, biggest lift |
| 5 | **F — Repurpose existing digit CNN for localization** | n/a | n/a | Collapses into D | not a shortcut |
| 6 | **A — MSER** | 0% | 101ms | None | poor fit, POC-confirmed |

\* Candidate-generation recall; classification-into-vital accuracy is a separate, untrained number (see §08).

```
python tier2_poc.py — foreign-palette proxy frame, 960×560, 6 ground-truth fields

adaptive-threshold+CC   candidates=13  recall@IoU0.3=1.00  latency=50ms   mean IoU 0.82–0.87
Canny+contour           candidates=13  recall@IoU0.3=1.00  latency=13ms   mean IoU 0.73–0.81
MSER                    candidates=1   recall@IoU0.3=0.00  latency=101ms (swept 3 delta/area configs — same result each time)
```

MSER wants graded-intensity, textured blob regions (its natural habitat is scene text with lighting gradients). Crisp, solid-fill vector-rendered glyphs on a flat background collapse to one or two giant extremal regions instead of per-glyph ones — confirmed, not assumed, across three parameter sweeps. It's a known technique name that turned out to be the wrong fit for *this* content; worth remembering when the next tempting-sounding technique comes up. `detect_screen()` already proves Canny+contour is the right family for this codebase — it's the same approach, one layer down.

D (a lightweight trained detector) would let one model do localization *and* vital-association together, and has real headroom over the hybrid. But `train_cnn.py` today trains a 28×28 *glyph classifier* — a different architecture (no bounding-box regression, no NMS, no anchor grid) and a training loop that doesn't exist. Torch is already a dependency, so it's buildable without new packages — but it's genuine training-infrastructure work, not a config change, which is why it ranks below the hybrid for a first version.

---

## 04 — Recommended architecture

### The smallest slice that could plausibly work

| Stage | Status | Notes |
|---|---|---|
| Screen detection + rectification | unchanged | `detect_screen()` — already colour-agnostic |
| Candidate region generation | new | Adaptive-threshold+CC primary, Canny+contour as ensemble signal — both POC-validated above |
| Field classifier (which vital, or reject) | new | Same `DigitCNN` architecture, retargeted: candidate crop → {hr, spo2, nibp, etco2, temp, rr, not-a-vital} |
| ROI extraction | unchanged contract | Same `VitalRoiResult`; `source_colour` becomes optional |
| OCR (`read_vital`) | unchanged | Tesseract or ONNX, doesn't know how its crop was found |
| Confidence fusion | new | See §07 — same 0–100 output shape |
| reconcile / alerts / persistence / WS | unchanged | Strictly downstream of both engines already |

No parallel camera pipeline — this replaces one stage inside the existing `read_frame()` call graph, selected by config (§10), exactly the seam identified in §01.

---

## 05 — Training data strategy

### The simulator has camera-artifact diversity. It has zero layout diversity.

`simulator/randomize/augment.py` already varies perspective, glare, dim, blur, noise, and JPEG compression convincingly — that axis is solid and reusable as-is. But `VITAL_COLORS`, fonts, and box positions are **fixed constants** across all 3 existing layouts (grid/sidebar/compact). Randomizing colour while keeping everything else fixed — the failure mode explicitly flagged for this spike — is exactly what today's simulator does *not* do, and exactly what it would need to start doing.

New axis needed (`simulator/randomize/layout_random.py`, additive — doesn't touch the 3 existing layouts): per-render random colour palette (not `VITAL_COLORS`), font pool (4–6 real system fonts, not one Consolas fallback chain), randomized box positions/sizes, light-*and*-dark monitor themes, a rendered bezel + room background so screen detection trains end-to-end too.

- **Train/val/test** — drawn from the randomizer, effectively unlimited supply; stratify by colour-theme and layout-seed the same way `build_dataset.py` already stratifies by augment-level.
- **Real holdout** — a small (20–50+) hand-photographed set of actual devices, **never** mixed into train/val. This has to be collected by a person; no synthetic axis substitutes for it, which is exactly why it's the generalization test.

---

## 06 — Detection target design

### Seven classes, not generic text

Closest to **Option A** (six fixed classes) — but reached via the hybrid's two stages rather than one multi-head detector: candidate generation stays class-agnostic (free, no training), and a small classifier assigns **7 classes** — the six vitals plus an explicit `not-a-vital` reject class — reusing `DigitCNN`'s architecture and training loop nearly unchanged, just retargeted from single-glyph to whole-candidate-crop.

NIBP needs no special detector logic: once its two-line block is identified as one candidate, it flows into the *same* `read_vital("nibp")` call that already splits sys/dia/mean downstream (`ocr.py`'s `_split_text_lines` / `segment.py`'s `TWO_LINE_VITALS`) — worth confirming the candidate generator's dilation kernel merges NIBP's two lines into one box on real content, in the follow-up POC.

---

## 07 — Confidence fusion design

### Gate on the weakest signal, never average it away

Combine detector confidence, OCR confidence, digit-CNN confidence (when `OCR_ENGINE=onnx`), and format validity via **MIN, not average** — one weak signal must not be masked by a strong one. This matches the codebase's own existing idiom exactly: `OnnxDigitEngine` already gates a whole number to unreadable on one bad glyph; `reconcile()` already holds-last-confirmed on any single rejection reason. Reuse it rather than inventing a new scheme.

> **Explicit exclusion:** Physiological plausibility stays **downstream-only**, inside `reconcile()`, exactly where it already lives — it must never feed back into this fusion. A physiologically-normal number read off weak visual evidence has to stay low-confidence; folding plausibility into the vision score would let a lucky guess manufacture confidence it didn't earn.

Output shape is unchanged: one float, 0–100, per vital — `reconcile()`'s `confidence_tier()` needs no changes at all.

---

## 08 — Real-monitor benchmark plan

### Build the ruler before the thing it measures

One-time hand-labeled ground truth (box + value) for 20–50+ real photos, stored in the same `sample_XXXX.json` shape `app/eval/harness.py` and `measure_roi_jitter.py` already read — so all existing IoU/eval tooling works against real photos unmodified.

1. **Region recall** — % of ground-truth fields with a candidate @ IoU ≥ 0.3 (the exact metric used above)
2. **OCR accuracy** — % correct strings among successfully-localized fields (isolates OCR error from localization error)
3. **Complete-vital accuracy** — % where localization *and* the read value both match — the number that actually matters clinically
4. **False-positive rate** — confident hits on non-vital content (waveforms, labels, UI chrome) — what the reject class exists to control
5. **Latency** — wall-clock per frame

This harness is buildable in isolation, before any model work — every later decision should move a real number here, not a plausible-sounding technique name (see MSER, §03).

---

## 09 — Performance targets

### OCR is the bottleneck either way

Measured this session: Tesseract's `read_vital` costs **1.4–1.9s per frame** for all 6 vitals — unchanged by anything here, since OCR itself isn't touched. The new candidate-generation stage measured **13–50ms**; a batched CNN classifier over ~13 candidates should land in the same tens-of-milliseconds range as the existing 398KB `digit_cnn.onnx`'s batched inference.

Net: Tier-2 adds roughly 50–150ms against an existing 1.4–1.9s stage. The original milestone's 1–2 fps target stays realistic without any batching, caching, or engine swap on Tier-2's account. If latency ever needs to drop, `OCR_ENGINE=onnx` (already implemented, opt-in) is the bigger lever — not the new detector.

---

## 10 — Backward compatibility

### One more orthogonal switch, same pattern already in use

Add `ROI_ENGINE=colour` (default, unchanged Tier-1) / `ROI_ENGINE=tier2`, via a second lazy-singleton factory mirroring `read_frame.py`'s existing `get_default_engine()` exactly — env var, opt-in, gated on the model file actually existing, never silently switched. `OCR_ENGINE` and `ROI_ENGINE` are already orthogonal in the interface (`read_frame()` calls ROI extraction, then OCR, as two separate steps) — any of {colour, tier2} × {tesseract, onnx} becomes selectable without touching reconcile, alerts, or persistence, which sit strictly downstream of both.

Product-level: Calibration's camera-vs-screen selection (built this session) is the natural place to also capture "known VITAL monitor" vs. "unknown/real monitor" as a per-session override — worth a follow-up product decision, not an architecture blocker.

---

## 11 — Failure safety

### It already knows how to say "I don't know"

The reject class from §06 is the gate: a candidate that doesn't clear a minimum field-classifier confidence is never associated with any vital — same "return `None`, never guess" contract every existing engine already follows. If HR is confidently found and NIBP isn't, HR proceeds through OCR and `reconcile()` normally while NIBP's fields flow in as `None` — `reconcile()` already handles this per-field, independently, today, for genuine Tier-1 misses. **Zero new logic needed** for partial detection — it's the same path a Tier-1 miss already takes, which is the strongest sign §01's seam is the right one.

---

## 12 — Effort, milestones, and risks

### ~2–3 weeks, benchmark-first

| # | Milestone | Time | Notes |
|---|---|---|---|
| 1 | Benchmark harness + real-photo holdout | 1–2 days | Mostly manual photo collection/labeling; reuses existing IoU/eval-harness math. Build this **first** — everything after is measured against it. |
| 2 | Layout/colour/font randomizer | 2–3 days | New `simulator/randomize` axis: colour, font, position, light/dark theme diversity — additive, doesn't touch the 3 existing layouts. |
| 3 | Candidate generator as `ROI_ENGINE=tier2` | ~1 day | Classical-only first pass, validated against Milestone 1's harness. Most of today's POC code is already most of this. |
| 4 | Field-classifier CNN | 2–3 days | Dataset build + train + ONNX export, reusing `train_cnn.py`'s architecture and tooling almost unchanged. |
| 5 | Fusion, config plumbing, integration tests, real benchmark run | 2–3 days | Mirrors the style of `test_pipeline_roi.py`. Ends with an actual number against real photos, not a projection. |

**Total: ~8–12 focused engineering days** for a first working version — realistically **2–3 calendar weeks** once the inevitable real-photo tuning loop is included (this codebase's own S4/S11 history — the jitter-measurement task before the digit CNN could train cleanly — shows that loop is real, not hypothetical, here).

### Biggest open risk

Real monitors span both antialiased flat-panel UI fonts (favorable — closer to what the simulator already renders) *and* genuine seven-segment LED/LCD digit displays (unfavorable — visually nothing like a rendered vector font). The photographed device this session appears to be the favorable case; a training set built only from rendered fonts may not transfer to the unfavorable one without adding segment-style rendering to the simulator — a content gap, not just a colour gap.

### Other risks

Glare/reflection on real curved glass exceeds the synthetic glare model's range; synthetic-vs-real camera sensor statistics are a classic, never-fully-closed domain-gap problem — the holdout benchmark is the only honest measurement of it, and it's inherently iterative. Scope discipline matters most here: "generalized recognition" can silently expand into solving OCR, detection, and domain adaptation as three separate research problems — §04's hybrid is deliberately the smallest slice that reuses three already-proven pieces of this exact repo instead.

---

*vital / backend/app/pipeline · spike only, no implementation changes · POC script + foreign-palette proxy frame available on request*
